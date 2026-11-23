"""NZXT Lift Elite vendor HID protocol (unofficial, reverse-engineered).

Packets are 64 bytes on the vendor interface (USB interface 1) of either the
mouse (``1e71:2129``, cable) or its 2.4 GHz receiver (``1e71:2101``). The
same packets work through both: byte 1 is the *target* (``02`` = mouse,
``01`` = receiver), so ``4e 02 …`` sent to the receiver is forwarded to the
mouse over the radio.

Frame layout (CAM's frame builder)::

    0  0x4e              magic
    1  target            1 = receiver, 2 = mouse
    2  0x80 | (n + 1)    n = payload bytes after the command byte
    3  frame index       0 for single-frame commands
    4  command
    5… payload

The device often answers ``4e e5 …`` (busy/ack) first; that is skipped.

This module is pure encoding/decoding with no I/O. See PROTOCOL.md and
FIRMWARE_UPDATE.md for where each field comes from and how sure we are.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import actions as _actions
from .errors import DangerousCommandError, ProtocolError, ValidationError
from .i18n import _

PACKET_SIZE = 64
MAGIC = 0x4E
BUSY_PREFIX = bytes((0x4E, 0xE5))

VENDOR_ID = 0x1E71
PRODUCT_ID = 0x2129  # mouse, USB cable
PRODUCT_ID_DONGLE = 0x2101  # 2.4 GHz receiver
PRODUCT_IDS = (PRODUCT_ID, PRODUCT_ID_DONGLE)
VENDOR_INTERFACE = 0x01

TARGET_DONGLE = 0x01
TARGET_MOUSE = 0x02

PROFILE_COUNT = 5
PROFILE_INDEX_MIN = 0
PROFILE_INDEX_MAX = 4
DPI_STAGE_COUNT = 5
DPI_STAGE_MIN = 1
DPI_STAGE_MAX = 5
# PAW3395: 26 000 DPI max. Every value CAM wrote was a multiple of 50, so be
# conservative and only allow 50-DPI steps.
DPI_VALUE_MIN = 50
DPI_VALUE_MAX = 26_000
DPI_STEP = 50
LIFTOFF_VALUES = (1, 2)  # 1 mm / 2 mm (CAM shows exactly these two)

# Offsets in the full 64-byte profile packet (read reply or write command).
OFF_POLLING = 6
OFF_LIFTOFF = 7
OFF_STAGE_COUNT = 8
OFF_STAGE = 9  # active DPI stage, 0-based (0 = first value, 4 = fifth); confirmed on device
OFF_DPI = 10  # 5 x uint16 little-endian
OFF_MASK = 20
OFF_COLOR = 21  # 2 bytes, RGB565 little-endian: stored profile-indicator field
OFF_MOTION = 23
BODY_START = 6
KNOWN_BODY_END = 24

# Factory LED colours as stored on a stock mouse (little-endian RGB565).
# These exact bytes were read from the device and decode to the manual's
# Blue / Red / Green / Yellow / Purple. The factory colours flash correctly
# for ~2 s on a long press of the purple button (confirmed on the real
# mouse). Custom colour writes (same encoding) are under test by the user.
# restore_factory_led_colors writes these exact bytes (not a re-encode of
# #rrggbb); the GUI / liftctl set --color / plugin also write the field.
FACTORY_LED_COLORS: tuple[bytes, ...] = (
    bytes.fromhex("1f00"),  # profile 1, Blue   (~#0000ff)
    bytes.fromhex("00f8"),  # profile 2, Red    (~#ff0000)
    bytes.fromhex("e007"),  # profile 3, Green  (~#00ff00)
    bytes.fromhex("40fd"),  # profile 4, Yellow (~#ffaa00)
    bytes.fromhex("1ff8"),  # profile 5, Purple (~#ff00ff)
)
FACTORY_LED_NAMES: tuple[str, ...] = ("Blue", "Red", "Green", "Yellow", "Purple")
FACTORY_LED_APPROX_HEX: tuple[str, ...] = ("#0000ff", "#ff0000", "#00ff00", "#ffaa00", "#ff00ff")


def header(cmd: int, payload_len: int = 0, target: int = TARGET_MOUSE) -> bytes:
    """The 5-byte header ``4e TT 80|(n+1) 00 CMD``."""
    return bytes((MAGIC, target, 0x80 | (payload_len + 1), 0x00, cmd))


# --- commands --------------------------------------------------------------
CMD_INFO = 0x0B  # 4e 02 81 00 0b -> 4e 02 9e 00 <30 bytes, no status byte>
CMD_UID = 0x03  # 4e 02 81 00 03 -> 4e 02 90 00 <16-byte device id>; CAM startup read
CMD_STATUS = 0xB0  # 4e 02 81 00 b0 -> 4e 02 97 00 01 …
CMD_ID_WORD = 0xB8  # 4e 02 81 00 b8 -> 4e 02 84 00 <4-byte id>; same word as bytes 8..11 of 0x03
CMD_GET_ACTIVE_PROFILE = 0x44  # 4e 02 81 00 44 -> 4e 02 82 00 01 PP; CAM CMD_GET_PROFILE_NUMBER
CMD_SET_ACTIVE_PROFILE = 0x45  # 4e 02 82 00 45 PP -> 4e 02 81 00 01; CAM CMD_SET_PROFILE_NUMBER
CMD_READ_PROFILE = 0x92  # 4e 02 82 00 92 PP -> 4e 02 94 00 01 PP <body>
CMD_WRITE_PROFILE = 0x93  # 4e 02 94 00 93 PP <body> -> 4e 02 82 00 01
CMD_READ_BUTTON = 0x51  # 4e 02 84 00 51 PP BB (00) -> 4e 02 85 00 01 PP BB 04 AA
CMD_WRITE_BUTTON = 0x50  # 4e 02 85 00 50 PP BB 04 AA -> 4e 02 82 00 01

# 0x44 / 0x45 are the firmware get/set pair for the onboard active profile
# (CAM names: CMD_GET_PROFILE_NUMBER / CMD_SET_PROFILE_NUMBER). Firmware
# (app_cmd.c case 0x44 / 0x45, FUN_000340f0) and a CAM pcap confirm the
# packet shapes. 0x45 is NOT in SAFE_COMMANDS: it is sent only through the
# explicit allowlist (exact `4e 02 82 00 45 PP` packet) by
# LiftElite.activate_profile — `liftctl activate` and, with this flag on, the
# GUI's "Rendi attivo" + Applica. Confirmed on the real mouse 2026-11-23.
ENABLE_ACTIVE_PROFILE_APPLY = True  # confirmed on the real mouse with `liftctl activate` (2026-11-23)

PROFILE_BODY_LEN = KNOWN_BODY_END - BODY_START  # 18 known bytes
REMAP_TYPE = 0x04

REPLY_OK = bytes((0x4E, TARGET_MOUSE, 0x82, 0x00, 0x01))
REPLY_INFO_HDR = bytes((0x4E, TARGET_MOUSE, 0x9E, 0x00))
REPLY_STATUS_HDR = bytes((0x4E, TARGET_MOUSE, 0x97, 0x00, 0x01))
# The 0x01 after the b0 header is not a fixed status byte: it is the
# firmware's "fuel gauge data valid" flag (0 right after power-up, before the
# first complete gauge read). Match only the 4-byte prefix so a 00 still
# parses (as "battery unknown") instead of timing out.
REPLY_STATUS_PREFIX = bytes((0x4E, TARGET_MOUSE, 0x97, 0x00))
REPLY_UID_HDR = bytes((0x4E, TARGET_MOUSE, 0x90, 0x00))
REPLY_ID_WORD_HDR = bytes((0x4E, TARGET_MOUSE, 0x84, 0x00))
REPLY_GET_ACTIVE_PROFILE_HDR = bytes((0x4E, TARGET_MOUSE, 0x82, 0x00, 0x01))
REPLY_SET_ACTIVE_PROFILE_HDR = bytes((0x4E, TARGET_MOUSE, 0x81, 0x00, 0x01))
REPLY_READ_PROFILE_HDR = bytes((0x4E, TARGET_MOUSE, 0x94, 0x00, 0x01))
REPLY_READ_BUTTON_HDR = bytes((0x4E, TARGET_MOUSE, 0x85, 0x00, 0x01))

# Legacy names kept for callers of 0.1
CMD_WRITE_PROFILE_HDR = bytes((0x4E, TARGET_MOUSE, 0x94, 0x00, CMD_WRITE_PROFILE))

# --- DANGEROUS: firmware / boot commands ------------------------------------
# These were decoded from CAM and the firmware (FIRMWARE_UPDATE.md). The
# update procedure is NOT verified on hardware (the mouse never entered its
# updater during CAM's attempts). The library refuses to send any of them:
# LiftElite._write raises DangerousCommandError. Do not remove that guard
# without a verified, tested procedure and a recovery plan.
DANGEROUS_CMD_FLASH_WRITE = 0xA1  # raw image chunk
DANGEROUS_CMD_FLASH_VERIFY = 0xA2  # checksum + size, commit
DANGEROUS_CMD_FLASH_END = 0xA3  # end write session
DANGEROUS_CMD_BRIDGE_BOOT = 0xA4  # bridge bootloader
DANGEROUS_CMD_FLASH_BEGIN = 0xA5  # erase partition at addr
DANGEROUS_CMD_RESET = 0xF1  # soft reset
DANGEROUS_CMD_UNKNOWN_F4 = 0xF4  # unknown, starts a timer
DANGEROUS_CMD_BOOT_MAILBOX = 0xF5  # "next boot: updater/app"
DANGEROUS_CMD_UNKNOWN_F6 = 0xF6  # unknown flag
DANGEROUS_COMMANDS = frozenset(
    {
        DANGEROUS_CMD_FLASH_WRITE,
        DANGEROUS_CMD_FLASH_VERIFY,
        DANGEROUS_CMD_FLASH_END,
        DANGEROUS_CMD_BRIDGE_BOOT,
        DANGEROUS_CMD_FLASH_BEGIN,
        DANGEROUS_CMD_RESET,
        DANGEROUS_CMD_UNKNOWN_F4,
        DANGEROUS_CMD_BOOT_MAILBOX,
        DANGEROUS_CMD_UNKNOWN_F6,
    }
)

#: Commands this library is allowed to send. Everything else is refused.
SAFE_COMMANDS = frozenset(
    {
        CMD_INFO,
        CMD_UID,
        CMD_STATUS,
        CMD_ID_WORD,
        CMD_GET_ACTIVE_PROFILE,
        CMD_READ_PROFILE,
        CMD_WRITE_PROFILE,
        CMD_READ_BUTTON,
        CMD_WRITE_BUTTON,
    }
)
#: Read-only subset (used by --dry-run, debug-dump, and tests that assert no writes).
#: CMD_SET_ACTIVE_PROFILE (0x45) is deliberately absent, even though the
#: packet format is known, until ENABLE_ACTIVE_PROFILE_APPLY is turned on.
READ_ONLY_COMMANDS = frozenset(
    {CMD_INFO, CMD_UID, CMD_STATUS, CMD_ID_WORD, CMD_GET_ACTIVE_PROFILE, CMD_READ_PROFILE, CMD_READ_BUTTON}
)


def command_of(packet: bytes) -> int | None:
    if len(packet) >= 5 and packet[0] == MAGIC:
        return packet[4]
    return None


#: Commands outside SAFE_COMMANDS that one explicit code path may send when it
#: passes ``allow=``. Only 0x45 (set active profile), used by
#: ``LiftElite.activate_profile`` (``liftctl activate``) and, once
#: ENABLE_ACTIVE_PROFILE_APPLY is on, by the GUI's Applica.
EXPLICIT_ALLOWLIST = frozenset({CMD_SET_ACTIVE_PROFILE})


def _check_set_active_profile(packet: bytes) -> None:
    """0x45 must be exactly ``4e 02 82 00 45 PP`` + zero padding, PP 0..4."""
    ok = (
        len(packet) == PACKET_SIZE
        and packet[1] == TARGET_MOUSE
        and packet[2] == 0x82
        and packet[3] == 0x00
        and packet[5] <= 4
        and not any(packet[6:])
    )
    if not ok:
        raise DangerousCommandError(f"invalid 0x45 packet: {packet[:8].hex(' ')}")


def assert_safe_packet(packet: bytes, *, allow: frozenset[int] = frozenset()) -> None:
    """Raise unless ``packet`` is one of the known, safe commands.

    ``allow`` may only contain commands from :data:`EXPLICIT_ALLOWLIST`;
    firmware/boot commands can never be allowed.
    """
    if not allow <= EXPLICIT_ALLOWLIST:
        raise DangerousCommandError(f"allowlist not permitted: {sorted(hex(c) for c in allow)}")
    if len(packet) < 5 or packet[0] != MAGIC or packet[1] not in (TARGET_MOUSE, TARGET_DONGLE):
        raise DangerousCommandError(f"refusing to send a malformed packet: {packet[:5].hex()}")
    cmd = packet[4]
    if cmd in DANGEROUS_COMMANDS:
        raise DangerousCommandError(
            f"refusing to send firmware/boot command 0x{cmd:02x}: the update "
            "procedure is unverified and could brick the mouse"
        )
    if cmd in allow:
        if cmd == CMD_SET_ACTIVE_PROFILE:
            _check_set_active_profile(packet)
        return
    if cmd not in SAFE_COMMANDS:
        raise DangerousCommandError(f"refusing to send unknown command 0x{cmd:02x}")
    if packet[1] == TARGET_DONGLE and cmd != CMD_INFO:
        raise DangerousCommandError("only the info read (0x0b) may be sent to the receiver")


# --- polling ---------------------------------------------------------------
POLLING_HZ = {0: 250, 1: 500, 2: 1000, 3: 2000, 4: 4000, 5: 8000}
HZ_TO_CODE = {hz: code for code, hz in POLLING_HZ.items()}
WIRELESS_MAX_HZ = 4000
WIRED_ONLY_HZ = 8000

# --- buttons ---------------------------------------------------------------
BUTTON_IDS = {"left": 0, "middle": 1, "right": 2, "forward": 3, "back": 4, "dpi": 5}
ID_TO_BUTTON = {v: k for k, v in BUTTON_IDS.items()}
BUTTON_NAMES = tuple(ID_TO_BUTTON[i] for i in range(len(ID_TO_BUTTON)))
BUTTON_COUNT = len(BUTTON_NAMES)
FACTORY_ACTIONS = _actions.FACTORY_ACTIONS
# Order in which CAM sends the factory reset (button, action).
FACTORY_REMAP: tuple[tuple[int, int], ...] = (
    (2, 0xA7),
    (4, 0xA9),
    (1, 0xA6),
    (5, 0xAA),
    (0, 0xA5),
    (3, 0xA8),
)
# 0.1 names, still accepted
ACTION_CODES = {"left": 0xA5, "middle": 0xA6, "right": 0xA7, "forward": 0xA8, "back": 0xA9, "dpi": 0xAA}
CODE_TO_ACTION = {v: k for k, v in ACTION_CODES.items()}
ACTION_MIN_MOUSE = _actions.MOUSE_ACTION_MIN


# --- helpers ---------------------------------------------------------------
def empty_packet() -> bytearray:
    return bytearray(PACKET_SIZE)


def pad_packet(data: bytes) -> bytes:
    if len(data) > PACKET_SIZE:
        raise ProtocolError(f"packet longer than {PACKET_SIZE} bytes: {len(data)}")
    if len(data) == PACKET_SIZE:
        return bytes(data)
    out = empty_packet()
    out[: len(data)] = data
    return bytes(out)


def is_busy_reply(packet: bytes) -> bool:
    return len(packet) >= 2 and packet[:2] == BUSY_PREFIX


def format_hex(data: bytes, width: int = 16) -> str:
    lines: list[str] = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_part = f"{' '.join(f'{b:02x}' for b in chunk):<{width * 3 - 1}}"
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:04x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def _u16le(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _put_u16le(buf: bytearray, offset: int, value: int) -> None:
    buf[offset] = value & 0xFF
    buf[offset + 1] = (value >> 8) & 0xFF


# --- validation ------------------------------------------------------------
def validate_profile_index(index: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int) or not (
        PROFILE_INDEX_MIN <= index <= PROFILE_INDEX_MAX
    ):
        raise ValidationError(f"profile must be {PROFILE_INDEX_MIN}..{PROFILE_INDEX_MAX}, got {index}")
    return index


def validate_polling_hz(hz: int) -> int:
    if hz not in HZ_TO_CODE:
        allowed = ", ".join(str(v) for v in POLLING_HZ.values())
        raise ValidationError(f"polling must be one of {allowed}, got {hz}")
    return hz


def validate_liftoff(value: int) -> int:
    if value not in LIFTOFF_VALUES:
        raise ValidationError(f"lift-off must be 1 or 2 (mm), got {value}")
    return value


def validate_stage(stage: int, stage_count: int = DPI_STAGE_COUNT) -> int:
    """User-facing DPI stage number (1..stage_count)."""
    if isinstance(stage, bool) or not isinstance(stage, int) or not (DPI_STAGE_MIN <= stage <= stage_count):
        raise ValidationError(f"DPI stage must be {DPI_STAGE_MIN}..{stage_count}, got {stage}")
    return stage


def decode_wire_stage(raw: int, stage_count: int = DPI_STAGE_COUNT) -> int:
    """Wire byte 0..count-1 → user-facing stage 1..count.

    Confirmed on the real mouse (2026-11-17): cycling the purple button produced
    bytes 0, 2, 3, 4, then wrapped to 0, and at 0 the pointer was very slow
    (400 DPI, the first stored stage).
    """
    if not 0 <= raw < stage_count:
        raise ProtocolError(f"implausible current DPI stage byte {raw} (count {stage_count})")
    return raw + 1


def encode_wire_stage(stage: int, stage_count: int = DPI_STAGE_COUNT) -> int:
    """User-facing stage 1..count → wire byte 0..count-1."""
    return validate_stage(stage, stage_count) - 1


def validate_dpi_value(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"DPI must be an integer, got {value!r}")
    if not (DPI_VALUE_MIN <= value <= DPI_VALUE_MAX):
        raise ValidationError(f"DPI must be {DPI_VALUE_MIN}..{DPI_VALUE_MAX}, got {value}")
    if value % DPI_STEP:
        raise ValidationError(f"DPI must be a multiple of {DPI_STEP}, got {value}")
    return value


def validate_dpi_list(values, count: int = DPI_STAGE_COUNT) -> list[int]:
    values = list(values)
    if len(values) != count:
        raise ValidationError(f"expected exactly {count} DPI stages, got {len(values)}")
    return [validate_dpi_value(v) for v in values]


def polling_warning(hz: int, transport: str | None = None) -> str | None:
    """Short (translated) warning for rates that depend on the connection."""
    if hz == WIRED_ONLY_HZ:
        if transport == "wireless":
            return _("8000 Hz works only wired: in wireless the mouse uses 4000 Hz at most.")
        return _("8000 Hz works only with the mouse connected by cable; in wireless the maximum is 4000 Hz.")
    if hz == WIRELESS_MAX_HZ and transport != "wired":
        return _("4000 Hz is the maximum with the receiver; 8000 Hz is available only wired.")
    return None


def parse_polling_byte(code: int) -> int:
    if code not in POLLING_HZ:
        raise ProtocolError(f"unknown polling code {code}")
    return POLLING_HZ[code]


def parse_button(name: str | int) -> int:
    if isinstance(name, int) and not isinstance(name, bool):
        if name not in ID_TO_BUTTON:
            raise ValidationError(f"button id must be 0..{BUTTON_COUNT - 1}, got {name}")
        return name
    key = str(name).strip().lower()
    if key.isdigit() and int(key) in ID_TO_BUTTON:
        return int(key)
    aliases = {"side-forward": "forward", "side-back": "back", "backward": "back", "wheel": "middle"}
    key = aliases.get(key, key)
    if key not in BUTTON_IDS:
        raise ValidationError(f"button must be one of {', '.join(BUTTON_IDS)}, got {name!r}")
    return BUTTON_IDS[key]


def parse_action(spec: str | int, *, allow_unknown: bool = False) -> int:
    return _actions.parse_action(spec, allow_unknown=allow_unknown)


def format_action(code: int) -> str:
    return _actions.action_name(code)


def validate_buttons(mapping: dict, *, allow_unknown: bool = False) -> dict[str, int]:
    """Validate ``{button: action}`` and return ``{canonical button: code}``."""
    out: dict[str, int] = {}
    for name, action in mapping.items():
        bid = parse_button(name)
        out[ID_TO_BUTTON[bid]] = parse_action(action, allow_unknown=allow_unknown)
    return out


# --- RGB565 profile colour ---------------------------------------------------
def rgb565_to_rgb(raw: bytes) -> tuple[int, int, int]:
    """Profile LED colour (bytes 21..22, little-endian RGB565) to 8-bit RGB.

    The five captured profiles are 0x001f blue, 0xf800 red, 0x07e0 green,
    0xfd40 yellow/orange and 0xf81f purple, which matches the colours the
    manual lists for profiles 1..5.
    """
    v = raw[0] | (raw[1] << 8)
    r5, g6, b5 = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r5 * 255 + 15) // 31, (g6 * 255 + 31) // 63, (b5 * 255 + 15) // 31


def rgb_to_rgb565(r: int, g: int, b: int) -> bytes:
    for c in (r, g, b):
        if not 0 <= c <= 255:
            raise ValidationError(f"colour component out of range: {c}")
    v = ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)
    return bytes((v & 0xFF, v >> 8))


def parse_color(text: str) -> bytes:
    """``#rrggbb``/``rrggbb`` -> RGB565 bytes; ``rgb565:1f00`` passes through raw."""
    t = text.strip().lower()
    if t.startswith("rgb565:"):
        try:
            raw = bytes.fromhex(t[7:])
        except ValueError as exc:
            raise ValidationError(f"invalid RGB565 hex {text!r}") from exc
        if len(raw) != 2:
            raise ValidationError("RGB565 colour must be 2 bytes, e.g. rgb565:1f00")
        return raw
    t = t.lstrip("#")
    if len(t) != 6:
        raise ValidationError(f"colour must be #rrggbb, got {text!r}")
    try:
        r, g, b = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    except ValueError as exc:
        raise ValidationError(f"colour must be #rrggbb, got {text!r}") from exc
    return rgb_to_rgb565(r, g, b)


