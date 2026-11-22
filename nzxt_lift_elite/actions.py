"""Button actions of the NZXT Lift Elite (remap byte ``AA``).

The remap command carries a single action byte:

- ``0xa5..0xaa`` are the mouse's own actions (clicks, side buttons, DPI cycle);
- every other value is a USB HID usage on the Keyboard/Keypad page (0x07),
  which the mouse sends through its keyboard interface. CAM lists the whole
  table in usage order, from ``A`` (0x04) to ``KeypadHexadecimal`` (0xdd),
  then the modifiers (0xe0..0xe7) and four media keys (0xe8..0xeb).

The media keys are the *legacy keyboard-page* codes (0x7f..0x81 and
0xe8..0xeb), not Consumer-page usages. Linux' hid-input maps them to
KEY_MUTE, KEY_VOLUMEUP, KEY_VOLUMEDOWN, KEY_PLAYPAUSE, KEY_STOPCD,
KEY_PREVIOUSSONG and KEY_NEXTSONG, so they work without a driver.

Everything here is pure data; :func:`find_action` and :func:`parse_action`
turn user text into a code, :func:`action_name` does the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError

# Categories, in the order a picker should show them.
CAT_MOUSE = "mouse"
CAT_MEDIA = "media"
CAT_LETTERS = "letters"
CAT_DIGITS = "digits"
CAT_FUNCTION = "function"
CAT_MODIFIERS = "modifiers"
CAT_NAVIGATION = "navigation"
CAT_EDITING = "editing"
CAT_SYMBOLS = "symbols"
CAT_KEYPAD = "keypad"
CAT_SYSTEM = "system"
CAT_INTERNATIONAL = "international"
CAT_OTHER = "other"

CATEGORIES: tuple[str, ...] = (
    CAT_MOUSE,
    CAT_MEDIA,
    CAT_LETTERS,
    CAT_DIGITS,
    CAT_FUNCTION,
    CAT_MODIFIERS,
    CAT_NAVIGATION,
    CAT_EDITING,
    CAT_SYMBOLS,
    CAT_KEYPAD,
    CAT_SYSTEM,
    CAT_INTERNATIONAL,
    CAT_OTHER,
)

MOUSE_ACTION_MIN = 0xA5
MOUSE_ACTION_MAX = 0xAA


@dataclass(frozen=True)
class Action:
    code: int
    name: str  # canonical CLI/JSON name, e.g. "volume-up"
    label: str  # short English label (msgid for the GUI translation)
    category: str
    aliases: tuple[str, ...] = ()

    @property
    def is_mouse(self) -> bool:
        return MOUSE_ACTION_MIN <= self.code <= MOUSE_ACTION_MAX

    @property
    def is_key(self) -> bool:
        return not self.is_mouse

    @property
    def hex(self) -> str:
        return f"0x{self.code:02x}"


def _a(code: int, name: str, label: str, category: str, *aliases: str) -> Action:
    return Action(code, name, label, category, tuple(aliases))


_TABLE: list[Action] = [
    # --- mouse actions (confirmed with CAM, button Forward, 2026-11-17)
    _a(0xA5, "left-click", "Left click", CAT_MOUSE, "left", "click", "button-left"),
    _a(0xA6, "middle-click", "Middle click", CAT_MOUSE, "middle", "button-middle"),
    _a(0xA7, "right-click", "Right click", CAT_MOUSE, "right", "button-right"),
    _a(0xA8, "mouse-forward", "Mouse forward", CAT_MOUSE, "forward", "button-forward"),
    _a(0xA9, "mouse-back", "Mouse back", CAT_MOUSE, "back", "backward", "button-back"),
    _a(0xAA, "dpi-cycle", "DPI cycle", CAT_MOUSE, "dpi", "mouse-dpi"),
]

# Letters a..z (0x04..0x1d)
for _i in range(26):
    _c = chr(ord("a") + _i)
    _TABLE.append(_a(0x04 + _i, _c, _c.upper(), CAT_LETTERS))
# Digits 1..9, 0 (0x1e..0x27)
for _i in range(10):
    _d = str((_i + 1) % 10)
    _TABLE.append(_a(0x1E + _i, _d, _d, CAT_DIGITS))

_TABLE += [
    _a(0x28, "enter", "Enter", CAT_EDITING, "return"),
    _a(0x29, "escape", "Esc", CAT_EDITING, "esc"),
    _a(0x2A, "backspace", "Backspace", CAT_EDITING),
    _a(0x2B, "tab", "Tab", CAT_EDITING),
    _a(0x2C, "space", "Space", CAT_EDITING, "spacebar"),
    _a(0x2D, "minus", "- _", CAT_SYMBOLS, "dash"),
    _a(0x2E, "equal", "= +", CAT_SYMBOLS, "equals"),
    _a(0x2F, "left-bracket", "[ {", CAT_SYMBOLS, "lbracket"),
    _a(0x30, "right-bracket", "] }", CAT_SYMBOLS, "rbracket"),
    _a(0x31, "backslash", "\\ |", CAT_SYMBOLS),
    _a(0x32, "non-us-hash", "Non-US # ~", CAT_SYMBOLS),
    _a(0x33, "semicolon", "; :", CAT_SYMBOLS),
    _a(0x34, "apostrophe", "' \"", CAT_SYMBOLS, "quote"),
    _a(0x35, "grave", "` ~", CAT_SYMBOLS, "backtick"),
    _a(0x36, "comma", ", <", CAT_SYMBOLS),
    _a(0x37, "period", ". >", CAT_SYMBOLS, "dot"),
    _a(0x38, "slash", "/ ?", CAT_SYMBOLS),
    _a(0x39, "caps-lock", "Caps Lock", CAT_EDITING, "capslock"),
]
for _i in range(1, 13):  # F1..F12 (0x3a..0x45)
    _TABLE.append(_a(0x39 + _i, f"f{_i}", f"F{_i}", CAT_FUNCTION))
_TABLE += [
    _a(0x46, "print-screen", "Print Screen", CAT_SYSTEM, "printscreen", "print"),
    _a(0x47, "scroll-lock", "Scroll Lock", CAT_SYSTEM, "scrolllock"),
    _a(0x48, "pause", "Pause", CAT_SYSTEM, "break"),
    _a(0x49, "insert", "Insert", CAT_EDITING, "ins"),
    _a(0x4A, "home", "Home", CAT_NAVIGATION),
    _a(0x4B, "page-up", "Page Up", CAT_NAVIGATION, "pageup", "pgup"),
    _a(0x4C, "delete", "Delete", CAT_EDITING, "del"),
    _a(0x4D, "end", "End", CAT_NAVIGATION),
    _a(0x4E, "page-down", "Page Down", CAT_NAVIGATION, "pagedown", "pgdn"),
    _a(0x4F, "right-arrow", "Right arrow", CAT_NAVIGATION, "arrow-right"),
    _a(0x50, "left-arrow", "Left arrow", CAT_NAVIGATION, "arrow-left"),
    _a(0x51, "down-arrow", "Down arrow", CAT_NAVIGATION, "arrow-down", "down"),
    _a(0x52, "up-arrow", "Up arrow", CAT_NAVIGATION, "arrow-up", "up"),
    _a(0x53, "num-lock", "Num Lock", CAT_KEYPAD, "numlock"),
    _a(0x54, "keypad-slash", "Keypad /", CAT_KEYPAD, "kp-divide"),
    _a(0x55, "keypad-asterisk", "Keypad *", CAT_KEYPAD, "kp-multiply"),
    _a(0x56, "keypad-minus", "Keypad -", CAT_KEYPAD, "kp-minus"),
    _a(0x57, "keypad-plus", "Keypad +", CAT_KEYPAD, "kp-plus"),
    _a(0x58, "keypad-enter", "Keypad Enter", CAT_KEYPAD, "kp-enter"),
]
for _i in range(10):  # Keypad 1..9, 0 (0x59..0x62)
    _d = str((_i + 1) % 10)
    _TABLE.append(_a(0x59 + _i, f"keypad-{_d}", f"Keypad {_d}", CAT_KEYPAD, f"kp-{_d}", f"kp{_d}"))
_TABLE += [
    _a(0x63, "keypad-period", "Keypad .", CAT_KEYPAD, "kp-period", "kp-dot"),
    _a(0x64, "non-us-backslash", "Non-US \\ |", CAT_SYMBOLS, "102nd"),
    _a(0x65, "application", "Menu (Application)", CAT_SYSTEM, "compose", "context-menu"),
    _a(0x66, "power", "Power", CAT_SYSTEM),
    _a(0x67, "keypad-equal", "Keypad =", CAT_KEYPAD, "kp-equal"),
]
for _i in range(13, 25):  # F13..F24 (0x68..0x73)
    _TABLE.append(_a(0x68 + _i - 13, f"f{_i}", f"F{_i}", CAT_FUNCTION))
_TABLE += [
    _a(0x74, "execute", "Execute", CAT_SYSTEM),
    _a(0x75, "help", "Help", CAT_SYSTEM),
    _a(0x76, "menu", "Menu", CAT_SYSTEM),
    _a(0x77, "select", "Select", CAT_SYSTEM),
    _a(0x78, "kb-stop", "Stop (keyboard)", CAT_SYSTEM),
    _a(0x79, "again", "Again", CAT_EDITING, "redo"),
    _a(0x7A, "undo", "Undo", CAT_EDITING),
    _a(0x7B, "cut", "Cut", CAT_EDITING),
    _a(0x7C, "copy", "Copy", CAT_EDITING),
    _a(0x7D, "paste", "Paste", CAT_EDITING),
    _a(0x7E, "find", "Find", CAT_EDITING),
    # media (legacy keyboard page) — confirmed with CAM
    _a(0x7F, "mute", "Mute", CAT_MEDIA, "volume-mute"),
    _a(0x80, "volume-up", "Volume up", CAT_MEDIA, "volup", "vol-up"),
    _a(0x81, "volume-down", "Volume down", CAT_MEDIA, "voldown", "vol-down"),
    _a(0x82, "locking-caps-lock", "Locking Caps Lock", CAT_OTHER),
    _a(0x83, "locking-num-lock", "Locking Num Lock", CAT_OTHER),
    _a(0x84, "locking-scroll-lock", "Locking Scroll Lock", CAT_OTHER),
    _a(0x85, "keypad-comma", "Keypad ,", CAT_KEYPAD, "kp-comma"),
    _a(0x86, "keypad-equal-as400", "Keypad = (AS/400)", CAT_KEYPAD),
]
for _i in range(1, 10):  # International1..9 (0x87..0x8f)
    _TABLE.append(_a(0x86 + _i, f"international{_i}", f"International {_i}", CAT_INTERNATIONAL))
for _i in range(1, 10):  # LANG1..9 (0x90..0x98)
    _TABLE.append(_a(0x8F + _i, f"lang{_i}", f"LANG{_i}", CAT_INTERNATIONAL))
_TABLE += [
    _a(0x99, "alternate-erase", "Alternate Erase", CAT_OTHER),
    _a(0x9A, "sysreq", "SysReq / Attention", CAT_SYSTEM, "attention"),
    _a(0x9B, "cancel", "Cancel", CAT_OTHER),
    _a(0x9C, "clear", "Clear", CAT_OTHER),
    _a(0x9D, "prior", "Prior", CAT_OTHER),
    _a(0x9E, "return-key", "Return (alt)", CAT_OTHER),
    _a(0x9F, "separator", "Separator", CAT_OTHER),
    _a(0xA0, "out", "Out", CAT_OTHER),
    _a(0xA1, "oper", "Oper", CAT_OTHER),
    _a(0xA2, "clear-again", "Clear / Again", CAT_OTHER),
    _a(0xA3, "crsel", "CrSel / Props", CAT_OTHER),
    _a(0xA4, "exsel", "ExSel", CAT_OTHER),
    # 0xa5..0xaf are reserved in the HID table; the mouse uses a5..aa itself.
    _a(0xB0, "keypad-00", "Keypad 00", CAT_KEYPAD),
    _a(0xB1, "keypad-000", "Keypad 000", CAT_KEYPAD),
    _a(0xB2, "thousands-separator", "Thousands separator", CAT_KEYPAD),
    _a(0xB3, "decimal-separator", "Decimal separator", CAT_KEYPAD),
    _a(0xB4, "currency-unit", "Currency unit", CAT_KEYPAD),
    _a(0xB5, "currency-sub-unit", "Currency sub-unit", CAT_KEYPAD),
    _a(0xB6, "keypad-left-paren", "Keypad (", CAT_KEYPAD),
    _a(0xB7, "keypad-right-paren", "Keypad )", CAT_KEYPAD),
    _a(0xB8, "keypad-left-brace", "Keypad {", CAT_KEYPAD),
    _a(0xB9, "keypad-right-brace", "Keypad }", CAT_KEYPAD),
    _a(0xBA, "keypad-tab", "Keypad Tab", CAT_KEYPAD),
    _a(0xBB, "keypad-backspace", "Keypad Backspace", CAT_KEYPAD),
    _a(0xBC, "keypad-a", "Keypad A", CAT_KEYPAD),
    _a(0xBD, "keypad-b", "Keypad B", CAT_KEYPAD),
    _a(0xBE, "keypad-c", "Keypad C", CAT_KEYPAD),
    _a(0xBF, "keypad-d", "Keypad D", CAT_KEYPAD),
    _a(0xC0, "keypad-e", "Keypad E", CAT_KEYPAD),
    _a(0xC1, "keypad-f", "Keypad F", CAT_KEYPAD),
    _a(0xC2, "keypad-xor", "Keypad XOR", CAT_KEYPAD),
    _a(0xC3, "keypad-caret", "Keypad ^", CAT_KEYPAD),
    _a(0xC4, "keypad-percent", "Keypad %", CAT_KEYPAD),
    _a(0xC5, "keypad-less", "Keypad <", CAT_KEYPAD),
    _a(0xC6, "keypad-greater", "Keypad >", CAT_KEYPAD),
    _a(0xC7, "keypad-ampersand", "Keypad &", CAT_KEYPAD),
    _a(0xC8, "keypad-double-ampersand", "Keypad &&", CAT_KEYPAD),
    _a(0xC9, "keypad-pipe", "Keypad |", CAT_KEYPAD),
    _a(0xCA, "keypad-double-pipe", "Keypad ||", CAT_KEYPAD),
    _a(0xCB, "keypad-colon", "Keypad :", CAT_KEYPAD),
    _a(0xCC, "keypad-hash", "Keypad #", CAT_KEYPAD),
    _a(0xCD, "keypad-space", "Keypad Space", CAT_KEYPAD),
    _a(0xCE, "keypad-at", "Keypad @", CAT_KEYPAD),
    _a(0xCF, "keypad-exclamation", "Keypad !", CAT_KEYPAD),
    _a(0xD0, "keypad-memory-store", "Keypad Memory Store", CAT_KEYPAD),
    _a(0xD1, "keypad-memory-recall", "Keypad Memory Recall", CAT_KEYPAD),
    _a(0xD2, "keypad-memory-clear", "Keypad Memory Clear", CAT_KEYPAD),
    _a(0xD3, "keypad-memory-add", "Keypad Memory Add", CAT_KEYPAD),
    _a(0xD4, "keypad-memory-subtract", "Keypad Memory Subtract", CAT_KEYPAD),
    _a(0xD5, "keypad-memory-multiply", "Keypad Memory Multiply", CAT_KEYPAD),
    _a(0xD6, "keypad-memory-divide", "Keypad Memory Divide", CAT_KEYPAD),
    _a(0xD7, "keypad-plus-minus", "Keypad +/-", CAT_KEYPAD),
    _a(0xD8, "keypad-clear", "Keypad Clear", CAT_KEYPAD),
    _a(0xD9, "keypad-clear-entry", "Keypad Clear Entry", CAT_KEYPAD),
    _a(0xDA, "keypad-binary", "Keypad Binary", CAT_KEYPAD),
    _a(0xDB, "keypad-octal", "Keypad Octal", CAT_KEYPAD),
    _a(0xDC, "keypad-decimal", "Keypad Decimal", CAT_KEYPAD),
    _a(0xDD, "keypad-hexadecimal", "Keypad Hexadecimal", CAT_KEYPAD),
    # modifiers (0xe0..0xe7); e7 confirmed with CAM, the rest follow the HID table
    _a(0xE0, "ctrl", "Ctrl", CAT_MODIFIERS, "lctrl", "left-ctrl", "control"),
    _a(0xE1, "shift", "Shift", CAT_MODIFIERS, "lshift", "left-shift"),
    _a(0xE2, "alt", "Alt", CAT_MODIFIERS, "lalt", "left-alt"),
    _a(0xE3, "left-gui", "Left Super (Windows)", CAT_MODIFIERS, "lgui", "super", "win", "meta", "left-super"),
    _a(0xE4, "right-ctrl", "Right Ctrl", CAT_MODIFIERS, "rctrl"),
    _a(0xE5, "right-shift", "Right Shift", CAT_MODIFIERS, "rshift"),
    _a(0xE6, "altgr", "AltGr", CAT_MODIFIERS, "ralt", "right-alt"),
    _a(0xE7, "right-gui", "Right Super (Windows)", CAT_MODIFIERS, "rgui", "right-super", "right-win"),
    # media keys after the modifiers — confirmed with CAM
    _a(0xE8, "play-pause", "Play / Pause", CAT_MEDIA, "playpause", "play"),
    _a(0xE9, "stop", "Stop", CAT_MEDIA, "media-stop"),
    _a(0xEA, "previous-track", "Previous track", CAT_MEDIA, "prev-track", "previous", "media-previous"),
    _a(0xEB, "next-track", "Next track", CAT_MEDIA, "next", "media-next"),
]

ACTIONS: tuple[Action, ...] = tuple(_TABLE)
BY_CODE: dict[int, Action] = {}
BY_NAME: dict[str, Action] = {}
for _act in ACTIONS:
    if _act.code in BY_CODE:  # pragma: no cover - table sanity
        raise RuntimeError(f"duplicate action code {_act.code:#x}")
    BY_CODE[_act.code] = _act
    for _n in (_act.name, *_act.aliases):
        if _n in BY_NAME:  # pragma: no cover - table sanity
            raise RuntimeError(f"duplicate action name {_n!r}")
        BY_NAME[_n] = _act

#: Default (factory) action for each button index 0..5.
FACTORY_ACTIONS: tuple[int, ...] = (0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA)


def _normalize(text: str) -> str:
    return text.strip().lower().replace("_", "-").replace(" ", "-")


def action_by_code(code: int) -> Action | None:
    return BY_CODE.get(code)


def action_name(code: int) -> str:
    """Canonical name for a code; unknown codes become ``key:0xNN``."""
    act = BY_CODE.get(code)
    if act is not None:
        return act.name
    return f"key:0x{code:02x}"


def parse_action(spec: str | int, *, allow_unknown: bool = False) -> int:
    """Turn ``volume-up``, ``key:a``, ``key:0x04``, ``0x04`` or ``4`` into a code.

    Legacy names from liftctl 0.1 (``left``, ``forward``, ``dpi``…) still mean
    the mouse actions; with the ``key:`` prefix the arrow keys win
    (``key:left`` = left arrow), as in 0.1.
    """
    if isinstance(spec, int) and not isinstance(spec, bool):
        code = spec
    else:
        text = str(spec).strip()
        if not text:
            raise ValidationError("empty action")
        lower = _normalize(text)
        code = None
        if lower.startswith("key:"):
            token = lower[4:]
            legacy_keys = {"left": 0x50, "right": 0x4F, "up": 0x52, "down": 0x51,
                           "lgui": 0xE3, "rgui": 0xE7}
            if token in legacy_keys:
                code = legacy_keys[token]
            elif token in BY_NAME and not BY_NAME[token].is_mouse:
                code = BY_NAME[token].code
            else:
                code = _parse_number(token, bare_hex=True)
                if code is None:
                    raise ValidationError(
                        f"unknown key {text[4:]!r}; use a name (key:a, key:f5) "
                        "or a hex HID usage (key:0x04)"
                    )
        elif lower in BY_NAME:
            code = BY_NAME[lower].code
        else:
            code = _parse_number(lower)
            if code is None:
                raise ValidationError(
                    f"unknown action {text!r}; see `liftctl actions` for the list"
                )
    if not (0 <= code <= 0xFF):
        raise ValidationError(f"action code out of range 0x00..0xff: {code}")
    if code not in BY_CODE and not allow_unknown:
        raise ValidationError(
            f"action code 0x{code:02x} is not in the known table; "
            "pass allow_unknown (CLI: --allow-unknown-code) to send it anyway"
        )
    return code


def _parse_number(token: str, *, bare_hex: bool = False) -> int | None:
    """``0x1f`` everywhere; bare hex (``1f``) only after ``key:`` as in 0.1."""
    try:
        if token.startswith("0x"):
            return int(token[2:], 16)
        if bare_hex and token:
            return int(token, 16)
    except ValueError:
        return None
    return None


def search_actions(query: str = "", category: str | None = None,
                   translate=None) -> list[Action]:
    """Filter the table for a picker: matches name, aliases, label, hex code.

    ``translate`` (optional) maps an English label to the UI language so the
    user can type the label they see (e.g. "volume su").
    """
    q = query.strip().lower()
    out: list[Action] = []
    for act in ACTIONS:
        if category and act.category != category:
            continue
        if not q:
            out.append(act)
            continue
        hay = [act.name, act.label.lower(), act.hex, *act.aliases]
        if translate is not None:
            hay.append(str(translate(act.label)).lower())
        if any(q in h for h in hay) or q.replace(" ", "-") in act.name:
            out.append(act)
    # exact name / label matches first
    out.sort(key=lambda a: (0 if q and (a.name == q or a.label.lower() == q) else 1))
    return out
