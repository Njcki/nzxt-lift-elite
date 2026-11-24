"""A software Lift Elite for tests and for ``lift-elite-control --mock``.

:class:`MouseEmulator` answers the packets the library sends, with the
captured CAM replies as its initial state. :class:`FakeHidraw` wires it to a
real file descriptor (a ``SOCK_SEQPACKET`` socket pair, so ``select`` /
``os.read`` / ``os.write`` and packet boundaries behave like hidraw) and
serves it from a background thread.

The emulator records every packet and flags anything outside the known
command set (``unsafe``), so tests can assert that firmware commands are
never sent.
"""

from __future__ import annotations

import os
import select
import socket
import threading
from dataclasses import dataclass, field

from .device import HidrawInfo, LiftElite
from .protocol import (
    BUSY_PREFIX,
    CAPTURED_INFO_REPLY,
    CHARGE_CHARGING,
    CHARGE_DISCHARGING,
    CHARGE_FULL,
    LIION_CURVE,
    CMD_GET_ACTIVE_PROFILE,
    CMD_ID_WORD,
    CMD_INFO,
    CMD_LINK_STATUS,
    CMD_READ_BUTTON,
    CMD_READ_PROFILE,
    CMD_SET_ACTIVE_PROFILE,
    CMD_STATUS,
    CMD_UID,
    CMD_WRITE_BUTTON,
    CMD_WRITE_PROFILE,
    FACTORY_ACTIONS,
    PACKET_SIZE,
    PRODUCT_ID,
    PRODUCT_ID_DONGLE,
    REPLY_GET_ACTIVE_PROFILE_HDR,
    REPLY_ID_WORD_HDR,
    REPLY_OK,
    REPLY_READ_BUTTON_HDR,
    REPLY_READ_PROFILE_HDR,
    REPLY_SET_ACTIVE_PROFILE_HDR,
    REPLY_UID_HDR,
    SAFE_COMMANDS,
    TARGET_DONGLE,
    TARGET_MOUSE,
    VENDOR_ID,
    LINK_BIT_BRIDGE,
    LINK_BIT_USB_DATA,
    LINK_BIT_VBUS,
    OFF_STAGE,
    OFF_STAGE_COUNT,
    REPLY_LINK_STATUS_PREFIX,
    build_status_reply,
    captured_read_reply,
    pad_packet,
)


def mv_for_percent(percent: int) -> int:
    """Inverse of the Li-ion fallback curve (for realistic emulated voltages)."""
    pts = LIION_CURVE
    pct = max(0, min(100, percent))
    for (v0, p0), (v1, p1) in zip(pts, pts[1:]):
        if p0 <= pct <= p1:
            return round(v0 + (v1 - v0) * (pct - p0) / max(1, p1 - p0))
    return pts[-1][0]


@dataclass
class EmulatedBattery:
    """Fuel-gauge state answered in the b0 reply (firmware layout).

    Defaults reproduce the captured wired reply (full, cable in)."""

    percent: int = 100
    millivolts: int = 4201
    charge_state: int = CHARGE_FULL
    current_ma: int = 0
    temperature_c: int = 24
    valid: bool = True

    @classmethod
    def at(cls, percent: int, state: int = CHARGE_DISCHARGING) -> "EmulatedBattery":
        mv = mv_for_percent(percent)
        current = -9
        if state == CHARGE_CHARGING:
            mv, current = min(4200, mv + 90), 150  # charging lifts the cell voltage
        elif state == CHARGE_FULL:
            current = 0
        return cls(percent=percent, millivolts=mv, charge_state=state, current_ma=current, temperature_c=27)

    def reply(self) -> bytes:
        return build_status_reply(percent=self.percent, millivolts=self.millivolts, charge_state=self.charge_state,
                                  current_ma=self.current_ma, temperature_c=self.temperature_c, valid=self.valid)


_STATE_WORDS = {"charging": CHARGE_CHARGING, "full": CHARGE_FULL, "battery": CHARGE_DISCHARGING,
                "discharging": CHARGE_DISCHARGING}


