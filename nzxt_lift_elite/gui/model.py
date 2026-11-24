"""GUI state without GTK: drafts per slot, validation, change summaries,
DPI slider mapping and the device session (real or emulated).

Everything here is unit-tested headless; ``app.py`` only renders it.
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..actions import CATEGORIES, FACTORY_ACTIONS, Action, action_by_code, search_actions
from ..device import DeviceState, LiftElite, button_changes
from ..protocol import BatteryStatus
from ..errors import InactiveProfileRemapError, ProtocolError, ValidationError
from ..profile import Profile, ProfileConfig, dumps_profiles, load_profiles_json
from ..protocol import (
    BUTTON_NAMES,
    DPI_STEP,
    DPI_VALUE_MAX,
    DPI_VALUE_MIN,
    ENABLE_ACTIVE_PROFILE_APPLY,
    LIFTOFF_VALUES,
    POLLING_HZ,
    STAGE_COUNT_MAX,
    STAGE_COUNT_MIN,
    LinkStatus,
    color_hex,
    polling_warning,
    rgb_to_rgb565,
)
from ..store import ProfileStore
from .i18n import _

# 8000 Hz is offered on both links: over the receiver the radio stays at
# 4000 Hz but the sensor keeps its high-performance mode (see polling_warning).
POLLING_CHOICES: tuple[int, ...] = tuple(POLLING_HZ[i] for i in sorted(POLLING_HZ))
LIFTOFF_CHOICES: tuple[int, ...] = LIFTOFF_VALUES
# Sleep (AFK) timeout presets in minutes; 0 = never. A value written by
# another tool that is not in the list is shown as an extra entry.
AFK_CHOICES: tuple[int, ...] = (0, 1, 2, 5, 10, 15, 30, 60)
STAGE_COUNT_CHOICES: tuple[int, ...] = tuple(range(STAGE_COUNT_MIN, STAGE_COUNT_MAX + 1))


def afk_choices(*current: int) -> tuple[int, ...]:
    """Dropdown entries: the presets plus any of ``current`` (the edited value
    and the one on the mouse) that is a custom value, in order."""
    extra = {v for v in current if v is not None and v not in AFK_CHOICES}
    if not extra:
        return AFK_CHOICES
    return tuple(sorted(set(AFK_CHOICES) | extra))


def afk_choice_label(minutes: int) -> str:
    return _("Never") if minutes == 0 else _("{n} min", n=minutes)

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


CHARGE_LABELS = {
    "charging": "Charging",
    "full": "Full (cable connected)",
    "discharging": "On battery",
}


def charge_label(b: BatteryStatus) -> str:
    return _(CHARGE_LABELS.get(b.charge_state_name, "Unknown"))


def _decimal(value: float, digits: int) -> str:
    text = f"{value:.{digits}f}"
    from .i18n import language

    return text.replace(".", ",") if language() == "it" else text


def battery_ok(b: BatteryStatus | None) -> bool:
    return b is not None and b.available and b.percent is not None


def battery_subtitle(b: BatteryStatus | None) -> str:
    """One line for the Device section: ``72 % · 4.04 V · Charging``."""
    if not battery_ok(b):
        return _("Not available")
    est = " " + _("(estimated)") if b.estimated else ""
    return f"{b.percent} %{est} · {_decimal(b.millivolts / 1000, 2)} V · {charge_label(b)}"


def link_tooltip(link: LinkStatus | None) -> str | None:
    """Tooltip of the Connection row: the read-only bc status bits."""
    if link is None:
        return None
    yes, no = _("yes"), _("no")
    return "\n".join((
        _("USB cable power: {v}", v=yes if link.cable_power else no),
        _("USB data (wired mode): {v}", v=yes if link.usb_data else no),
        _("Internal link to the 8K bridge: {v}", v=_("up") if link.bridge_link else _("down")),
    ))


def connection_subtitle(transport_text: str, link: LinkStatus | None, path: str | None = None) -> str:
    """Connection row text; cable power without USB data (receiver + charging cable) is appended."""
    text = transport_text
    if link is not None and transport_text and link.cable_power and not link.usb_data:
        text += " · " + _("cable power")
    if path:
        text += f" · {path}"
    return text


def battery_tooltip(b: BatteryStatus | None, age: float | None = None) -> str:
    """Tooltip text; ``age`` (seconds) adds a "last reading" line for an old value."""
    if not battery_ok(b):
        return _("Battery level not available (the mouse has not reported it yet)")
    lines = [_("Battery: {pct} %", pct=b.percent) + (" " + _("(estimated)") if b.estimated else ""),
             _("Voltage: {v} V", v=_decimal(b.millivolts / 1000, 3)),
             charge_label(b)]
    if b.temperature_c is not None:
        lines.append(_("Temperature: {t} °C", t=b.temperature_c))
    if age is not None:
        lines.append(_("Last reading {n} min ago", n=max(1, round(age / 60))))
    return "\n".join(lines)


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
    # «Rendi attivo»: slot to activate (0x45) at the next Applica, or None.
    pending_activation: int | None = None
    # Last good battery reading (b0) and when it was taken (time.monotonic()),
    # or None. A failed read keeps the previous value.
    battery: BatteryStatus | None = None
    battery_time: float | None = None

    def set_battery(self, battery: BatteryStatus | None, now: float | None = None) -> bool:
        """Store a reading; None (failed read) keeps the last value. Returns True if stored."""
        if battery is None:
            return False
        self.battery = battery
        self.battery_time = time.monotonic() if now is None else now
        return True

    def battery_age(self, now: float | None = None) -> float | None:
        if self.battery_time is None:
            return None
        return (time.monotonic() if now is None else now) - self.battery_time

    def battery_stale(self, now: float | None = None) -> bool:
        age = self.battery_age(now)
        return age is not None and age > BATTERY_STALE_S

    # --- loading
    def load_state(self, state: DeviceState, *, keep_selection: bool = True) -> None:
        first = not self.loaded
        self.state = state
        self.device = {p.index: p for p in state.profiles}
        self.drafts = dict(self.device)
        self.onboard_active_profile = state.active_profile
        self.set_battery(state.battery)
        if first or not keep_selection or self.selected not in self.device:
            # Start on the profile the mouse is using (0x44), else profile 1.
            active = state.active_profile
            self.selected = active if active in self.device else 0

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
        self.set_battery(state.battery)
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
            raise ValidationError(_("slot {index} not read", index=index))
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

    def set_stage_count(self, count: int) -> Profile:
        """Stages in the purple-button cycle (1..5); the active stage is clamped into it."""
        return self._set(self.current().with_changes(stage_count=count))

    def set_afk(self, minutes: int) -> Profile:
        return self._set(self.current().with_changes(afk_minutes=minutes))

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
            self.pending_activation = None
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
        """True when Applica must activate ``pending_activation`` (0x45) first."""
        if not ENABLE_ACTIVE_PROFILE_APPLY or self.pending_activation is None:
            return False
        return self.pending_activation != self.onboard_active_profile

    def request_activation(self, index: int | None) -> None:
        """«Rendi attivo»: activate ``index`` on the next Applica (None = cancel).

        Editing a profile never activates it by itself; this explicit choice
        does. Asking for the profile that is already active cancels it.
        """
        if index is not None and (index not in self.device or index == self.onboard_active_profile):
            index = None
        self.pending_activation = index

    def buttons_writable(self, index: int | None = None) -> bool:
        """Buttons (0x50) land only in the active profile: editable when ``index``
        is active, unknown, or will be activated first by Applica."""
        i = self.selected if index is None else index
        if self.onboard_active_profile is None or i == self.onboard_active_profile:
            return True
        return self.activation_pending and self.pending_activation == i

    @property
    def has_pending_apply(self) -> bool:
        """Something for Applica: unsaved edits, or an activation."""
        return self.any_dirty or self.activation_pending

    def mark_activated(self, index: int) -> None:
        self.onboard_active_profile = index
        if self.pending_activation == index:
            self.pending_activation = None

    def changes(self, index: int) -> list[str]:
        a, b = self.device[index], self.drafts[index]
        out: list[str] = []
        if a.polling_hz != b.polling_hz:
            out.append(f"{_('Polling')}: {a.polling_hz} → {b.polling_hz} Hz")
        if a.liftoff != b.liftoff:
            out.append(f"{_('Lift-off')}: {a.liftoff} → {b.liftoff} mm")
        if a.motion_sync != b.motion_sync:
            out.append(f"{_('Motion sync')}: {_('on') if a.motion_sync else _('off')} → {_('on') if b.motion_sync else _('off')}")
        if a.stage_count != b.stage_count:
            out.append(f"{_('DPI stages in the cycle')}: {a.stage_count} → {b.stage_count}")
        for i, (x, y) in enumerate(zip(a.dpi, b.dpi, strict=False), start=1):
            if x != y:
                out.append(f"{_('Stage {n}', n=i)}: {x} → {y} DPI")
        if a.afk_minutes != b.afk_minutes:
            out.append(f"{_('Sleep after')}: {afk_choice_label(a.afk_minutes)} → {afk_choice_label(b.afk_minutes)}")
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

    def adopt_device_stage(self, slot: int, stage: int, active_profile: int | None = None) -> bool:
        """The mouse reports ``stage`` (1-based) as active in ``slot`` (purple button).

        Called by the GUI's live poll. Updates the *applied* baseline so the
        change is not shown as an edit and Applica does not revert it:

        - the device snapshot of ``slot`` takes the new stage;
        - if the draft had the same stage as the old baseline (no pending
          stage choice), the draft follows the mouse too; all other unsaved
          edits in the draft (DPI values, polling, buttons…) are kept;
        - if the user had picked another stage that is not applied yet, that
          choice stays pending (it will be written on Applica); if it now
          equals the mouse's stage, it simply stops being a change.

        Returns True when anything visible changed. Never writes.
        """
        changed = False
        if active_profile is not None and active_profile != self.onboard_active_profile:
            self.onboard_active_profile = active_profile
            changed = True
        base = self.device.get(slot)
        if base is None or not (1 <= stage <= base.stage_count) or base.stage == stage:
            return changed
        draft = self.drafts.get(slot, base)
        self.device[slot] = base.with_changes(stage=stage)
        if draft.stage == base.stage:
            self.drafts[slot] = draft.with_changes(stage=stage)
        return True

    def adopt_poll(self, active_profile: int | None, slot: int, stage: int) -> "PollChange":
        """Merge one live-poll result (0x44 + 0x92 of the active profile).

        - The onboard-active marker follows the mouse (long press).
        - The DPI stage of ``slot`` follows the mouse (short press), see
          :meth:`adopt_device_stage`; unsaved edits are kept.
        - The *edited* profile is a separate choice. It follows the mouse
          only when the user was editing the previously active profile and
          has no unsaved edits anywhere; otherwise it stays where it is.
        Never writes.
        """
        prev = self.onboard_active_profile
        active_changed = active_profile is not None and active_profile != prev and active_profile in self.device
        if active_changed:
            self.onboard_active_profile = active_profile
            if self.pending_activation == active_profile:
                self.pending_activation = None  # long press did it already
        stage_changed = self.adopt_device_stage(slot, stage)
        switched = False
        if (active_changed and prev is not None and self.selected == prev
                and not self.any_dirty and self.pending_activation is None):
            self.selected = active_profile
            switched = True
        return PollChange(active_changed, stage_changed, switched)

    def mark_applied(self, profile: Profile, sent: Profile | None = None) -> None:
        """``profile`` was written and read back. With ``sent`` (auto-apply),
        a draft edited again while the write was running is kept (it is
        still dirty and goes out with the next batch)."""
        self.device[profile.index] = profile
        if sent is None or self.drafts.get(profile.index) == sent:
            self.drafts[profile.index] = profile

    def pending_signature(self) -> tuple:
        """What auto-apply would write now (to restart the debounce only on real edits)."""
        act = self.pending_activation if self.activation_pending else None
        return tuple((i, self.drafts[i]) for i in self.dirty_slots()), act

    # --- warnings
    def polling_note(self) -> str | None:
        return polling_warning(self.current().polling_hz, self.transport)

    @property
    def link(self) -> LinkStatus | None:
        """Last bc reading (cable power / USB data / internal link), or None."""
        return self.state.link if self.state is not None else None


# --- session: talks to the device; run it off the GTK main thread ----------------


@dataclass(frozen=True)
class PollChange:
    """What :meth:`EditorModel.adopt_poll` changed."""

    active_changed: bool
    stage_changed: bool
    switched: bool  # the edited profile followed the mouse

    def __bool__(self) -> bool:
        return self.active_changed or self.stage_changed or self.switched


@dataclass(frozen=True)
class StagePoll:
    """Result of :meth:`Session.poll_active_stage`."""

    active_profile: int | None  # internal slot from 0x44, None if 0x44 failed
    slot: int  # slot whose body was read
    stage: int  # 1-based active DPI stage of that slot
    battery: BatteryStatus | None = None  # only when asked (with_battery) and read OK
    battery_read: bool = False  # a battery read was attempted in this poll


# Live poll cadence (ms). Wireless is slower: every request goes over the air.
POLL_INTERVAL_WIRED_MS = 700
POLL_INTERVAL_WIRELESS_MS = 1000
POLL_BACKOFF_MS = 5000  # after repeated failures (mouse asleep, busy, unplugged)
POLL_FAILURES_BEFORE_BACKOFF = 3
# Battery (b0) cadence. The fuel gauge changes slowly, so:
BATTERY_POLL_FOCUSED_S = 60.0  # focused window: b0 added to the live poll (cable and receiver)
BATTERY_POLL_BACKGROUND_S = 300.0  # unfocused / minimised: b0 only, nothing else
BATTERY_POLL_BACKGROUND_BACKOFF_S = 600.0  # background, after repeated failures (asleep)
BATTERY_FAILURES_BEFORE_BACKOFF = 3
BATTERY_REFRESH_ON_FOCUS_S = 60.0  # on focus, read at once if the value is older than this
BATTERY_STALE_S = 15 * 60.0  # older readings are shown dimmed (value kept)


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
        # One device conversation at a time inside this process: worker
        # threads (read, apply, live poll) share one fd, and flock does not
        # serialise threads that use the same open file.
        self._io = threading.RLock()
        # Auto-apply («Apply immediately») backs up each slot only once per
        # session, before its first automatic write (not once per change).
        self.backed_up: set[int] = set()

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
        with self._io:
            try:
                return self._open().read_state()
            except Exception:
                self.close()  # re-detect next time (receiver/cable may have changed)
                raise

    @contextmanager
    def _quick(self, dev: LiftElite, timeout: float):
        """Short timeouts, one attempt, no waiting for another program's lock."""
        saved = dev.timeout, dev.retries, dev.lock_timeout
        dev.timeout, dev.retries, dev.lock_timeout = timeout, 1, 0.0
        try:
            yield dev
        finally:
            dev.timeout, dev.retries, dev.lock_timeout = saved

    def poll_active_stage(self, fallback_slot: int | None = None, *, timeout: float = 0.3,
                          with_battery: bool = False) -> "StagePoll | None":
        """Live read of the active DPI stage (after a purple-button press).

        Read-only: ``0x44`` (active profile) then one ``0x92`` profile read
        (byte 9 = stage, 0-based, returned 1-based). No button reads, no
        writes. With ``with_battery`` it also reads ``b0`` (battery); a
        failed battery read does not fail the poll. Returns None without touching the mouse when another device
        operation is running in this process (Apply, full read) or the
        device is not open (hotplug re-detects; the poll never opens it).
        Raises DeviceBusyError / ProtocolError / OSError on failure.
        """
        if not self._io.acquire(blocking=False):
            return None
        try:
            dev = self._dev
            if dev is None:
                return None
            with self._quick(dev, timeout), dev.locked():
                try:
                    active = dev.read_active_profile()
                except ProtocolError:
                    if fallback_slot is None:
                        raise
                    active = None
                slot = active if active is not None else fallback_slot
                profile = dev.read_profile(slot, with_buttons=False)
                battery = None
                if with_battery:
                    try:
                        battery = dev.read_battery()
                    except ProtocolError:
                        battery = None
            return StagePoll(active_profile=active, slot=slot, stage=profile.stage, battery=battery,
                             battery_read=with_battery)
        finally:
            self._io.release()

    def poll_battery(self, *, timeout: float = 0.6) -> "BatteryStatus | None":
        """Background battery read: ONLY ``b0`` (no 0x44 / 0x92), one short
        attempt, under the device lock. Returns None without touching the
        mouse when another operation (Apply, reset, full read, live poll) is
        running in this process or the device is not open.
        Raises DeviceBusyError / ProtocolError / OSError on failure."""
        if not self._io.acquire(blocking=False):
            return None
        try:
            dev = self._dev
            if dev is None:
                return None
            with self._quick(dev, timeout):
                return dev.read_battery()
        finally:
            self._io.release()

    def apply(
        self, targets: list[Profile], *, activate_profile: int | None = None, auto: bool = False
    ) -> tuple[list[Profile], Path | None]:
        """Backup, (activate,) write each slot, verify; returns profiles read back.

        ``activate_profile`` (internal slot) is sent with command 0x45 only
        when ``ENABLE_ACTIVE_PROFILE_APPLY`` is True. It is sent FIRST,
        because button writes (0x50) only ever land in the active profile.
        """
        with self._io:
            return self._apply_locked(targets, activate_profile=activate_profile, auto=auto)

    def backup_needed(self, current: list[Profile], auto: bool) -> list[Profile]:
        """Profiles to back up: all of them for a manual Apply; for auto-apply
        only the slots not yet backed up in this session."""
        if not auto:
            return list(current)
        return [c for c in current if c.index not in self.backed_up]

    def _backup(self, profiles: list[Profile], reason: str) -> Path | None:
        if not profiles:
            return None
        self.backed_up.update(p.index for p in profiles)
        if self.is_mock:
            return None
        try:
            return self.store.backup(profiles, reason=reason)
        except OSError:
            return None

    def reset_defaults(self, progress=None):
        """Factory reset of all five profiles (see LiftElite.reset_defaults):
        backup of all profiles first, only verified writes plus 0x45, the
        profile active before is made active again at the end."""
        with self._io:
            dev = self._open()
            result = dev.reset_defaults(backup=lambda profiles: self._backup(profiles, "reset"), progress=progress)
            if not self.is_mock:
                for p in result.results:
                    try:
                        self.store.remember_slot(p)
                    except OSError:
                        pass
            return result

    def _apply_locked(
        self, targets: list[Profile], *, activate_profile: int | None = None, auto: bool = False
    ) -> tuple[list[Profile], Path | None]:
        dev = self._open()
        backup = None
        activate = activate_profile if ENABLE_ACTIVE_PROFILE_APPLY else None
        with dev.locked():
            current = [dev.read_profile(p.index) for p in targets]
            # Buttons can only be written to the profile active on the mouse
            # (firmware ignores the profile byte of 0x50). Check every slot
            # before the first write so a refused Apply writes nothing.
            for t, c in zip(targets, current, strict=True):
                if not button_changes(c, t):
                    continue
                if activate is not None:
                    if t.index != activate:
                        raise InactiveProfileRemapError(
                            _("the buttons of profile {n} cannot be written: Apply activates profile {m}. "
                              "Nothing was written.", n=t.index + 1, m=activate + 1)
                        )
                else:
                    dev.require_active_for_remap(t.index)
            backup = self._backup(self.backup_needed(current, auto), "auto" if auto else "gui")
            if activate is not None:
                dev.set_active_profile(activate)  # first: buttons need it active
            results = [dev.apply_profile(t, current=c) for t, c in zip(targets, current, strict=True)]
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
    raise ValidationError(_("the file contains {n} profiles and none for slot {slot}", n=len(configs), slot=slot))
