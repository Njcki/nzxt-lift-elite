"""GUI battery indicator: Cairo drawing (headless), texts, live poll, widget + window."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

from nzxt_lift_elite.gui import i18n
from nzxt_lift_elite.gui.battery import (
    AMBER,
    GREEN,
    LOW_LEVEL,
    RED,
    WARN_LEVEL,
    battery_geometry,
    draw_battery,
    fill_width,
    level_class,
    level_color,
)
from nzxt_lift_elite.gui.model import (
    CHARGE_LABELS,
    EditorModel,
    Session,
    battery_subtitle,
    battery_tooltip,
)
from nzxt_lift_elite.i18n_it import IT
from nzxt_lift_elite.protocol import (
    CHARGE_CHARGING,
    CHARGE_DISCHARGING,
    CHARGE_FULL,
    CMD_STATUS,
    build_status_reply,
    parse_battery_reply,
)

try:
    import cairo

    _HAVE_CAIRO = hasattr(cairo, "ImageSurface")
except ImportError:  # pragma: no cover
    _HAVE_CAIRO = False

LEVELS = (0, 5, 15, 50, 100)
SCALE = 4
W, H = 40, 20


def _render(level, *, charging=False, plugged=False, dark=False, glow=0.0, fg=(0.1, 0.1, 0.1)):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W * SCALE, H * SCALE)
    cr = cairo.Context(surf)
    cr.scale(SCALE, SCALE)
    draw_battery(cr, W, H, level=level, charging=charging, plugged=plugged, fg=fg, dark=dark, glow=glow)
    surf.flush()
    data = bytes(surf.get_data())
    stride = surf.get_stride()

    def px(x, y):  # logical coordinates -> (r, g, b, a) 0..255 (premultiplied)
        o = int(y * SCALE) * stride + int(x * SCALE) * 4
        b, g, r, a = data[o : o + 4]
        return r, g, b, a

    return px


def _close(rgb, ref, tol=60):
    return all(abs(a - b * 255) <= tol for a, b in zip(rgb, ref))


@unittest.skipUnless(_HAVE_CAIRO, "pycairo not available")
class DrawingTest(unittest.TestCase):
    def test_fill_is_exactly_proportional(self):
        g = battery_geometry(W, H)
        for lv in LEVELS:
            self.assertAlmostEqual(fill_width(lv, g["iw"]), g["iw"] * lv / 100)
        self.assertEqual(fill_width(None, g["iw"]), 0)
        self.assertAlmostEqual(fill_width(150, g["iw"]), g["iw"])

    def _coloured_columns(self, px, g, ref):
        # sample the lower part of the well (below the gloss), column by column
        y = g["iy"] + g["ih"] * 0.75
        n = 0
        x = g["ix"] + 0.5 / SCALE
        step = 1 / SCALE
        while x < g["ix"] + g["iw"]:
            r, gg, b, a = px(x, y)
            if a > 200 and _close((r, gg, b), ref, 70):
                n += 1
            x += step
        return n / SCALE

    def test_drawn_fill_matches_level(self):
        g = battery_geometry(W, H)
        for dark in (False, True):
            for lv in LEVELS:
                for charging in (False, True):
                    with self.subTest(level=lv, charging=charging, dark=dark):
                        px = _render(lv, charging=charging, dark=dark, glow=0.0 if not charging else 1.0)
                        ref = level_color(lv, dark)
                        width = self._coloured_columns(px, g, ref)
                        expected = fill_width(lv, g["iw"])
                        if charging and lv >= 50:
                            # the bolt covers part of the fill
                            self.assertLessEqual(width, expected + 1.0)
                            self.assertGreater(width, expected * 0.6)
                        else:
                            self.assertAlmostEqual(width, expected, delta=1.0)

    def test_bolt_only_when_charging_or_plugged(self):
        g = battery_geometry(W, H)
        cx, cy = g["bx"] + g["bw"] / 2, g["by"] + g["bh"] / 2
        for lv in (80, 100):  # bolt centre over the fill (at 50 % it sits on the fill edge)
            plain = _render(lv)(cx, cy + 0.5)
            charging = _render(lv, charging=True, glow=1.0)(cx, cy + 0.5)
            full = _render(lv, plugged=True)(cx, cy + 0.5)
            self.assertNotEqual(plain[:3], charging[:3])
            self.assertGreater(min(charging[:3]), 230)  # white bolt over the fill
            self.assertGreater(min(full[:3]), 230)

    def test_bolt_over_empty_well_uses_foreground(self):
        g = battery_geometry(W, H)
        cx, cy = g["bx"] + g["bw"] / 2, g["by"] + g["bh"] / 2
        r, gg, b, a = _render(5, charging=True, fg=(0.1, 0.1, 0.1))(cx, cy + 0.5)
        self.assertLess(max(r, gg, b), 90)  # dark bolt on a light theme

    def test_glow_changes_pixels(self):
        g = battery_geometry(W, H)
        x, y = g["bx"] + g["bw"] * 0.70, g["by"] + g["bh"] / 2
        off = _render(15, charging=True, glow=0.0)(x, y)
        on = _render(15, charging=True, glow=1.0)(x, y)
        self.assertNotEqual(off, on)

    def test_unknown_has_no_fill_and_no_bolt(self):
        g = battery_geometry(W, H)
        px = _render(None, charging=True)
        cx, cy = g["bx"] + g["bw"] / 2, g["by"] + g["bh"] / 2
        r, gg, b, a = px(cx, cy)
        self.assertLess(a, 40)  # only the faint well

    def test_low_level_tints_the_well(self):
        g = battery_geometry(W, H)
        x, y = g["ix"] + g["iw"] * 0.7, g["iy"] + g["ih"] * 0.75
        low = _render(5)(x, y)
        ok = _render(50)(x, y)
        self.assertGreater(low[0], low[1])  # reddish
        self.assertNotEqual(low, ok)

    def test_colours(self):
        for dark in (False, True):
            i = 1 if dark else 0
            self.assertEqual(level_color(100, dark), GREEN[i])
            self.assertEqual(level_color(50, dark), GREEN[i])
            self.assertEqual(level_color(15, dark), AMBER[i])
            self.assertEqual(level_color(5, dark), RED[i])
            self.assertEqual(level_color(0, dark), RED[i])
            # blends at the thresholds instead of jumping
            mid = level_color(WARN_LEVEL, dark)
            self.assertNotIn(mid, (GREEN[i], AMBER[i]))
            mid = level_color(LOW_LEVEL, dark)
            self.assertNotIn(mid, (AMBER[i], RED[i]))

    def test_label_class(self):
        self.assertIsNone(level_class(50, False))
        self.assertEqual(level_class(15, False), "battery-warn")
        self.assertEqual(level_class(5, False), "battery-low")
        self.assertIsNone(level_class(5, True))  # charging: no warning colour
        self.assertIsNone(level_class(None, False))


class TextTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("it")

    def test_subtitle_and_tooltip(self):
        b = parse_battery_reply(build_status_reply(percent=72, millivolts=4044, charge_state=CHARGE_CHARGING,
                                                   temperature_c=27))
        i18n.set_language("en")
        self.assertEqual(battery_subtitle(b), "72 % · 4.04 V · Charging")
        self.assertEqual(battery_tooltip(b), "Battery: 72 %\nVoltage: 4.044 V\nCharging\nTemperature: 27 °C")
        i18n.set_language("it")
        self.assertEqual(battery_subtitle(b), "72 % · 4,04 V · In carica")
        self.assertIn("Tensione: 4,044 V", battery_tooltip(b))

    def test_states_and_unknown(self):
        i18n.set_language("en")
        full = parse_battery_reply(build_status_reply(charge_state=CHARGE_FULL))
        self.assertIn("Full (cable connected)", battery_subtitle(full))
        off = parse_battery_reply(build_status_reply(percent=40, millivolts=3760, charge_state=CHARGE_DISCHARGING))
        self.assertIn("On battery", battery_subtitle(off))
        self.assertEqual(battery_subtitle(None), "Not available")
        self.assertIn("not available", battery_tooltip(parse_battery_reply(build_status_reply(valid=False))))

    def test_charge_labels_translated(self):
        from nzxt_lift_elite.cli import CHARGE_LABELS as CLI_LABELS

        for text in list(CHARGE_LABELS.values()) + list(CLI_LABELS.values()) + ["Unknown"]:
            self.assertIn(text, IT)


class PollTest(unittest.TestCase):
    def setUp(self):
        self.session = Session(mock="wired")
        self.model = EditorModel()
        self.model.load_state(self.session.read_state())
        self.emu = self.session._fake.emulator

    def tearDown(self):
        self.session.close()

    def test_connect_reads_battery(self):
        self.assertEqual(self.model.battery.percent, 72)
        self.assertTrue(self.model.battery.charging)

    def test_poll_reads_battery_only_when_asked(self):
        self.emu.log.clear()
        res = self.session.poll_active_stage(0)
        self.assertFalse(res.battery_read)
        self.assertNotIn(CMD_STATUS, {p[4] for p in self.emu.log})
        self.emu.battery.percent = 71
        res = self.session.poll_active_stage(0, with_battery=True)
        self.assertTrue(res.battery_read)
        self.assertEqual(res.battery.percent, 71)
        self.assertEqual(self.emu.writes, [])
        self.assertEqual(self.emu.unsafe, [])

    def test_battery_failure_does_not_fail_the_poll(self):
        dev = self.session._dev
        with mock.patch.object(dev, "read_battery", side_effect=__import__(
                "nzxt_lift_elite.errors", fromlist=["ProtocolError"]).ProtocolError("asleep")):
            res = self.session.poll_active_stage(0, with_battery=True)
        self.assertTrue(res.battery_read)
        self.assertIsNone(res.battery)
        self.assertEqual(res.slot, 0)


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
class WidgetTest(unittest.TestCase):
    def setUp(self):
        from gi.repository import Adw

        Adw.init()

    def test_indicator_states(self):
        from nzxt_lift_elite.gui.battery import BatteryIndicator

        ind = BatteryIndicator()
        self.assertEqual(ind.label.get_label(), "—")
        self.assertTrue(ind.has_css_class("unknown"))
        b = parse_battery_reply(build_status_reply(percent=72, millivolts=4044, charge_state=CHARGE_CHARGING))
        ind.set_status(b)
        self.assertEqual(ind.label.get_label(), "72%")  # unmapped: no animation, value at once
        self.assertTrue(ind.has_css_class("charging"))
        self.assertFalse(ind.has_css_class("unknown"))
        self.assertTrue(ind.glyph.pulsing)
        ind.set_pulse_enabled(False)  # window lost focus: pulse stops
        self.assertFalse(ind.glyph.pulsing)
        low = parse_battery_reply(build_status_reply(percent=5, millivolts=3500, charge_state=CHARGE_DISCHARGING))
        ind.set_status(low)
        self.assertEqual(ind.label.get_label(), "5%")
        self.assertTrue(ind.label.has_css_class("battery-low"))
        self.assertFalse(ind.glyph.pulsing)
        ind.set_status(None)
        self.assertEqual(ind.label.get_label(), "—")
        self.assertIsNone(ind.glyph.level)

    def test_level_animation_eases_to_target(self):
        from nzxt_lift_elite.gui.battery import BatteryGlyph

        g = BatteryGlyph()
        frames = []
        g.on_level_frame = frames.append
        g.set_state(80, False, False, animate=False)
        g.get_mapped = lambda: True  # pretend it is on screen
        g.add_tick_callback = lambda cb: 1
        g.remove_tick_callback = lambda _id: None
        g.set_state(40, False, False)
        self.assertTrue(g._animating)
        g._t0 -= g.ANIM_SECONDS / 2
        g._on_tick(g, None)
        self.assertTrue(40 < g.level < 80)
        g._t0 -= g.ANIM_SECONDS
        g._on_tick(g, None)
        self.assertEqual(g.level, 40)
        self.assertFalse(g._animating)
        self.assertEqual(frames[-1], 40)


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class WindowTest(unittest.TestCase):
    def test_window_shows_battery(self):
        from gi.repository import GLib

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen = {}
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "LIFT_ELITE_MOCK_BATTERY": "15"}):
            application = gui.LiftEliteApp(gui.parse_args(["--mock", "--lang", "en"]))

            def check():
                win = application.window
                if win is None or not win.model.loaded:
                    if time.monotonic() - seen.setdefault("t0", time.monotonic()) > 10:
                        application.quit()
                        return False
                    return True
                seen["label"] = win.battery.label.get_label()
                seen["row"] = win.row_battery.get_subtitle()
                seen["tooltip"] = win.battery.get_tooltip_text()
                seen["warn"] = win.battery.label.has_css_class("battery-warn")
                seen["writes"] = len(win.session._fake.emulator.writes)
                win._force_close = True
                application.quit()
                return False

            GLib.timeout_add(100, check)
            application.run(["lift-elite-control"])
        i18n.set_language("it")
        self.assertEqual(seen.get("label"), "15%")
        self.assertTrue(seen["row"].startswith("15 % · 3.6"))
        self.assertIn("On battery", seen["tooltip"])
        self.assertTrue(seen["warn"])
        self.assertEqual(seen["writes"], 0)


if __name__ == "__main__":
    unittest.main()


# --- periodic refresh (focused / background) ---------------------------------------------

from nzxt_lift_elite.errors import ProtocolError  # noqa: E402
from nzxt_lift_elite.gui import model as gui_model  # noqa: E402


class RefreshModelTest(unittest.TestCase):
    def test_intervals_are_constants(self):
        self.assertEqual(gui_model.BATTERY_POLL_FOCUSED_S, 60)
        self.assertEqual(gui_model.BATTERY_POLL_BACKGROUND_S, 300)
        self.assertEqual(gui_model.BATTERY_REFRESH_ON_FOCUS_S, 60)
        self.assertEqual(gui_model.BATTERY_STALE_S, 15 * 60)
        self.assertGreater(gui_model.BATTERY_POLL_BACKGROUND_BACKOFF_S, gui_model.BATTERY_POLL_BACKGROUND_S)
        self.assertFalse(hasattr(gui_model, "BATTERY_POLL_WIRED_S"))  # one focused cadence for both links

    def test_failed_read_keeps_last_value_and_ages(self):
        m = EditorModel()
        b = parse_battery_reply(build_status_reply(percent=61, millivolts=3880, charge_state=CHARGE_DISCHARGING))
        self.assertTrue(m.set_battery(b, now=1000.0))
        self.assertFalse(m.set_battery(None, now=1300.0))
        self.assertEqual(m.battery.percent, 61)
        self.assertEqual(m.battery_age(now=1300.0), 300.0)
        self.assertFalse(m.battery_stale(now=1000.0 + 14 * 60))
        self.assertTrue(m.battery_stale(now=1000.0 + 16 * 60))
        self.assertIsNone(EditorModel().battery_age())

    def test_tooltip_last_reading(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")
        b = parse_battery_reply(build_status_reply(percent=61, millivolts=3880, charge_state=CHARGE_DISCHARGING))
        self.assertTrue(battery_tooltip(b, 20 * 60).endswith("Last reading 20 min ago"))
        self.assertNotIn("Last reading", battery_tooltip(b))


class BackgroundPollTest(unittest.TestCase):
    def setUp(self):
        self.session = Session(mock="wireless")
        self.session.read_state()
        self.emu = self.session._fake.emulator
        self.emu.log.clear()

    def tearDown(self):
        self.session.close()

    def test_background_read_is_b0_only(self):
        self.emu.battery.percent = 58
        b = self.session.poll_battery()
        self.assertEqual(b.percent, 58)
        self.assertEqual({p[4] for p in self.emu.log}, {CMD_STATUS})  # no 0x44 / 0x92 / writes
        self.assertEqual(self.emu.writes, [])

    def test_never_overlaps_an_apply(self):
        import threading

        held, release = threading.Event(), threading.Event()

        def busy():  # what Apply / reset do: hold the session I/O lock
            with self.session._io:
                held.set()
                release.wait(5)

        t = threading.Thread(target=busy)
        t.start()
        held.wait(5)
        try:
            self.assertIsNone(self.session.poll_battery())
            self.assertEqual(self.emu.log, [])  # the mouse was not touched
        finally:
            release.set()
            t.join()

    def test_asleep_raises_quietly_for_the_caller(self):
        self.emu.asleep = True
        with self.assertRaises(ProtocolError):
            self.session.poll_battery(timeout=0.05)

    def test_not_open_returns_none(self):
        self.session.close()
        self.assertIsNone(self.session.poll_battery())


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class WindowRefreshTest(unittest.TestCase):
    """Focus transitions and timers on the real window (mock mouse, no WM)."""

    def _run(self, body):
        from gi.repository import GLib

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = {}
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name, "LIFT_ELITE_MOCK_BATTERY": "64"}):
            application = gui.LiftEliteApp(gui.parse_args(["--mock-wireless", "--lang", "en"]))

            def check():
                win = application.window
                if win is None or not win.model.loaded:
                    if time.monotonic() - out.setdefault("t0", time.monotonic()) > 10:
                        out["timed_out"] = True
                        application.quit()
                        return False
                    return True
                try:
                    body(win, out)
                except Exception as exc:  # noqa: BLE001 - reported below
                    out["error"] = exc
                win._force_close = True
                application.quit()
                return False

            GLib.timeout_add(100, check)
            application.run(["lift-elite-control"])
        i18n.set_language("it")
        if "error" in out:
            raise out["error"]
        self.assertNotIn("timed_out", out, "window never loaded")
        return out

    @staticmethod
    def _wait(cond, timeout=5.0):
        from gi.repository import GLib

        ctx = GLib.MainContext.default()
        end = time.monotonic() + timeout
        while not cond() and time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.005)
        return cond()

    def test_unfocused_background_read_and_failure(self):
        def body(win, out):
            emu = win.session._fake.emulator
            win.is_active = lambda: False
            win._cancel_all_polls()
            delays = []
            real_timeout_add = __import__("gi.repository.GLib", fromlist=["GLib"]).timeout_add
            with mock.patch("nzxt_lift_elite.gui.app.GLib.timeout_add",
                            side_effect=lambda ms, fn: delays.append((ms, fn)) or real_timeout_add(10**7, fn)):
                win._battery_attempt = time.monotonic()
                win._schedule_poll()
            out["live_armed"] = bool(win._poll_source)
            out["bg_armed"] = bool(win._bg_battery_source)
            out["bg_delay_ms"] = [ms for ms, fn in delays if fn == win._bg_battery_tick]
            win._on_active_changed()
            out["pulse_unfocused"] = win.battery.glyph.pulse_enabled
            # the background tick itself: b0 only
            win._cancel_bg_battery()
            emu.log.clear()
            emu.battery.percent = 55
            win._bg_battery_tick()
            self._wait(lambda: not win._bg_battery_inflight)
            out["bg_cmds"] = {p[4] for p in emu.log}
            out["after_ok"] = (win.model.battery.percent, win.row_battery.get_subtitle())
            # asleep: value kept, no error, then dimmed once it is old
            win._cancel_bg_battery()
            emu.asleep = True
            win._bg_battery_tick()
            self._wait(lambda: not win._bg_battery_inflight, 10)
            out["after_fail"] = (win.model.battery.percent, win._bg_battery_failures)
            out["stale_fresh"] = win.battery.has_css_class("stale")
            win.model.battery_time = time.monotonic() - 20 * 60
            win._sync_battery()
            out["stale_old"] = win.battery.has_css_class("stale")
            out["tooltip_old"] = win.battery.get_tooltip_text()
            win._bg_battery_failures = 3
            out["backoff"] = win._bg_battery_interval()
            emu.asleep = False
            win._cancel_all_polls()

        out = self._run(body)
        self.assertFalse(out["live_armed"])  # no 0x44/0x92 poll while unfocused
        self.assertTrue(out["bg_armed"])
        self.assertTrue(out["bg_delay_ms"])
        self.assertAlmostEqual(out["bg_delay_ms"][0] / 1000, gui_model.BATTERY_POLL_BACKGROUND_S, delta=2)
        self.assertFalse(out["pulse_unfocused"])
        self.assertEqual(out["bg_cmds"], {CMD_STATUS})
        self.assertEqual(out["after_ok"][0], 55)
        self.assertTrue(out["after_ok"][1].startswith("55 %"))
        self.assertEqual(out["after_fail"], (55, 1))
        self.assertFalse(out["stale_fresh"])
        self.assertTrue(out["stale_old"])
        self.assertIn("Last reading 20 min ago", out["tooltip_old"])
        self.assertEqual(out["backoff"], gui_model.BATTERY_POLL_BACKGROUND_BACKOFF_S)

    def test_focus_return_and_focused_cadence(self):
        def body(win, out):
            win._cancel_all_polls()
            win.is_active = lambda: True
            # recent value: focus does not force a read
            win.model.battery_time = time.monotonic() - 10
            win._battery_attempt = time.monotonic() - 10
            win._on_active_changed()
            out["recent_attempt_kept"] = win._battery_attempt > 0
            out["bg_after_focus"] = bool(win._bg_battery_source)
            out["pulse_focused"] = win.battery.glyph.pulse_enabled
            win._cancel_all_polls()
            # old value (> 60 s): immediate refresh with b0 in the live poll
            win.model.battery_time = time.monotonic() - 120
            win._on_active_changed()
            out["forced_attempt"] = win._battery_attempt
            out["live_armed"] = bool(win._poll_source)
            win._cancel_all_polls()
            emu = win.session._fake.emulator
            emu.log.clear()
            emu.battery.percent = 47
            win._poll_tick()
            out["with_battery"] = win._poll_with_battery
            self._wait(lambda: not win._poll_inflight)
            out["cmds"] = {p[4] for p in emu.log}
            out["pct"] = win.model.battery.percent
            win._cancel_all_polls()
            # just read: the next live poll skips b0 until 60 s have passed
            emu.log.clear()
            win._poll_tick()
            out["with_battery_next"] = win._poll_with_battery
            self._wait(lambda: not win._poll_inflight)
            out["cmds_next"] = {p[4] for p in emu.log}
            win._cancel_all_polls()
            win._battery_attempt = time.monotonic() - gui_model.BATTERY_POLL_FOCUSED_S - 1
            win._poll_tick()
            out["with_battery_due"] = win._poll_with_battery
            self._wait(lambda: not win._poll_inflight)
            win._cancel_all_polls()

        out = self._run(body)
        self.assertTrue(out["recent_attempt_kept"])
        self.assertFalse(out["bg_after_focus"])
        self.assertTrue(out["pulse_focused"])
        self.assertEqual(out["forced_attempt"], 0.0)
        self.assertTrue(out["live_armed"])
        self.assertTrue(out["with_battery"])
        self.assertIn(CMD_STATUS, out["cmds"])
        self.assertEqual(out["pct"], 47)
        self.assertFalse(out["with_battery_next"])
        self.assertNotIn(CMD_STATUS, out["cmds_next"])
        self.assertTrue(out["with_battery_due"])
