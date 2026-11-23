"""GUI access to the shared i18n layer (see :mod:`nzxt_lift_elite.i18n`)."""

from __future__ import annotations

from .. import i18n as _core
from ..i18n import CATALOGS, LANGUAGE_NAMES, IT, gettext, system_language  # noqa: F401
from ..i18n import _ as _  # noqa: F401


def detect_language() -> str:
    return _core.detect_language()


def set_language(lang: str) -> None:
    _core.set_language(lang)


def language() -> str:
    return _core.language()
