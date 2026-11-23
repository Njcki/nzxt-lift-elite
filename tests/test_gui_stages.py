"""DPI stage rows in the real GTK window (mock mouse; needs a display).

- slider geometry never depends on the label column (edited / unedited
  active stage, bold active title): same x and width in every row/state;
- a purple-button short press on the (emulated) mouse moves the radio in
  real time without dropping unsaved edits, using read-only commands only.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

from tests.test_gui_smoke import _gtk_available


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class GuiStagesTest(unittest.TestCase):
    def test_slider_geometry_and_live_stage(self):
        import gi

        gi.require_version("Graphene", "1.0")
        from gi.repository import GLib, Graphene

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen: dict = {}

        def geom(win):
            out = []
            for s in win.stage_scales:
                _ok, pt = s.compute_point(win, Graphene.Point().init(0, 0))
                out.append((round(pt.x), s.get_width()))
            return tuple(out)

        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "GTK_A11Y": "none"}), \
                mock.patch.object(gui.MainWindow, "is_active", lambda self: True):
            application = gui.LiftEliteApp(gui.parse_args(["--mock", "--lang", "it"]))
            steps: list = []

            def start():
                win = application.window
                if win is None or not win.model.loaded or win.stage_scales[0].get_width() <= 0:
                    if time.monotonic() - seen.setdefault("t0", time.monotonic()) > 10:
                        application.quit()
                        return False
                    return True
                emu = win.session._fake.emulator
                stage = win.model.current().stage
                orig = win.model.original().dpi[stage - 1]

                def s_unedited():
                    seen["unedited"] = geom(win)
                    # mouse preview: side by side at the default size, clicks still map
                    d = win.diagram
                    seen["layout"] = d.layout
                    seen["art"] = d.art.__name__
                    seen["hit_wheel"] = d._hit(*d.art.button_center("middle", d._xf))
                    seen["hit_purple"] = d._hit(*d.art.button_center("dpi", d._xf))
                    seen["narrow"] = d.layout_for_width(200)
                    seen["wide_want"] = min(d.art.side_content_height(1600), d.SIDE_MAX_HEIGHT)
                    win.stage_scales[stage - 1].set_value(gui.dpi_to_slider(orig + 350))  # like a drag

                def s_edited():
                    seen["diagram_h"] = win.diagram.get_height()  # after the idle resize
                    seen["edited"] = geom(win)
                    seen["subtitle"] = win.stage_subtitles[stage - 1].get_label()
                    win.stage_scales[stage - 1].set_value(gui.dpi_to_slider(orig))  # back to applied

                def s_back():
                    seen["back"] = geom(win)
                    win.model.set_dpi(1, 450)  # unsaved edit that must survive the press
                    win.populate()
                    emu.log.clear()
                    emu.press_dpi_button()  # stage 2 -> 3 on the mouse
                    win._cancel_poll()
                    win._poll_tick()

                def s_after_poll():
                    if win._poll_inflight:
                        steps.insert(0, s_after_poll)
                        return
                    seen["radio"] = [c.get_active() for c in win.stage_checks]
                    seen["draft_stage"] = win.model.current().stage
                    seen["base_stage"] = win.model.original().stage
                    seen["dpi1"] = win.model.current().dpi[0]
                    seen["sub3"] = win.stage_subtitles[2].get_label()
                    seen["cmds"] = {p[4] for p in emu.log}
                    seen["writes"] = list(emu.writes)
                    seen["after_press"] = geom(win)
                    # long press with an unsaved edit: marker moves, edited profile stays
                    emu.long_press_dpi_button()  # slot 0 -> 1
                    win._cancel_poll()
                    win._poll_tick()

                def s_after_long_dirty():
                    if win._poll_inflight:
                        steps.insert(0, s_after_long_dirty)
                        return
                    seen["dirty_marker"] = [b.has_css_class("onboard-active") for b in win.slot_buttons]
                    seen["dirty_label"] = win.active_label.get_label()
                    seen["dirty_selected"] = win.model.selected
                    seen["dirty_dpi1"] = win.model.current().dpi[0]
                    seen["hint"] = win.slot_hint.get_label()
                    # discard edits, then edit nothing and long-press again: follows
                    win.discard()
                    win.model.select(1)
                    win.populate()
                    emu.long_press_dpi_button()  # slot 1 -> 2
                    win._cancel_poll()
                    win._poll_tick()

                def s_after_long_clean():
                    if win._poll_inflight:
                        steps.insert(0, s_after_long_clean)
                        return
                    seen["clean_selected"] = win.model.selected
                    seen["clean_toggle"] = [b.get_active() for b in win.slot_buttons]
                    seen["clean_marker"] = [b.has_css_class("onboard-active") for b in win.slot_buttons]
                    seen["all_cmds"] = {p[4] for p in emu.log}
                    seen["writes2"] = list(emu.writes)
                    seen["liftoff_sub"] = win.liftoff_row.get_subtitle()
                    win._force_close = True
                    application.quit()

                steps.extend([s_unedited, s_edited, s_back, s_after_poll, s_after_long_dirty, s_after_long_clean])

                def tick():
                    if steps:
                        steps.pop(0)()
                        return True
                    return False

                GLib.timeout_add(150, tick)
                return False

            GLib.timeout_add(100, start)
            application.run(["lift-elite-control"])

        self.assertIn("unedited", seen)
        self.assertEqual(seen["layout"], "side")
        self.assertLessEqual(seen["diagram_h"], 282)
        self.assertEqual(seen["wide_want"], 280)
        self.assertEqual(seen["hit_wheel"], "middle")
        self.assertEqual(seen["hit_purple"], "dpi")
        self.assertEqual(seen["narrow"], "stacked")
        self.assertEqual(len(set(seen["unedited"])), 1, seen["unedited"])  # all rows aligned
        self.assertEqual(seen["unedited"], seen["edited"])
        self.assertEqual(seen["unedited"], seen["back"])
        self.assertEqual(seen["unedited"], seen["after_press"])
        self.assertIn("→", seen["subtitle"])
        self.assertEqual(seen["radio"], [False, False, True, False, False])
        self.assertEqual((seen["draft_stage"], seen["base_stage"]), (3, 3))
        self.assertEqual(seen["dpi1"], 450)  # unsaved edit kept
        self.assertTrue(seen["sub3"].startswith("attivo"))
        self.assertEqual(seen["cmds"], {0x44, 0x92})  # read-only poll
        self.assertEqual(seen["writes"], [])
        # long press with unsaved edits
        self.assertEqual(seen["dirty_marker"], [False, True, False, False, False])
        self.assertEqual(seen["dirty_label"], "Attivo sul mouse: profilo 2")
        self.assertEqual(seen["dirty_selected"], 0)
        self.assertEqual(seen["dirty_dpi1"], 450)
        self.assertIn("tasto viola", seen["hint"])
        # long press, no edits, editing the active profile: selection follows
        self.assertEqual(seen["clean_selected"], 2)
        self.assertEqual(seen["clean_toggle"], [False, False, True, False, False])
        self.assertEqual(seen["clean_marker"], [False, False, True, False, False])
        self.assertTrue(seen["all_cmds"] <= {0x44, 0x92})
        self.assertEqual(seen["writes2"], [])
        self.assertIn("sensore smette di tracciare", seen["liftoff_sub"])


if __name__ == "__main__":
    unittest.main()
