"""hidraw access to an NZXT Lift Elite, via cable or via the 2.4 GHz receiver.

The library talks to ``/dev/hidrawN`` with ``os.open`` / ``os.read`` /
``os.write`` and a ``select`` timeout. The right node is the one whose
uevent advertises HID_ID vendor 1E71 and product 2129 (mouse, cable) or
2101 (receiver), on USB interface 01 (the vendor interface). The cable
exposes three hidraw nodes; only the vendor interface is opened. The same
``4e 02 …`` packets work on both: the receiver forwards them to the mouse.

The report descriptor tells whether 0x4E is a Report ID: if it is, the
64-byte packets are written as-is, otherwise a leading 0x00 is prepended
(and stripped on read if the kernel echoes it).

Several programs (CLI, GUI, CoolerControl plugin) may open the node at the
same time; every exchange takes an exclusive ``flock`` on the node so their
requests and replies cannot interleave.

Safety: :meth:`LiftElite._write` refuses every packet that is not one of
the known read/write configuration commands (see
:data:`nzxt_lift_elite.protocol.SAFE_COMMANDS`). Firmware and boot commands
can never be sent through this class.
"""

from __future__ import annotations

import errno
import fcntl
import os
import select
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import DangerousCommandError, DeviceBusyError, DeviceNotFoundError, ProtocolError, VerificationError
from .profile import Profile, ProfileConfig, diff_profiles
from .protocol import (
    BUTTON_NAMES,
    BUSY_PREFIX,
    FACTORY_REMAP,
    PACKET_SIZE,
    PRODUCT_ID,
    PRODUCT_ID_DONGLE,
    PRODUCT_IDS,
    ENABLE_ACTIVE_PROFILE_APPLY,
    READ_ONLY_COMMANDS,
    REPLY_GET_ACTIVE_PROFILE_HDR,
    REPLY_SET_ACTIVE_PROFILE_HDR,
    REPLY_ID_WORD_HDR,
    REPLY_OK,
    REPLY_STATUS_HDR,
    REPLY_UID_HDR,
    TARGET_DONGLE,
    TARGET_MOUSE,
    VENDOR_ID,
    VENDOR_INTERFACE,
    FirmwareInfo,
    assert_safe_packet,
    command_of,
    format_action,
    format_hex,
    info_packet,
    info_reply_prefix,
    is_busy_reply,
    id_word_packet,
    parse_active_profile_reply,
    parse_button,
    parse_button_reply,
    parse_info_reply,
    read_active_profile_packet,
    read_button_packet,
    read_button_reply_prefix,
    read_profile_packet,
    read_profile_reply_prefix,
    remap_packet,
    set_active_profile_packet,
    status_packet,
    uid_packet,
    validate_profile_index,
    write_profile_packet,
    FACTORY_LED_COLORS,
)

SYS_HIDRAW = Path("/sys/class/hidraw")
DEFAULT_TIMEOUT = 1.0
DEFAULT_RETRIES = 3
LOCK_TIMEOUT = 5.0

TRANSPORT_WIRED = "wired"
TRANSPORT_WIRELESS = "wireless"
TRANSPORTS = ("auto", TRANSPORT_WIRED, TRANSPORT_WIRELESS)

DebugHook = Callable[[str, bytes], None]


# --- report descriptor ------------------------------------------------------


@dataclass(frozen=True)
class ReportDescriptorInfo:
    report_ids: frozenset[int]
    usage_pages: frozenset[int]

    @property
    def has_vendor_usage_page(self) -> bool:
        return any(page >= 0xFF00 or page == 0x00FF for page in self.usage_pages)

    def uses_report_id(self, report_id: int) -> bool:
        return report_id in self.report_ids


