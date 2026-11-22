"""Original vector silhouette of the NZXT Lift Elite for the GUI preview.

The outline and proportions follow the real mouse (symmetrical shell,
wide blunt nose, sides almost parallel with a slight taper to a rounded
rear, two large side buttons on the left, scroll-wheel channel, status
LED just behind the main-button seam, purple multi-function button on
the underside behind the sensor). This is a new drawing, not a trace or
embed of a product photo or of NZXT's logo.
"""

from __future__ import annotations

import math

# Design box. Top view occupies the upper part; a smaller underside view
# sits below so the purple DPI / profile button can be shown where it
# really is and stay clickable.
W, H = 220.0, 372.0

# Top-view body (pixel units in the design box). Length/width ≈ 118.8/60.8.
CX = 118.0
Y0 = 10.0
LENGTH = 220.0
Y1 = Y0 + LENGTH  # 230

# Main-button seam, slightly further back in the centre.
SEAM_SIDE = Y0 + 92.0
SEAM_CENTER = Y0 + 100.0

# Scroll wheel.
WHEEL = (CX - 8.5, Y0 + 16.0, 17.0, 48.0)

# Status LED: small slot on top, just behind the button seam (A-4 in the manual).
LED = (CX - 11.0, SEAM_CENTER + 6.0, 22.0, 5.5)

# Side buttons (left only, right-handed). They sit in a recess and
# protrude slightly from the outline. Forward is the upper one.
FORWARD = (48.0, Y0 + 58.0, 16.0, 38.0, 6.0)
BACK = (49.0, Y0 + 104.0, 15.5, 38.0, 6.0)

# Underside inset (true bottom view, nose still up — left/right mirrored).
U_SCALE = 0.50
U_CX = CX
U_Y0 = 248.0
U_LEN = LENGTH * U_SCALE
U_Y1 = U_Y0 + U_LEN

# Purple multi-function button, immediately behind the sensor.
# In design space of the underside inset (already scaled).
PURPLE = (U_CX - 14.0, U_Y0 + U_LEN * 0.58, 28.0, 13.0, 5.0)

# Changed-indicator dots.
DOTS = {
    "left": (CX - 36, Y0 + 52),
    "right": (CX + 36, Y0 + 52),
    "middle": (CX, Y0 + 12),
    "forward": (42, Y0 + 72),
    "back": (42, Y0 + 120),
    "dpi": (PURPLE[0] + PURPLE[2] + 10, PURPLE[1] + PURPLE[3] / 2),
}

BUTTON_ORDER = ("middle", "dpi", "forward", "back", "left", "right")

PURPLE_RGB = (0.57, 0.25, 0.93)


def fit(width: float, height: float) -> tuple[float, float, float]:
    """Return (scale, origin_x, origin_y) mapping the design box into the widget."""
    scale = min(width / W, height / H) * 0.98
    return scale, (width - W * scale) / 2, (height - H * scale) / 2


def rounded_rect(cr, x, y, w, h, r) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# Where the sides give way to a smooth oval rump (no corners).
T_CAP = 0.82
# Cubic Bézier kappa for a quarter-ellipse (4/3 * tan(pi/8)).
KAPPA = 0.5522847498307936


def _half_widths(t: float) -> float:
    """Half-width of the shell at t in [0, 1] (nose → rear), in design pixels.

    Tuned to the official 60.8 mm width and the TechPowerUp caliper
    stations (front 63.1, grip 61.0, back 59.4 mm) at 220 px = 118.8 mm.
    Behind T_CAP the outline is a semi-ellipse, so the rump is a smooth
    rounded oval with no corners or point.
    """
    # scale: 220/118.8 ≈ 1.8515 px/mm
    s = LENGTH / 118.8
    t = max(0.0, min(1.0, t))
    if t >= T_CAP:
        rx = 29.7 * s  # ~59.4 mm rear width
        u = (t - T_CAP) / (1.0 - T_CAP)
        return rx * math.sqrt(max(0.0, 1.0 - u * u))
    # Smooth stations (t, half-width mm) up to the start of the rump
    stations = (
        (0.00, 18.0),
        (0.03, 26.5),
        (0.07, 30.4),
        (0.12, 31.55),
        (0.25, 31.4),
        (0.40, 30.8),
        (0.55, 30.5),
        (0.70, 30.2),
        (T_CAP, 29.7),
    )
    for i in range(len(stations) - 1):
        t0, w0 = stations[i]
        t1, w1 = stations[i + 1]
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0)
            return (w0 + (w1 - w0) * u) * s
    return 29.7 * s


