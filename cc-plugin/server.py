#!/usr/bin/env python3
"""CoolerControl device-service plugin for the NZXT Lift Elite.

The mouse has no fans or temperatures. It shows up in CoolerControl as a
device with "lighting" channels that work as selectors for one onboard
profile slot (``profile_slot`` in config.json, set from the plugin page):

* ``dpi_stage``    active DPI stage (stage-1 .. stage-5)
* ``polling``      polling rate (250 .. 8000 Hz; 8000 only through the cable)
* ``liftoff``      lift-off distance (1 / 2 mm)
* ``motion_sync``  on / off
* ``profile_led``  colour of the profile LED (one colour, stored as RGB565;
  shown for about two seconds on a long press of the purple button)

Every request reads the slot first and writes only when something changes,
then reads it back (same library as ``liftctl`` and the GUI; a file lock keeps
them from interleaving). ResetChannel restores what the slot held when the
plugin started. Battery level is not exposed: the reply that probably carries
it has not been decoded with certainty.

No firmware command is reachable from here: the library refuses them.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from concurrent import futures
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
# development checkout: the library sits one folder up
if (HERE.parent / "nzxt_lift_elite" / "__init__.py").is_file():
    sys.path.insert(0, str(HERE.parent))

from nzxt_lift_elite import __version__ as LIB_VERSION  # noqa: E402
from nzxt_lift_elite.device import LiftElite, find_hidraw  # noqa: E402
from nzxt_lift_elite.hotplug import Monitor, scan  # noqa: E402
from nzxt_lift_elite.errors import LiftEliteError  # noqa: E402
from nzxt_lift_elite.i18n import _  # noqa: E402
from nzxt_lift_elite.profile import Profile  # noqa: E402
from nzxt_lift_elite.protocol import (  # noqa: E402
    DPI_STAGE_COUNT,
    LIFTOFF_VALUES,
    POLLING_HZ,
    rgb565_to_rgb,
    rgb_to_rgb565,
)

PLUGIN_ID = "nzxt_lift_elite"
DEVICE_ID = "lift-elite"
DEFAULT_SOCKET = f"/run/coolercontrol-plugin-{PLUGIN_ID}.sock"
CONFIG_FILE = "config.json"
VERSION = "0.2.0"

log = logging.getLogger(PLUGIN_ID)

Opener = Callable[[], LiftElite]


def default_opener() -> LiftElite:
    return LiftElite.open(timeout=1.0)


@dataclass(frozen=True)
class Mode:
    name: str
    label: str
    colors: int = 0  # 0 = pure selector, 1 = needs one colour


@dataclass(frozen=True)
class Channel:
    id: str
    label: str
    modes: tuple[Mode, ...]


class PluginError(Exception):
    """A request CoolerControl sent that cannot be honoured (bad channel/mode)."""


class PluginCore:
    """Everything the gRPC service does, without gRPC (unit-testable)."""

    def __init__(self, plugin_dir: Path, opener: Opener = default_opener) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.opener = opener
        self.started = time.monotonic()
        self.initial: dict[int, Profile] = {}
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._monitor: Monitor | None = None

    # --- configuration ----------------------------------------------------

    def config(self) -> dict:
        try:
            data = json.loads((self.plugin_dir / CONFIG_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("config.json ignored: %s", exc)
            return {}
        return data if isinstance(data, dict) else {}

    def slot(self) -> int:
        value = self.config().get("profile_slot", 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
        return value if 0 <= value <= 4 else 0

    # --- device access ----------------------------------------------------

    def _with_device(self, fn):
        with self._lock:
            dev = self.opener()
            try:
                with dev.locked():
                    result = fn(dev)
                self.last_error = None
                return result
            except (LiftEliteError, OSError) as exc:
                self.last_error = str(exc)
                raise
            finally:
                try:
                    dev.close()
                except OSError:
                    pass

    def present(self) -> bool:
        try:
            return scan().connected
        except (LiftEliteError, OSError):
            return False

    def start_monitor(self, monitor: Monitor | None = None) -> str | None:
        """Watch hidraw; reopen happens on the next request. Never writes."""
        self.stop_monitor()
        mon = monitor if monitor is not None else Monitor()
        self._monitor = mon
        return mon.start(self._on_hotplug)

    def stop_monitor(self) -> None:
        mon = self._monitor
        self._monitor = None
        if mon is not None:
            mon.stop()

    def _on_hotplug(self, previous, current) -> None:
        if previous.identity() == current.identity():
            return
        if current.connected and not previous.connected:
            log.info("Lift Elite connected (%s, %s)", current.chosen.path, current.chosen.transport)
            self.last_error = None
        elif previous.connected and not current.connected:
            log.info("Lift Elite disconnected")
        elif current.chosen and previous.chosen:
            log.info(
                "Lift Elite switched to %s (%s)",
                current.chosen.path, current.chosen.transport,
            )
            self.last_error = None

    def read_slot(self, slot: int | None = None) -> Profile:
        index = self.slot() if slot is None else slot
        return self._with_device(lambda dev: dev.read_profile(index, with_buttons=False))

    def initialize(self) -> Profile:
        slot = self.slot()
        profile = self.read_slot(slot)
        self.initial.setdefault(slot, profile)
        log.info("slot %d: %s", slot, describe(profile))
        return profile

    # --- channel description ----------------------------------------------

    def channels(self, profile: Profile | None = None) -> list[Channel]:
        dpi = profile.dpi_stages if profile is not None else ()
        stage_modes = []
        for n in range(1, DPI_STAGE_COUNT + 1):
            label = _("Stage {n}", n=n)
            if len(dpi) >= n:
                label += f" · {dpi[n - 1]} DPI"
            stage_modes.append(Mode(f"stage-{n}", label))
        slot = self.slot() + 1
        polling = []
        for hz in POLLING_HZ.values():
            note = " (" + _("cable only") + ")" if hz == 8000 else ""
            polling.append(Mode(f"{hz}hz", f"{hz} Hz{note}"))
        return [
            Channel("dpi_stage", _("DPI stage (profile {n})", n=slot), tuple(stage_modes)),
            Channel("polling", _("Polling rate (profile {n})", n=slot), tuple(polling)),
            Channel("liftoff", _("Lift-off (profile {n})", n=slot), tuple(Mode(f"{v}mm", f"{v} mm") for v in LIFTOFF_VALUES)),
            Channel("motion_sync", _("Motion sync (profile {n})", n=slot), (Mode("on", _("On")), Mode("off", _("Off")))),
            Channel("profile_led", _("Profile LED (profile {n})", n=slot), (Mode("static", _("Static colour"), colors=1),)),
        ]

    # --- writes -----------------------------------------------------------

    @staticmethod
    def changes_for(channel: str, mode: str, colors: list[tuple[int, int, int]] | None = None) -> dict:
        try:
            if channel == "dpi_stage":
                if not mode.startswith("stage-"):
                    raise ValueError
                stage = int(mode.removeprefix("stage-"))
                if not 1 <= stage <= DPI_STAGE_COUNT:
                    raise ValueError
                return {"stage": stage}
            if channel == "polling":
                hz = int(mode.removesuffix("hz"))
                if hz not in POLLING_HZ.values():
                    raise ValueError
                return {"polling_hz": hz}
            if channel == "liftoff":
                mm = int(mode.removesuffix("mm"))
                if mm not in LIFTOFF_VALUES:
                    raise ValueError
                return {"liftoff": mm}
            if channel == "motion_sync":
                if mode not in ("on", "off"):
                    raise ValueError
                return {"motion_sync": mode == "on"}
            if channel == "profile_led":
                if mode != "static" or not colors:
                    raise ValueError
                r, g, b = (max(0, min(255, int(c))) for c in colors[0])
                return {"color": rgb_to_rgb565(r, g, b)}
        except ValueError:
            raise PluginError(f"mode '{mode}' is not valid for channel '{channel}'") from None
        raise PluginError(f"unknown channel '{channel}'")

    def apply(self, channel: str, mode: str, colors: list[tuple[int, int, int]] | None = None) -> bool:
        """Apply one selector; returns True if the mouse was written."""
        changes = self.changes_for(channel, mode, colors)
        slot = self.slot()

        def run(dev: LiftElite) -> bool:
            current = dev.read_profile(slot, with_buttons=False)
            self.initial.setdefault(slot, current)
            target = current.with_changes(**changes)
            if target.known_body() == current.known_body():
                log.debug("slot %d %s=%s: already set", slot, channel, mode)
                return False
            back = dev.apply_profile(target, current=current)
            log.info("slot %d %s=%s -> %s", slot, channel, mode, describe(back))
            return True

        return self._with_device(run)

    def reset(self, channel: str) -> bool:
        slot = self.slot()
        start = self.initial.get(slot)
        if start is None:
            log.info("reset %s: no starting state recorded for slot %d", channel, slot)
            return False
        if channel == "dpi_stage":
            return self.apply(channel, f"stage-{start.stage}")
        if channel == "polling":
            return self.apply(channel, f"{start.polling_hz}hz")
        if channel == "liftoff":
            return self.apply(channel, f"{start.liftoff}mm")
        if channel == "motion_sync":
            return self.apply(channel, "on" if start.motion_sync else "off")
        if channel == "profile_led":
            return self.apply(channel, "static", [rgb565_to_rgb(start.color)])
        raise PluginError(f"unknown channel '{channel}'")

    def health(self) -> tuple[str, str]:
        """('ok'|'warning', reason)."""
        if not self.present():
            return "warning", "mouse/receiver not found"
        if self.last_error:
            return "warning", self.last_error
        return "ok", ""


def describe(p: Profile) -> str:
    return (
        f"stage {p.stage} ({p.current_dpi} DPI), {p.polling_hz} Hz, lift-off {p.liftoff} mm, "
        f"motion sync {'on' if p.motion_sync else 'off'}, LED {p.color_rgb_hex}"
    )


# --- gRPC -------------------------------------------------------------------


def load_stubs(gen_dir: Path | None = None):
    candidates = [gen_dir] if gen_dir else []
    candidates += [HERE / "gen", Path("/usr/lib/nzxt-lift-elite/coolercontrol/gen")]
    for cand in candidates:
        if cand and (cand / "coolercontrol").is_dir():
            sys.path.insert(0, str(cand))
            break
    import grpc  # noqa: F401
    from coolercontrol.device_service.v1 import (  # type: ignore[import-not-found]
        device_service_pb2_grpc,
        health_pb2,
        initialize_device_pb2,
        lcd_pb2,
        lighting_pb2,
        list_devices_pb2,
        reset_channel_pb2,
        shutdown_pb2,
        status_pb2,
        custom_function_one_pb2,
        enable_manual_fan_control_pb2,
        fixed_duty_pb2,
        speed_profile_pb2,
    )
    from coolercontrol.models.v1 import (  # type: ignore[import-not-found]
        channel_info_pb2,
        device_info_pb2,
        device_pb2,
        driver_info_pb2,
        lighting_modes_pb2,
    )

    return {k: v for k, v in locals().items() if k.endswith("_pb2") or k.endswith("_grpc")}


def make_servicer(core: PluginCore, stubs: dict, on_shutdown: Callable[[], None]):
    import grpc

    s = stubs
    Base = s["device_service_pb2_grpc"].DeviceServiceServicer
    Health = s["health_pb2"].HealthResponse

    def build_device(profile: Profile | None):
        LM = s["lighting_modes_pb2"].LightingModes
        channels = {}
        for ch in core.channels(profile):
            modes = [
                LM.LightingMode(
                    name=m.name, frontend_name=m.label, min_colors=m.colors, max_colors=m.colors,
                    speed_enabled=False, backward_enabled=False,
                )
                for m in ch.modes
            ]
            channels[ch.id] = s["channel_info_pb2"].ChannelInfo(label=ch.label, lighting_modes=LM(lighting_mode=modes))
        driver = s["driver_info_pb2"].DriverInfo(name="nzxt-lift-elite", version=LIB_VERSION)
        try:
            driver.locations.append(find_hidraw().path)
        except LiftEliteError:
            pass
        info = s["device_info_pb2"].DeviceInfo(channels=channels, model="NZXT Lift Elite", driver_info=driver)
        return s["device_pb2"].Device(id=DEVICE_ID, name="Lift Elite", info=info)

    def check_device(device_id: str, context) -> None:
        if device_id != DEVICE_ID:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown device '{device_id}'")

    def run(context, fn):
        try:
            return fn()
        except PluginError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except LiftEliteError as exc:
            log.error("%s", exc)
            context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

    def unsupported(name: str):
        def handler(self, request, context):
            context.abort(grpc.StatusCode.UNIMPLEMENTED, f"{name} is not supported by a mouse")
        return handler

    class Servicer(Base):
        def Health(self, request, context):
            state, reason = core.health()
            if reason:
                log.debug("health: %s", reason)
            status = Health.STATUS_OK if state == "ok" else Health.STATUS_WARNING
            return Health(name=PLUGIN_ID, version=VERSION, status=status,
                          uptime_seconds=int(time.monotonic() - core.started))

        def ListDevices(self, request, context):
            if not core.present():
                log.warning("no NZXT Lift Elite (cable or receiver) found")
                return s["list_devices_pb2"].ListDevicesResponse()
            try:
                profile = core.read_slot()
            except LiftEliteError as exc:
                log.warning("mouse not answering (%s); listing it anyway", exc)
                profile = None
            return s["list_devices_pb2"].ListDevicesResponse(devices=[build_device(profile)])

        def InitializeDevice(self, request, context):
            check_device(request.device_id, context)
            try:
                core.initialize()
            except LiftEliteError as exc:
                # a sleeping wireless mouse must not make CoolerControl drop the device
                log.warning("initialize: %s", exc)
            return s["initialize_device_pb2"].InitializeDeviceResponse()

        def Shutdown(self, request, context):
            log.info("shutdown requested")
            threading.Thread(target=on_shutdown, daemon=True).start()
            return s["shutdown_pb2"].ShutdownResponse()

        def Status(self, request, context):
            check_device(request.device_id, context)
            return s["status_pb2"].StatusResponse()  # no sensors (battery not decoded)

        def ResetChannel(self, request, context):
            check_device(request.device_id, context)
            run(context, lambda: core.reset(request.channel_id))
            return s["reset_channel_pb2"].ResetChannelResponse()

        def Lighting(self, request, context):
            check_device(request.device_id, context)
            colors = [(c.r, c.g, c.b) for c in request.setting.colors]
            run(context, lambda: core.apply(request.channel_id, request.setting.mode, colors))
            return s["lighting_pb2"].LightingResponse()

        EnableManualFanControl = unsupported("EnableManualFanControl")
        FixedDuty = unsupported("FixedDuty")
        SpeedProfile = unsupported("SpeedProfile")
        Lcd = unsupported("Lcd")
        CustomFunctionOne = unsupported("CustomFunctionOne")

    return Servicer()


def serve(core: PluginCore, socket_path: str, stubs: dict) -> None:
    import grpc

    stop = threading.Event()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    s = stubs
    s["device_service_pb2_grpc"].add_DeviceServiceServicer_to_server(make_servicer(core, s, stop.set), server)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    server.add_insecure_port(f"unix:{socket_path}")
    server.start()
    log.info("listening on %s (profile slot %d)", socket_path, core.slot() + 1)
    try:
        backend = core.start_monitor()
        log.info("hotplug monitor: %s", backend)
    except Exception as exc:  # noqa: BLE001
        log.warning("hotplug monitor not started: %s", exc)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    core.stop_monitor()
    server.stop(grace=2).wait()
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass


def setup_logging(debug: bool) -> None:
    level_name = "DEBUG" if debug else os.environ.get("CC_LOG", "INFO").upper()
    level = getattr(logging, level_name.split(",")[0], logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--socket", default=DEFAULT_SOCKET)
    ap.add_argument("--plugin-dir", type=Path, default=HERE, help="folder with config.json")
    ap.add_argument("--gen-dir", type=Path, help="folder with the generated gRPC stubs")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    setup_logging(args.debug)

    opener: Opener = default_opener
    if args.mock:
        from nzxt_lift_elite.mock import MouseEmulator, open_mock

        emu = MouseEmulator()
        holders = []

        def opener() -> LiftElite:  # type: ignore[no-redef]
            dev, fake = open_mock("wired", emu)
            holders.append(fake)
            return dev

        core = PluginCore(args.plugin_dir, opener)
        core.present = lambda: True  # type: ignore[method-assign]
    else:
        core = PluginCore(args.plugin_dir, opener)
    serve(core, args.socket, load_stubs(args.gen_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