def color_hex(raw: bytes) -> str:
    r, g, b = rgb565_to_rgb(raw)
    return f"#{r:02x}{g:02x}{b:02x}"


# --- packet builders ---------------------------------------------------------
def info_packet(target: int = TARGET_MOUSE) -> bytes:
    return pad_packet(header(CMD_INFO, 0, target))


def info_reply_prefix(target: int = TARGET_MOUSE) -> bytes:
    return bytes((MAGIC, target, 0x9E, 0x00))


def status_packet() -> bytes:
    return pad_packet(header(CMD_STATUS))


def uid_packet() -> bytes:
    """Read-only 0x03: 16-byte device id (CAM startup, firmware copies UICR/FICR words)."""
    return pad_packet(header(CMD_UID))


def id_word_packet() -> bytes:
    """Read-only 0xb8: 4-byte id word (same value as bytes 8..11 of the 0x03 reply)."""
    return pad_packet(header(CMD_ID_WORD))


def read_active_profile_packet() -> bytes:
    """Read-only 0x44: get the onboard active profile index (0..4)."""
    return pad_packet(header(CMD_GET_ACTIVE_PROFILE))


def parse_active_profile_reply(data: bytes) -> int:
    """Return the internal slot (0..4) from a 0x44 reply ``4e 02 82 00 01 PP``."""
    if len(data) < 6 or data[:5] != REPLY_GET_ACTIVE_PROFILE_HDR:
        raise ProtocolError(f"risposta 0x44 inattesa: {data[:8].hex(' ')}")
    index = data[5]
    return validate_profile_index(index)