def battery_from_env(transport: str, spec: str | None = None) -> EmulatedBattery:
    """Demo battery: ``LIFT_ELITE_MOCK_BATTERY=PCT[:charging|full|battery]`` or
    ``unknown`` (screenshots / demos only), else a realistic default: charging
    on the cable, on battery through the receiver."""
    spec = os.environ.get("LIFT_ELITE_MOCK_BATTERY", "") if spec is None else spec
    spec = spec.strip().lower()
    if spec == "unknown":
        b = EmulatedBattery.at(0)
        b.valid = False
        return b
    if spec:
        pct_s, _sep, word = spec.partition(":")
        try:
            pct = max(0, min(100, int(pct_s)))
        except ValueError:
            pct = 64
        return EmulatedBattery.at(pct, _STATE_WORDS.get(word, CHARGE_DISCHARGING))
    if transport == "wireless":
        return EmulatedBattery.at(64, CHARGE_DISCHARGING)
    return EmulatedBattery.at(72, CHARGE_CHARGING)


@dataclass
class MouseEmulator:
    busy_every: int = 0  # answer 4e e5 before every n-th reply (0 = never)
    ignore_writes: bool = False  # simulate a write that does not stick
    asleep: bool = False  # wireless mouse in standby: no replies at all
    profiles: dict[int, bytearray] = field(default_factory=dict)
    buttons: dict[tuple[int, int], int] = field(default_factory=dict)
    active_profile: int = 0
    battery: EmulatedBattery = field(default_factory=EmulatedBattery)
    # bc status bits: cable power | wired USB data | internal bridge link.
    # Default = on the cable; open_mock("wireless") clears them. None = the
    # mouse does not answer bc (e.g. a receiver that does not forward it).
    link_bits: int | None = LINK_BIT_VBUS | LINK_BIT_USB_DATA | LINK_BIT_BRIDGE
    log: list[bytes] = field(default_factory=list)
    unsafe: list[bytes] = field(default_factory=list)
    _n: int = 0

    def __post_init__(self) -> None:
        if not self.profiles:
            self.profiles = {i: bytearray(captured_read_reply(i)) for i in range(5)}
        if not self.buttons:
            self.buttons = {(p, b): FACTORY_ACTIONS[b] for p in range(5) for b in range(6)}

    @property
    def writes(self) -> list[bytes]:
        return [p for p in self.log if p[4] in (CMD_WRITE_PROFILE, CMD_WRITE_BUTTON, CMD_SET_ACTIVE_PROFILE)]

    def press_dpi_button(self) -> int:
        """Simulate a short press of the purple button: next DPI stage of the
        active profile (byte 9, 0-based, wraps at byte 8). Returns the new
        0-based stage. Like the firmware, nothing is sent to the host."""
        body = self.profiles[self.active_profile]
        count = body[8] or 1
        body[9] = (body[9] + 1) % count
        return body[9]

    def long_press_dpi_button(self) -> int:
        """Simulate a long press of the purple button: next onboard profile
        (wraps 5 -> 1). Returns the new internal slot. Nothing is sent."""
        self.active_profile = (self.active_profile + 1) % 5
        return self.active_profile

    def handle(self, packet: bytes) -> list[bytes]:
        pkt = pad_packet(packet[:PACKET_SIZE])
        self.log.append(pkt)
        if pkt[0] != 0x4E or pkt[4] not in SAFE_COMMANDS | {CMD_SET_ACTIVE_PROFILE}:
            self.unsafe.append(pkt)
            return []
        if self.asleep:
            return []
        self._n += 1
        out: list[bytes] = []
        if self.busy_every and self._n % self.busy_every == 0:
            out.append(pad_packet(BUSY_PREFIX + b"\x00\x01"))
        target, cmd = pkt[1], pkt[4]
        if target == TARGET_DONGLE:
            if cmd == CMD_INFO:
                info = bytearray(pad_packet(CAPTURED_INFO_REPLY))
                info[1] = TARGET_DONGLE
                info[4:14] = b"Receiver\x00\x00"
                out.append(bytes(info))
            return out
        if cmd == CMD_INFO:
            out.append(pad_packet(CAPTURED_INFO_REPLY))
        elif cmd == CMD_STATUS:
            out.append(self.battery.reply())
        elif cmd == CMD_UID:
            # Synthetic id (not a real device serial).
            out.append(pad_packet(REPLY_UID_HDR + bytes(range(16))))
        elif cmd == CMD_LINK_STATUS:
            if self.link_bits is not None:
                out.append(pad_packet(REPLY_LINK_STATUS_PREFIX + bytes((self.link_bits & 0xFF,))))
        elif cmd == CMD_ID_WORD:
            out.append(pad_packet(REPLY_ID_WORD_HDR + bytes(range(8, 12))))
        elif cmd == CMD_GET_ACTIVE_PROFILE:
            out.append(pad_packet(REPLY_GET_ACTIVE_PROFILE_HDR + bytes((self.active_profile,))))
        elif cmd == CMD_SET_ACTIVE_PROFILE:
            # Firmware case 0x45: PP > 4 keeps the current profile, then
            # FUN_000340f0(PP) (same routine as the long press).
            if pkt[5] <= 4:
                self.active_profile = pkt[5]
            out.append(pad_packet(REPLY_SET_ACTIVE_PROFILE_HDR))
        elif cmd == CMD_READ_PROFILE:
            idx = pkt[5]
            if idx in self.profiles:
                out.append(bytes(self.profiles[idx]))
        elif cmd == CMD_WRITE_PROFILE:
            idx = pkt[5]
            if idx in self.profiles:
                if not self.ignore_writes:
                    new = bytearray(pkt)
                    new[:6] = REPLY_READ_PROFILE_HDR + bytes((idx,))
                    # Firmware 0x93: the stage count is copied as-is, the
                    # active stage is forced to 0 when it is >= the count.
                    if new[OFF_STAGE] >= new[OFF_STAGE_COUNT]:
                        new[OFF_STAGE] = 0
                    self.profiles[idx] = new
                out.append(pad_packet(REPLY_OK))
        elif cmd == CMD_READ_BUTTON:
            pp, bb = pkt[5], pkt[6]
            if (pp, bb) in self.buttons:
                out.append(pad_packet(REPLY_READ_BUTTON_HDR + bytes((pp, bb, 0x04, self.buttons[(pp, bb)]))))
        elif cmd == CMD_WRITE_BUTTON:
            pp, bb, kind, aa = pkt[5], pkt[6], pkt[7], pkt[8]
            # Like the firmware (app_cmd.c case 0x50, type 04): the PP byte
            # is ignored and the ACTIVE profile's button is written.
            target = self.active_profile
            if pp < 5 and (target, bb) in self.buttons and kind == 0x04:
                if not self.ignore_writes:
                    self.buttons[(target, bb)] = aa
                out.append(pad_packet(REPLY_OK))
        return out