def add_body_path(cr, *, cx: float = CX, y0: float = Y0, length: float = LENGTH, scale_x: float = 1.0) -> None:
    """Closed outline of the shell. ``scale_x`` of -1 mirrors for the underside."""
    steps = 40
    cr.new_path()
    # right edge, nose → start of the rump
    first = True
    for i in range(steps + 1):
        t = T_CAP * (i / steps)
        y = y0 + t * length
        x = cx + _half_widths(t) * scale_x
        if first:
            cr.move_to(x, y)
            first = False
        else:
            cr.line_to(x, y)
    # smooth oval rump: two cubic curves approximating a semi-ellipse
    rx = _half_widths(T_CAP) * scale_x
    ry = (1.0 - T_CAP) * length
    y_cap = y0 + T_CAP * length
    y_end = y0 + length
    cr.curve_to(cx + rx, y_cap + KAPPA * ry, cx + KAPPA * rx, y_end, cx, y_end)
    cr.curve_to(cx - KAPPA * rx, y_end, cx - rx, y_cap + KAPPA * ry, cx - rx, y_cap)
    # left edge, rump → nose
    for i in range(steps - 1, -1, -1):
        t = T_CAP * (i / steps)
        y = y0 + t * length
        cr.line_to(cx - _half_widths(t) * scale_x, y)
    cr.close_path()


def add_seam(cr) -> None:
    """Curved main-button / palm seam, centre a little further back."""
    cr.new_path()
    left = CX - _half_widths((SEAM_SIDE - Y0) / LENGTH) + 1.5
    right = CX + _half_widths((SEAM_SIDE - Y0) / LENGTH) - 1.5
    cr.move_to(left, SEAM_SIDE)
    cr.curve_to(CX - 30, SEAM_CENTER - 1, CX + 30, SEAM_CENTER - 1, right, SEAM_SIDE)


def _seam_y_at(x: float) -> float:
    """Approximate seam y at a given x (quadratic, centre further back)."""
    hw = _half_widths((SEAM_SIDE - Y0) / LENGTH) - 1.5
    if hw <= 0:
        return SEAM_CENTER
    u = max(-1.0, min(1.0, (x - CX) / hw))
    # u=0 → SEAM_CENTER, |u|=1 → SEAM_SIDE
    return SEAM_CENTER + (SEAM_SIDE - SEAM_CENTER) * (u * u)


def add_button_path(cr, name: str) -> None:
    cr.new_path()
    if name == "left":
        _add_main_button(cr, left=True)
    elif name == "right":
        _add_main_button(cr, left=False)
    elif name == "middle":
        x, y, w, h = WHEEL
        rounded_rect(cr, x, y, w, h, 8.0)
    elif name == "forward":
        x, y, w, h, r = FORWARD
        rounded_rect(cr, x, y, w, h, r)
    elif name == "back":
        x, y, w, h, r = BACK
        rounded_rect(cr, x, y, w, h, r)
    elif name == "dpi":
        x, y, w, h, r = PURPLE
        rounded_rect(cr, x, y, w, h, r)


def _add_main_button(cr, *, left: bool) -> None:
    """Left or right primary button, following the shell and the wheel cut-out."""
    wx, wy, ww, wh = WHEEL
    gap = 1.6  # split between the two buttons
    inner = CX - gap if left else CX + gap
    # Walk the outer edge from the nose down to the seam, then back
    # along the seam and up around the wheel.
    sign = -1 if left else 1
    steps = 28
    t_seam = (SEAM_SIDE - Y0) / LENGTH
    t_nose = 0.002
    cr.new_path()
    # nose, slightly off-centre so the two buttons don't overlap
    cr.move_to(inner, Y0 + 2.0)
    # along the front cap to the outer corner, then down the side
    for i in range(1, steps + 1):
        t = t_nose + (t_seam - t_nose) * (i / steps)
        y = Y0 + t * LENGTH
        hw = _half_widths(t)
        # stay 1 px inside the outline
        cr.line_to(CX + sign * (hw - 1.2), y)
    # along the seam toward the centre
    seam_x_outer = CX + sign * (_half_widths(t_seam) - 1.2)
    cr.line_to(seam_x_outer, _seam_y_at(seam_x_outer))
    # stop at the centre split, just outside the wheel
    mid_x = inner
    cr.line_to(mid_x, _seam_y_at(mid_x))
    # up the inner split to the bottom of the wheel
    wheel_bottom = wy + wh + 2.0
    cr.line_to(mid_x, wheel_bottom)
    # around the wheel cut-out (bottom → front)
    if left:
        cr.arc(CX, wy + wh / 2, ww / 2 + 2.2, math.pi / 2, -math.pi / 2)
    else:
        cr.arc_negative(CX, wy + wh / 2, ww / 2 + 2.2, math.pi / 2, 3 * math.pi / 2)
    # back to the nose along the split
    cr.line_to(inner, Y0 + 2.0)
    cr.close_path()


