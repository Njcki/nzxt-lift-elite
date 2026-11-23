"""Battery indicator: a hand-drawn battery glyph plus the percentage.

The drawing (:func:`draw_battery`) is plain Cairo so it can be unit-tested
without a display; :class:`BatteryIndicator` is the GTK widget that animates
it (smooth level changes, a soft pulsing glow on the charging bolt while the
window is focused) and shows the percent with tabular numerals.

Values come from the read-only ``b0`` status reply (fuel gauge state of
charge, see ``protocol.parse_battery_reply``).
"""

from __future__ import annotations

import math
import time

# --- colours -------------------------------------------------------------------------------

# (light theme, dark theme) RGB in 0..1, from the GNOME palette.
GREEN = ((0x26 / 255, 0xA2 / 255, 0x69 / 255), (0x33 / 255, 0xD1 / 255, 0x7A / 255))
AMBER = ((0xE6 / 255, 0x8A / 255, 0x00 / 255), (0xF8 / 255, 0xB4 / 255, 0x30 / 255))
RED = ((0xE0 / 255, 0x1B / 255, 0x24 / 255), (0xFF / 255, 0x6B / 255, 0x5F / 255))

# Level thresholds (percent). Between the *_FROM and *_TO values the colour
# blends smoothly, so an animated level change never "jumps" colour.
WARN_LEVEL = 20  # amber at or below
LOW_LEVEL = 10  # red at or below
_BLEND = 3.0


