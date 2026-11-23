"""Regression: edit buttons of a NON-active profile and press Applica.

Real mouse: editing profile 2 (slot 1) while profile 1 (slot 0) was active,
middle -> Mute, Applica -> the firmware put Mute on profile 1 (it ignores the
profile byte of 0x50) and the read-back of profile 2 failed. Now the write is
refused before anything is sent, the active profile stays untouched, and it
works once profile 2 is active. The live poller runs concurrently.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

from tests.test_gui_smoke import _gtk_available


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class GuiRemapActiveTest(unittest.TestCase):
    def test_non_active_profile_buttons(self):
        from gi.repository import GLib

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen: dict = {}
        errors: list = []

        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "GTK_A11Y": "none"}), \
                mock.patch.object(gui.MainWindow, "is_active", lambda self: True), \
                mock.patch.object(gui.MainWindow, "error_dialog", lambda self, h, b: errors.append((h, b))):
            application = gui.LiftEliteApp(gui.parse_args(["--mock", "--lang", "it"]))
            steps: list = []

            def start():
                win = application.window
                if win is None or not win.model.loaded:
                    if time.monotonic() - seen.setdefault("t0", time.monotonic()) > 10:
                        application.quit()
                        return False
                    return True
                emu = win.session._fake.emulator

                def poll_now():
                    if not win._poll_inflight:
                        win._cancel_poll()
                        win._poll_tick()

                def s_edit_profile2():
                    win.model.select(1)  # editing profile 2; profile 1 active on the mouse
                    win.populate()
                    seen["locked"] = win.buttons_locked()
                    seen["rows_sensitive"] = [r.get_sensitive() for r in win.button_rows.values()]
                    seen["desc"] = win.buttons_group.get_description()
                    # a pending button edit (e.g. from a named profile / before the lock)
                    win.model.set_button("middle", 0x7F)
                    win.populate()
                    emu.log.clear()
                    poll_now()  # poller running concurrently with Apply
                    win.apply()
                    poll_now()

                def s_check_refused():
                    if win.spinner.get_spinning() or win._poll_inflight:
                        steps.insert(0, s_check_refused)
                        return
                    seen["errors"] = list(errors)
                    seen["writes"] = list(emu.writes)
                    seen["slot0_middle"] = emu.buttons[(0, 1)]
                    seen["slot1_middle"] = emu.buttons[(1, 1)]
                    seen["still_dirty"] = win.model.dirty_slots()
                    seen["selected"] = win.model.selected
                    # user long-presses to profile 2, poller notices, then Applica
                    emu.long_press_dpi_button()
                    poll_now()

                def s_apply_active():
                    if win._poll_inflight:
                        steps.insert(0, s_apply_active)
                        return
                    seen["unlocked"] = not win.buttons_locked()
                    emu.log.clear()
                    win.apply()
                    poll_now()

                def s_check_ok():
                    if win.spinner.get_spinning() or win._poll_inflight:
                        steps.insert(0, s_check_ok)
                        return
                    seen["writes_ok"] = [(w[4], w[5], w[6], w[8]) for w in emu.writes]
                    seen["slot0_middle_after"] = emu.buttons[(0, 1)]
                    seen["slot1_middle_after"] = emu.buttons[(1, 1)]
                    seen["dirty_after"] = win.model.dirty_slots()
                    seen["sent"] = {p[4] for p in emu.log}
                    win._force_close = True
                    application.quit()

                steps.extend([s_edit_profile2, s_check_refused, s_apply_active, s_check_ok])

                def tick():
                    if steps:
                        steps.pop(0)()
                        return True
                    return False

                GLib.timeout_add(120, tick)
                return False

            GLib.timeout_add(100, start)
            application.run(["lift-elite-control"])

        self.assertTrue(seen["locked"])
        self.assertEqual(set(seen["rows_sensitive"]), {False})
        self.assertIn("solo sul profilo attivo", seen["desc"])
        # refused: nothing written, profile 1 (active) untouched, edit kept
        self.assertEqual(len(seen["errors"]), 1)
        self.assertIn("solo sul profilo attivo", seen["errors"][0][1])
        self.assertIn("profilo 2", seen["errors"][0][1])
        self.assertEqual(seen["writes"], [])
        self.assertEqual(seen["slot0_middle"], 0xA6)
        self.assertEqual(seen["slot1_middle"], 0xA6)
        self.assertEqual(seen["still_dirty"], [1])
        self.assertEqual(seen["selected"], 1)
        # after the long press: written to profile 2 only, read back OK
        self.assertTrue(seen["unlocked"])
        self.assertEqual(seen["writes_ok"], [(0x50, 1, 1, 0x7F)])
        self.assertEqual(seen["slot1_middle_after"], 0x7F)
        self.assertEqual(seen["slot0_middle_after"], 0xA6)
        self.assertEqual(seen["dirty_after"], [])
        self.assertNotIn(0x45, seen["sent"])


    def test_make_active_then_apply(self):
        """«Rendi attivo» on profile 2: rows unlock, Applica sends 0x45 first, then 0x50."""
        from gi.repository import GLib

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen: dict = {}
        errors: list = []

        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "GTK_A11Y": "none"}), \
                mock.patch.object(gui.MainWindow, "is_active", lambda self: True), \
                mock.patch.object(gui.MainWindow, "error_dialog", lambda self, h, b: errors.append((h, b))):
            application = gui.LiftEliteApp(gui.parse_args(["--mock", "--lang", "it"]))
            steps: list = []

            def start():
                win = application.window
                if win is None or not win.model.loaded:
                    if time.monotonic() - seen.setdefault("t0", time.monotonic()) > 10:
                        application.quit()
                        return False
                    return True
                emu = win.session._fake.emulator

                def s_edit():
                    win.slot_buttons[1].set_active(True)  # edit profile 2; 1 active
                    seen["btn_visible"] = win.activate_btn.get_visible()
                    seen["btn_label"] = win.activate_btn.get_label()
                    seen["locked_before"] = win.buttons_locked()
                    seen["apply_before"] = win.apply_btn.get_sensitive()
                    win.activate_btn.set_active(True)  # «Rendi attivo»
                    seen["apply_after"] = win.apply_btn.get_sensitive()
                    seen["locked_after"] = win.buttons_locked()
                    seen["rows"] = {r.get_sensitive() for r in win.button_rows.values()}
                    seen["label"] = win.active_label.get_label()
                    seen["hint"] = win.slot_hint.get_label()
                    win.model.set_button("middle", 0x7F)
                    win.populate()
                    emu.log.clear()
                    win.apply()

                def s_check():
                    if win.spinner.get_spinning() or win._poll_inflight:
                        steps.insert(0, s_check)
                        return
                    seen["errors"] = list(errors)
                    seen["order"] = [w[4] for w in emu.writes]
                    seen["active"] = emu.active_profile
                    seen["slot0_middle"] = emu.buttons[(0, 1)]
                    seen["slot1_middle"] = emu.buttons[(1, 1)]
                    seen["pending"] = win.model.has_pending_apply
                    seen["btn_visible_after"] = win.activate_btn.get_visible()
                    seen["label_after"] = win.active_label.get_label()
                    win._force_close = True
                    application.quit()

                steps.extend([s_edit, s_check])

                def tick():
                    if steps:
                        steps.pop(0)()
                        return True
                    return False

                GLib.timeout_add(120, tick)
                return False

            GLib.timeout_add(100, start)
            application.run(["lift-elite-control"])

        self.assertTrue(seen["btn_visible"])
        self.assertEqual(seen["btn_label"], "Rendi attivo")
        self.assertTrue(seen["locked_before"])
        self.assertFalse(seen["apply_before"])
        self.assertTrue(seen["apply_after"])  # activation alone is something to apply
        self.assertFalse(seen["locked_after"])
        self.assertEqual(seen["rows"], {True})
        self.assertIn("profilo 1 → profilo 2 con Applica", seen["label"])
        self.assertIn("Rendi attivo", seen["hint"])
        self.assertEqual(seen["errors"], [])
        self.assertEqual(seen["order"][0], 0x45)  # activate first
        self.assertIn(0x50, seen["order"])
        self.assertEqual(seen["active"], 1)
        self.assertEqual(seen["slot1_middle"], 0x7F)
        self.assertEqual(seen["slot0_middle"], 0xA6)
        self.assertFalse(seen["pending"])
        self.assertFalse(seen["btn_visible_after"])
        self.assertIn("Attivo sul mouse: profilo 2", seen["label_after"])
        self.assertNotIn("→", seen["label_after"])


if __name__ == "__main__":
    unittest.main()