def add_grip_panel(cr, *, left: bool) -> None:
    """Side rubber panel, with the characteristic angled rear cut."""
    sign = -1 if left else 1
    t0, t1, t_slash = 0.18, 0.78, 0.62
    cr.new_path()
    # outer edge
    steps = 24
    first = True
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * (i / steps)
        y = Y0 + t * LENGTH
        hw = _half_widths(t) - 0.8
        x = CX + sign * hw
        if first:
            cr.move_to(x, y)
            first = False
        else:
            cr.line_to(x, y)
    # angled rear cut inward, then inner edge back up
    y_end = Y0 + t1 * LENGTH
    hw_end = _half_widths(t1) - 0.8
    inner = 14.0
    cr.line_to(CX + sign * (hw_end - inner), y_end)
    y_slash = Y0 + t_slash * LENGTH
    hw_slash = _half_widths(t_slash) - 0.8
    cr.line_to(CX + sign * (hw_slash - inner), y_slash)
    y_start = Y0 + t0 * LENGTH
    hw_start = _half_widths(t0) - 0.8
    cr.line_to(CX + sign * (hw_start - 11.0), y_start)
    cr.close_path()


def add_underside_body(cr) -> None:
    add_body_path(cr, cx=U_CX, y0=U_Y0, length=U_LEN, scale_x=U_SCALE)


def _u(t: float, ox: float = 0.0) -> tuple[float, float]:
    """Point on the underside: t in [0,1] nose→rear, ox in design-mm toward the right of the *bottom* view."""
    s = U_SCALE
    y = U_Y0 + t * U_LEN
    x = U_CX + ox * s * (LENGTH / 118.8)
    return x, y


def add_front_skate(cr) -> None:
    """Horseshoe PTFE skate around the USB-C opening."""
    # Outer of the front foot follows the nose, inner is a U cut-out.
    cr.new_path()
    steps = 18
    t_outer0, t_outer1 = 0.015, 0.20
    # right outer, nose to the arms
    first = True
    for i in range(steps + 1):
        t = t_outer0 + (t_outer1 - t_outer0) * (i / steps)
        hw = _half_widths(t) * U_SCALE * 0.92
        x, y = U_CX + hw, U_Y0 + t * U_LEN
        if first:
            cr.move_to(x, y)
            first = False
        else:
            cr.line_to(x, y)
    # across the back of the U, then left outer back to the nose
    cr.line_to(U_CX - _half_widths(t_outer1) * U_SCALE * 0.92, U_Y0 + t_outer1 * U_LEN)
    for i in range(steps, -1, -1):
        t = t_outer0 + (t_outer1 - t_outer0) * (i / steps)
        hw = _half_widths(t) * U_SCALE * 0.92
        cr.line_to(U_CX - hw, U_Y0 + t * U_LEN)
    cr.close_path()
    # inner hole (will be cut with fill rule, or drawn as a separate stroke)
    # Use a sub-path going the other way so even-odd fill punches a hole.
    t_in0, t_in1 = 0.055, 0.155
    cr.new_sub_path()
    cr.move_to(U_CX, U_Y0 + t_in0 * U_LEN)
    cr.line_to(U_CX + 16 * U_SCALE, U_Y0 + t_in0 * U_LEN + 4)
    cr.line_to(U_CX + 18 * U_SCALE, U_Y0 + t_in1 * U_LEN)
    cr.line_to(U_CX - 18 * U_SCALE, U_Y0 + t_in1 * U_LEN)
    cr.line_to(U_CX - 16 * U_SCALE, U_Y0 + t_in0 * U_LEN + 4)
    cr.close_path()


