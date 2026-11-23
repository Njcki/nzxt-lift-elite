"""Profile files on disk: ``~/.config/nzxt-lift-elite/``.

- ``profiles/<name>.json``: named profiles (one setup, no slot number),
  which can be loaded into any of the five onboard slots;
- ``slots/slot-N.json``: the last state read from or written to slot N,
  refreshed after every verified write;
- ``backups/<time>.json``: a copy of every slot that is about to be
  overwritten, taken before the write (kept: the last 50).

``XDG_CONFIG_HOME`` is honoured. Nothing here talks to the mouse.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .i18n import _
from .profile import (
    profiles_document,
    Profile,
    ProfileConfig,
    dumps_profiles,
    load_profiles_json,
    loads_profiles,
)

APP_DIR = "nzxt-lift-elite"
MAX_BACKUPS = 50


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._").lower()
    if not text:
        raise ValidationError(_("invalid profile name: {name}", name=repr(name)))
    return text[:64]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class NamedProfile:
    name: str
    path: Path
    config: ProfileConfig
    mtime: float


class ProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else config_dir()

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def slots_dir(self) -> Path:
        return self.root / "slots"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    # --- named profiles ----------------------------------------------------

    def path_for(self, name: str) -> Path:
        return self.profiles_dir / f"{slugify(name)}.json"

    def save_named(self, name: str, profile: Profile, *, overwrite: bool = True, include_color: bool = False) -> Path:
        """Save one slot as a named profile.

        The LED colour identifies the slot (blue, red, green, ... out of the
        factory), so by default it is left out: loading the profile into
        another slot keeps that slot's colour.
        """
        path = self.path_for(name)
        if path.exists() and not overwrite:
            raise ValidationError(_("a profile called {name} already exists", name=repr(name)))
        doc = profiles_document([profile], name=name.strip(), include_index=False)
        if not include_color:
            for entry in doc["profiles"]:
                entry.pop("color", None)
                entry.pop("indicator", None)
        _atomic_write(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        return path

    def load_named(self, name: str) -> ProfileConfig:
        path = self.path_for(name)
        if not path.is_file():
            raise ValidationError(_("profile {name} not found in {path}", name=repr(name), path=self.profiles_dir))
        configs = load_profiles_json(path)
        if len(configs) != 1:
            raise ValidationError(_("{path} contains {n} profiles, one is needed", path=path, n=len(configs)))
        return configs[0]

    def delete_named(self, name: str) -> None:
        path = self.path_for(name)
        if not path.is_file():
            raise ValidationError(_("profile {name} not found", name=repr(name)))
        path.unlink()

    def list_named(self) -> list[NamedProfile]:
        out: list[NamedProfile] = []
        if not self.profiles_dir.is_dir():
            return out
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                configs = load_profiles_json(path)
            except ValidationError:
                continue
            if len(configs) != 1:
                continue
            cfg = configs[0]
            out.append(NamedProfile(cfg.name or path.stem, path, cfg, path.stat().st_mtime))
        out.sort(key=lambda n: n.name.lower())
        return out

    # --- slots / backups ---------------------------------------------------

    def slot_path(self, index: int) -> Path:
        return self.slots_dir / f"slot-{index}.json"

    def remember_slot(self, profile: Profile) -> Path:
        path = self.slot_path(profile.index)
        _atomic_write(path, dumps_profiles([profile], name=f"slot {profile.index}"))
        return path

    def load_slot(self, index: int) -> ProfileConfig | None:
        path = self.slot_path(index)
        if not path.is_file():
            return None
        return load_profiles_json(path)[0]

    def backup(self, profiles: list[Profile], reason: str = "write") -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slots = "-".join(str(p.index) for p in profiles)
        path = self.backups_dir / f"{stamp}-{reason}-slot{slots}.json"
        n = 1
        while path.exists():
            n += 1
            path = self.backups_dir / f"{stamp}-{reason}-slot{slots}-{n}.json"
        _atomic_write(path, dumps_profiles(profiles, name=f"backup {stamp}"))
        self._prune()
        return path

    def _prune(self) -> None:
        files = sorted(self.backups_dir.glob("*.json"))
        for old in files[:-MAX_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass


def export_text(profiles: list[Profile], name: str | None = None) -> str:
    return dumps_profiles(profiles, name=name)


def import_text(text: str) -> list[ProfileConfig]:
    return loads_profiles(text)


__all__ = ["ProfileStore", "NamedProfile", "config_dir", "slugify", "export_text", "import_text", "json"]