def set_active_profile_packet(index: int) -> bytes:
    """Build the 0x45 set-active-profile packet. Not in SAFE_COMMANDS."""
    validate_profile_index(index)
    return pad_packet(header(CMD_SET_ACTIVE_PROFILE, 1) + bytes((index,)))


def read_profile_packet(index: int) -> bytes:
    validate_profile_index(index)
    return pad_packet(header(CMD_READ_PROFILE, 1) + bytes((index,)))


def read_profile_reply_prefix(index: int) -> bytes:
    validate_profile_index(index)
    return REPLY_READ_PROFILE_HDR + bytes((index,))


def write_profile_packet(index: int, read_reply: bytes) -> bytes:
    """Build a 0x93 write from a 64-byte read reply, copying everything from byte 6."""
    validate_profile_index(index)
    if len(read_reply) < KNOWN_BODY_END:
        raise ProtocolError(f"profile read reply too short ({len(read_reply)} bytes)")
    pkt = empty_packet()
    pkt[:5] = CMD_WRITE_PROFILE_HDR
    pkt[5] = index
    pkt[BODY_START:] = pad_packet(read_reply)[BODY_START:]
    return bytes(pkt)


def read_button_packet(profile: int, button: int | str) -> bytes:
    validate_profile_index(profile)
    bid = parse_button(button)
    # CAM sends 4e 02 84 00 51 PP BB: the length byte counts 3 payload bytes,
    # the third one is 00.
    return pad_packet(header(CMD_READ_BUTTON, 3) + bytes((profile, bid, 0)))


