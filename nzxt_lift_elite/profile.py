"""Profile model and JSON import/export for the NZXT Lift Elite.

A :class:`Profile` is one of the five onboard slots as read from the mouse:
the decoded body (polling, lift-off, DPI stages, motion sync, LED colour),
the raw 64-byte packet it came from (unknown bytes are preserved on write)
and, when read, the six button mappings.

JSON (format 2)::

    {
      "device": "NZXT Lift Elite", "format": 2, "name": "FPS",
      "profiles": [
        {"profile": 0, "polling_hz": 1000, "liftoff": 1,
         "dpi_stages": [400, 800, 1600, 3200, 6400], "dpi_stage": 2,
         "motion_sync": true, "color": "#0000ff",
         "buttons": {"left": "left-click", "forward": "volume-up", ...}}
      ]
    }

Every field of a profile entry is optional: a load only changes what the
file contains (read, modify, write, verify). Format 1 files from liftctl
0.1 (``indicator`` as RGB565 hex, ``buttons`` null) are still accepted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .i18n import _
from .protocol import (
    BUTTON_NAMES,
    DPI_STAGE_COUNT,
    FACTORY_ACTIONS,
    PROFILE_COUNT,
    ProfileBody,
    apply_body_fields,
    color_hex,
    encoded_body,
    format_action,
    parse_button,
    parse_color,
    parse_profile_reply,
    polling_warning,
    validate_buttons,
    validate_dpi_list,
    validate_dpi_value,
    validate_liftoff,
    validate_polling_hz,
    validate_profile_index,
    validate_stage,
    write_profile_packet,
)

JSON_FORMAT = 2
JSON_FORMATS_ACCEPTED = (1, 2)
JSON_DEVICE = "NZXT Lift Elite"
JSON_USB = "1e71:2129"
JSON_USB_ACCEPTED = {"1e71:2129", "1e71:2101", "1e712129", "1e712101"}

Buttons = dict[str, int]


def factory_buttons() -> Buttons:
    return {name: FACTORY_ACTIONS[i] for i, name in enumerate(BUTTON_NAMES)}


@dataclass(frozen=True)
class Profile:
    """One of the five onboard profiles."""

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
    buttons: Buttons | None = None

    @classmethod
    def from_reply(cls, packet: bytes, expected_index: int | None = None, buttons: Buttons | None = None) -> Profile:
        return cls.from_body(parse_profile_reply(packet, expected_index=expected_index), buttons=buttons)

    @classmethod
    def from_body(cls, body: ProfileBody, buttons: Buttons | None = None) -> Profile:
        return cls(
            index=body.index,
            polling_hz=body.polling_hz,
            liftoff=body.liftoff,
            stage_count=body.stage_count,
            stage=body.stage,
            dpi=body.dpi,
            stage_mask=body.stage_mask,
            color=body.color,
            motion_sync=body.motion_sync,
            raw=body.raw,
            buttons=dict(buttons) if buttons is not None else None,
        )

    @property
    def dpi_stages(self) -> tuple[int, ...]:
        return tuple(self.dpi[: self.stage_count])

    @property
    def current_dpi(self) -> int:
        return self.dpi[min(max(self.stage, 1), self.stage_count) - 1]

    @property
    def color_rgb_hex(self) -> str:
        return color_hex(self.color)

    def polling_warning(self, transport: str | None = None) -> str | None:
        return polling_warning(self.polling_hz, transport)

    def known_body(self) -> bytes:
        return encoded_body(self.raw)

    def write_packet(self) -> bytes:
        return write_profile_packet(self.index, self.raw)

    def with_buttons(self, buttons: Buttons | None) -> Profile:
        return replace(self, buttons=dict(buttons) if buttons is not None else None)

    def with_changes(
        self,
        *,
        index: int | None = None,
        polling_hz: int | None = None,
        liftoff: int | None = None,
        stage: int | None = None,
        dpi=None,
        dpi_stages: dict[int, int] | None = None,
        motion_sync: bool | None = None,
        color: bytes | None = None,
        buttons: dict | None = None,
        allow_unknown_actions: bool = False,
    ) -> Profile:
        """Return a copy with only the given fields changed.

        ``buttons`` is merged into the current mapping (a partial dict only
        changes those buttons).
        """
        target = index if index is not None else self.index
        new_raw = apply_body_fields(
            self.raw,
            index=target,
            polling_hz=polling_hz,
            liftoff=liftoff,
            stage=stage,
            dpi=dpi,
            dpi_stages=dpi_stages,
            motion_sync=motion_sync,
            color=color,
        )
        new_buttons = None if self.buttons is None else dict(self.buttons)
        if buttons:
            parsed = validate_buttons(buttons, allow_unknown=allow_unknown_actions)
            new_buttons = {**(new_buttons or {}), **parsed}
        return Profile.from_reply(new_raw, expected_index=target, buttons=new_buttons)

    def set_dpi_stage(self, stage: int, value: int) -> Profile:
        return self.with_changes(dpi_stages={stage: validate_dpi_value(value)})

    def button_names(self) -> dict[str, str] | None:
        if self.buttons is None:
            return None
        return {b: format_action(self.buttons[b]) for b in BUTTON_NAMES if b in self.buttons}

    def to_json_dict(self, *, include_index: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if include_index:
            data["profile"] = self.index
        data.update(
            {
                "polling_hz": self.polling_hz,
                "liftoff": self.liftoff,
                "dpi_stages": list(self.dpi_stages),
                "dpi_stage": self.stage,
                "motion_sync": self.motion_sync,
                "color": self.color_rgb_hex,
                "indicator": self.color.hex(),
                "buttons": self.button_names(),
            }
        )
        return data

    def to_config(self) -> ProfileConfig:
        return ProfileConfig(
            index=self.index,
            polling_hz=self.polling_hz,
            liftoff=self.liftoff,
            dpi_stages=self.dpi_stages,
            dpi_stage=self.stage if self.stage >= 1 else None,
            motion_sync=self.motion_sync,
            indicator=self.color,
            buttons=dict(self.buttons) if self.buttons is not None else None,
        )


def diff_profiles(expected: Profile, actual: Profile) -> list[str]:
    """Human-readable differences (empty list = identical where it matters)."""
    problems: list[str] = []
    if expected.polling_hz != actual.polling_hz:
        problems.append(f"polling {actual.polling_hz} Hz != {expected.polling_hz} Hz")
    if expected.liftoff != actual.liftoff:
        problems.append(f"lift-off {actual.liftoff} != {expected.liftoff}")
    if expected.stage != actual.stage:
        problems.append(_("DPI stage") + f" {actual.stage} != {expected.stage}")
    if expected.dpi_stages != actual.dpi_stages:
        problems.append(_("DPI stage list") + f" {list(actual.dpi_stages)} != {list(expected.dpi_stages)}")
    if expected.motion_sync != actual.motion_sync:
        problems.append(f"motion sync {actual.motion_sync} != {expected.motion_sync}")
    if expected.color != actual.color:
        problems.append(_("colour") + f" {actual.color.hex()} != {expected.color.hex()}")
    if expected.known_body() != actual.known_body() and not problems:
        problems.append(f"body {actual.known_body().hex()} != {expected.known_body().hex()}")
    if expected.buttons is not None and actual.buttons is not None:
        for b in BUTTON_NAMES:
            if b in expected.buttons and expected.buttons[b] != actual.buttons.get(b):
                problems.append(
                    _("button") + f" {b}: {format_action(actual.buttons.get(b, -1) & 0xFF)} != {format_action(expected.buttons[b])}"
                )
    return problems


def profiles_document(profiles: Iterable[Profile], *, name: str | None = None, include_index: bool = True) -> dict[str, Any]:
    doc: dict[str, Any] = {"device": JSON_DEVICE, "usb": JSON_USB, "format": JSON_FORMAT}
    if name:
        doc["name"] = name
    doc["note"] = (
        "Unofficial protocol, worked out from NZXT CAM. Every field is optional: "
        "loading changes only the fields present."
    )
    doc["profiles"] = [p.to_json_dict(include_index=include_index) for p in profiles]
    return doc


def dumps_profiles(profiles: Iterable[Profile], **kw: Any) -> str:
    return json.dumps(profiles_document(profiles, **kw), indent=2, ensure_ascii=False) + "\n"


def save_profiles_json(path: str | Path, profiles: Iterable[Profile], **kw: Any) -> None:
    Path(path).write_text(dumps_profiles(profiles, **kw), encoding="utf-8")


@dataclass(frozen=True)
class ProfileConfig:
    """Editable subset of a profile, as stored in a JSON file (all optional)."""

    index: int | None = None
    polling_hz: int | None = None
    liftoff: int | None = None
    dpi_stages: tuple[int, ...] | None = None
    dpi_stage: int | None = None
    dpi_stage_overrides: dict[int, int] = field(default_factory=dict)
    motion_sync: bool | None = None
    indicator: bytes | None = None
    buttons: Buttons | None = None
    name: str | None = None

    def apply_to(self, profile: Profile, *, target_index: int | None = None) -> Profile:
        index = target_index if target_index is not None else (self.index if self.index is not None else profile.index)
        dpi = self.dpi_stages
        if dpi is not None and len(dpi) != profile.stage_count:
            raise ValidationError(
                _("the file has {n} DPI stages, the mouse profile has {m}", n=len(dpi), m=profile.stage_count)
            )
        # Named profiles omit colour (save_named include_color=False), so
        # loading one into another slot leaves that slot's LED unchanged.
        # Full JSON exports and backups do include it and apply it here.
        updated = profile.with_changes(
            index=index,
            polling_hz=self.polling_hz,
            liftoff=self.liftoff,
            dpi=dpi,
            dpi_stages=self.dpi_stage_overrides or None,
            stage=self.dpi_stage,
            motion_sync=self.motion_sync,
            color=self.indicator,
        )
        if self.buttons is not None:
            merged = {**(profile.buttons or {}), **self.buttons}
            updated = updated.with_buttons(merged)
        return updated

    def to_json_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.index is not None:
            d["profile"] = self.index
        if self.polling_hz is not None:
            d["polling_hz"] = self.polling_hz
        if self.liftoff is not None:
            d["liftoff"] = self.liftoff
        if self.dpi_stages is not None:
            d["dpi_stages"] = list(self.dpi_stages)
        if self.dpi_stage is not None:
            d["dpi_stage"] = self.dpi_stage
        if self.dpi_stage_overrides:
            d["dpi_stage_overrides"] = {str(k): v for k, v in self.dpi_stage_overrides.items()}
        if self.motion_sync is not None:
            d["motion_sync"] = self.motion_sync
        if self.indicator is not None:
            d["color"] = color_hex(self.indicator)
            d["indicator"] = self.indicator.hex()
        if self.buttons is not None:
            d["buttons"] = {b: format_action(c) for b, c in self.buttons.items()}
        return d


_KNOWN_KEYS = {
    "profile",
    "polling_hz",
    "liftoff",
    "dpi_stages",
    "dpi_stage",
    "dpi_stage_overrides",
    "motion_sync",
    "indicator",
    "color",
    "buttons",
    "name",
}


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(_("{field} must be an integer, got {value}", field=field_name, value=repr(value)))
    return value


def _parse_indicator(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(_('indicator must be a 4-digit hex string, e.g. "1f00"'))
    text = value.strip().lower().removeprefix("0x")
    try:
        data = bytes.fromhex(text)
    except ValueError as exc:
        raise ValidationError(_("invalid indicator: {value}", value=repr(value))) from exc
    if len(data) != 2:
        raise ValidationError(_("indicator must be 2 bytes (4 hex digits)"))
    return data


def profile_config_from_dict(data: dict[str, Any], *, allow_unknown_actions: bool = False) -> ProfileConfig:
    if not isinstance(data, dict):
        raise ValidationError(_("every profile must be a JSON object"))
    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise ValidationError(f"chiavi JSON sconosciute: {', '.join(sorted(unknown))}")

    index = _optional_int(data.get("profile"), "profile")
    if index is not None:
        validate_profile_index(index)
    polling_hz = _optional_int(data.get("polling_hz"), "polling_hz")
    if polling_hz is not None:
        validate_polling_hz(polling_hz)
    liftoff = _optional_int(data.get("liftoff"), "liftoff")
    if liftoff is not None:
        validate_liftoff(liftoff)

    parsed_dpi: tuple[int, ...] | None = None
    if data.get("dpi_stages") is not None:
        raw = data["dpi_stages"]
        if not isinstance(raw, list):
            raise ValidationError(_("dpi_stages must be a list of 5 integers"))
        parsed_dpi = tuple(validate_dpi_list(raw, DPI_STAGE_COUNT))

    dpi_stage = _optional_int(data.get("dpi_stage"), "dpi_stage")
    if dpi_stage is not None:
        validate_stage(dpi_stage, DPI_STAGE_COUNT)

    overrides_raw = data.get("dpi_stage_overrides") or {}
    if not isinstance(overrides_raw, dict):
        raise ValidationError(_('dpi_stage_overrides must be an object {"1": 800, ...}'))
    overrides: dict[int, int] = {}
    for key, value in overrides_raw.items():
        try:
            stage = int(key)
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("invalid DPI stage {value}", value=repr(key))) from exc
        validate_stage(stage, DPI_STAGE_COUNT)
        overrides[stage] = validate_dpi_value(value)

    motion = data.get("motion_sync")
    if motion is not None and not isinstance(motion, bool):
        raise ValidationError(_("motion_sync must be true or false"))

    indicator = None
    if data.get("color") is not None:
        if not isinstance(data["color"], str):
            raise ValidationError(_('color must be "#rrggbb"'))
        indicator = parse_color(data["color"])
    elif data.get("indicator") is not None:
        indicator = _parse_indicator(data.get("indicator"))

    buttons = data.get("buttons")
    parsed_buttons: Buttons | None
    if buttons is None:
        parsed_buttons = None
    elif not isinstance(buttons, dict):
        raise ValidationError(_("buttons must be an object or null"))
    else:
        for name in buttons:
            parse_button(name)
        parsed_buttons = validate_buttons(buttons, allow_unknown=allow_unknown_actions)

    name = data.get("name")
    if name is not None and not isinstance(name, str):
        raise ValidationError(_("name must be a string"))

    return ProfileConfig(
        index=index,
        polling_hz=polling_hz,
        liftoff=liftoff,
        dpi_stages=parsed_dpi,
        dpi_stage=dpi_stage,
        dpi_stage_overrides=overrides,
        motion_sync=motion,
        indicator=indicator,
        buttons=parsed_buttons,
        name=name,
    )


def parse_profiles_document(raw: Any, *, allow_unknown_actions: bool = False) -> list[ProfileConfig]:
    """Accept a full document, a list, or a single profile object."""
    doc_name = None
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict) and "profiles" in raw:
        fmt = raw.get("format")
        if fmt is not None and fmt not in JSON_FORMATS_ACCEPTED:
            raise ValidationError(_("JSON format {fmt} not supported (expected {ok})", fmt=fmt, ok=JSON_FORMATS_ACCEPTED))
        usb = raw.get("usb")
        if usb is not None:
            usbs = usb if isinstance(usb, list) else [usb]
            if not any(str(u).lower() in JSON_USB_ACCEPTED for u in usbs):
                raise ValidationError(_("the file is for USB device {usb}, not for the Lift Elite", usb=usb))
        entries = raw["profiles"]
        doc_name = raw.get("name") if isinstance(raw.get("name"), str) else None
        if not isinstance(entries, list):
            raise ValidationError(_("profiles must be a JSON array"))
    elif isinstance(raw, dict) and (set(raw) & (_KNOWN_KEYS - {"name"})):
        entries = [raw]
    else:
        raise ValidationError(_("the JSON must contain a profiles array or a single profile"))
    if not entries:
        raise ValidationError(_("the JSON contains no profiles"))
    configs = [profile_config_from_dict(e, allow_unknown_actions=allow_unknown_actions) for e in entries]
    if doc_name:
        configs = [c if c.name else replace(c, name=doc_name) for c in configs]
    return configs


def loads_profiles(text: str, **kw: Any) -> list[ProfileConfig]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(_("invalid JSON: {error}", error=exc)) from exc
    return parse_profiles_document(raw, **kw)


def load_profiles_json(path: str | Path, **kw: Any) -> list[ProfileConfig]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"impossibile leggere {path}: {exc.strerror or exc}") from exc
    try:
        return loads_profiles(text, **kw)
    except ValidationError as exc:
        raise ValidationError(f"{path}: {exc}") from exc


def select_config(
    configs: list[ProfileConfig],
    *,
    profile_index: int | None,
    require_all: bool,
) -> list[tuple[int, ProfileConfig]]:
    """Decide which configs apply to which onboard slots."""
    if require_all:
        by_index: dict[int, ProfileConfig] = {}
        for cfg in configs:
            if cfg.index is None:
                raise ValidationError(_("with --all every profile in the file needs the profile field"))
            by_index[cfg.index] = cfg
        missing = [i for i in range(PROFILE_COUNT) if i not in by_index]
        if missing:
            raise ValidationError(
                _("--all needs all 5 profiles in the file; missing: {missing}", missing=", ".join(map(str, missing)))
            )
        return [(i, by_index[i]) for i in range(PROFILE_COUNT)]
    if profile_index is None:
        raise ValidationError(_("give --profile N or --all"))
    validate_profile_index(profile_index)
    matching = [c for c in configs if c.index == profile_index]
    if len(matching) == 1:
        return [(profile_index, matching[0])]
    if len(matching) > 1:
        raise ValidationError(_("the file has more than one entry for profile {n}", n=profile_index))
    if len(configs) == 1:
        return [(profile_index, configs[0])]
    raise ValidationError(_("the file has no entry for profile {n} and contains more than one", n=profile_index))
