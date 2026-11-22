"""GUI state without GTK: drafts per slot, validation, change summaries,
DPI slider mapping and the device session (real or emulated).

Everything here is unit-tested headless; ``app.py`` only renders it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..actions import CATEGORIES, FACTORY_ACTIONS, Action, action_by_code, search_actions
from ..device import DeviceState, LiftElite
from ..errors import ValidationError
from ..profile import Profile, ProfileConfig, dumps_profiles, load_profiles_json
from ..protocol import (
    BUTTON_NAMES,
    DPI_STEP,
    DPI_VALUE_MAX,
    DPI_VALUE_MIN,
    ENABLE_ACTIVE_PROFILE_APPLY,
    LIFTOFF_VALUES,
    POLLING_HZ,
    color_hex,
    polling_warning,
    rgb_to_rgb565,
)
from ..store import ProfileStore
from .i18n import _

POLLING_CHOICES: tuple[int, ...] = tuple(POLLING_HZ[i] for i in sorted(POLLING_HZ))
LIFTOFF_CHOICES: tuple[int, ...] = LIFTOFF_VALUES

BUTTON_LABELS = {
    "left": "Left button",
    "middle": "Middle button (wheel)",
    "right": "Right button",
    "forward": "Side forward",
    "back": "Side back",
    "dpi": "DPI button",
}
CATEGORY_LABELS = {
    "mouse": "Mouse",
    "media": "Media",
    "letters": "Letters",
    "digits": "Digits",
    "function": "Function keys",
    "modifiers": "Modifiers",
    "navigation": "Navigation",
    "editing": "Editing",
    "symbols": "Symbols",
    "keypad": "Keypad",
    "system": "System",
    "international": "International",
    "other": "Other",
}
assert set(CATEGORY_LABELS) == set(CATEGORIES)

# --- DPI slider: logarithmic, snapped to 50 ------------------------------------
SLIDER_MAX = 1000
GUI_DPI_MIN = 100  # 50 is legal but useless on a 26K sensor; spin button allows 50
_LOG_MIN = math.log(GUI_DPI_MIN)
_LOG_MAX = math.log(DPI_VALUE_MAX)


def snap_dpi(value: float) -> int:
    v = int(round(value / DPI_STEP)) * DPI_STEP
    return max(DPI_VALUE_MIN, min(DPI_VALUE_MAX, v))


def dpi_to_slider(dpi: int) -> float:
    d = max(GUI_DPI_MIN, min(DPI_VALUE_MAX, dpi))
    return (math.log(d) - _LOG_MIN) / (_LOG_MAX - _LOG_MIN) * SLIDER_MAX


def slider_to_dpi(pos: float) -> int:
    p = max(0.0, min(float(SLIDER_MAX), pos)) / SLIDER_MAX
    return snap_dpi(math.exp(_LOG_MIN + p * (_LOG_MAX - _LOG_MIN)))


def action_label(code: int) -> str:
    act = action_by_code(code)
    if act is None:
        return _("Unknown code 0x{code}", code=f"{code:02x}")
    return _(act.label)


def action_search(query: str, category: str | None = None) -> list[Action]:
    return search_actions(query, category, translate=_)


def group_actions(actions: list[Action]) -> list[tuple[str, list[Action]]]:
    by_cat: dict[str, list[Action]] = {}
    for act in actions:
        by_cat.setdefault(act.category, []).append(act)
    return [(c, by_cat[c]) for c in CATEGORIES if c in by_cat]


def transport_label(transport: str | None) -> str:
    return {"wired": _("Cable"), "wireless": _("Wireless (receiver)")}.get(transport or "", "—")


# --- editor ------------------------------------------------------------------------


@dataclass
class EditorModel:
    """Device snapshot + one editable draft per onboard slot."""

    state: DeviceState | None = None
    device: dict[int, Profile] = field(default_factory=dict)
    drafts: dict[int, Profile] = field(default_factory=dict)
    selected: int = 0
    # Last value read from command 0x44 (internal slot), or None.
    onboard_active_profile: int | None = None

    # --- loading
    def load_state(self, state: DeviceState, *, keep_selection: bool = True) -> None:
        self.state = state
        self.device = {p.index: p for p in state.profiles}
        self.drafts = dict(self.device)
        self.onboard_active_profile = state.active_profile
        if not keep_selection or self.selected not in self.device:
            self.selected = 0

    def adopt_state(self, state: "DeviceState") -> bool:
        """Refresh the device snapshot after a reconnect.

        Dirty drafts are kept so unsaved edits survive a plug/unplug or a
        switch from receiver to cable. Clean slots take the newly read values.
        Returns True if at least one unsaved edit was preserved. Never writes.
        """
        dirty = {i for i in self.drafts if self.is_dirty(i)} if self.loaded else set()
        kept_drafts = {i: self.drafts[i] for i in dirty}
        self.state = state
        self.device = {p.index: p for p in state.profiles}
        self.drafts = dict(self.device)
        self.onboard_active_profile = state.active_profile
        kept = False
        for index, draft in kept_drafts.items():
            if index in self.drafts:
                self.drafts[index] = draft
                kept = True
        if self.selected not in self.device:
            self.selected = next(iter(self.device), 0)
        return kept

    @property
    def loaded(self) -> bool:
        return bool(self.device)

    @property
    def transport(self) -> str | None:
        return self.state.info.transport if self.state else None

    def current(self) -> Profile:
        return self.drafts[self.selected]

    def original(self, index: int | None = None) -> Profile:
        return self.device[self.selected if index is None else index]

    def select(self, index: int) -> None:
        if index not in self.drafts:
            raise ValidationError(f"slot {index} non letto")
        self.selected = index

    # --- edits (all return the new draft)
    def _set(self, profile: Profile) -> Profile:
        self.drafts[profile.index] = profile
        return profile

    def set_polling(self, hz: int) -> Profile:
        return self._set(self.current().with_changes(polling_hz=hz))

    def set_liftoff(self, mm: int) -> Profile:
        return self._set(self.current().with_changes(liftoff=mm))

    def set_motion_sync(self, on: bool) -> Profile:
        return self._set(self.current().with_changes(motion_sync=on))

    def set_dpi(self, stage: int, value: int) -> Profile:
        return self._set(self.current().with_changes(dpi_stages={stage: snap_dpi(value)}))

    def set_active_stage(self, stage: int) -> Profile:
        return self._set(self.current().with_changes(stage=stage))

    def set_color_rgb(self, r: int, g: int, b: int) -> Profile:
        return self._set(self.current().with_changes(color=rgb_to_rgb565(r, g, b)))

    def set_button(self, button: str, code: int) -> Profile:
        cur = self.current()
        buttons = dict(cur.buttons or {})
        buttons[button] = code
        return self._set(cur.with_buttons(buttons))

    def factory_buttons(self) -> Profile:
        cur = self.current()
        return self._set(cur.with_buttons({b: FACTORY_ACTIONS[i] for i, b in enumerate(BUTTON_NAMES)}))

    def apply_config(self, config: ProfileConfig) -> Profile:
        """Load a named/imported profile into the selected slot's draft."""
        return self._set(config.apply_to(self.current(), target_index=self.selected))

    def revert(self, index: int | None = None) -> None:
        if index is None:
            self.drafts = dict(self.device)
        else:
            self.drafts[index] = self.device[index]

    # --- dirty tracking
    def is_dirty(self, index: int | None = None) -> bool:
        i = self.selected if index is None else index
        if i not in self.device:
            return False
        a, b = self.device[i], self.drafts[i]
        return a.known_body() != b.known_body() or (a.buttons or {}) != (b.buttons or {})

    def dirty_slots(self) -> list[int]:
        return [i for i in sorted(self.drafts) if self.is_dirty(i)]

    @property
    def any_dirty(self) -> bool:
        return bool(self.dirty_slots())

    @property
    def activation_pending(self) -> bool:
        """True when the selected slot should be activated on Apply (flag on only)."""
        if not ENABLE_ACTIVE_PROFILE_APPLY or self.onboard_active_profile is None:
            return False
        return self.selected != self.onboard_active_profile

    def mark_activated(self, index: int) -> None:
        self.onboard_active_profile = index

    def changes(self, index: int) -> list[str]:
        a, b = self.device[index], self.drafts[index]
        out: list[str] = []
        if a.polling_hz != b.polling_hz:
            out.append(f"{_('Polling')}: {a.polling_hz} → {b.polling_hz} Hz")
        if a.liftoff != b.liftoff:
            out.append(f"{_('Lift-off')}: {a.liftoff} → {b.liftoff} mm")
        if a.motion_sync != b.motion_sync:
            out.append(f"{_('Motion sync')}: {_('on') if a.motion_sync else _('off')} → {_('on') if b.motion_sync else _('off')}")
        for i, (x, y) in enumerate(zip(a.dpi_stages, b.dpi_stages, strict=False), start=1):
            if x != y:
                out.append(f"{_('Stage {n}', n=i)}: {x} → {y} DPI")
        if a.stage != b.stage:
            out.append(f"{_('Active stage')}: {a.stage} → {b.stage}")
        if a.color != b.color:
            out.append(f"{_('LED colour')}: {color_hex(a.color)} → {color_hex(b.color)}")
        ab, bb = a.buttons or {}, b.buttons or {}
        for name in BUTTON_NAMES:
            if name in bb and ab.get(name) != bb.get(name):
                before = action_label(ab[name]) if name in ab else "?"
                out.append(f"{_(BUTTON_LABELS[name])}: {before} → {action_label(bb[name])}")
        return out

    def summary(self) -> list[tuple[int, list[str]]]:
        return [(i, self.changes(i)) for i in self.dirty_slots()]

    def mark_applied(self, profile: Profile) -> None:
        self.device[profile.index] = profile
        self.drafts[profile.index] = profile

    # --- warnings
    def polling_note(self) -> str | None:
        return polling_warning(self.current().polling_hz, self.transport)