def read_button_reply_prefix(profile: int, button: int | str) -> bytes:
    return REPLY_READ_BUTTON_HDR + bytes((validate_profile_index(profile), parse_button(button)))


def parse_button_reply(packet: bytes, profile: int | None = None, button: int | None = None) -> tuple[int, int, int]:
    """Return ``(profile, button, action)`` from ``4e 02 85 00 01 PP BB 04 AA``."""
    data = pad_packet(packet[:PACKET_SIZE])
    if data[:5] != REPLY_READ_BUTTON_HDR:
        raise ProtocolError(f"not a button reply: {data[:6].hex()}")
    pp, bb, kind, aa = data[5], data[6], data[7], data[8]
    if profile is not None and pp != profile:
        raise ProtocolError(f"button reply for profile {pp}, expected {profile}")
    if button is not None and bb != button:
        raise ProtocolError(f"button reply for button {bb}, expected {button}")
    if bb not in ID_TO_BUTTON:
        raise ProtocolError(f"button reply with unknown button {bb}")
    if kind != REMAP_TYPE:
        raise ProtocolError(f"button reply with unknown mapping type 0x{kind:02x} (expected 04)")
    return pp, bb, aa


def remap_packet(profile: int, button: int | str, action: int | str, *, allow_unknown: bool = False) -> bytes:
    """``4e 02 85 00 50 PP BB 04 AA``: set ``button`` of onboard ``profile``."""
    validate_profile_index(profile)
    bid = parse_button(button)
    code = parse_action(action, allow_unknown=allow_unknown)
    return pad_packet(header(CMD_WRITE_BUTTON, 4) + bytes((profile, bid, REMAP_TYPE, code)))


