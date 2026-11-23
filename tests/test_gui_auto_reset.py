"""GUI: «Applica subito» (debounced auto-apply) and «Ripristina tutto»."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_gui_smoke import _gtk_available


# Tests that drive the real X pointer with xdotool. Opt-in (they would move the
# user's mouse pointer during `makepkg`): LIFT_ELITE_POINTER_TESTS=1 on a
# throwaway X display (e.g. Xvfb).
POINTER_TESTS = bool(os.environ.get("LIFT_ELITE_POINTER_TESTS") == "1" and __import__("shutil").which("xdotool")
                     and os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"))
POINTER_SKIP = "pointer tests: set LIFT_ELITE_POINTER_TESTS=1 on a throwaway X display with xdotool"


def run_gui(test, settings: dict, script, lang: str = "it", timeout: float = 25.0) -> dict:
    """Start the GUI on the emulator and run ``script(win, emu, seen, steps)``.

    Each step is a callable run every 100 ms; returning True repeats it
    (a wait), anything else moves on."""
    from gi.repository import GLib

    from nzxt_lift_elite.gui import app as gui

    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    cfg = Path(tmp.name) / "nzxt-lift-elite"
    cfg.mkdir(parents=True)
    (cfg / "settings.json").write_text(json.dumps(settings))
    seen: dict = {"errors": [], "toasts": []}
    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "GTK_A11Y": "none"}), \
            mock.patch.object(gui.MainWindow, "is_active", lambda self: True), \
            mock.patch.object(gui.MainWindow, "error_dialog", lambda self, h, b: seen["errors"].append((h, b))), \
            mock.patch.object(gui.MainWindow, "toast", lambda self, t, timeout=4: seen["toasts"].append(t)):
        application = gui.LiftEliteApp(gui.parse_args(["--mock", "--lang", lang]))
        steps: list = []
        t0 = time.monotonic()

        def start():
            win = application.window
            if win is None or not win.model.loaded:
                if time.monotonic() - t0 > 10:
                    application.quit()
                    return False
                return True
            script(win, win.session._fake.emulator, seen, steps)

            def tick():
                if time.monotonic() - t0 > timeout:
                    seen["timeout"] = True
                    win._force_close = True
                    application.quit()
                    return False
                if not steps:
                    win._force_close = True
                    application.quit()
                    return False
                try:
                    again = steps[0]()
                except Exception as exc:  # noqa: BLE001 - report instead of hanging
                    seen["exception"] = repr(exc)
                    steps.clear()
                    return True
                if not again:
                    steps.pop(0)
                return True

            GLib.timeout_add(100, tick)
            return False

        GLib.timeout_add(100, start)
        try:
            application.run(["lift-elite-control"])
        finally:
            from nzxt_lift_elite import i18n

            i18n.set_language("it")
    test.assertNotIn("exception", seen)
    test.assertNotIn("timeout", seen)
    return seen


def idle(win) -> bool:
    """True while auto-apply has a pending timer or a write in flight."""
    return bool(win._auto_source or win._auto_inflight or win.spinner.get_spinning())


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class AutoApplyTest(unittest.TestCase):
    def test_auto_apply(self):
        def script(win, emu, seen, steps):
            backups: list = []
            real_backup = win.session._backup

            def spy(profiles, reason):
                if profiles:
                    backups.append(([p.index for p in profiles], reason))
                return real_backup(profiles, reason)

            win.session._backup = spy
            seen["backups"] = backups

            def s_edits():
                seen["apply_visible"] = win.apply_btn.get_visible()
                seen["discard_visible"] = win.discard_btn.get_visible()
                seen["status_visible"] = win.auto_status.get_visible()
                seen["menu_state"] = win.lookup_action("auto-apply").get_state().get_boolean()
                emu.log.clear()
                win.polling_row.set_selected(win.polling_row.get_selected())  # no-op
                win.model.set_polling(1000)
                win.populate()
                win.model.set_liftoff(2)
                win.populate()
                for v in (30, 31, 32, 33):  # a slider being dragged
                    win.stage_scales[1].set_value(v)
                seen["writes_during_debounce"] = len(emu.writes)
                seen["status_pending"] = win.auto_status.get_label()

            def s_wait1():
                return idle(win)

            def s_check1():
                seen["batch1"] = [(w[4], w[5]) for w in emu.writes]
                seen["dev1"] = win.session._dev.read_profile(0, with_buttons=False)
                seen["model_dpi2"] = win.model.current().dpi[1]
                seen["dirty1"] = win.model.dirty_slots()
                seen["status_done"] = win.auto_status.get_label()
                emu.log.clear()
                win.model.set_motion_sync(not win.model.current().motion_sync)
                win.populate()

            def s_check2():
                if idle(win):
                    return True
                seen["batch2"] = [(w[4], w[5]) for w in emu.writes]
                # a write that does not stick -> error, control back to the mouse value
                emu.ignore_writes = True
                seen["polling_before_err"] = win.model.original().polling_hz
                win.model.set_polling(500)
                win.populate()

            def s_check_err():
                if idle(win):
                    return True
                emu.ignore_writes = False
                seen["err_count"] = len(seen["errors"])
                seen["polling_after_err"] = win.model.current().polling_hz
                seen["polling_row_after_err"] = win.polling_row.get_selected()
                seen["dirty_after_err"] = win.model.dirty_slots()
                # profile 2 is not active: buttons locked until «Rendi attivo»
                win.slot_buttons[1].set_active(True)
                seen["locked"] = win.buttons_locked()
                emu.log.clear()
                win.activate_btn.set_active(True)

            def s_check_activate():
                if idle(win):
                    return True
                seen["active_after"] = emu.active_profile
                seen["activate_writes"] = [(w[4], w[5]) for w in emu.writes]
                seen["unlocked"] = not win.buttons_locked()
                emu.log.clear()
                win._on_action_chosen("middle", 0x7F)

            def s_check_button():
                if idle(win):
                    return True
                seen["button_writes"] = [(w[4], w[5], w[6], w[8]) for w in emu.writes]
                seen["slot1_middle"] = emu.buttons[(1, 1)]
                seen["slot0_middle"] = emu.buttons[(0, 1)]

            steps.extend([s_edits, s_wait1, s_check1, s_check2, s_check_err, s_check_activate, s_check_button])

        seen = run_gui(self, {"auto_apply": True}, script)
        from nzxt_lift_elite.gui.app import slider_to_dpi

        self.assertFalse(seen["apply_visible"])
        self.assertFalse(seen["discard_visible"])
        self.assertTrue(seen["status_visible"])
        self.assertTrue(seen["menu_state"])
        # debounced: nothing written right away, then ONE body write for slot 0
        self.assertEqual(seen["writes_during_debounce"], 0)
        self.assertEqual(seen["status_pending"], "Applicazione…")
        self.assertEqual(seen["batch1"], [(0x93, 0)])
        self.assertEqual(seen["dev1"].polling_hz, 1000)
        self.assertEqual(seen["dev1"].liftoff, 2)
        self.assertEqual(seen["dev1"].dpi[1], slider_to_dpi(33))
        self.assertEqual(seen["model_dpi2"], slider_to_dpi(33))
        self.assertEqual(seen["dirty1"], [])
        self.assertEqual(seen["status_done"], "✓ Applicato")
        self.assertEqual(seen["batch2"], [(0x93, 0)])
        # one backup per profile per session, not one per change
        self.assertEqual([b for b in seen["backups"] if b[0] == [0]], [([0], "auto")])
        # error: shown, control reverted to the mouse value
        self.assertEqual(seen["err_count"], 1)
        self.assertEqual(seen["errors"][0][0], "Scrittura non riuscita")
        self.assertIn("tornate ai valori del mouse", seen["errors"][0][1])
        self.assertEqual(seen["polling_after_err"], seen["polling_before_err"])
        self.assertEqual(seen["dirty_after_err"], [])
        # «Rendi attivo» in auto mode activates right away (0x45 only)
        self.assertTrue(seen["locked"])
        self.assertEqual(seen["active_after"], 1)
        self.assertEqual(seen["activate_writes"], [(0x45, 1), (0x93, 1)])  # body re-sent after 0x45
        self.assertTrue(seen["unlocked"])
        self.assertEqual(seen["button_writes"], [(0x50, 1, 1, 0x7F)])
        self.assertEqual(seen["slot1_middle"], 0x7F)
        self.assertEqual(seen["slot0_middle"], 0xA6)

    def test_slider_applies_on_release(self):
        """Auto mode: dragging a slider writes nothing until the button is released."""
        from gi.repository import Gdk

        def script(win, emu, seen, steps):
            n = {"w": 0}

            def s_press():
                emu.log.clear()
                scale = win.stage_scales[2]
                win.slider_pressed(3)
                for v in (40, 45, 50):
                    scale.set_value(v)

            def s_hold():  # ~1 s held: longer than the debounce
                n["w"] += 1
                return n["w"] < 10

            def s_release():
                seen["writes_held"] = len(emu.writes)
                seen["status_held"] = win.auto_status.get_label()
                win.slider_released(3)

            def s_after():
                if idle(win) or win._slider_held is not None:
                    return True
                seen["writes_after"] = [(w[4], w[5]) for w in emu.writes]
                seen["dpi3"] = win.session._dev.read_profile(0, with_buttons=False).dpi[2]
                # the GTK event hook maps press/release correctly
                class Ev:
                    def __init__(self, t): self.t = t
                    def get_event_type(self): return self.t
                win._on_scale_event(None, Ev(Gdk.EventType.TOUCH_BEGIN), 4)
                seen["held_touch"] = win._slider_held
                win._on_scale_event(None, Ev(Gdk.EventType.BUTTON_RELEASE), 4)
                seen["held_after"] = win._slider_held

            steps.extend([s_press, s_hold, s_release, s_after])

        seen = run_gui(self, {"auto_apply": True}, script)
        from nzxt_lift_elite.gui.app import slider_to_dpi

        self.assertEqual(seen["writes_held"], 0)
        self.assertEqual(seen["status_held"], "Applicazione…")
        self.assertEqual(seen["writes_after"], [(0x93, 0)])
        self.assertEqual(seen["dpi3"], slider_to_dpi(50))
        self.assertEqual(seen["held_touch"], 4)
        self.assertIsNone(seen["held_after"])

    @unittest.skipUnless(POINTER_TESTS, POINTER_SKIP)
    def test_real_mouse_drag(self):
        """Real X pointer events (xdotool): no write while the button is down, one on release."""
        import subprocess

        from gi.repository import Graphene

        def xdo(*args):
            return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=5).stdout

        def script(win, emu, seen, steps):
            n = {"w": 0}
            pos = {}

            def wait(k):
                def f():
                    n["w"] += 1
                    if n["w"] < k:
                        return True
                    n["w"] = 0
                return f

            def s_down():
                wids = xdo("search", "--name", "^Lift Elite Control$").split()
                if not wids:
                    seen["skip"] = "window not found"
                    steps.clear()
                    return
                pos["wid"] = wids[-1]
                scale = win.stage_scales[3]
                ok, pt = scale.compute_point(win, Graphene.Point().init(scale.get_width() * 0.3, scale.get_height() / 2))
                pos["x"], pos["y"] = int(pt.x), int(pt.y)
                pos["w"] = scale.get_width()
                emu.log.clear()
                xdo("mousemove", "--window", pos["wid"], str(pos["x"]), str(pos["y"]))
                xdo("mousedown", "1")

            def s_move():
                xdo("mousemove", "--window", pos["wid"], str(pos["x"] + int(pos["w"] * 0.2)), str(pos["y"]))

            def s_check_held():
                seen["held"] = win._slider_held
                seen["writes_held"] = len(emu.writes)
                seen["dirty_held"] = win.model.dirty_slots()
                xdo("mouseup", "1")

            def s_after():
                if idle(win) or win._slider_held is not None:
                    n["w"] += 1
                    return n["w"] < 40
                seen["writes_after"] = [(w[4], w[5]) for w in emu.writes]
                seen["dirty_after"] = win.model.dirty_slots()
                xdo("mousemove", "0", "0")  # park the pointer: no tooltip over the next test's window

            steps.extend([s_down, wait(3), s_move, wait(12), s_check_held, wait(2), s_after])

        seen = run_gui(self, {"auto_apply": True}, script)
        if "skip" in seen:
            self.skipTest(seen["skip"])
        self.assertIn(seen["held"], (3, 4, 5))  # a slider is held (CSD offsets may shift the row)
        self.assertEqual(seen["dirty_held"], [0])  # edited, held for > 1 s ...
        self.assertEqual(seen["writes_held"], 0)  # ... but nothing written yet
        self.assertEqual(seen["writes_after"], [(0x93, 0)])
        self.assertEqual(seen["dirty_after"], [])

    def test_manual_mode_default(self):
        def script(win, emu, seen, steps):
            def s():
                seen["apply_visible"] = win.apply_btn.get_visible()
                seen["status_visible"] = win.auto_status.get_visible()
                emu.log.clear()
                win.model.set_polling(1000)
                win.populate()

            def wait():
                seen["polls"] = seen.get("polls", 0) + 1
                return seen["polls"] < 8  # 800 ms: longer than the debounce

            def check():
                seen["writes"] = list(emu.writes)
                seen["dirty"] = win.model.dirty_slots()

            steps.extend([s, wait, check])

        seen = run_gui(self, {}, script)
        self.assertTrue(seen["apply_visible"])
        self.assertFalse(seen["status_visible"])
        self.assertEqual(seen["writes"], [])  # manual mode: nothing without Applica
        self.assertEqual(seen["dirty"], [0])


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class PreviewResizeTest(unittest.TestCase):
    def test_resize_and_persist(self):
        from nzxt_lift_elite.gui.app import MouseDiagram

        def script(win, emu, seen, steps):
            d = win.diagram

            def s_settle():
                seen["settle"] = seen.get("settle", 0) + 1
                return seen["settle"] < 30 and (d.layout != "side" or d.get_height() > d.max_height + 2)

            def s0():
                seen["start"] = (d.max_height, d.get_height(), d.layout, win.sidebar.get_width())
                g = _FakeDrag()
                win._on_grip_begin(g, 0, 0)
                win._on_grip_update(g, 0, 80)
                win._on_grip_end(g, 0, 120)  # 300 + 120 = 420 (max)

            def wait():
                seen["w"] = seen.get("w", 0) + 1
                return seen["w"] % 4 != 0

            def s1():
                seen["big"] = (d.max_height, d.get_height(), d.layout, win.sidebar.get_width(), d.get_width())
                from nzxt_lift_elite.settings import load_settings

                seen["saved_big"] = load_settings()["preview_height"]
                # hotspots follow the size: the scroll wheel centre still hits "middle"
                cx, cy = d.art.button_center("middle", d._xf)
                seen["hit_big"] = d._hit(cx, cy)
                win.set_preview_height(100, save=True)  # clamped to 180

            def s2():
                seen["small"] = (d.max_height, d.get_height(), d.layout)
                cx, cy = d.art.button_center("dpi", d._xf)
                seen["hit_small"] = d._hit(cx, cy)
                from nzxt_lift_elite.settings import load_settings

                seen["saved_small"] = load_settings()["preview_height"]

            steps.extend([s_settle, s0, wait, wait, wait, wait, s1, wait, wait, wait, wait, s2])

        seen = run_gui(self, {"preview_height": 300}, script)
        self.assertEqual(seen["start"][0], 300)  # restored from settings.json
        self.assertEqual(seen["start"][2], "side")
        self.assertLessEqual(seen["start"][1], 302)
        self.assertEqual(seen["big"][0], MouseDiagram.MAX_HEIGHT)
        self.assertEqual(seen["big"][2], "side")  # sidebar widened: still side by side
        self.assertGreaterEqual(seen["big"][1], 410)
        self.assertGreater(seen["big"][3], seen["start"][3])
        self.assertEqual(seen["saved_big"], 420)
        self.assertEqual(seen["hit_big"], "middle")
        self.assertEqual(seen["small"][0], MouseDiagram.MIN_HEIGHT)
        self.assertLessEqual(seen["small"][1], 182)
        self.assertEqual(seen["hit_small"], "dpi")
        self.assertEqual(seen["saved_small"], 180)

    @unittest.skipUnless(POINTER_TESTS, POINTER_SKIP)
    def test_real_grip_drag_smooth(self):
        """Real pointer drag on the grip: heights follow the pointer monotonically
        (no feedback oscillation), one resize per frame at most, the SVG is not
        re-rendered while dragging, settings saved once on release."""
        import subprocess

        from gi.repository import Graphene

        from nzxt_lift_elite.gui import svgdiagram

        def xdo(*args):
            return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=5).stdout

        def script(win, emu, seen, steps):
            d = win.diagram
            pos, frames, saves, renders = {}, [], [], []
            seen.update(frames=frames, saves=saves, renders=renders)
            n = {"w": 0}

            real_base = svgdiagram._base_surface

            def counting_base(*a, **k):
                renders.append(d.live_resize)
                return real_base(*a, **k)

            svgdiagram._base_surface = counting_base
            import nzxt_lift_elite.gui.app as appmod
            real_save = appmod.save_setting
            appmod.save_setting = lambda k, v: (saves.append((k, v)), real_save(k, v))
            seen["restore"] = lambda: (setattr(svgdiagram, "_base_surface", real_base),
                                       setattr(appmod, "save_setting", real_save))

            def settle():
                n["w"] += 1
                return n["w"] < 30 and (d.layout != "side" or d.get_height() > d.max_height + 2)

            def s_down():
                wids = xdo("search", "--name", "^Lift Elite Control$").split()
                if not wids:
                    seen["skip"] = "window not found"
                    steps.clear()
                    return
                pos["wid"] = wids[-1]
                g = win.preview_grip
                ok, pt = g.compute_point(win, Graphene.Point().init(g.get_width() / 2, g.get_height() / 2))
                sx, sy = win.get_surface_transform()  # client-side decorations: shadow margin
                pos["x"], pos["y"] = int(pt.x + sx), int(pt.y + sy)
                seen["start"] = d.max_height
                win.preview_grip.add_tick_callback(lambda *_a: (frames.append(
                    (seen.get("phase"), d.get_height(), d.max_height)), True)[1])
                xdo("mousemove", "--window", pos["wid"], str(pos["x"]), str(pos["y"]))
                xdo("mousedown", "1")
                seen["phase"] = "down"

            def mover(sign, count):
                state = {"i": 0}

                def f():
                    state["i"] += 1
                    pos["y"] += sign * 8
                    xdo("mousemove", "--window", pos["wid"], str(pos["x"]), str(pos["y"]))
                    return state["i"] < count
                return f

            def s_turn():
                seen["peak"] = d.max_height
                seen["phase"] = "up"

            def s_release():
                seen["phase"] = None
                xdo("mouseup", "1")

            def s_end():
                seen["end"] = (d.max_height, d.get_height(), d.live_resize)
                seen["restore"]()
                xdo("mousemove", "0", "0")  # park the pointer: no tooltip over the next test's window

            def wait():
                n["w"] += 1
                return n["w"] % 3 != 0

            steps.extend([settle, s_down, wait, mover(+1, 12), wait, s_turn, mover(-1, 20), wait,
                          s_release, wait, wait, s_end])

        seen = run_gui(self, {"preview_height": 280}, script)
        if "restore" in seen:
            seen["restore"]()
        if seen.get("skip"):
            self.skipTest(seen["skip"])
        self.assertNotIn("exception", seen)
        down = [h for ph, h, _m in seen["frames"] if ph == "down"]
        up = [h for ph, h, _m in seen["frames"] if ph == "up"]
        self.assertGreater(len(down), 3)
        self.assertEqual(down, sorted(down), down)  # growing drag: never shrinks
        self.assertEqual(up, sorted(up, reverse=True), up)  # shrinking drag: never grows back
        self.assertGreaterEqual(seen["peak"], seen["start"] + 80)  # follows the pointer (12 × 8 px)
        self.assertLessEqual(seen["end"][0], seen["peak"] - 140)
        self.assertFalse(seen["end"][2])
        self.assertNotIn(True, seen["renders"])  # no librsvg render while dragging
        self.assertEqual([k for k, _v in seen["saves"]], ["preview_height"])  # once, on release
        self.assertEqual(seen["saves"][0][1], seen["end"][0])

    def test_default_height(self):
        from nzxt_lift_elite.gui.app import MouseDiagram

        self.assertEqual(MouseDiagram.clamp_height(None), 280)
        self.assertEqual(MouseDiagram.clamp_height("junk"), 280)
        self.assertEqual(MouseDiagram.clamp_height(1000), 420)
        self.assertEqual(MouseDiagram.clamp_height(10), 180)


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class ResetAllGuiTest(unittest.TestCase):
    def test_reset_button_and_flow(self):
        from gi.repository import Adw

        def script(win, emu, seen, steps):
            emu.buttons[(2, 1)] = 0x7F
            emu.active_profile = 3
            win.model.onboard_active_profile = 3

            def s_open():
                header = win.refresh_btn.get_parent()
                seen["same_box"] = win.reset_btn.get_parent() is header
                kids = []
                child = header.get_first_child()
                while child is not None:
                    kids.append(child)
                    child = child.get_next_sibling()
                seen["order"] = kids.index(win.reset_btn) - kids.index(win.refresh_btn)
                seen["tooltip"] = win.reset_btn.get_tooltip_text()
                seen["icon"] = win.reset_btn.get_icon_name()
                win.model.set_polling(500)  # unapplied edit: dialog warns, reset drops it
                win.confirm_reset()
                dlg = win.reset_dialog
                seen["heading"] = dlg.get_heading()
                seen["body"] = dlg.get_body()
                seen["destructive"] = dlg.get_response_appearance("reset") == Adw.ResponseAppearance.DESTRUCTIVE
                seen["default"] = dlg.get_default_response()
                dlg.close()
                emu.log.clear()
                win.reset_all()

            def s_wait():
                return win.spinner.get_spinning() or not win.model.loaded

            def s_check():
                if win.spinner.get_spinning():
                    return True
                seen["buttons"] = {k: v for k, v in emu.buttons.items() if v != {0: 0xA5, 1: 0xA6, 2: 0xA7, 3: 0xA8, 4: 0xA9, 5: 0xAA}[k[1]]}
                seen["active"] = emu.active_profile
                seen["acts"] = [p[5] for p in emu.log if p[4] == 0x45]
                seen["dirty"] = win.model.dirty_slots()
                seen["leds"] = [win.model.device[i].color.hex() for i in range(5)]

            steps.extend([s_open, s_wait, s_check])

        seen = run_gui(self, {}, script)
        self.assertTrue(seen["same_box"])
        self.assertEqual(seen["order"], 1)  # right after «Rileggi il mouse»
        self.assertIn("Ripristina tutto", seen["tooltip"])
        self.assertEqual(seen["icon"], "edit-clear-all-symbolic")
        self.assertIn("Ripristinare tutto", seen["heading"])
        self.assertIn("400/800/1600/3200/6400", seen["body"])
        self.assertIn("tabella dei profili predefiniti del firmware", seen["body"])
        self.assertIn("andranno perse", seen["body"])
        self.assertTrue(seen["destructive"])
        self.assertEqual(seen["default"], "cancel")
        self.assertEqual(seen["buttons"], {})
        self.assertEqual(seen["active"], 3)
        self.assertEqual(seen["acts"], [2, 3])
        self.assertEqual(seen["dirty"], [])
        self.assertEqual(seen["leds"], ["1f00", "00f8", "e007", "40fd", "1ff8"])
        self.assertTrue(any("ripristinati" in t for t in seen["toasts"]))


class _FakeDrag:
    """Stand-in for the grip's Gtk.GestureDrag (start point in grip coordinates)."""

    def get_start_point(self):
        return True, 5.0, 5.0