def add_rear_skate(cr) -> None:
    t0, t1 = 0.78, 0.98
    cr.new_path()
    steps = 28
    first = True
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * (i / steps)
        hw = _half_widths(t) * U_SCALE * 0.88
        x, y = U_CX + hw, U_Y0 + t * U_LEN
        if first:
            cr.move_to(x, y)
            first = False
        else:
            cr.line_to(x, y)
    for i in range(steps, -1, -1):
        t = t0 + (t1 - t0) * (i / steps)
        hw = _half_widths(t) * U_SCALE * 0.88
        cr.line_to(U_CX - hw, U_Y0 + t * U_LEN)
    cr.close_path()


def add_sensor(cr) -> None:
    # Optical sensor window, mid body, slightly ahead of the purple button.
    w, h = 16.0, 22.0
    x = U_CX - w / 2
    y = U_Y0 + U_LEN * 0.40
    rounded_rect(cr, x, y, w, h, 3.0)


def add_x_frame(cr) -> None:
    """Open underside beams (the characteristic X / skeleton)."""
    # Two thin beams from the front arms to the rear, crossing at the sensor.
    cr.new_path()
    # We'll stroke these as lines rather than a filled shape.

    def pt(t, ox_mm):
        return (U_CX + ox_mm * U_SCALE * (LENGTH / 118.8), U_Y0 + t * U_LEN)

    return [
        (pt(0.20, -18), pt(0.78, 8)),
        (pt(0.20, 18), pt(0.78, -8)),
    ]


def hit_test(px: float, py: float) -> str | None:
    """Hit-test a point in design coordinates. More specific regions first."""
    import cairo

    surf = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
    cr = cairo.Context(surf)
    for name in BUTTON_ORDER:
        add_button_path(cr, name)
        if cr.in_fill(px, py):
            return name
    return None


def widget_hit(x: float, y: float, xf: tuple[float, float, float]) -> str | None:
    scale, ox, oy = xf
    if scale <= 0:
        return None
    return hit_test((x - ox) / scale, (y - oy) / scale)