def factory_remap_packets(profile: int) -> list[bytes]:
    return [remap_packet(profile, b, a) for b, a in FACTORY_REMAP]


# --- profile body ----------------------------------------------------------
@dataclass(frozen=True)
class ProfileBody:
    """Decoded fields of a profile, plus the raw 64-byte packet."""

    index: int
    polling_hz: int
    liftoff: int
    stage_count: int
    stage: int
    dpi: tuple[int, ...]
    stage_mask: int
    color: bytes
    motion_sync: bool
    raw: bytes

    @property
    def current_dpi(self) -> int:
        return self.dpi[min(max(self.stage, 1), len(self.dpi)) - 1]


def parse_profile_reply(packet: bytes, expected_index: int | None = None) -> ProfileBody:
    if len(packet) < KNOWN_BODY_END:
        raise ProtocolError(f"profile packet too short ({len(packet)} bytes)")
    data = pad_packet(bytes(packet[:PACKET_SIZE]))
    if data[0] != MAGIC or data[1] != TARGET_MOUSE:
        raise ProtocolError(f"profile packet does not start with 4e 02: {data[:4].hex()}")
    index = data[5]
    if expected_index is not None and index != expected_index:
        raise ProtocolError(f"expected profile {expected_index}, reply has {index}")
    validate_profile_index(index)
    polling_hz = parse_polling_byte(data[OFF_POLLING])
    stage_count = data[OFF_STAGE_COUNT]
    if not 1 <= stage_count <= DPI_STAGE_COUNT:
        raise ProtocolError(f"implausible DPI stage count {stage_count}")
    stage = decode_wire_stage(data[OFF_STAGE], stage_count)
    dpi = tuple(_u16le(data, OFF_DPI + 2 * i) for i in range(DPI_STAGE_COUNT))
    return ProfileBody(
        index=index,
        polling_hz=polling_hz,
        liftoff=data[OFF_LIFTOFF],
        stage_count=stage_count,
        stage=stage,
        dpi=dpi,
        stage_mask=data[OFF_MASK],
        color=bytes(data[OFF_COLOR : OFF_COLOR + 2]),
        motion_sync=data[OFF_MOTION] != 0,
        raw=data,
    )


