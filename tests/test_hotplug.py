"""Hotplug presence and the polling monitor (fake sysfs, no real device)."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
import tempfile

from nzxt_lift_elite.hotplug import Monitor, Presence, pyudev_available, scan
from nzxt_lift_elite.protocol import PRODUCT_ID, PRODUCT_ID_DONGLE, VENDOR_ID

from tests.test_hidraw import DESC_MOUSE, DESC_WITH_4E, UeventAndSelectionTest


write_node = UeventAndSelectionTest._write_node


class ScanTest(unittest.TestCase):
    def test_prefers_cable_when_both_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_node(root, "hidraw1", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID_DONGLE)
            write_node(root, "hidraw3", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID)
            presence = scan(sys_root=root)
            self.assertIsNotNone(presence.wired)
            self.assertIsNotNone(presence.wireless)
            self.assertEqual(presence.chosen.path, "/dev/hidraw3")
            self.assertEqual(presence.chosen.transport, "wired")
            self.assertTrue(presence.connected)

    def test_receiver_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_node(root, "hidraw2", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID_DONGLE)
            presence = scan(sys_root=root)
            self.assertIsNone(presence.wired)
            self.assertEqual(presence.chosen.transport, "wireless")

    def test_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            presence = scan(sys_root=Path(tmp))
            self.assertFalse(presence.connected)
            self.assertIsNone(presence.chosen)

    def test_cable_with_three_hidraw_nodes_picks_vendor_interface(self):
        # Real mouse (1e71:2129) exposes three hidraw nodes; only interface 01
        # is the vendor protocol endpoint (the one liftctl/the GUI must open).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_node(root, "hidraw22", interface=0, descriptor=DESC_MOUSE, product=PRODUCT_ID)
            write_node(root, "hidraw24", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID)
            write_node(root, "hidraw25", interface=2, descriptor=DESC_MOUSE, product=PRODUCT_ID)
            presence = scan(sys_root=root)
            self.assertEqual(presence.chosen.path, "/dev/hidraw24")
            self.assertEqual(presence.chosen.interface, 1)
            self.assertTrue(presence.chosen.has_vendor_usage_page)

    def test_transport_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_node(root, "hidraw1", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID_DONGLE)
            write_node(root, "hidraw3", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID)
            only_rx = scan(sys_root=root, transport="wireless")
            self.assertEqual(only_rx.chosen.transport, "wireless")


class MonitorPollTest(unittest.TestCase):
    def test_poll_emits_on_plug_and_unplug(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []
            mon = Monitor(sys_root=root, interval=0.05, use_udev=False)

            def on_change(prev, cur):
                events.append((prev.identity(), cur.identity()))

            self.assertEqual(mon.start(on_change), "poll")
            self.addCleanup(mon.stop)
            self.assertFalse(mon.snapshot().connected)

            write_node(root, "hidraw4", interface=1, descriptor=DESC_WITH_4E, product=PRODUCT_ID)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not any(e[1][0] == "/dev/hidraw4" for e in events):
                time.sleep(0.05)
            self.assertTrue(mon.snapshot().connected, events)
            self.assertEqual(mon.snapshot().chosen.path, "/dev/hidraw4")

            # Unplug: remove the sysfs node.
            import shutil
            shutil.rmtree(root / "hidraw4")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and mon.snapshot().connected:
                time.sleep(0.05)
            self.assertFalse(mon.snapshot().connected)
            self.assertTrue(any(e[0][0] == "/dev/hidraw4" and e[1][0] is None for e in events), events)

    def test_injected_scanner_and_rescan(self):
        state = {"p": Presence()}
        events = []
        mon = Monitor(scanner=lambda: state["p"], interval=60, use_udev=False)
        mon.start(lambda a, b: events.append((a, b)))
        self.addCleanup(mon.stop)
        # Build a minimal presence change via rescan (no thread wait).
        from nzxt_lift_elite.device import HidrawInfo

        def fake(path, pid):
            return HidrawInfo(
                path=path, name=Path(path).name, bus=3, vendor_id=VENDOR_ID, product_id=pid,
                interface=1, report_ids=frozenset({0x4E}), usage_pages=frozenset({0xFF00}),
                has_vendor_usage_page=True, prepend_zero_report_id=False,
            )

        wired = fake("/dev/hidraw5", PRODUCT_ID)
        state["p"] = Presence(wired=wired, chosen=wired)
        mon.rescan()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0][1].connected)
        # Same identity: no second event.
        mon.rescan()
        self.assertEqual(len(events), 1)
        # Prefer cable when a receiver also appears: chosen stays the cable.
        rx = fake("/dev/hidraw6", PRODUCT_ID_DONGLE)
        state["p"] = Presence(wired=wired, wireless=rx, chosen=wired)
        mon.rescan()
        self.assertEqual(events[-1][1].chosen.path, "/dev/hidraw5")
        self.assertEqual(events[-1][1].wireless.path, "/dev/hidraw6")

    def test_udev_unavailable_falls_back_to_poll(self):
        mon = Monitor(sys_root=Path("/tmp"), interval=60, use_udev=True)
        # Even if pyudev is installed, a forced failing start should poll.
        mon._start_udev = lambda: False  # type: ignore[method-assign]
        self.assertEqual(mon.start(lambda *_: None), "poll")
        mon.stop()
        # The helper itself is just an import check.
        self.assertIsInstance(pyudev_available(), bool)


class EditorAdoptTest(unittest.TestCase):
    """Reconnect must not wipe unsaved edits (uses the mock mouse)."""

    def test_adopt_state_keeps_dirty_drafts(self):
        from nzxt_lift_elite.gui.model import EditorModel
        from nzxt_lift_elite.mock import open_mock

        dev, fake = open_mock("wired")
        self.addCleanup(fake.close)
        self.addCleanup(dev.close)
        state = dev.read_state()
        model = EditorModel()
        model.load_state(state)
        model.set_polling(500)
        self.assertTrue(model.is_dirty(0))
        # Pretend the mouse came back with the original 8000 Hz still on the device.
        state2 = dev.read_state()
        kept = model.adopt_state(state2)
        self.assertTrue(kept)
        self.assertEqual(model.current().polling_hz, 500)
        self.assertEqual(model.original().polling_hz, 8000)
        self.assertTrue(model.is_dirty(0))

    def test_adopt_state_without_edits_takes_device(self):
        from nzxt_lift_elite.gui.model import EditorModel
        from nzxt_lift_elite.mock import open_mock

        dev, fake = open_mock("wired")
        self.addCleanup(fake.close)
        self.addCleanup(dev.close)
        model = EditorModel()
        model.load_state(dev.read_state())
        kept = model.adopt_state(dev.read_state())
        self.assertFalse(kept)
        self.assertFalse(model.any_dirty)


class UdevSettleTest(unittest.TestCase):
    def test_ready_when_initialized_or_removed(self):
        from nzxt_lift_elite.hotplug import udev_event_is_ready

        class Dev:
            def __init__(self, initialized=True, tags=None, action="add"):
                self.is_initialized = initialized
                self.tags = tags
                self.action = action

        self.assertTrue(udev_event_is_ready("add", None))
        self.assertFalse(udev_event_is_ready("add", Dev(initialized=False)))
        self.assertFalse(udev_event_is_ready("add", Dev(initialized=True, tags={"seat"})))
        self.assertTrue(udev_event_is_ready("add", Dev(initialized=True, tags={"uaccess", "seat"})))
        self.assertTrue(udev_event_is_ready("add", Dev(initialized=True, tags=None)))
        self.assertTrue(udev_event_is_ready("remove", Dev(initialized=False, tags={"seat"})))
        self.assertTrue(udev_event_is_ready("unbind", Dev(initialized=False)))


class OpenRetryTest(unittest.TestCase):
    def test_permission_error_retries_then_succeeds(self):
        from nzxt_lift_elite.device import open_hidraw

        calls = {"n": 0}
        slept = []

        def opener(path, flags):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("denied")
            self.assertEqual(path, "/dev/hidraw24")
            return 99

        fd = open_hidraw("/dev/hidraw24", opener=opener, sleeper=slept.append, timeout=2.0)
        self.assertEqual(fd, 99)
        self.assertEqual(calls["n"], 3)
        self.assertGreaterEqual(len(slept), 2)
        self.assertGreater(slept[1], slept[0])  # backoff grows

    def test_permission_error_after_timeout_is_raised(self):
        from nzxt_lift_elite.device import open_hidraw

        def opener(path, flags):
            raise PermissionError("denied")

        with self.assertRaises(PermissionError) as ctx:
            open_hidraw("/dev/hidraw24", opener=opener, sleeper=lambda _s: None, timeout=0.0)
        self.assertIn("permesso negato su /dev/hidraw24", str(ctx.exception))
        self.assertIn("70-nzxt-lift-elite.rules", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()
