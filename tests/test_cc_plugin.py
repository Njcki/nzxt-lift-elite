"""CoolerControl plugin: core logic against the emulator, plus a real gRPC
round trip over a Unix socket when grpcio + grpcio-tools are installed."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from nzxt_lift_elite.mock import MouseEmulator, open_mock
from nzxt_lift_elite.protocol import rgb565_to_rgb

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "cc-plugin"


def load_server():
    spec = importlib.util.spec_from_file_location("cc_lift_server", PLUGIN / "server.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


server = load_server()


class Harness:
    def __init__(self, slot: int | None = None) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        if slot is not None:
            (self.dir / "config.json").write_text(json.dumps({"profile_slot": slot}))
        self.emu = MouseEmulator()
        self.fakes = []
        self.core = server.PluginCore(self.dir, self.open)
        self.core.present = lambda: True

    def open(self):
        dev, fake = open_mock("wired", self.emu)
        self.fakes.append(fake)
        return dev

    def close(self):
        for f in self.fakes:
            f.close()
        self.tmp.cleanup()


class PluginCoreTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def profile(self, slot=0):
        dev, fake = open_mock("wired", self.h.emu)
        try:
            return dev.read_profile(slot, with_buttons=False)
        finally:
            dev.close()
            fake.close()

    def test_channels_and_modes(self):
        chans = {c.id: c for c in self.h.core.channels(self.profile())}
        self.assertEqual(set(chans), {"dpi_stage", "polling", "liftoff", "motion_sync", "profile_led"})
        self.assertEqual([m.name for m in chans["dpi_stage"].modes], [f"stage-{n}" for n in range(1, 6)])
        self.assertIn("400 DPI", chans["dpi_stage"].modes[0].label)
        self.assertEqual(chans["profile_led"].modes[0].colors, 1)

    def test_no_write_when_unchanged(self):
        cur = self.profile()
        self.assertFalse(self.h.core.apply("polling", f"{cur.polling_hz}hz"))
        self.assertEqual(self.h.emu.writes, [])

    def test_apply_and_reset(self):
        self.h.core.initialize()
        start = self.profile()
        self.assertTrue(self.h.core.apply("polling", "1000hz"))
        self.assertTrue(self.h.core.apply("dpi_stage", "stage-3"))
        self.assertTrue(self.h.core.apply("motion_sync", "off"))
        self.assertTrue(self.h.core.apply("profile_led", "static", [(255, 0, 0)]))
        now = self.profile()
        self.assertEqual((now.polling_hz, now.stage, now.motion_sync), (1000, 3, False))
        self.assertEqual(rgb565_to_rgb(now.color), (255, 0, 0))
        self.assertEqual(now.dpi, start.dpi)  # nothing else touched
        for ch in ("polling", "dpi_stage", "motion_sync", "profile_led"):
            self.h.core.reset(ch)
        self.assertEqual(self.profile().known_body(), start.known_body())

    def test_slot_from_config(self):
        h = Harness(slot=2)
        self.addCleanup(h.close)
        h.core.apply("liftoff", "2mm")
        dev, fake = open_mock("wired", h.emu)
        try:
            self.assertEqual(dev.read_profile(2, with_buttons=False).liftoff, 2)
        finally:
            dev.close()
            fake.close()
        # other slots untouched
        for packet in h.emu.writes:
            self.assertEqual(packet[5], 2)

    def test_invalid_requests(self):
        for ch, mode in (("polling", "123hz"), ("dpi_stage", "stage-9"), ("liftoff", "3mm"),
                         ("nope", "x"), ("profile_led", "static")):
            with self.assertRaises(server.PluginError):
                self.h.core.apply(ch, mode)
        self.assertEqual(self.h.emu.writes, [])

    def test_bad_config_falls_back_to_slot_0(self):
        (self.h.dir / "config.json").write_text("{not json")
        with self.assertLogs("nzxt_lift_elite", "WARNING"):
            self.assertEqual(self.h.core.slot(), 0)
        (self.h.dir / "config.json").write_text(json.dumps({"profile_slot": 7}))
        self.assertEqual(self.h.core.slot(), 0)

    def test_reopen_after_disconnect(self):
        from nzxt_lift_elite.errors import DeviceNotFoundError
        from nzxt_lift_elite.hotplug import Presence

        calls = {"n": 0}
        real = self.h.open

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise DeviceNotFoundError("unplugged")
            return real()

        self.h.core.opener = flaky
        with self.assertRaises(DeviceNotFoundError):
            self.h.core.read_slot()
        # Next request reopens successfully (same as a reconnect).
        profile = self.h.core.read_slot()
        self.assertEqual(profile.index, 0)
        self.assertGreaterEqual(calls["n"], 2)

        # A monitor callback must not crash the plugin or write anything.
        writes_before = list(self.h.emu.writes)
        self.h.core._on_hotplug(Presence(), Presence())  # no-op identities empty->empty
        gone = Presence()
        from nzxt_lift_elite.device import HidrawInfo
        from nzxt_lift_elite.protocol import PRODUCT_ID, VENDOR_ID
        info = HidrawInfo(
            path="/dev/hidraw9", name="hidraw9", bus=3, vendor_id=VENDOR_ID, product_id=PRODUCT_ID,
            interface=1, report_ids=frozenset({0x4E}), usage_pages=frozenset({0xFF00}),
            has_vendor_usage_page=True, prepend_zero_report_id=False,
        )
        present = Presence(wired=info, chosen=info)
        self.h.core._on_hotplug(gone, present)  # connect
        self.h.core._on_hotplug(present, gone)  # disconnect
        self.assertEqual(self.h.emu.writes, writes_before)


def _make_stubs(out: Path) -> bool:
    try:
        import grpc  # noqa: F401
    except ImportError:
        return False
    proc = subprocess.run(
        ["sh", str(PLUGIN / "build_stubs.sh"), str(out)], capture_output=True, text=True,
        env={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"},
    )
    return proc.returncode == 0


class GrpcRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.gen = Path(cls.tmp.name) / "gen"
        if not _make_stubs(cls.gen):
            cls.tmp.cleanup()
            raise unittest.SkipTest("grpcio / a recent grpcio-tools not available")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_round_trip(self):
        import grpc

        h = Harness()
        self.addCleanup(h.close)
        stubs = server.load_stubs(self.gen)
        sock = str(h.dir / "plugin.sock")
        t = threading.Thread(target=server.serve, args=(h.core, sock, stubs), daemon=True)
        # serve() installs signal handlers, only allowed in the main thread: patch them out
        orig = server.signal.signal
        server.signal.signal = lambda *a: None
        self.addCleanup(setattr, server.signal, "signal", orig)
        t.start()
        ch = grpc.insecure_channel(f"unix:{sock}")
        grpc.channel_ready_future(ch).result(timeout=10)
        stub = stubs["device_service_pb2_grpc"].DeviceServiceStub(ch)

        health = stub.Health(stubs["health_pb2"].HealthRequest())
        self.assertEqual(health.status, stubs["health_pb2"].HealthResponse.STATUS_OK)
        devices = stub.ListDevices(stubs["list_devices_pb2"].ListDevicesRequest()).devices
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].id, "lift-elite")
        self.assertIn("polling", devices[0].info.channels)
        stub.InitializeDevice(stubs["initialize_device_pb2"].InitializeDeviceRequest(device_id="lift-elite"))
        L = stubs["lighting_pb2"]
        stub.Lighting(L.LightingRequest(device_id="lift-elite", channel_id="polling",
                                        setting=L.LightingSetting(mode="500hz")))
        self.assertEqual(len(h.emu.writes), 1)
        with self.assertRaises(grpc.RpcError) as ctx:
            stub.Lighting(L.LightingRequest(device_id="lift-elite", channel_id="polling",
                                            setting=L.LightingSetting(mode="7hz")))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        with self.assertRaises(grpc.RpcError) as ctx:
            stub.FixedDuty(stubs["fixed_duty_pb2"].FixedDutyRequest())
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.UNIMPLEMENTED)
        stub.ResetChannel(stubs["reset_channel_pb2"].ResetChannelRequest(device_id="lift-elite", channel_id="polling"))
        self.assertEqual(len(h.emu.writes), 2)
        self.assertEqual(len(stub.Status(stubs["status_pb2"].StatusRequest(device_id="lift-elite")).status), 0)
        stub.Shutdown(stubs["shutdown_pb2"].ShutdownRequest())
        t.join(timeout=10)
        self.assertFalse(t.is_alive())
        ch.close()



if __name__ == "__main__":
    unittest.main()