def parse_profile_body(body: bytes, index: int) -> ProfileBody:
    pkt = empty_packet()
    pkt[:6] = REPLY_READ_PROFILE_HDR + bytes((index,))
    pkt[BODY_START : BODY_START + len(body)] = body
    return parse_profile_reply(bytes(pkt), expected_index=index)


def apply_body_fields(
    raw: bytes,
    *,
    index: int | None = None,
    polling_hz: int | None = None,
    liftoff: int | None = None,
    stage: int | None = None,
    dpi=None,
    dpi_stages: dict[int, int] | None = None,
    motion_sync: bool | None = None,
    color: bytes | None = None,
) -> bytes:
    """Return a new 64-byte packet with only the requested fields changed.

    The stage count (byte 8) and the stage mask (byte 20) are never changed:
    their exact meaning is not confirmed (captures always show 5 / 0x0f), so
    ``dpi`` must carry exactly ``stage_count`` values. Unknown bytes are
    preserved from ``raw``.
    """
    data = bytearray(pad_packet(raw))
    count = data[OFF_STAGE_COUNT] or DPI_STAGE_COUNT
    if index is not None:
        data[5] = validate_profile_index(index)
    if polling_hz is not None:
        data[OFF_POLLING] = HZ_TO_CODE[validate_polling_hz(polling_hz)]
    if liftoff is not None:
        data[OFF_LIFTOFF] = validate_liftoff(liftoff)
    if dpi is not None:
        for i, value in enumerate(validate_dpi_list(dpi, count)):
            _put_u16le(data, OFF_DPI + 2 * i, value)
    if dpi_stages:
        for stage_no, value in dpi_stages.items():
            validate_stage(stage_no, count)
            _put_u16le(data, OFF_DPI + 2 * (stage_no - 1), validate_dpi_value(value))
    if stage is not None:
        data[OFF_STAGE] = encode_wire_stage(stage, count)
    if motion_sync is not None:
        data[OFF_MOTION] = 1 if motion_sync else 0
    if color is not None:
        if len(color) != 2:
            raise ValidationError("profile colour must be exactly 2 bytes (RGB565)")
        data[OFF_COLOR : OFF_COLOR + 2] = color
    return bytes(data)


def encoded_body(packet: bytes) -> bytes:
    """Known profile body (bytes 6..23) for comparisons."""
    return bytes(pad_packet(packet)[BODY_START:KNOWN_BODY_END])


# --- info (0x0b) -------------------------------------------------------------
MODE_UPDATER = 0
MODE_APPLICATION = 1
MODE_NAMES = {0: "updater", 1: "application", 2: "application-b"}


