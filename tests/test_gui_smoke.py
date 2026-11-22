"""Build the GTK window against the emulated mouse (needs a display; skipped otherwise).

Run headless with:  xvfb-run -a python3 -m unittest tests.test_gui_smoke
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock


def _gtk_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class GuiSmokeTest(unittest.TestCase):
    def test_window_builds_and_reads_mock(self):
        from gi.repository import GLib

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "GTK_A11Y": "none"}):
            args = gui.parse_args(["--mock", "--lang", "it"])
            application = gui.LiftEliteApp(args)
            seen = {}

            def check():
                win = application.window
                if win is None or not win.model.loaded:
                    if time.monotonic() - seen.setdefault("t0", time.monotonic()) > 10:
                        application.quit()
                        return False
                    return True
                seen["slots"] = len(win.model.drafts)
                seen["polling"] = win.polling_row.get_selected()
                seen["chip"] = win.button_chips["forward"].get_label()
                seen["apply_sensitive"] = win.apply_btn.get_sensitive()
                seen["has_slot_subtitle"] = hasattr(win, "slot_subtitle")
                seen["slot_hint"] = win.slot_hint.get_label()
                seen["dev_desc"] = win.dev_group.get_description() or ""
                # edit -> dirty -> apply through the emulator
                win.model.set_button("forward", 0x80)
                win.populate()
                seen["dirty"] = win.apply_btn.get_sensitive()
                emu = win.session._fake.emulator
                seen["writes_before_apply"] = len(emu.writes)
                win.apply()

                def after_apply():
                    if win.model.any_dirty:
                        return True
                    seen["writes_after_apply"] = len(emu.writes)
                    seen["emu_forward"] = emu.buttons[(0, 3)]
                    win._force_close = True
                    application.quit()
                    return False

                GLib.timeout_add(50, after_apply)
                return False

            GLib.timeout_add(100, check)
            application.run(["lift-elite-control"])

        self.assertEqual(seen.get("slots"), 5)
        self.assertEqual(seen["polling"], 5)  # captured slot 0: 8000 Hz
        self.assertEqual(seen["chip"], "Mouse avanti")
        self.assertFalse(seen["apply_sensitive"])  # nothing to apply at startup
        self.assertFalse(seen["has_slot_subtitle"])
        self.assertIn("tasto viola", seen["slot_hint"])
        self.assertNotIn("sola lettura", seen["dev_desc"].lower())
        self.assertNotIn("LED #", seen["slot_hint"])
        self.assertEqual(seen["writes_before_apply"], 0)  # no writes on startup
        self.assertTrue(seen["dirty"])
        self.assertEqual(seen["writes_after_apply"], 1)
        self.assertEqual(seen["emu_forward"], 0x80)


if __name__ == "__main__":
    unittest.main()