def parse_report_descriptor(data: bytes) -> ReportDescriptorInfo:
    """Parse HID short/long items enough to collect Usage Pages and Report IDs."""
    report_ids: set[int] = set()
    usage_pages: set[int] = set()
    i = 0
    end = len(data)
    while i < end:
        prefix = data[i]
        # Long item: 0xFE, size, long tag, data...
        if prefix == 0xFE:
            if i + 2 >= end:
                break
            size = data[i + 1]
            i += 3 + size
            continue
        b_size = prefix & 0x03
        b_type = (prefix >> 2) & 0x03
        b_tag = (prefix >> 4) & 0x0F
        data_len = (4 if b_size == 3 else b_size)
        i += 1
        if i + data_len > end:
            break
        item = data[i : i + data_len]
        i += data_len
        value = int.from_bytes(item, "little") if item else 0
        if b_type == 1 and b_tag == 0:  # Global, Usage Page
            usage_pages.add(value)
        elif b_type == 1 and b_tag == 8 and item:  # Global, Report ID
            report_ids.add(item[0])
    return ReportDescriptorInfo(
        report_ids=frozenset(report_ids),
        usage_pages=frozenset(usage_pages),
    )


def parse_uevent_hid_id(uevent: str) -> tuple[int, int, int] | None:
    """Return (bus, vendor, product) from a hidraw uevent blob, or None."""
    for line in uevent.splitlines():
        if line.startswith("HID_ID="):
            parts = line.split("=", 1)[1].strip().split(":")
            if len(parts) != 3:
                return None
            try:
                return tuple(int(p, 16) for p in parts)  # type: ignore[return-value]
            except ValueError:
                return None
    return None


def _read_interface_number(device_dir: Path) -> int | None:
    try:
        resolved = device_dir.resolve()
    except OSError:
        resolved = device_dir
    for parent in (device_dir, resolved, *resolved.parents):
        candidate = parent / "bInterfaceNumber"
        if candidate.is_file():
            try:
                text = candidate.read_text(encoding="ascii").strip()
            except OSError:
                return None
            try:
                return int(text, 16)
            except ValueError:
                return None
    return None


def _read_report_descriptor(device_dir: Path) -> bytes | None:
    try:
        return (device_dir / "report_descriptor").read_bytes()
    except OSError:
        return None


@dataclass(frozen=True)
class HidrawInfo:
    """A hidraw node that belongs to the Lift Elite (mouse or receiver)."""

    path: str
    name: str
    bus: int
    vendor_id: int
    product_id: int
    interface: int | None
    report_ids: frozenset[int]
    usage_pages: frozenset[int]
    has_vendor_usage_page: bool
    prepend_zero_report_id: bool

    @property
    def uses_4e_report_id(self) -> bool:
        return 0x4E in self.report_ids

    @property
    def transport(self) -> str:
        return TRANSPORT_WIRELESS if self.product_id == PRODUCT_ID_DONGLE else TRANSPORT_WIRED

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"


def inspect_hidraw(name: str, sys_root: Path = SYS_HIDRAW) -> HidrawInfo | None:
    """Return info if this hidraw node is a Lift Elite interface, else None."""
    device_dir = sys_root / name / "device"
    try:
        uevent = (device_dir / "uevent").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    ids = parse_uevent_hid_id(uevent)
    if ids is None:
        return None
    bus, vendor, product = ids
    if vendor != VENDOR_ID or product not in PRODUCT_IDS:
        return None
    interface = _read_interface_number(device_dir)
    desc_bytes = _read_report_descriptor(device_dir)
    if desc_bytes is not None:
        desc = parse_report_descriptor(desc_bytes)
        report_ids, usage_pages, has_vendor = desc.report_ids, desc.usage_pages, desc.has_vendor_usage_page
    else:
        report_ids, usage_pages, has_vendor = frozenset(), frozenset(), False
    return HidrawInfo(
        path=f"/dev/{name}",
        name=name,
        bus=bus,
        vendor_id=vendor,
        product_id=product,
        interface=interface,
        report_ids=report_ids,
        usage_pages=usage_pages,
        has_vendor_usage_page=has_vendor,
        # 0x4E is a Report ID -> write as-is; otherwise prepend report ID 0.
        prepend_zero_report_id=0x4E not in report_ids,
    )


def iter_lift_elite_hidraw(sys_root: Path = SYS_HIDRAW) -> list[HidrawInfo]:
    if not sys_root.is_dir():
        return []
    try:
        names = sorted(p.name for p in sys_root.iterdir() if p.name.startswith("hidraw"))
    except OSError:
        return []
    return [info for info in (inspect_hidraw(n, sys_root=sys_root) for n in names) if info is not None]