@dataclass(frozen=True)
class FirmwareInfo:
    """Reply to ``0b``: 30 bytes after ``4e TT 9e 00`` (no status byte)."""

    target: int
    name: str
    config: int
    mode: int
    app_version: tuple[int, int, int]
    bridge_version: tuple[int, int, int]
    updater_version: tuple[int, int, int]
    raw: bytes

    @property
    def mode_name(self) -> str:
        return MODE_NAMES.get(self.mode, f"unknown-{self.mode}")

    @staticmethod
    def fmt(v: tuple[int, int, int]) -> str:
        return f"{v[0]}.{v[1]}.{v[2]}"

    def to_dict(self) -> dict:
        return {
            "target": "mouse" if self.target == TARGET_MOUSE else "receiver",
            "name": self.name,
            "mode": self.mode_name,
            "app_version": self.fmt(self.app_version),
            "bridge_version": self.fmt(self.bridge_version),
            "updater_version": self.fmt(self.updater_version),
        }


def _version(data: bytes, off: int) -> tuple[int, int, int]:
    return data[off], data[off + 1], _u16le(data, off + 2)


def parse_info_reply(packet: bytes, target: int = TARGET_MOUSE) -> FirmwareInfo:
    data = pad_packet(bytes(packet[:PACKET_SIZE]))
    if data[:4] != info_reply_prefix(target):
        raise ProtocolError(f"not an info reply: {data[:4].hex()}")
    p = data[4:34]
    name = p[0:16].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return FirmwareInfo(
        target=target,
        name=name,
        config=p[16],
        mode=p[17],
        app_version=_version(p, 18),
        bridge_version=_version(p, 22),
        updater_version=_version(p, 26),
        raw=data,
    )


# --- battery / status (0xb0) -------------------------------------------------
# Decoded from the mouse firmware (Mouse_App 1.01.0005): the firmware
# command handler (case 0xb0) replies with 23 bytes:
#   [0]      gauge-valid flag (RAM 0x20005967; set to 1 after a full gauge read,
#            cleared when the gauge is re-initialised)
#   [1..18]  battery struct (RAM 0x2000596c, 18 bytes, u16 little-endian),
#            filled from an I2C fuel gauge whose register map matches the
#            CellWise CW2217 (table at 0x3ba64: reg 0x02 VCELL, 0x04 SOC,
#            0x06 TEMP, 0x0E CURRENT, 0xA4 cycle count, 0xA6 SOH):
#              +0  design capacity mAh (constant 320)
#              +2  cell voltage mV        (VCELL * 0.3125)
#              +4  current, signed         (median of 15 CURRENT samples, firmware-scaled;
#                                           > 0 while charging; unit provisional, ~mA)
#              +6  full capacity mAh (constant 320)
#              +8  remaining capacity mAh  (= capacity * SOC / 100)
#              +10 cycle count             (CYCLECNT >> 4)
#              +12 state of charge %       (gauge SOC; capped at 99 while charging)
#              +14 state of health %
#              +16 temperature °C (int8)   (TEMP / 2 - 40)
#              +17 charge state            (0 = on battery, 1 = charging, 2 = cable in and full)
#   [19..22] power-management word (RAM 0x200000e8; written by 0xb1, which this
#            library never sends; low half is the low-battery cut-off threshold)
# The percent therefore comes straight from the fuel gauge; no voltage curve
# is needed. battery_percent_from_mv() is only a fallback for an out-of-range
# SOC byte and marks the result as estimated.
STATUS_PAYLOAD = 4  # absolute offset of the payload in the 64-byte packet
CHARGE_DISCHARGING = 0
CHARGE_CHARGING = 1
CHARGE_FULL = 2
CHARGE_STATE_NAMES = {CHARGE_DISCHARGING: "discharging", CHARGE_CHARGING: "charging", CHARGE_FULL: "full"}
BATTERY_MV_MIN = 2800  # anything outside this window is treated as garbage
BATTERY_MV_MAX = 4500

# Typical 1-cell Li-ion/LiPo resting curve (mV, %), used only as a fallback.
LIION_CURVE: tuple[tuple[int, int], ...] = (
    (3300, 0), (3500, 4), (3600, 10), (3650, 18), (3700, 28), (3750, 40),
    (3800, 50), (3900, 65), (4000, 78), (4100, 90), (4200, 100),
)


def battery_percent_from_mv(mv: int) -> int:
    """Estimate the charge from the cell voltage (linear between LIION_CURVE points)."""
    pts = LIION_CURVE
    if mv <= pts[0][0]:
        return 0
    if mv >= pts[-1][0]:
        return 100
    for (v0, p0), (v1, p1) in zip(pts, pts[1:]):
        if v0 <= mv <= v1:
            return round(p0 + (p1 - p0) * (mv - v0) / (v1 - v0))
    return 0  # pragma: no cover


@dataclass(frozen=True)
class BatteryStatus:
    """Decoded ``b0`` reply. ``available`` is False when the gauge has no
    valid data yet or the values are out of range; ``percent`` is then None."""

    available: bool
    percent: int | None
    millivolts: int | None
    charge_state: int | None
    current_ma: int | None
    temperature_c: int | None
    health_percent: int | None
    cycles: int | None
    remaining_mah: int | None
    capacity_mah: int | None
    estimated: bool  # percent computed from millivolts (SOC byte out of range)
    raw: bytes  # the 23 payload bytes after ``4e 02 97 00``

    @property
    def charging(self) -> bool:
        return self.charge_state == CHARGE_CHARGING

    @property
    def external_power(self) -> bool:
        """Cable plugged in (charging or full)."""
        return self.charge_state in (CHARGE_CHARGING, CHARGE_FULL)

    @property
    def charge_state_name(self) -> str:
        if self.charge_state is None:
            return "unknown"
        return CHARGE_STATE_NAMES.get(self.charge_state, f"unknown-{self.charge_state}")

    @property
    def volts(self) -> float | None:
        return None if self.millivolts is None else self.millivolts / 1000

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "percent": self.percent,
            "estimated": self.estimated,
            "millivolts": self.millivolts,
            "charging": self.charging,
            "external_power": self.external_power,
            "charge_state": self.charge_state_name,
            "current_ma": self.current_ma,
            "temperature_c": self.temperature_c,
            "health_percent": self.health_percent,
            "cycles": self.cycles,
            "remaining_mah": self.remaining_mah,
            "capacity_mah": self.capacity_mah,
            "raw": self.raw.hex(),
        }


