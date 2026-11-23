"""Test package setup: make every test hermetic.

Runs before any ``nzxt_lift_elite`` import (``unittest discover -t .`` imports
this package first), because ``i18n`` picks its language at import time.

- HOME / XDG_* point to a private temp dir, so the user's
  ``~/.config/nzxt-lift-elite/settings.json`` (language, auto-apply, preview
  size...), backups and GTK/dconf state are never read or written.
- Locale is C.UTF-8 and LIFT_ELITE_LANG=it (the tests assert the Italian
  texts; English tests switch explicitly). GTK/libadwaita own strings stay
  untranslated.
- No real hidraw device: /dev/hidraw* cannot be opened and sysfs discovery
  sees an empty directory, so a connected mouse can't be found or written.
"""

import atexit
import os
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="lift-elite-tests-")
atexit.register(shutil.rmtree, _HOME, True)
for _var, _sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                   ("XDG_CACHE_HOME", ".cache"), ("XDG_STATE_HOME", ".local/state")):
    os.makedirs(os.path.join(_HOME, _sub), exist_ok=True)
    os.environ[_var] = os.path.join(_HOME, _sub)
os.environ.pop("LANGUAGE", None)
os.environ.pop("LC_MESSAGES", None)
os.environ["LANG"] = "C.UTF-8"
os.environ["LC_ALL"] = "C.UTF-8"
os.environ["LIFT_ELITE_LANG"] = "it"
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["GTK_A11Y"] = "none"
os.environ.pop("LIFT_ELITE_DIAGRAM", None)

# --- never touch a real device -------------------------------------------------
EMPTY_SYS_HIDRAW = os.path.join(_HOME, "sys-class-hidraw")
os.makedirs(EMPTY_SYS_HIDRAW, exist_ok=True)
_real_os_open = os.open


def _guarded_open(path, flags, *args, **kwargs):
    if os.fspath(path).startswith("/dev/hidraw"):
        raise PermissionError(f"tests must not open real hidraw devices: {path}")
    return _real_os_open(path, flags, *args, **kwargs)


os.open = _guarded_open


def _isolate_sysfs():
    from pathlib import Path

    from nzxt_lift_elite import device, hotplug

    empty = Path(EMPTY_SYS_HIDRAW)
    real = device.SYS_HIDRAW
    for mod in (device, hotplug):
        if getattr(mod, "SYS_HIDRAW", None) == real:
            mod.SYS_HIDRAW = empty
        for obj in list(vars(mod).values()):
            funcs = [obj] + ([v for v in vars(obj).values() if callable(v)] if isinstance(obj, type) else [])
            for fn in funcs:
                fn = getattr(fn, "__func__", fn)
                d = getattr(fn, "__defaults__", None)
                if d and any(x == real for x in d if isinstance(x, Path)):
                    fn.__defaults__ = tuple(empty if isinstance(x, Path) and x == real else x for x in d)
                kd = getattr(fn, "__kwdefaults__", None)
                if kd:
                    for k, v in kd.items():
                        if isinstance(v, Path) and v == real:
                            kd[k] = empty
    # open_hidraw captured os.open as a default argument at import time
    for fn in (device.open_hidraw,):
        if fn.__defaults__:
            fn.__defaults__ = tuple(_guarded_open if x is _real_os_open else x for x in fn.__defaults__)
        if fn.__kwdefaults__:
            for k, v in fn.__kwdefaults__.items():
                if v is _real_os_open:
                    fn.__kwdefaults__[k] = _guarded_open


_isolate_sysfs()
