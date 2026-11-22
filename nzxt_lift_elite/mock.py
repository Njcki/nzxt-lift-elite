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
    CAPTURED_STATUS_REPLY,
    CMD_GET_ACTIVE_PROFILE,
    CMD_ID_WORD,
    CMD_INFO,
    CMD_READ_BUTTON,
    CMD_READ_PROFILE,
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
    REPLY_UID_HDR,
    SAFE_COMMANDS,
    TARGET_DONGLE,
    TARGET_MOUSE,
    VENDOR_ID,
    captured_read_reply,
    pad_packet,
)


@dataclass
class MouseEmulator:
    busy_every: int = 0  # answer 4e e5 before every n-th reply (0 = never)
    ignore_writes: bool = False  # simulate a write that does not stick
    asleep: bool = False  # wireless mouse in standby: no replies at all
    profiles: dict[int, bytearray] = field(default_factory=dict)
    buttons: dict[tuple[int, int], int] = field(default_factory=dict)
    active_profile: int = 0
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
        return [p for p in self.log if p[4] in (CMD_WRITE_PROFILE, CMD_WRITE_BUTTON)]

    def handle(self, packet: bytes) -> list[bytes]:
        pkt = pad_packet(packet[:PACKET_SIZE])
        self.log.append(pkt)
        if pkt[0] != 0x4E or pkt[4] not in SAFE_COMMANDS:
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
            out.append(pad_packet(CAPTURED_STATUS_REPLY))
        elif cmd == CMD_UID:
            # Synthetic id (not a real device serial).
            out.append(pad_packet(REPLY_UID_HDR + bytes(range(16))))
        elif cmd == CMD_ID_WORD:
            out.append(pad_packet(REPLY_ID_WORD_HDR + bytes(range(8, 12))))
        elif cmd == CMD_GET_ACTIVE_PROFILE:
            out.append(pad_packet(REPLY_GET_ACTIVE_PROFILE_HDR + bytes((self.active_profile,))))
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
                    self.profiles[idx] = new
                out.append(pad_packet(REPLY_OK))
        elif cmd == CMD_READ_BUTTON:
            pp, bb = pkt[5], pkt[6]
            if (pp, bb) in self.buttons:
                out.append(pad_packet(REPLY_READ_BUTTON_HDR + bytes((pp, bb, 0x04, self.buttons[(pp, bb)]))))
        elif cmd == CMD_WRITE_BUTTON:
            pp, bb, kind, aa = pkt[5], pkt[6], pkt[7], pkt[8]
            if (pp, bb) in self.buttons and kind == 0x04:
                if not self.ignore_writes:
                    self.buttons[(pp, bb)] = aa
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
    fake = FakeHidraw(emulator)
    kwargs.setdefault("timeout", 0.5)
    dev = LiftElite(info=fake_info(transport), fd=fake.fd, **kwargs)
    return dev, fake