UNKNOWN_BATTERY = BatteryStatus(False, None, None, None, None, None, None, None, None, None, False, b"")


def parse_battery_reply(packet: bytes) -> BatteryStatus:
    """Decode a ``4e 02 97 00 …`` (``b0``) reply. Never raises for odd
    values: returns ``available=False`` instead; raises ProtocolError only
    when the packet is not a b0 reply at all."""
    data = pad_packet(bytes(packet[:PACKET_SIZE]))
    if data[:4] != REPLY_STATUS_PREFIX:
        raise ProtocolError(f"not a status reply: {data[:4].hex()}")
    p = data[STATUS_PAYLOAD : STATUS_PAYLOAD + 23]
    valid = p[0] == 1
    mv = _u16le(p, 3)
    current = _u16le(p, 5)
    if current >= 0x8000:
        current -= 0x10000
    soc = _u16le(p, 13)
    soh = p[15]
    temp = p[17] - 256 if p[17] >= 0x80 else p[17]
    state = p[18]
    sane_mv = BATTERY_MV_MIN <= mv <= BATTERY_MV_MAX
    if not valid or not sane_mv:
        return BatteryStatus(False, None, mv or None, state if valid else None, None, None, None, None, None,
                             None, False, bytes(p))
    estimated = soc > 100
    percent = battery_percent_from_mv(mv) if estimated else soc
    return BatteryStatus(
        available=True,
        percent=percent,
        millivolts=mv,
        charge_state=state if state in CHARGE_STATE_NAMES else None,
        current_ma=current,
        temperature_c=temp,
        health_percent=soh if soh <= 100 else None,
        cycles=_u16le(p, 11),
        remaining_mah=_u16le(p, 9),
        capacity_mah=_u16le(p, 7) or None,
        estimated=estimated,
        raw=bytes(p),
    )


def build_status_reply(*, percent: int = 100, millivolts: int = 4201, charge_state: int = CHARGE_FULL,
                       current_ma: int = 0, temperature_c: int = 24, health_percent: int = 100,
                       cycles: int = 0, valid: bool = True, capacity_mah: int = 320) -> bytes:
    """A b0 reply in the firmware layout (for the emulator and tests)."""
    pkt = empty_packet()
    pkt[:4] = REPLY_STATUS_PREFIX
    p = STATUS_PAYLOAD
    pkt[p] = 1 if valid else 0
    _put_u16le(pkt, p + 1, capacity_mah)
    _put_u16le(pkt, p + 3, millivolts)
    _put_u16le(pkt, p + 5, current_ma & 0xFFFF)
    _put_u16le(pkt, p + 7, capacity_mah)
    _put_u16le(pkt, p + 9, capacity_mah * percent // 100)
    _put_u16le(pkt, p + 11, cycles)
    _put_u16le(pkt, p + 13, percent)
    pkt[p + 15] = health_percent
    pkt[p + 17] = temperature_c & 0xFF
    pkt[p + 18] = charge_state
    pkt[p + 19 : p + 23] = bytes.fromhex("08004001")  # power word as captured
    return bytes(pkt)


# --- captured data (CAM, wired, used by tests and the mock device) ----------
CAPTURED_PROFILE_0_REPLY = bytes.fromhex("4e029400010005010501900120034006800c00190f1f0001")
CAPTURED_BODIES = {
    0: bytes.fromhex("05010501900120034006800c00190f1f0001"),
    1: bytes.fromhex("02010501900120034006800c00190f00f801"),
    2: bytes.fromhex("030105012003b0044006800c00190fe00701"),
    3: bytes.fromhex("02010502900120034006800c00190f40fd01"),
    4: bytes.fromhex("02010501900120034006800c00190f1ff801"),
}
# 0b reply: "HyperSonic", config 2, application mode, app 1.1.1, bridge 1.1.3, updater 1.1.0
CAPTURED_INFO_REPLY = bytes.fromhex(
    "4e029e00" + b"HyperSonic".hex() + "00" * 6 + "0201" + "01010100" + "01010300" + "01010000"
)
# b0 status reply as captured after a profile write (wired CAM session):
# gauge valid, 4201 mV, 0 mA, SOC 100 %, SOH 100 %, 24 °C, charge state 2
# (cable in, full). See parse_battery_reply / PROTOCOL.md.
CAPTURED_STATUS_REPLY = bytes.fromhex("4e02970001400169100000400140010000640064001802080040 01".replace(" ", ""))


def captured_read_reply(index: int) -> bytes:
    validate_profile_index(index)
    pkt = empty_packet()
    pkt[:6] = REPLY_READ_PROFILE_HDR + bytes((index,))
    body = CAPTURED_BODIES[index]
    pkt[BODY_START : BODY_START + len(body)] = body
    return bytes(pkt)
