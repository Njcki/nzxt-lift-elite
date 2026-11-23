"""User preferences in ``~/.config/nzxt-lift-elite/settings.json``.

Keys: ``language`` ("auto" | "it" | "en"), ``auto_apply`` (bool),
``theme`` ("default" | "light" | "dark"), ``preview_height`` (180..420 px). Unknown keys are kept. Nothing
here talks to the mouse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict[str, object] = {"language": "auto", "auto_apply": False, "theme": "default", "preview_height": 280}


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "nzxt-lift-elite" / "settings.json"


def load_settings() -> dict[str, object]:
    data = dict(DEFAULTS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(raw, dict):
        data.update(raw)
    return data


def save_setting(key: str, value: object) -> dict[str, object]:
    data = load_settings()
    data[key] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return data
