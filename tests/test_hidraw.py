"""Report-descriptor parsing and hidraw node selection (no real device)."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from nzxt_lift_elite.device import (
    choose_vendor_hidraw,
    inspect_hidraw,
    parse_report_descriptor,
    parse_uevent_hid_id,
)
from nzxt_lift_elite.protocol import PRODUCT_ID, VENDOR_ID


# Usage Page (Vendor 0xFF00), Report ID 0x4E, 64-byte input/output.
DESC_WITH_4E = bytes.fromhex(
    "06 00 FF"  # Usage Page (0xFF00)
    "09 01"     # Usage
    "A1 01"     # Collection (Application)
    "85 4E"     # Report ID (0x4E)
    "15 00"     # Logical Min
    "26 FF 00"  # Logical Max
    "75 08"     # Report Size 8
    "95 40"     # Report Count 64
    "09 01"
    "81 02"     # Input
    "09 01"
    "91 02"     # Output
    "C0"
)

# Same vendor page, no Report ID items.
DESC_NO_ID = bytes.fromhex(
    "06 00 FF"
    "09 01"
    "A1 01"
    "15 00"
    "26 FF 00"
    "75 08"
    "95 40"
    "09 01"
    "81 02"
    "09 01"
    "91 02"
    "C0"
)

# Standard mouse page, Report ID 0x01 — not the vendor interface.
DESC_MOUSE = bytes.fromhex(
    "05 01"  # Usage Page (Generic Desktop)
    "09 02"  # Usage (Mouse)
    "A1 01"
    "85 01"  # Report ID 1
    "C0"
)


class ReportDescriptorTest(unittest.TestCase):
    def test_vendor_page_with_report_id_4e(self):
        info = parse_report_descriptor(DESC_WITH_4E)
        self.assertIn(0x4E, info.report_ids)
        self.assertTrue(info.uses_report_id(0x4E))
        self.assertTrue(info.has_vendor_usage_page)
        self.assertIn(0xFF00, info.usage_pages)
        # Decision used by the device layer: do not prepend 0x00.
        self.assertFalse(0x4E not in info.report_ids)

    def test_vendor_page_without_report_id(self):
        info = parse_report_descriptor(DESC_NO_ID)
        self.assertFalse(info.report_ids)
        self.assertTrue(info.has_vendor_usage_page)
        self.assertTrue(0x4E not in info.report_ids)  # prepend 0x00

    def test_standard_mouse_descriptor(self):
        info = parse_report_descriptor(DESC_MOUSE)
        self.assertEqual(info.report_ids, frozenset({0x01}))
        self.assertFalse(info.has_vendor_usage_page)
        self.assertFalse(info.uses_report_id(0x4E))

    def test_long_item_is_skipped(self):
        # 0xFE, size 3, tag 0, three data bytes, then a Report ID.
        data = bytes.fromhex("FE 03 00 AA BB CC 85 4E")
        info = parse_report_descriptor(data)
        self.assertEqual(info.report_ids, frozenset({0x4E}))


class UeventAndSelectionTest(unittest.TestCase):
    def test_parse_uevent_hid_id(self):
        text = (
            "DRIVER=hid-generic\n"
            "HID_ID=0003:00001E71:00002129\n"
            "HID_NAME=NZXT Lift Elite Wireless\n"
        )
        self.assertEqual(parse_uevent_hid_id(text), (0x0003, VENDOR_ID, PRODUCT_ID))
        self.assertIsNone(parse_uevent_hid_id("HID_NAME=foo\n"))

    def test_inspect_and_choose_prefers_interface_01(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_node(root, "hidraw0", interface=0, descriptor=DESC_MOUSE)
            self._write_node(root, "hidraw1", interface=1, descriptor=DESC_WITH_4E)
            # Wrong device must be ignored.
            self._write_node(
                root, "hidraw2", interface=1, descriptor=DESC_WITH_4E,
                vendor=0x1234, product=0x5678,
            )

            iface0 = inspect_hidraw("hidraw0", sys_root=root)
            iface1 = inspect_hidraw("hidraw1", sys_root=root)
            other = inspect_hidraw("hidraw2", sys_root=root)
            self.assertIsNotNone(iface0)
            self.assertIsNotNone(iface1)
            self.assertIsNone(other)
            self.assertEqual(iface0.interface, 0)
            self.assertEqual(iface1.interface, 1)
            self.assertTrue(iface1.uses_4e_report_id)
            self.assertFalse(iface1.prepend_zero_report_id)
            self.assertTrue(iface0.prepend_zero_report_id)  # 0x4E not in its IDs

            chosen = choose_vendor_hidraw([iface0, iface1])
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen.name, "hidraw1")
            self.assertEqual(chosen.path, "/dev/hidraw1")

    def test_prepend_zero_when_descriptor_has_no_report_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_node(root, "hidraw3", interface=1, descriptor=DESC_NO_ID)
            info = inspect_hidraw("hidraw3", sys_root=root)
            self.assertIsNotNone(info)
            self.assertTrue(info.prepend_zero_report_id)
            self.assertTrue(info.has_vendor_usage_page)

    @staticmethod
    def _write_node(
        root: Path,
        name: str,
        *,
        interface: int,
        descriptor: bytes,
        vendor: int = VENDOR_ID,
        product: int = PRODUCT_ID,
    ) -> None:
        # hidraw/<name>/device is a real directory here (sysfs uses a symlink).
        device = root / name / "device"
        iface = root / name / "iface"
        iface.mkdir(parents=True)
        device.mkdir(parents=True)
        (device / "uevent").write_text(
            f"HID_ID=0003:{vendor:08X}:{product:08X}\n",
            encoding="utf-8",
        )
        (device / "report_descriptor").write_bytes(descriptor)
        (iface / "bInterfaceNumber").write_text(f"{interface:02x}\n", encoding="ascii")
        # Point "device" resolution upward so _read_interface_number can walk
        # to the sibling iface dir... easier: put bInterfaceNumber in a parent
        # of the resolved device path. Write it also next to device via a
        # fake usb interface directory that device resolves into.
        # inspect uses device_dir = sys_root/name/device and walks parents,
        # so put bInterfaceNumber in sys_root/name as well.
        (root / name / "bInterfaceNumber").write_text(f"{interface:02x}\n", encoding="ascii")


if __name__ == "__main__":
    unittest.main()


class HermeticGuardTest(unittest.TestCase):
    """tests/__init__.py keeps the suite away from the user's mouse and settings."""

    def test_real_hidraw_blocked(self):
        import os

        from nzxt_lift_elite import device

        with self.assertRaises(PermissionError):
            os.open("/dev/hidraw0", os.O_RDWR)
        with self.assertRaises(PermissionError):
            device.open_hidraw("/dev/hidraw0", timeout=0)
        self.assertEqual(device.iter_lift_elite_hidraw(), [])

    def test_private_home(self):
        import os
        import tempfile

        self.assertTrue(os.environ["XDG_CONFIG_HOME"].startswith(tempfile.gettempdir()))
        self.assertEqual(os.environ["LIFT_ELITE_LANG"], "it")