# --- session: talks to the device; run it off the GTK main thread ----------------


class Session:
    """Opens the mouse (or the emulator) and runs reads / verified writes."""

    def __init__(self, *, mock: str | None = None, device_path: str | None = None,
                 transport: str = "auto", store: ProfileStore | None = None) -> None:
        self.mock = mock  # None, "wired" or "wireless"
        self.device_path = device_path
        self.transport = transport
        self.store = store or ProfileStore()
        self._dev: LiftElite | None = None
        self._fake = None

    @property
    def is_mock(self) -> bool:
        return self.mock is not None

    def _open(self) -> LiftElite:
        if self._dev is not None:
            return self._dev
        if self.mock:
            from ..mock import open_mock

            self._dev, self._fake = open_mock(self.mock)
        else:
            self._dev = LiftElite.open(self.device_path, transport=self.transport)
        return self._dev

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()
        if self._fake is not None:
            self._fake.close()
        self._dev = None
        self._fake = None

    def read_state(self) -> DeviceState:
        try:
            return self._open().read_state()
        except Exception:
            self.close()  # re-detect next time (receiver/cable may have changed)
            raise

    def apply(
        self, targets: list[Profile], *, activate_profile: int | None = None
    ) -> tuple[list[Profile], Path | None]:
        """Backup, write each slot, verify; returns profiles read back.

        ``activate_profile`` (internal slot) is sent with command 0x45 only
        when ``ENABLE_ACTIVE_PROFILE_APPLY`` is True. The flag stays off
        until the user confirms 0x44/0x45 on the real mouse.
        """
        dev = self._open()
        backup = None
        with dev.locked():
            current = [dev.read_profile(p.index) for p in targets]
            if not self.is_mock:
                try:
                    backup = self.store.backup(current, reason="gui")
                except OSError:
                    backup = None
            results = [dev.apply_profile(t, current=c) for t, c in zip(targets, current, strict=True)]
            if ENABLE_ACTIVE_PROFILE_APPLY and activate_profile is not None:
                dev.set_active_profile(activate_profile)
        if not self.is_mock:
            for p in results:
                try:
                    self.store.remember_slot(p)
                except OSError:
                    pass
        return results, backup


def export_profiles(path: str | Path, profiles: list[Profile], name: str | None = None) -> Path:
    path = Path(path)
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    path.write_text(dumps_profiles(profiles, name=name), encoding="utf-8")
    return path


def import_profile(path: str | Path, slot: int) -> ProfileConfig:
    """Pick the entry for ``slot`` (or the only one) from a JSON file."""
    configs = load_profiles_json(path)
    if len(configs) == 1:
        return configs[0]
    match = [c for c in configs if c.index == slot]
    if match:
        return match[0]
    raise ValidationError(f"il file contiene {len(configs)} profili e nessuno per lo slot {slot}")