def widget_texts(root) -> list[str]:
    """Every visible-ish string in a widget tree (labels, titles, tooltips...)."""
    out: list[str] = []
    stack = [root]
    while stack:
        w = stack.pop()
        if type(w).__name__ == "GtkTooltipWindow":
            # GTK's per-display tooltip popup gets parented to whichever window the
            # pointer is over and keeps the last text it showed, possibly from a
            # previous (Italian) test window. Not part of our UI tree.
            continue
        for getter in ("get_label", "get_title", "get_subtitle", "get_tooltip_text", "get_description",
                       "get_text", "get_placeholder_text", "get_heading", "get_body"):
            fn = getattr(w, getter, None)
            if fn is None:
                continue
            try:
                value = fn()
            except TypeError:
                continue
            if isinstance(value, str) and value.strip():
                out.append(value)
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def menu_texts(model) -> list[str]:
    from gi.repository import Gio, GLib

    out = []
    for i in range(model.get_n_items()):
        label = model.get_item_attribute_value(i, "label", GLib.VariantType.new("s"))
        if label is not None:
            out.append(label.get_string())
        for link in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
            sub = model.get_item_link(i, link)
            if sub is not None:
                out.extend(menu_texts(sub))
    return out


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class EnglishGuiTest(unittest.TestCase):
    def test_no_italian_in_english(self):
        from tests.test_i18n import ITALIAN

        def script(win, emu, seen, steps):
            texts: list[str] = []
            seen["texts"] = texts

            def grab(tag):
                texts.extend(f"{tag}: {t}" for t in widget_texts(win))

            def s_main():
                grab("main")
                texts.extend("menu: " + t for t in menu_texts(win._build_menu()))
                win.slot_buttons[1].set_active(True)  # non-active profile: lock hints
                grab("slot2")
                win.activate_btn.set_active(True)
                grab("activate")
                win.model.set_polling(8000)
                win.populate()
                grab("edited")
                win.confirm_reset()
                texts.extend(f"reset: {t}" for t in (win.reset_dialog.get_heading(), win.reset_dialog.get_body()))
                win.reset_dialog.close()
                win.lookup_action("auto-apply").change_state(__import__("gi").repository.GLib.Variant.new_boolean(True))
                grab("auto")
                win.lookup_action("auto-apply").change_state(__import__("gi").repository.GLib.Variant.new_boolean(False))
                win.discard()

            steps.extend([s_main])

        seen = run_gui(self, {}, script, lang="en")
        texts = seen["texts"] + seen["toasts"]
        self.assertGreater(len(texts), 80)
        leaks = [t for t in texts if ITALIAN.search(t)]
        self.assertEqual(leaks, [])
        joined = "\n".join(texts)
        for want in ("Make active", "Apply immediately", "Reset everything to factory defaults?", "Read the mouse again",
                     "Language", "Applying…", "activates it on the mouse right away"):
            self.assertIn(want, joined)


if __name__ == "__main__":
    unittest.main()
