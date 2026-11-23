"""Tiny i18n layer shared by liftctl, the GUI and the CoolerControl plugin.

English msgids, one dict per language (``CATALOGS``); English is the
msgid itself. Language: ``LIFT_ELITE_LANG`` (it / en) if set, else the
``language`` preference (settings.json: "auto" / "it" / "en"), else the
system locale (``LANGUAGE``, ``LC_ALL``, ``LC_MESSAGES``, ``LANG``):
Italian for ``it*``, English otherwise.
"""

from __future__ import annotations

import os

from .i18n_it import IT

CATALOGS: dict[str, dict[str, str]] = {"it": IT, "en": {}}
LANGUAGE_NAMES = {"it": "Italiano", "en": "English"}


def _norm(value: str) -> str:
    return value.strip().lower().split(":")[0].split("_")[0].split(".")[0].split("-")[0]


def system_language() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if not value or _norm(value) in ("c", "posix"):
            continue
        return "it" if _norm(value) == "it" else "en"
    return "en"


def detect_language() -> str:
    env = os.environ.get("LIFT_ELITE_LANG", "")
    if env.strip():
        lang = _norm(env)
        return lang if lang in CATALOGS else "en"
    try:
        from .settings import load_settings

        pref = load_settings().get("language", "auto")
    except Exception:  # noqa: BLE001 - a broken settings file must not break startup
        pref = "auto"
    if pref in CATALOGS:
        return pref
    return system_language()


_LANG = detect_language()


def set_language(lang: str) -> None:
    global _LANG
    _LANG = lang if lang in CATALOGS else "en"


def language() -> str:
    return _LANG


def _(msgid: str, **kwargs: object) -> str:
    text = CATALOGS.get(_LANG, {}).get(msgid, msgid)
    return text.format(**kwargs) if kwargs else text


gettext = _