def _mix(a, b, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def level_color(level: float, dark: bool = False) -> tuple[float, float, float]:
    """Fill colour for ``level`` percent: green → amber (≤20 %) → red (≤10 %)."""
    i = 1 if dark else 0
    g, a, r = GREEN[i], AMBER[i], RED[i]
    if level >= WARN_LEVEL + _BLEND:
        return g
    if level > WARN_LEVEL - _BLEND:
        return _mix(a, g, (level - (WARN_LEVEL - _BLEND)) / (2 * _BLEND))
    if level >= LOW_LEVEL + _BLEND:
        return a
    if level > LOW_LEVEL - _BLEND:
        return _mix(r, a, (level - (LOW_LEVEL - _BLEND)) / (2 * _BLEND))
    return r


def level_class(level: float | None, charging: bool) -> str | None:
    """CSS class for the percent label (None = normal text colour)."""
    if level is None or charging:
        return None
    if level <= LOW_LEVEL:
        return "battery-low"
    if level <= WARN_LEVEL:
        return "battery-warn"
    return None


# --- geometry ------------------------------------------------------------------------------


def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# Lightning bolt in a 10 x 16 box.
_BOLT = ((6.4, 0.0), (0.6, 9.3), (4.7, 9.3), (3.5, 16.0), (9.4, 6.3), (5.3, 6.3), (6.4, 0.0))


def battery_geometry(w: float, h: float) -> dict[str, float]:
    """Body / well rectangles for a glyph of ``w`` x ``h`` (exposed for tests)."""
    nub_w = max(2.0, round(h * 0.15, 1))
    gap = max(1.0, h * 0.06)
    stroke = max(1.2, h * 0.085)
    bx, by = stroke / 2, stroke / 2
    bw, bh = w - nub_w - gap - stroke, h - stroke
    r_body = bh * 0.32
    pad = stroke / 2 + max(1.3, h * 0.085)
    ix, iy = bx + pad, by + pad
    iw, ih = bw - 2 * pad, bh - 2 * pad
    r_in = max(0.8, r_body - pad + stroke / 2)
    return dict(nub_w=nub_w, gap=gap, stroke=stroke, bx=bx, by=by, bw=bw, bh=bh, r_body=r_body,
                ix=ix, iy=iy, iw=iw, ih=ih, r_in=r_in)


def fill_width(level: float | None, inner_width: float) -> float:
    """Exactly proportional fill width for ``level`` percent."""
    if level is None:
        return 0.0
    return inner_width * max(0.0, min(100.0, level)) / 100.0


def draw_battery(cr, w: float, h: float, *, level: float | None, charging: bool = False,
                 plugged: bool = False, fg=(0.0, 0.0, 0.0), dark: bool = False, glow: float = 0.0) -> None:
    """Draw the glyph at (0, 0) in a ``w`` x ``h`` box.

    ``level`` None = unknown (dimmed outline, no fill). ``charging`` shows
    the bolt with a glow of strength ``glow`` (0..1, the widget pulses it);
    ``plugged`` (cable in, full) shows the bolt without glow.
    """
    g = battery_geometry(w, h)
    fr, fgc, fb = fg[:3]
    unknown = level is None
    outline_a = 0.30 if unknown else (0.70 if dark else 0.62)

    # track (empty well)
    low = not unknown and not (charging or plugged) and level <= LOW_LEVEL
    _rounded_rect(cr, g["ix"], g["iy"], g["iw"], g["ih"], g["r_in"])
    if low:  # nearly empty: tint the empty well so the warning reads at a glance
        cr.set_source_rgba(*RED[1 if dark else 0], 0.16 if dark else 0.12)
    else:
        cr.set_source_rgba(fr, fgc, fb, 0.05 if unknown else (0.11 if dark else 0.075))
    cr.fill()

    # fill: own rounded rect, clipped to the well (so a sliver stays smooth)
    fw = fill_width(level, g["iw"])
    if fw > 0.05:
        import cairo

        color = level_color(level, dark)
        cr.save()
        _rounded_rect(cr, g["ix"], g["iy"], g["iw"], g["ih"], g["r_in"])
        cr.clip()
        _rounded_rect(cr, g["ix"], g["iy"], fw, g["ih"], min(g["r_in"], fw / 2))
        grad = cairo.LinearGradient(0, g["iy"], 0, g["iy"] + g["ih"])
        top = _mix(color, (1, 1, 1), 0.30 if not dark else 0.22)
        bottom = _mix(color, (0, 0, 0), 0.14 if not dark else 0.18)
        grad.add_color_stop_rgb(0.0, *top)
        grad.add_color_stop_rgb(0.55, *color)
        grad.add_color_stop_rgb(1.0, *bottom)
        cr.set_source(grad)
        cr.fill_preserve()
        # glossy highlight on the upper half
        gloss = cairo.LinearGradient(0, g["iy"], 0, g["iy"] + g["ih"] * 0.55)
        gloss.add_color_stop_rgba(0.0, 1, 1, 1, 0.30)
        gloss.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
        cr.set_source(gloss)
        cr.fill()
        cr.restore()

    # body outline
    cr.set_line_width(g["stroke"])
    if unknown:
        cr.set_dash([g["stroke"] * 1.6, g["stroke"] * 1.4])
    _rounded_rect(cr, g["bx"], g["by"], g["bw"], g["bh"], g["r_body"])
    outline = _mix((fr, fgc, fb), RED[1 if dark else 0], 0.55) if low else (fr, fgc, fb)
    cr.set_source_rgba(*outline, outline_a)
    cr.stroke()
    cr.set_dash([])

    # terminal nub (rounded on the outer side only)
    nx = g["bx"] + g["bw"] + g["stroke"] / 2 + g["gap"]
    nh = g["bh"] * 0.40
    ny = g["by"] + (g["bh"] - nh) / 2
    nw = g["nub_w"]
    rr = min(nw, nh / 2) * 0.9
    cr.new_sub_path()
    cr.move_to(nx, ny)
    cr.line_to(nx + nw - rr, ny)
    cr.arc(nx + nw - rr, ny + rr, rr, -math.pi / 2, 0)
    cr.line_to(nx + nw, ny + nh - rr)
    cr.arc(nx + nw - rr, ny + nh - rr, rr, 0, math.pi / 2)
    cr.line_to(nx, ny + nh)
    cr.close_path()
    cr.set_source_rgba(*outline, outline_a * 0.9)
    cr.fill()

    if (charging or plugged) and not unknown:
        _draw_bolt(cr, g, fw, level_color(level, dark), glow if charging else 0.0, fg, dark)


def _bolt_path(cr, g: dict[str, float]) -> None:
    bh = g["bh"] * 0.84
    s = bh / 16.0
    bw = 10.0 * s
    cx = g["bx"] + g["bw"] / 2
    cy = g["by"] + g["bh"] / 2
    x0, y0 = cx - bw / 2, cy - bh / 2
    cr.new_path()
    for i, (px, py) in enumerate(_BOLT):
        (cr.move_to if i == 0 else cr.line_to)(x0 + px * s, y0 + py * s)
    cr.close_path()


def _draw_bolt(cr, g: dict[str, float], fw: float, color, glow: float, fg, dark: bool) -> None:
    """Two-tone bolt: white over the fill, foreground colour over the empty
    well, with a thin cut-out rim. ``glow`` (0..1) adds a soft inner halo
    (clipped to the well) that the widget pulses while charging."""
    import cairo

    cr.save()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if glow > 0:
        # soft elliptical halo (radial gradient), clipped to the well
        cr.save()
        _rounded_rect(cr, g["ix"], g["iy"], g["iw"], g["ih"], g["r_in"])
        cr.clip()
        halo = _mix(GREEN[1 if dark else 0], (1, 1, 1), 0.45 if dark else 0.25)
        cx, cy = g["bx"] + g["bw"] / 2, g["by"] + g["bh"] / 2
        rx = g["iw"] * 0.42
        ry = g["ih"] * 0.95
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        rad = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
        peak = (0.85 if dark else 0.70) * glow
        rad.add_color_stop_rgba(0.0, *halo, peak)
        rad.add_color_stop_rgba(0.45, *halo, peak * 0.55)
        rad.add_color_stop_rgba(1.0, *halo, 0.0)
        cr.set_source(rad)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.fill()
        cr.restore()
    split = g["ix"] + fw
    rim = max(0.9, g["stroke"] * 0.8)
    # part over the fill: white with a rim in a darker shade of the fill
    cr.save()
    cr.rectangle(0, 0, split, g["by"] * 2 + g["bh"])
    cr.clip()
    _bolt_path(cr, g)
    cr.set_line_width(rim)
    cr.set_source_rgba(*_mix(color, (0, 0, 0), 0.45), 0.55)
    cr.stroke_preserve()
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    cr.restore()
    # part over the empty well: foreground colour
    cr.save()
    cr.rectangle(split, 0, g["bx"] * 2 + g["bw"] + 1, g["by"] * 2 + g["bh"])
    cr.clip()
    _bolt_path(cr, g)
    cr.set_source_rgba(fg[0], fg[1], fg[2], 0.88 if dark else 0.80)
    cr.fill()
    cr.restore()
    cr.restore()


# --- widget ---------------------------------------------------------------------------------

try:  # the GTK part is optional so the drawing can be tested headless
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
except (ImportError, ValueError):  # pragma: no cover
    Gtk = None  # type: ignore[assignment]


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


if Gtk is not None:

    class BatteryGlyph(Gtk.DrawingArea):
        """The drawn battery; animates level changes and the charging glow."""

        ANIM_SECONDS = 0.75
        PULSE_PERIOD = 2.2  # seconds per glow breath
        PULSE_FRAME = 0.04  # min seconds between glow redraws

        def __init__(self, width: int = 40, height: int = 20) -> None:
            super().__init__(content_width=width, content_height=height, valign=Gtk.Align.CENTER)
            self.add_css_class("battery-glyph")
            self.level: float | None = None  # shown (animated) value
            self.target: float | None = None
            self.charging = False
            self.plugged = False
            self.pulse_enabled = True  # window focused
            self.glow = 1.0
            self._from = 0.0
            self._t0 = 0.0
            self._animating = False
            self._tick_id = 0
            self._last_pulse = 0.0
            self._drawn_level: float | None = None
            self.on_level_frame = None  # callback(shown_level) for the percent label
            self.set_draw_func(self._draw)

        # state
        def set_state(self, level: float | None, charging: bool, plugged: bool, *, animate: bool = True) -> None:
            self.charging, self.plugged = charging, plugged
            if level is None:
                self.level = self.target = None
                self._animating = False
            elif self.level is None or not animate or not self.get_mapped():
                self.level = self.target = float(level)
                self._animating = False
            elif float(level) != self.target:
                self._from, self.target = self.level, float(level)
                self._t0 = time.monotonic()
                self._animating = True
            if self.on_level_frame is not None:
                self.on_level_frame(self.level)
            self._update_tick()
            self.queue_draw()

        def set_pulse_enabled(self, on: bool) -> None:
            self.pulse_enabled = on
            if not on:
                self.glow = 0.75  # steady, gentle glow while unfocused
            self._update_tick()
            self.queue_draw()

        @property
        def pulsing(self) -> bool:
            return self.charging and self.pulse_enabled and self.level is not None

        def _update_tick(self) -> None:
            want = self._animating or self.pulsing
            if want and not self._tick_id:
                self._tick_id = self.add_tick_callback(self._on_tick)
            elif not want and self._tick_id:
                self.remove_tick_callback(self._tick_id)
                self._tick_id = 0

        def _on_tick(self, _widget, _clock) -> bool:
            now = time.monotonic()
            if self._animating and self.target is not None:
                t = (now - self._t0) / self.ANIM_SECONDS
                self.level = self._from + (self.target - self._from) * _ease_out_cubic(t)
                if t >= 1:
                    self.level = self.target
                    self._animating = False
                if self.on_level_frame is not None:
                    self.on_level_frame(self.level)
            redraw = self._animating or self.level != self._drawn_level
            if self.pulsing and now - self._last_pulse >= self.PULSE_FRAME:
                # The glow breathes slowly: ~25 redraws/s is smooth and keeps
                # the cost negligible (the level animation still runs per frame).
                self._last_pulse = now
                phase = (now % self.PULSE_PERIOD) / self.PULSE_PERIOD
                self.glow = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
                redraw = True
            if redraw:
                self._drawn_level = self.level
                self.queue_draw()
            if self._animating or self.pulsing:
                return True
            self._tick_id = 0
            return False

        def _draw(self, _area, cr, width: int, height: int) -> None:
            col = self.get_color() if hasattr(self, "get_color") else None
            fg = (col.red, col.green, col.blue) if col is not None else (0.2, 0.2, 0.2)
            dark = Adw.StyleManager.get_default().get_dark()
            draw_battery(cr, width, height, level=self.level, charging=self.charging, plugged=self.plugged,
                         fg=fg, dark=dark, glow=self.glow)

    class BatteryIndicator(Gtk.Box):
        """Capsule with the battery glyph and the percent (tabular numerals)."""

        def __init__(self) -> None:
            super().__init__(spacing=6, valign=Gtk.Align.CENTER)
            self.add_css_class("battery-chip")
            self.glyph = BatteryGlyph()
            self.label = Gtk.Label(label="—")
            self.label.add_css_class("battery-percent")
            self.append(self.glyph)
            self.append(self.label)
            self.glyph.on_level_frame = self._on_frame
            self.status = None
            self.add_css_class("unknown")

        def _on_frame(self, level: float | None) -> None:
            text = "—" if level is None else f"{round(level)}%"
            if self.label.get_label() != text:
                self.label.set_label(text)

        def set_status(self, status, *, animate: bool = True) -> None:
            """``status``: a protocol.BatteryStatus, or None for unknown."""
            self.status = status
            ok = status is not None and status.available and status.percent is not None
            level = float(status.percent) if ok else None
            charging = bool(ok and status.charging)
            plugged = bool(ok and status.external_power)
            for cls in ("battery-low", "battery-warn", "unknown", "charging"):
                self.label.remove_css_class(cls)
                self.remove_css_class(cls)
            self.remove_css_class("low")
            (self.remove_css_class if ok else self.add_css_class)("unknown")
            if charging:
                self.add_css_class("charging")
            cls = level_class(level, plugged)
            if cls:
                self.label.add_css_class(cls)
            if cls == "battery-low":
                self.add_css_class("low")
            self.glyph.set_state(level, charging, plugged and not charging, animate=animate)

        def set_pulse_enabled(self, on: bool) -> None:
            self.glyph.set_pulse_enabled(on)