def _vendor_rank(info: HidrawInfo) -> int:
    if info.interface == VENDOR_INTERFACE and info.has_vendor_usage_page:
        return 0
    if info.interface == VENDOR_INTERFACE:
        return 1
    if info.has_vendor_usage_page:
        return 2
    return 3


def choose_vendor_hidraw(candidates: Iterable[HidrawInfo], transport: str = "auto") -> HidrawInfo | None:
    """Pick the vendor node: cable first (``auto``), then the receiver."""
    items = list(candidates)
    if transport == TRANSPORT_WIRED:
        items = [i for i in items if i.product_id == PRODUCT_ID]
    elif transport == TRANSPORT_WIRELESS:
        items = [i for i in items if i.product_id == PRODUCT_ID_DONGLE]
    if not items:
        return None
    # Only nodes that look like the vendor interface; fall back to anything.
    vendorish = [i for i in items if _vendor_rank(i) < 3] or items
    return sorted(vendorish, key=lambda i: (0 if i.product_id == PRODUCT_ID else 1, _vendor_rank(i), i.name))[0]


def list_devices(sys_root: Path = SYS_HIDRAW) -> list[HidrawInfo]:
    """Every vendor node found, cable first. Sysfs only: nothing is opened."""
    out = []
    for transport in (TRANSPORT_WIRED, TRANSPORT_WIRELESS):
        chosen = choose_vendor_hidraw(iter_lift_elite_hidraw(sys_root), transport)
        if chosen is not None:
            out.append(chosen)
    return out


# How long to keep retrying a PermissionError after a (re)plug: udev and
# systemd-logind apply the uaccess ACL a moment after the hidraw node appears.
PERMISSION_RETRY_SECONDS = 4.0


