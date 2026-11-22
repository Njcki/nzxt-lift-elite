"""Live detection of the Lift Elite cable and receiver.

Prefers the cable (1e71:2129) when the receiver (1e71:2101) is also
present. Uses a pyudev hidraw monitor when the module is available, and
falls back to polling ``/sys/class/hidraw`` every ``interval`` seconds.

An ``add`` event often arrives before systemd-logind applies the
``uaccess`` ACL, so the monitor waits until the udev device is
initialized (and, when tags are visible, until ``uaccess`` is among
them) before announcing a plug-in. Opening the node still retries
PermissionError for a few seconds (see :func:`device.open_hidraw`).
The cable exposes three hidraw nodes; :func:`scan` keeps the vendor
interface only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .device import (
    SYS_HIDRAW,
    HidrawInfo,
    choose_vendor_hidraw,
    inspect_hidraw,
    iter_lift_elite_hidraw,
)
from .protocol import PRODUCT_ID, PRODUCT_ID_DONGLE

log = logging.getLogger("nzxt_lift_elite.hotplug")

DEFAULT_INTERVAL = 1.5

OnChange = Callable[["Presence", "Presence"], None]


@dataclass(frozen=True)
class Presence:
    """Which Lift Elite vendor nodes are visible right now."""

    wired: HidrawInfo | None = None
    wireless: HidrawInfo | None = None
    chosen: HidrawInfo | None = None

    @property
    def connected(self) -> bool:
        return self.chosen is not None

    def identity(self) -> tuple:
        """Comparable key: chosen node first, then everything that is plugged in."""
        c = self.chosen
        w, r = self.wired, self.wireless
        return (
            c.path if c else None,
            c.transport if c else None,
            w.path if w else None,
            r.path if r else None,
        )


def scan(
    sys_root: Path = SYS_HIDRAW,
    transport: str = "auto",
    path: str | None = None,
) -> Presence:
    """Look at sysfs (nothing is opened). ``path`` pins a specific hidraw node."""
    if path:
        name = Path(path).name
        info = inspect_hidraw(name, sys_root=sys_root)
        if info is None:
            # The node may exist without a complete sysfs tree (tests).
            from pathlib import Path as P
            if P(path).exists() or (sys_root / name).exists():
                # Let find_hidraw-style fallback apply only when the path is a
                # known Lift Elite node; otherwise treat as absent.
                info = inspect_hidraw(name, sys_root=sys_root)
        wired = info if info is not None and info.product_id == PRODUCT_ID else None
        wireless = info if info is not None and info.product_id == PRODUCT_ID_DONGLE else None
        return Presence(wired=wired, wireless=wireless, chosen=info)

    found = iter_lift_elite_hidraw(sys_root)
    wired = choose_vendor_hidraw(found, "wired")
    wireless = choose_vendor_hidraw(found, "wireless")
    chosen = choose_vendor_hidraw(found, transport)
    return Presence(wired=wired, wireless=wireless, chosen=chosen)



def udev_event_is_ready(action=None, device=None) -> bool:
    """Return True when this hidraw udev event should trigger a rescan.

    Removals are always ready. An add/change is ready once udev has
    finished initializing the device. If tags have been published,
    wait until ``uaccess`` is among them — that is when the ACL that
    lets the logged-in user open the node is applied.
    """
    if device is None:
        return True
    act = (action or getattr(device, "action", None) or "").lower()
    if act in ("remove", "unbind"):
        return True
    try:
        if hasattr(device, "is_initialized") and not device.is_initialized:
            return False
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = getattr(device, "tags", None)
        tags = set(raw) if raw is not None else set()
    except Exception:  # noqa: BLE001
        tags = set()
    if tags and "uaccess" not in tags:
        return False
    return True


def pyudev_available() -> bool:
    try:
        import pyudev  # noqa: F401
    except ImportError:
        return False
    return True


class Monitor:
    """Watch hidraw and call ``on_change(previous, current)`` on a worker thread."""

    def __init__(
        self,
        *,
        sys_root: Path = SYS_HIDRAW,
        transport: str = "auto",
        path: str | None = None,
        interval: float = DEFAULT_INTERVAL,
        scanner: Callable[[], Presence] | None = None,
        use_udev: bool | None = None,
    ) -> None:
        self.sys_root = Path(sys_root)
        self.transport = transport
        self.path = path
        self.interval = interval
        self._scanner = scanner or (lambda: scan(self.sys_root, self.transport, self.path))
        self._use_udev = pyudev_available() if use_udev is None else use_udev
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = None
        self._on_change: OnChange | None = None
        self._current = Presence()
        self._lock = threading.Lock()
        self.backend: str | None = None

    def snapshot(self) -> Presence:
        with self._lock:
            return self._current

    def start(self, on_change: OnChange) -> str:
        """Start watching. Returns ``\"udev\"`` or ``\"poll\"``."""
        self.stop()
        self._on_change = on_change
        self._stop.clear()
        self._current = self._scanner()
        if self._use_udev and self._start_udev():
            self.backend = "udev"
        else:
            self._thread = threading.Thread(target=self._poll_loop, name="lift-elite-hotplug", daemon=True)
            self._thread.start()
            self.backend = "poll"
        log.debug("hotplug monitor started (%s)", self.backend)
        return self.backend

    def stop(self) -> None:
        self._stop.set()
        observer = self._observer
        self._observer = None
        if observer is not None:
            try:
                observer.stop()
            except Exception:  # noqa: BLE001
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
        self._on_change = None

    def rescan(self) -> Presence:
        """Force a scan and emit a change if the identity moved."""
        return self._emit(self._scanner())

    def _emit(self, fresh: Presence) -> Presence:
        with self._lock:
            previous = self._current
            if previous.identity() == fresh.identity():
                return previous
            self._current = fresh
        log.info(
            "hotplug: %s -> %s",
            previous.chosen.path if previous.chosen else "none",
            fresh.chosen.path if fresh.chosen else "none",
        )
        cb = self._on_change
        if cb is not None:
            try:
                cb(previous, fresh)
            except Exception:  # noqa: BLE001 - never kill the monitor thread
                log.exception("hotplug callback failed")
        return fresh

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._emit(self._scanner())
            except Exception:  # noqa: BLE001
                log.exception("hotplug poll failed")

    def _start_udev(self) -> bool:
        try:
            import pyudev
        except ImportError:
            return False
        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="hidraw")

            def _event(action=None, device=None):
                if self._stop.is_set():
                    return
                # Wait until udev has settled (uaccess ACL) on add/change.
                if not udev_event_is_ready(action, device):
                    return
                # Re-scan the whole hidraw class: add/remove/move of either
                # the cable or the receiver can change the preferred node
                # (the cable exposes three hidraw nodes; we keep interface 01).
                try:
                    self._emit(self._scanner())
                except Exception:  # noqa: BLE001
                    log.exception("hotplug udev event failed")

            # MonitorObserver's callback signature is (action, device).
            observer = pyudev.MonitorObserver(monitor, callback=_event, name="lift-elite-udev")
            observer.start()
            self._observer = observer
            # Also poll slowly as a safety net (missed netlink events).
            self._thread = threading.Thread(target=self._poll_loop, name="lift-elite-hotplug-aux", daemon=True)
            self._thread.start()
            return True
        except Exception as exc:  # noqa: BLE001 - no udev socket in some sandboxes
            log.debug("pyudev monitor unavailable (%s); polling", exc)
            return False