def draw(
    cr,
    width: float,
    height: float,
    *,
    led: tuple[int, int, int],
    selected: str | None,
    hover: str | None,
    changed: set[str],
    fg,
    accent,
    underside_label: str = "",
) -> tuple[float, float, float]:
    """Paint the preview and return the transform used (for hit-testing)."""
    import cairo

    scale, ox, oy = fit(width, height)
    cr.save()
    cr.translate(ox, oy)
    cr.scale(scale, scale)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    r, g, b = (c / 255 for c in led)
    fr, fg_, fb, fa = fg.red, fg.green, fg.blue, 1.0
    ar, ag, ab = accent.red, accent.green, accent.blue

    def source(rr, gg, bb, a):
        cr.set_source_rgba(rr, gg, bb, a)

    # Soft LED wash under the top-view palm, in the profile colour.
    cr.save()
    cr.translate(CX, LED[1] + 28)
    cr.scale(1.0, 0.55)
    glow = cairo.RadialGradient(0, 0, 2, 0, 0, 110)
    glow.add_color_stop_rgba(0, r, g, b, 0.42)
    glow.add_color_stop_rgba(0.5, r, g, b, 0.12)
    glow.add_color_stop_rgba(1, r, g, b, 0.0)
    cr.set_source(glow)
    cr.arc(0, 0, 110, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # --- top-view shell ---
    add_body_path(cr)
    source(fr, fg_, fb, 0.07)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.48)
    cr.set_line_width(2.2)
    cr.stroke()

    # grip panels (subtle)
    for left in (True, False):
        add_grip_panel(cr, left=left)
        source(fr, fg_, fb, 0.045)
        cr.fill_preserve()
        source(fr, fg_, fb, 0.16)
        cr.set_line_width(1.0)
        cr.stroke()
        # sparse stipple inside the panel
        cr.save()
        add_grip_panel(cr, left=left)
        cr.clip()
        source(fr, fg_, fb, 0.10)
        sign = -1 if left else 1
        y = Y0 + 0.20 * LENGTH
        while y < Y0 + 0.76 * LENGTH:
            t = (y - Y0) / LENGTH
            hw = _half_widths(t)
            x0 = CX + sign * (hw - 3)
            x1 = CX + sign * (hw - 13)
            x_lo, x_hi = (min(x0, x1), max(x0, x1))
            x = x_lo + (2 if int(y) % 2 == 0 else 4)
            while x < x_hi:
                cr.rectangle(x, y, 1.1, 1.1)
                x += 4.2
            y += 3.4
        cr.fill()
        cr.restore()

    # centre split (the part in front of the wheel)
    source(fr, fg_, fb, 0.28)
    cr.set_line_width(1.2)
    cr.move_to(CX, Y0 + 2)
    cr.line_to(CX, WHEEL[1] - 1)
    cr.stroke()

    # button seam
    add_seam(cr)
    source(fr, fg_, fb, 0.32)
    cr.set_line_width(1.2)
    cr.stroke()

    # clickable buttons
    for name in ("left", "right", "middle", "forward", "back"):
        add_button_path(cr, name)
        if name == selected:
            source(ar, ag, ab, 0.55)
        elif name == hover:
            source(ar, ag, ab, 0.28)
        else:
            source(fr, fg_, fb, 0.05)
        cr.fill_preserve()
        source(fr, fg_, fb, 0.80 if name == selected else 0.35)
        cr.set_line_width(1.4)
        cr.stroke()

    # wheel dimples
    wx, wy, ww, wh = WHEEL
    cr.save()
    add_button_path(cr, "middle")
    cr.clip()
    source(fr, fg_, fb, 0.22)
    for i in range(5):
        cy = wy + 8 + i * 8.2
        cr.arc(CX - 3.2, cy, 1.5, 0, 2 * math.pi)
        cr.arc(CX + 3.2, cy, 1.5, 0, 2 * math.pi)
        cr.fill()
    cr.restore()

    # status LED in the profile colour, plus a tight glow
    lx, ly, lw, lh = LED
    cr.save()
    cr.translate(lx + lw / 2, ly + lh / 2)
    led_glow = cairo.RadialGradient(0, 0, 0.5, 0, 0, 16)
    led_glow.add_color_stop_rgba(0, r, g, b, 0.85)
    led_glow.add_color_stop_rgba(1, r, g, b, 0.0)
    cr.set_source(led_glow)
    cr.arc(0, 0, 16, 0, 2 * math.pi)
    cr.fill()
    cr.restore()
    source(r, g, b, 1)
    rounded_rect(cr, lx, ly, lw, lh, 3)
    cr.fill()

    # --- underside inset ---
    if underside_label:
        cr.select_font_face("sans-serif")
        cr.set_font_size(11)
        source(fr, fg_, fb, 0.45)
        ext = cr.text_extents(underside_label)
        cr.move_to(U_CX - ext.width / 2, U_Y0 - 8)
        cr.show_text(underside_label)

    # faint connecting hint
    source(fr, fg_, fb, 0.12)
    cr.set_line_width(1.0)
    cr.move_to(CX, Y1 + 4)
    cr.line_to(U_CX, U_Y0 - (18 if underside_label else 6))
    cr.stroke()

    add_underside_body(cr)
    source(fr, fg_, fb, 0.05)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.38)
    cr.set_line_width(1.5)
    cr.stroke()

    # skates
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    add_front_skate(cr)
    source(fr, fg_, fb, 0.08)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.30)
    cr.set_line_width(1.0)
    cr.stroke()
    add_rear_skate(cr)
    source(fr, fg_, fb, 0.08)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.30)
    cr.set_line_width(1.0)
    cr.stroke()
    cr.set_fill_rule(cairo.FILL_RULE_WINDING)

    # X-frame beams
    source(fr, fg_, fb, 0.28)
    cr.set_line_width(3.2)
    for (x0, y0), (x1, y1) in add_x_frame(cr):
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        cr.stroke()

    # sensor window
    add_sensor(cr)
    source(fr, fg_, fb, 0.06)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.40)
    cr.set_line_width(1.1)
    cr.stroke()

    # USB-C hint at the nose of the underside
    source(fr, fg_, fb, 0.35)
    cr.set_line_width(1.1)
    rounded_rect(cr, U_CX - 6, U_Y0 + 6, 12, 4.5, 1.2)
    cr.stroke()

    # purple multi-function button
    add_button_path(cr, "dpi")
    if selected == "dpi":
        source(ar, ag, ab, 0.55)
    elif hover == "dpi":
        source(ar, ag, ab, 0.28)
    else:
        source(*PURPLE_RGB, 0.90)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.80 if selected == "dpi" else 0.45)
    cr.set_line_width(1.3)
    cr.stroke()

    # change dots
    for name in ("left", "right", "middle", "forward", "back", "dpi"):
        if name in changed:
            dx, dy = DOTS[name]
            source(ar, ag, ab, 1)
            cr.arc(dx, dy, 4.5, 0, 2 * math.pi)
            cr.fill()

    cr.restore()
    return scale, ox, oy