def open_hidraw(
    path: str,
    *,
    timeout: float = PERMISSION_RETRY_SECONDS,
    flags: int = os.O_RDWR | os.O_NONBLOCK,
    opener=os.open,
    sleeper=time.sleep,
) -> int:
    """Open ``path``, retrying PermissionError while udev settles the ACL.

    The first successful open is returned immediately. After ``timeout``
    seconds the last PermissionError is re-raised with the usual hint.
    ``opener`` and ``sleeper`` are injectable for tests.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    delay = 0.15
    last: PermissionError | None = None
    while True:
        try:
            return opener(path, flags)
        except PermissionError as exc:
            last = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PermissionError(
                    f"permesso negato su {path}. Installa 70-nzxt-lift-elite.rules "
                    "e ricollega il mouse/ricevitore (oppure: sudo udevadm trigger)."
                ) from exc
            sleeper(min(delay, remaining))
            delay = min(delay * 1.6, 0.7)
        except FileNotFoundError as exc:
            raise DeviceNotFoundError(f"{path} non esiste (mouse scollegato?)") from exc
    raise last  # pragma: no cover


def find_hidraw(sys_root: Path = SYS_HIDRAW, path: str | None = None, transport: str = "auto") -> HidrawInfo:
    """Locate the vendor hidraw node, or raise DeviceNotFoundError."""
    if path:
        name = Path(path).name
        info = inspect_hidraw(name, sys_root=sys_root)
        if info is None:
            # Explicit path without sysfs (tests, unusual setups).
            return HidrawInfo(
                path=path,
                name=name,
                bus=3,
                vendor_id=VENDOR_ID,
                product_id=PRODUCT_ID_DONGLE if transport == TRANSPORT_WIRELESS else PRODUCT_ID,
                interface=VENDOR_INTERFACE,
                report_ids=frozenset(),
                usage_pages=frozenset(),
                has_vendor_usage_page=False,
                prepend_zero_report_id=True,
            )
        return info
    chosen = choose_vendor_hidraw(iter_lift_elite_hidraw(sys_root=sys_root), transport)
    if chosen is None:
        what = {
            TRANSPORT_WIRED: "mouse via cavo (1e71:2129)",
            TRANSPORT_WIRELESS: "ricevitore (1e71:2101)",
        }.get(transport, "NZXT Lift Elite (1e71:2129 via cavo o ricevitore 1e71:2101)")
        raise DeviceNotFoundError(
            f"{what}: interfaccia hidraw non trovata. Il mouse o il ricevitore sono collegati? "
            "Serve la regola udev 70-nzxt-lift-elite.rules per l'accesso senza root."
        )
    return chosen


# --- device -----------------------------------------------------------------


@dataclass(frozen=True)
class DeviceState:
    """Everything the GUI shows, read in one go (read-only)."""

    info: HidrawInfo
    firmware: FirmwareInfo | None
    receiver_firmware: FirmwareInfo | None
    profiles: tuple[Profile, ...]
    status_raw: bytes | None = None
    # Internal slot 0..4 from command 0x44, or None if the read failed.
    # Interpretation (active onboard profile) is high-confidence but still
    # waiting for a real-mouse confirmation via `liftctl debug-dump`.
    active_profile: int | None = None


class LiftElite:
    """High-level access to one Lift Elite (via cable or receiver).

    Entry point for the CLI, the GUI and the CoolerControl plugin. All
    high-level methods take the device lock; reads never write.
    """

    def __init__(
        self,
        path: str | None = None,
        *,
        info: HidrawInfo | None = None,
        fd: int | None = None,
        transport: str = "auto",
        debug: DebugHook | bool | None = None,
        dry_run: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        sys_root: Path = SYS_HIDRAW,
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> None:
        self.info = info or find_hidraw(sys_root=sys_root, path=path, transport=transport)
        self.dry_run = dry_run
        self.timeout = timeout
        self.retries = retries
        self.lock_timeout = lock_timeout
        self._fd = fd
        self._owned_fd = False
        self._lock_depth = 0
        self.sent: list[bytes] = []  # every packet actually written (for tests / --debug)
        if isinstance(debug, bool):
            self._debug: DebugHook | None = _stderr_debug if debug else None
        else:
            self._debug = debug

    @property
    def transport(self) -> str:
        return self.info.transport

    @classmethod
    def open(cls, path: str | None = None, **kwargs: object) -> LiftElite:
        dev = cls(path=path, **kwargs)  # type: ignore[arg-type]
        dev.connect()
        return dev

    def connect(self) -> None:
        if self._fd is not None:
            return
        self._fd = open_hidraw(self.info.path)
        self._owned_fd = True

    def close(self) -> None:
        if self._owned_fd and self._fd is not None:
            os.close(self._fd)
        self._fd = None
        self._owned_fd = False

    def __enter__(self) -> LiftElite:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- low level ---------------------------------------------------------

    def _log(self, label: str, data: bytes) -> None:
        if self._debug is not None:
            self._debug(label, data)

    @contextmanager
    def locked(self):
        """Exclusive flock on the node for a sequence of exchanges (re-entrant)."""
        if self._fd is None:
            raise ProtocolError("device is not open")
        if self._lock_depth == 0:
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise DeviceBusyError(
                            "il mouse è occupato da un altro programma (GUI, liftctl o plugin CoolerControl)"
                        ) from None
                    time.sleep(0.05)
                except OSError as exc:
                    if exc.errno in (errno.EINVAL, errno.ENOTSUP, errno.EBADF):
                        break  # fd type without flock support (tests): no lock
                    raise
            self._flush_input()
        self._lock_depth += 1
        try:
            yield self
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0 and self._fd is not None:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:
                    pass

    def _encode_write(self, packet: bytes) -> bytes:
        if len(packet) != PACKET_SIZE:
            raise ProtocolError(f"protocol packet must be {PACKET_SIZE} bytes, got {len(packet)}")
        return b"\x00" + packet if self.info.prepend_zero_report_id else packet

    def _decode_read(self, data: bytes) -> bytes:
        if not data:
            return data
        if self.info.prepend_zero_report_id and data[0] == 0x00 and len(data) >= PACKET_SIZE + 1:
            data = data[1:]
        elif data[0] == 0x00 and len(data) > 1 and data[1] == 0x4E:
            data = data[1:]
        return data[:PACKET_SIZE]

    def _write(self, packet: bytes, *, actually_write: bool = True) -> None:
        # Hard safety gate: only known configuration commands, never firmware ones.
        assert_safe_packet(packet)
        self._log("OUT" if actually_write else "OUT (dry-run, non inviato)", packet)
        if not actually_write:
            return
        if self._fd is None:
            raise ProtocolError("device is not open")
        payload = self._encode_write(packet)
        written = os.write(self._fd, payload)
        if written != len(payload):
            raise ProtocolError(f"short write to {self.info.path}: {written}/{len(payload)}")
        self.sent.append(bytes(packet))

    def _read_available(self, remaining: float) -> bytes | None:
        if self._fd is None:
            raise ProtocolError("device is not open")
        ready, _, _ = select.select([self._fd], [], [], max(remaining, 0.0))
        if not ready:
            return None
        try:
            data = os.read(self._fd, PACKET_SIZE + 8)
        except BlockingIOError:
            return None
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.EIO):
                raise DeviceNotFoundError("il dispositivo è stato scollegato") from exc
            raise
        if not data:
            return None
        packet = self._decode_read(data)
        self._log("IN", packet)
        return packet

    def _flush_input(self) -> None:
        if self._fd is None:
            return
        while True:
            ready, _, _ = select.select([self._fd], [], [], 0)
            if not ready:
                return
            try:
                if not os.read(self._fd, PACKET_SIZE + 8):
                    return
            except (BlockingIOError, OSError):
                return

    def transact(
        self,
        packet: bytes,
        expect_prefix: bytes,
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> bytes:
        """Send ``packet`` and wait for a reply starting with ``expect_prefix``.

        ``4e e5`` busy replies and unrelated packets are skipped. Only
        read commands are retried; a write is sent once (retrying a write
        whose reply was lost would apply it twice).
        In dry-run mode writes are logged but not sent and return ``b""``.
        """
        cmd = command_of(packet)
        is_read = cmd in READ_ONLY_COMMANDS
        if self.dry_run and not is_read:
            self._write(packet, actually_write=False)
            return b""
        timeout = self.timeout if timeout is None else timeout
        retries = (self.retries if retries is None else retries) if is_read else 1
        last_unexpected: bytes | None = None
        with self.locked():
            for _attempt in range(max(retries, 1)):
                self._write(packet)
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining < 0:
                        break
                    incoming = self._read_available(remaining)
                    if incoming is None or is_busy_reply(incoming):
                        continue
                    if incoming[: len(expect_prefix)] == expect_prefix:
                        return incoming
                    last_unexpected = incoming
        detail = f"; ultima risposta inattesa: {last_unexpected[:8].hex(' ')}" if last_unexpected else ""
        hint = (
            " Il mouse è acceso e vicino al ricevitore? (in standby non risponde: muovilo e riprova)"
            if self.transport == TRANSPORT_WIRELESS
            else ""
        )
        raise ProtocolError(
            f"nessuna risposta {expect_prefix.hex(' ')} da {self.info.path} dopo {retries} tentativi{detail}.{hint}"
        )

    # --- reads -------------------------------------------------------------

    def firmware_info(self, target: int = TARGET_MOUSE) -> FirmwareInfo:
        """Read-only ``0b``: name, running mode and firmware versions."""
        reply = self.transact(info_packet(target), info_reply_prefix(target))
        return parse_info_reply(reply, target)

    def receiver_info(self) -> FirmwareInfo | None:
        """``0b`` sent to the receiver itself (only when connected through it).

        Not captured from CAM on the receiver yet (the command set is the
        same as the mouse's), so failures are swallowed and return None.
        """
        if self.transport != TRANSPORT_WIRELESS:
            return None
        try:
            return self.firmware_info(TARGET_DONGLE)
        except ProtocolError:
            return None

    def read_status(self) -> bytes:
        """Raw ``b0`` status reply (not decoded; see PROTOCOL.md)."""
        return self.transact(status_packet(), REPLY_STATUS_HDR)

    def read_uid(self) -> bytes:
        """Raw 16-byte payload of the read-only ``0x03`` device-id reply."""
        reply = self.transact(uid_packet(), REPLY_UID_HDR)
        return reply[4:20]

    def read_id_word(self) -> bytes:
        """Raw 4-byte payload of the read-only ``0xb8`` id-word reply."""
        reply = self.transact(id_word_packet(), REPLY_ID_WORD_HDR)
        return reply[4:8]

    def read_active_profile(self) -> int:
        """Read the onboard active profile index (internal slot 0..4) via ``0x44``.

        High confidence from firmware (app_cmd.c case 0x44) and a CAM
        capture; still treated as experimental until confirmed with
        ``liftctl debug-dump`` on the real mouse.
        """
        reply = self.transact(read_active_profile_packet(), REPLY_GET_ACTIVE_PROFILE_HDR)
        return parse_active_profile_reply(reply)

    def set_active_profile(self, index: int, *, verify: bool = True) -> int:
        """Switch the onboard active profile via ``0x45``. Disabled until confirmed."""
        validate_profile_index(index)
        if not ENABLE_ACTIVE_PROFILE_APPLY:
            raise DangerousCommandError(
                "attivazione del profilo (comando 0x45) non abilitata: "
                "in attesa di conferma sul mouse reale con liftctl debug-dump"
            )
        packet = set_active_profile_packet(index)
        with self.locked():
            self.transact(packet, REPLY_SET_ACTIVE_PROFILE_HDR)
            if self.dry_run:
                return index
            if not verify:
                return index
            read_back = self.read_active_profile()
            if read_back != index:
                raise VerificationError(
                    f"profilo attivo: scritto slot {index}, riletto slot {read_back}"
                )
            return read_back

    def debug_dump(self, *, with_buttons: bool = True) -> list[tuple[str, bytes, bytes | None, str | None]]:
        """Send every known safe read command and return (label, request, reply, error).

        Never writes. Never sends 0x45 or firmware/boot commands
        (f1/f4/f5/f6/a1-a5). A failed individual read is recorded and
        the dump continues.
        """
        jobs: list[tuple[str, bytes, bytes]] = [
            ("0b info", info_packet(), info_reply_prefix()),
            ("03 uid", uid_packet(), REPLY_UID_HDR),
            ("44 get-active-profile", read_active_profile_packet(), REPLY_GET_ACTIVE_PROFILE_HDR),
            ("b0 status", status_packet(), REPLY_STATUS_HDR),
            ("b8 id-word", id_word_packet(), REPLY_ID_WORD_HDR),
        ]
        for i in range(5):
            jobs.append((f"92 profile {i}", read_profile_packet(i), read_profile_reply_prefix(i)))
        if with_buttons:
            for i in range(5):
                for b, name in enumerate(BUTTON_NAMES):
                    jobs.append(
                        (
                            f"51 button p{i}/{name}",
                            read_button_packet(i, b),
                            read_button_reply_prefix(i, b),
                        )
                    )
        out: list[tuple[str, bytes, bytes | None, str | None]] = []
        with self.locked():
            for label, packet, prefix in jobs:
                try:
                    reply = self.transact(packet, prefix)
                    out.append((label, packet, reply, None))
                except (ProtocolError, OSError) as exc:
                    out.append((label, packet, None, str(exc)))
        return out

    def read_button(self, profile: int, button: int | str) -> int:
        bid = parse_button(button)
        reply = self.transact(read_button_packet(profile, bid), read_button_reply_prefix(profile, bid))
        return parse_button_reply(reply, profile, bid)[2]

    def read_buttons(self, profile: int) -> dict[str, int]:
        validate_profile_index(profile)
        with self.locked():
            return {name: self.read_button(profile, i) for i, name in enumerate(BUTTON_NAMES)}

    def read_profile(self, index: int, *, with_buttons: bool = True) -> Profile:
        with self.locked():
            reply = self.transact(read_profile_packet(index), read_profile_reply_prefix(index))
            buttons = self.read_buttons(index) if with_buttons else None
        return Profile.from_reply(reply, expected_index=index, buttons=buttons)

    def read_all_profiles(self, *, with_buttons: bool = True) -> list[Profile]:
        with self.locked():
            return [self.read_profile(i, with_buttons=with_buttons) for i in range(5)]

    def read_state(self, *, with_buttons: bool = True) -> DeviceState:
        with self.locked():
            fw = self.firmware_info()
            rx = self.receiver_info()
            profiles = tuple(self.read_all_profiles(with_buttons=with_buttons))
            active = None
            try:
                active = self.read_active_profile()
            except ProtocolError:
                pass
        return DeviceState(
            info=self.info, firmware=fw, receiver_firmware=rx, profiles=profiles, active_profile=active
        )

    # --- writes ------------------------------------------------------------

    def write_profile_body(self, profile: Profile, *, verify: bool = True) -> Profile:
        """Write the known body (polling / lift-off / DPI / motion sync / colour) of one slot."""
        packet = write_profile_packet(profile.index, profile.raw)
        with self.locked():
            self.transact(packet, REPLY_OK)
            if self.dry_run:
                return profile
            # CAM follows a profile write with a status read; do the same.
            try:
                self.read_status()
            except ProtocolError:
                pass
            if not verify:
                return profile
            back = self.read_profile(profile.index, with_buttons=False).with_buttons(profile.buttons)
        problems = diff_profiles(profile.with_buttons(None), back.with_buttons(None))
        if problems:
            raise VerificationError("la scrittura del profilo non è stata applicata: " + "; ".join(problems))
        return back

    def remap(self, profile: int, button: int | str, action: int | str, *, verify: bool = True,
              allow_unknown: bool = False) -> int:
        """Set one button of onboard ``profile`` and read it back."""
        packet = remap_packet(profile, button, action, allow_unknown=allow_unknown)
        code = packet[8]
        bid = packet[6]
        with self.locked():
            self.transact(packet, REPLY_OK)
            if self.dry_run or not verify:
                return code
            got = self.read_button(profile, bid)
        if got != code:
            raise VerificationError(
                f"tasto {BUTTON_NAMES[bid]} del profilo {profile}: letto {format_action(got)}, atteso {format_action(code)}"
            )
        return code

    def remap_reset(self, profile: int, *, verify: bool = True) -> None:
        """Restore the factory button map of ``profile`` (CAM's order)."""
        with self.locked():
            for bid, code in FACTORY_REMAP:
                self.remap(profile, bid, code, verify=verify)

    def apply_profile(self, target: Profile, *, verify: bool = True, current: Profile | None = None) -> Profile:
        """Make onboard slot ``target.index`` look like ``target``.

        Reads the slot first and only writes what differs (body and/or the
        single buttons that changed). Returns the profile read back.
        """
        with self.locked():
            cur = current or self.read_profile(target.index, with_buttons=target.buttons is not None)
            body_changed = cur.known_body() != target.known_body()
            result = cur
            if body_changed:
                # keep unknown trailing bytes from the device's current packet
                merged = Profile.from_reply(
                    cur.raw[:6] + target.raw[6:24] + cur.raw[24:], expected_index=target.index, buttons=cur.buttons
                )
                result = self.write_profile_body(merged, verify=verify).with_buttons(cur.buttons)
            if target.buttons is not None:
                new_buttons = dict(cur.buttons or {})
                for name in BUTTON_NAMES:
                    want = target.buttons.get(name)
                    if want is None or (cur.buttons is not None and cur.buttons.get(name) == want):
                        continue
                    new_buttons[name] = self.remap(target.index, name, want, verify=verify, allow_unknown=True)
                result = result.with_buttons(new_buttons)
        return result

    def restore_factory_led_colors(
        self, indexes: list[int] | None = None, *, verify: bool = True
    ) -> list[Profile]:
        """Write the factory RGB565 colour bytes of each slot and read them back.

        Only the colour field is changed; every other profile byte is kept.
        ``indexes`` are internal slots 0..4 (default: all five).
        """
        slots = list(range(5)) if indexes is None else list(indexes)
        results: list[Profile] = []
        with self.locked():
            for index in slots:
                if not 0 <= index < 5:
                    from .errors import ValidationError

                    raise ValidationError(f"profilo interno {index} non valido (0..4)")
                current = self.read_profile(index, with_buttons=False)
                want = FACTORY_LED_COLORS[index]
                if current.color == want:
                    results.append(current)
                    continue
                results.append(self.write_profile_body(current.with_changes(color=want), verify=verify))
        return results

    def apply_config(self, index: int, config: ProfileConfig, *, verify: bool = True) -> Profile:
        """Read slot ``index``, apply ``config`` (only its fields), write, verify."""
        with self.locked():
            current = self.read_profile(index, with_buttons=True)
            target = config.apply_to(current, target_index=index)
            return self.apply_profile(target, verify=verify, current=current)

    # 0.1 compatibility
    def write_profile(self, profile: Profile, *, verify: bool = True) -> Profile:
        return self.apply_profile(profile, verify=verify)


def _stderr_debug(label: str, data: bytes) -> None:
    import sys

    print(f"{label} ({len(data)} bytes)\n{format_hex(data)}", file=sys.stderr)