class FakeHidraw:
    """Serve a :class:`MouseEmulator` on a socket pair; ``fd`` is the host end."""

    def __init__(self, emulator: MouseEmulator | None = None, *, report_id_prefix: bool = True) -> None:
        self.emulator = emulator or MouseEmulator()
        self.report_id_prefix = report_id_prefix
        self._host, self._dev = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="fake-hidraw", daemon=True)
        self._thread.start()

    @property
    def fd(self) -> int:
        return self._host.fileno()

    def _serve(self) -> None:
        dev = self._dev.fileno()
        while not self._stop.is_set():
            ready, _, _ = select.select([dev], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(dev, 256)
            except OSError:
                return
            if not data:
                return
            if self.report_id_prefix and len(data) == PACKET_SIZE + 1 and data[0] == 0:
                data = data[1:]
            for reply in self.emulator.handle(data):
                try:
                    os.write(dev, reply)
                except OSError:
                    return

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._host.close()
        self._dev.close()

    def __enter__(self) -> FakeHidraw:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def fake_info(transport: str = "wired") -> HidrawInfo:
    pid = PRODUCT_ID_DONGLE if transport == "wireless" else PRODUCT_ID
    return HidrawInfo(
        path="/dev/hidraw-mock",
        name="hidraw-mock",
        bus=3,
        vendor_id=VENDOR_ID,
        product_id=pid,
        interface=1,
        report_ids=frozenset(),
        usage_pages=frozenset({0xFF00}),
        has_vendor_usage_page=True,
        prepend_zero_report_id=True,
    )


def open_mock(transport: str = "wired", emulator: MouseEmulator | None = None, **kwargs) -> tuple[LiftElite, FakeHidraw]:
    """Return a connected :class:`LiftElite` talking to a fresh emulator."""
    if emulator is None:
        emulator = MouseEmulator(battery=battery_from_env(transport))
        if transport == "wireless":
            emulator.link_bits = 0  # on battery, no USB: cable power / data / bridge bits clear
    fake = FakeHidraw(emulator)
    kwargs.setdefault("timeout", 0.5)
    dev = LiftElite(info=fake_info(transport), fd=fake.fd, **kwargs)
    return dev, fake
