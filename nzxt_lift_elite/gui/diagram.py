"""Original vector line drawing of the NZXT Lift Elite for the GUI preview.

Proportions (outline half-widths along the length, button seam, wheel,
LED slit, side buttons, grip panels, underside feet, X frame, sensor and
purple button) were measured from a reference picture of the real mouse
and turned into numbers below. Nothing from the picture is embedded: no
bitmap, no traced artwork and no NZXT logo, only these simple vectors.

Coordinates: design pixels, the top view is LENGTH long starting at Y0,
centred on CX. ``t`` is the position along the length (0 = nose, 1 =
rear) and widths are fractions of the length (``L``) unless noted.
"""

from __future__ import annotations

import math

# Design box. Top view occupies the upper part; a smaller underside view
# sits below (stacked) or the same size on the right (side by side).
W, H = 220.0, 372.0

CX = 118.0
Y0 = 10.0
LENGTH = 220.0
Y1 = Y0 + LENGTH  # 230
L = LENGTH


def _y(t: float) -> float:
    return Y0 + t * L


# Main-button / palm seam: front-most in the centre, curving back at the
# sides (measured t 0.447 centre, 0.487 at the edges).
SEAM_CENTER = _y(0.449)
SEAM_SIDE = _y(0.487)

# Central channel between the two main buttons (wheel + LED live in it).
CHANNEL_HALF = 0.043 * L

# Scroll wheel (textured), near the front of the channel.
WHEEL = (CX - 0.036 * L, _y(0.109), 0.072 * L, 0.173 * L)

# DPI / status LED: thin horizontal slit at the bottom of the channel,
# right on the seam.
LED = (CX - 0.042 * L, SEAM_CENTER - 4.6, 0.084 * L, 3.0)

# Underside inset (true bottom view, nose up; left/right mirrored).
U_SCALE = 0.50
U_CX = CX
U_Y0 = 248.0
U_LEN = LENGTH * U_SCALE
U_Y1 = U_Y0 + U_LEN

# Purple multi-function button, just behind the sensor (underside coords).
PURPLE = (U_CX - 0.086 * U_LEN, U_Y0 + 0.610 * U_LEN, 0.172 * U_LEN, 0.066 * U_LEN, 2.0)

BUTTON_ORDER = ("middle", "dpi", "forward", "back", "left", "right")

PURPLE_RGB = (0.57, 0.25, 0.93)

# --- outline ------------------------------------------------------------

# Rounded nose cap (0 .. T_NOSE): angle-sampled superellipse quarter, so the
# front is a smooth broad curve (no point, no bulge above the buttons).
T_NOSE = 0.10
NOSE_HALF = 0.2472  # × L at t = T_NOSE
NOSE_EXP = 2.0
# Behind T_CAP the outline is a semi-ellipse: the wide, rounded rump.
T_CAP = 0.68
REAR_HALF = 0.2725
# Half-widths measured on the reference (right side), t → half-width / L.
# Slightly wider at the back than at the front, gentle waist at ~0.45.
_STATIONS = (
    (T_NOSE, NOSE_HALF),
    (0.13, 0.2559),
    (0.16, 0.2606),
    (0.20, 0.2650),
    (0.25, 0.2674),
    (0.30, 0.2678),
    (0.35, 0.2660),
    (0.40, 0.2632),
    (0.45, 0.2612),
    (0.50, 0.2620),
    (0.55, 0.2652),
    (0.60, 0.2695),
    (0.65, 0.2720),
    (T_CAP, REAR_HALF),
)
# Cubic Bézier kappa for a quarter-ellipse (4/3 * tan(pi/8)).
KAPPA = 0.5522847498307936


def _catmull(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
    return 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * u ** 3)


def _half_widths(t: float) -> float:
    """Half-width of the shell at t in [0, 1] (nose → rear), in design pixels."""
    t = max(0.0, min(1.0, t))
    if t >= T_CAP:
        u = (t - T_CAP) / (1.0 - T_CAP)
        return REAR_HALF * L * math.sqrt(max(0.0, 1.0 - u * u))
    if t < T_NOSE:
        u = (T_NOSE - t) / T_NOSE
        return NOSE_HALF * L * max(0.0, 1.0 - u ** NOSE_EXP) ** (1.0 / NOSE_EXP)
    st = _STATIONS
    for i in range(len(st) - 1):
        t0, w0 = st[i]
        t1, w1 = st[i + 1]
        if t0 <= t <= t1:
            wa = st[i - 1][1] if i > 0 else w0
            wb = st[i + 2][1] if i + 2 < len(st) else w1
            return _catmull(wa, w0, w1, wb, (t - t0) / (t1 - t0)) * L
    return REAR_HALF * L


def outline_samples(t_end: float, n_side: int = 60, n_nose: int = 48) -> list[tuple[float, float]]:
    """(t, half-width) points from the tip to ``t_end``: angle-spaced on the
    rounded nose (smooth tip), then even along the sides."""
    a = NOSE_HALF * L
    pts: list[tuple[float, float]] = []
    for i in range(n_nose + 1):
        th = (math.pi / 2) * i / n_nose
        hw = a * math.sin(th) ** (2.0 / NOSE_EXP)
        t = T_NOSE * (1.0 - math.cos(th) ** (2.0 / NOSE_EXP))
        if t > t_end:
            break
        pts.append((t, hw))
    if t_end > T_NOSE:
        for i in range(1, n_side + 1):
            t = T_NOSE + (t_end - T_NOSE) * i / n_side
            pts.append((t, _half_widths(t)))
    return pts


def add_body_path(cr, *, cx: float = CX, y0: float = Y0, length: float = LENGTH, scale_x: float = 1.0) -> None:
    """Closed outline of the shell (``scale_x`` scales it for the underside inset)."""
    pts = outline_samples(T_CAP)
    k = length / LENGTH
    cr.new_path()
    cr.move_to(cx, y0)
    for t, hw in pts[1:]:
        cr.line_to(cx + hw * scale_x, y0 + t * length)
    rx = REAR_HALF * L * scale_x
    ry = (1.0 - T_CAP) * length
    y_cap = y0 + T_CAP * length
    y_end = y0 + length
    cr.curve_to(cx + rx, y_cap + KAPPA * ry, cx + KAPPA * rx, y_end, cx, y_end)
    cr.curve_to(cx - KAPPA * rx, y_end, cx - rx, y_cap + KAPPA * ry, cx - rx, y_cap)
    for t, hw in reversed(pts[1:-1]):
        cr.line_to(cx - hw * scale_x, y0 + t * length)
    cr.close_path()
    del k


def _edge(t: float, left: bool, inset: float = 0.0) -> tuple[float, float]:
    """Point on the outline at t, moved ``inset`` px inward (negative = outward)."""
    sign = -1 if left else 1
    return CX + sign * (_half_widths(t) - inset), _y(t)


# --- side buttons (left side, right-handed) -------------------------------
# Forward: t 0.278..0.482, a narrow piece along the edge, slightly wider at
# the bottom. Back: t 0.494..0.705, wider and ending in a point toward the
# rear. Both stick out of the outline a little (they are on the side wall).
_FWD_T = (0.278, 0.482)
_BACK_T = (0.494, 0.705)
_SIDE_OUT = 1.4


def _fwd_inner(t: float) -> float:
    u = (t - _FWD_T[0]) / (_FWD_T[1] - _FWD_T[0])
    return 0.030 * L + 0.009 * L * u


def _back_inner(t: float) -> float:
    u = (t - _BACK_T[0]) / (_BACK_T[1] - _BACK_T[0])
    return 0.028 * L + 0.046 * L * min(1.0, u / 0.82)


def _side_path_points(name: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    n = 16
    if name == "forward":
        t0, t1 = _FWD_T
        for i in range(n + 1):
            t = t0 + (t1 - t0) * i / n
            pts.append(_edge(t, True, -_SIDE_OUT))
        for i in range(n, -1, -1):
            t = t0 + (t1 - t0) * i / n
            pts.append(_edge(t, True, _fwd_inner(t)))
    else:
        t0, t1 = _BACK_T
        t_tip = t0 + (t1 - t0) * 0.82
        for i in range(n + 1):
            t = t0 + (t1 - t0) * i / n
            pts.append(_edge(t, True, -_SIDE_OUT))
        # pointed rear end: from the edge at t1 diagonally to the inner corner
        for i in range(n, -1, -1):
            t = t0 + (t_tip - t0) * i / n
            pts.append(_edge(t, True, _back_inner(t)))
    return pts


def _bbox(pts) -> tuple[float, float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), 3.0)


FORWARD = _bbox(_side_path_points("forward"))
BACK = _bbox(_side_path_points("back"))


def _add_poly(cr, pts, *, close: bool = True, smooth: float = 0.0) -> None:
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    if close:
        cr.close_path()


# --- top-view details ---------------------------------------------------

def _seam_y_at(x: float) -> float:
    """Seam y at x: front-most in the centre, curving back at the edges."""
    hw = _half_widths((SEAM_SIDE - Y0) / L)
    u = max(-1.0, min(1.0, (x - CX) / hw))
    return SEAM_CENTER + (SEAM_SIDE - SEAM_CENTER) * (u * u) ** 1.15


def add_seam(cr) -> None:
    cr.new_path()
    hw = _half_widths((SEAM_SIDE - Y0) / L) - 0.8
    n = 40
    for i in range(n + 1):
        x = CX - hw + 2 * hw * i / n
        (cr.move_to if i == 0 else cr.line_to)(x, _seam_y_at(x))


def add_button_path(cr, name: str) -> None:
    cr.new_path()
    if name == "left":
        _add_main_button(cr, left=True)
    elif name == "right":
        _add_main_button(cr, left=False)
    elif name == "middle":
        x, y, w, h = WHEEL
        rounded_rect(cr, x, y, w, h, 5.5)
    elif name in ("forward", "back"):
        _add_poly(cr, _side_path_points(name))
    elif name == "dpi":
        _add_purple(cr)


def _add_main_button(cr, *, left: bool) -> None:
    """Left or right primary button: outline (inset) → seam → channel edge."""
    sign = -1 if left else 1
    inset = 1.6
    inner = CX + sign * (CHANNEL_HALF + 0.8)
    t_seam = (SEAM_SIDE - Y0) / L
    cr.new_path()
    first = True
    for t, hw_out in outline_samples(t_seam):
        hw = hw_out - inset
        if hw <= CHANNEL_HALF + 0.8:
            continue
        y = _y(t) + inset * (1.0 - min(1.0, t / T_NOSE))
        if first:
            cr.move_to(inner, y)
            first = False
        cr.line_to(CX + sign * hw, y)
    # along the seam to the channel (seam is a curve)
    x_out = CX + sign * (_half_widths(t_seam) - inset)
    n = 16
    for i in range(n + 1):
        x = x_out + (inner - x_out) * i / n
        cr.line_to(x, _seam_y_at(x) - 0.9)
    cr.close_path()


def rounded_rect(cr, x, y, w, h, r) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def grip_points(left: bool) -> list[tuple[float, float]]:
    """Dotted rubber grip on each side: a strip along the edge that widens
    to a point (angled inner edge) and tapers off toward the rear."""
    if left:
        t0, t_peak, t1, w_peak = 0.505, 0.700, 0.800, 0.072
    else:
        t0, t_peak, t1, w_peak = 0.492, 0.670, 0.772, 0.070
    n = 20
    pts = [_edge(t0 + (t1 - t0) * i / n, left, 0.6) for i in range(n + 1)]
    # inner edge, rear → front: short return to the peak, then a long diagonal
    tail = [(t1, 0.004), (t_peak, w_peak), (t0 + 0.035, 0.020), (t0, 0.006)]
    for t, w in tail:
        pts.append(_edge(t, left, w * L))
    return pts


# --- underside ----------------------------------------------------------

def _u(t: float, k: float) -> tuple[float, float]:
    """Underside point: t along the length, k = offset / L to the right."""
    return U_CX + k * U_LEN, U_Y0 + t * U_LEN


def _u_half(t: float) -> float:
    return _half_widths(t) / L  # same shell, as a fraction of L


def add_underside_body(cr) -> None:
    add_body_path(cr, cx=U_CX, y0=U_Y0, length=U_LEN, scale_x=U_SCALE)


def add_underside_rim(cr) -> None:
    """Inner contour of the base plate, a little inside the shell."""
    cr.new_path()
    ts = [0.050 + (0.962 - 0.050) * i / 60 for i in range(61)]
    for i, t in enumerate(ts):
        (cr.move_to if i == 0 else cr.line_to)(*_u(t, _u_half(t) - 0.030))
    for t in reversed(ts):
        cr.line_to(*_u(t, -(_u_half(t) - 0.030)))
    cr.close_path()


def add_front_skate(cr) -> None:
    """Small front PTFE foot at the nose."""
    x0, y0 = _u(0.030, -0.062)
    x1, y1 = _u(0.085, 0.062)
    rounded_rect(cr, x0, y0, x1 - x0, y1 - y0, 3.0)


def add_rear_skate(cr) -> None:
    """Large rear PTFE foot: wide stadium following the rump."""
    x0, y0 = _u(0.842, -0.168)
    x1, y1 = _u(0.948, 0.168)
    rounded_rect(cr, x0, y0, x1 - x0, y1 - y0, (y1 - y0) / 2)


def frame_recesses() -> list[list[tuple[float, float]]]:
    """Dark open areas between the X-shaped frame arms (underside coords)."""
    return [
        # front trapezoid between the arms
        [_u(0.222, -0.115), _u(0.222, 0.115), _u(0.368, 0.080), _u(0.368, -0.080)],
        # side triangles (outer edge straight along the rim)
        [_u(0.240, -0.208), _u(0.485, -0.082), _u(0.726, -0.208)],
        [_u(0.240, 0.208), _u(0.485, 0.082), _u(0.726, 0.208)],
        # rear trapezoid, behind the purple button
        [_u(0.690, -0.082), _u(0.690, 0.082), _u(0.830, 0.118), _u(0.830, -0.118)],
    ]


def add_sensor(cr) -> None:
    x0, y0 = _u(0.440, -0.052)
    x1, y1 = _u(0.580, 0.052)
    rounded_rect(cr, x0, y0, x1 - x0, y1 - y0, 2.0)


def add_sensor_window(cr) -> None:
    x0, y0 = _u(0.470, -0.024)
    x1, y1 = _u(0.545, 0.024)
    rounded_rect(cr, x0, y0, x1 - x0, y1 - y0, 1.2)


def _add_purple(cr) -> None:
    """Purple button: slightly wider at the rear edge, rounded corners."""
    px, py, pw, ph, _r = PURPLE
    top_in = pw * 0.08
    cr.new_path()
    cr.move_to(px + top_in + 1.2, py)
    cr.line_to(px + pw - top_in - 1.2, py)
    cr.curve_to(px + pw - top_in, py, px + pw - top_in * 0.5, py + 0.5, px + pw - top_in * 0.4, py + 1.4)
    cr.line_to(px + pw, py + ph - 1.4)
    cr.curve_to(px + pw, py + ph - 0.4, px + pw - 0.6, py + ph, px + pw - 1.6, py + ph)
    cr.line_to(px + 1.6, py + ph)
    cr.curve_to(px + 0.6, py + ph, px, py + ph - 0.4, px, py + ph - 1.4)
    cr.line_to(px + top_in * 0.4, py + 1.4)
    cr.curve_to(px + top_in * 0.5, py + 0.5, px + top_in, py, px + top_in + 1.2, py)
    cr.close_path()


# Changed-indicator dots.
DOTS = {
    "left": (CX - 30, Y0 + 50),
    "right": (CX + 30, Y0 + 50),
    "middle": (CX, WHEEL[1] - 5),
    "forward": (FORWARD[0] - 5, FORWARD[1] + FORWARD[3] / 2),
    "back": (BACK[0] - 5, BACK[1] + BACK[3] * 0.4),
    "dpi": (PURPLE[0] + PURPLE[2] + 7, PURPLE[1] + PURPLE[3] / 2),
}

# --- layout ---------------------------------------------------------------

SIDE_GAP = 18.0
STACKED_BOX = (0.0, 0.0, W, H)
LABEL_SPACE = 20.0
_MAX_HALF = max(_half_widths(i / 400) for i in range(401))
_LEFT_EDGE = min(FORWARD[0], BACK[0]) - 4.0  # side buttons stick out on the left
SIDE_U_CX = CX + _MAX_HALF + SIDE_GAP + _MAX_HALF
SIDE_BOX = (_LEFT_EDGE, Y0 - 6.0, SIDE_U_CX + _MAX_HALF + 6.0 - _LEFT_EDGE, LENGTH + 12.0 + LABEL_SPACE)


def layout_box(layout: str) -> tuple[float, float, float, float]:
    return SIDE_BOX if layout == "side" else STACKED_BOX


def fit(width: float, height: float, layout: str = "stacked") -> tuple[float, float, float]:
    """Return (scale, origin_x, origin_y) mapping the design box into the widget."""
    bx, by, bw, bh = layout_box(layout)
    scale = min(width / bw, height / bh) * 0.98
    return scale, (width - bw * scale) / 2 - bx * scale, (height - bh * scale) / 2 - by * scale


def choose_layout(width: float, height: float) -> str:
    """Side by side when the two views fit at least as big as the stacked top view."""
    side = fit(width, height, "side")[0]
    stacked = fit(width, height, "stacked")[0]
    return "side" if side >= stacked * 0.92 else "stacked"


def side_content_height(width: float) -> int:
    """Height the widget needs for the side layout at ``width``."""
    _bx, _by, bw, bh = SIDE_BOX
    return int(math.ceil(width / bw * bh / 0.98)) + 2


def underside_transform(cr) -> None:
    """Map underside-inset design coords to the side-by-side position (same size as the top)."""
    cr.translate(SIDE_U_CX, Y0)
    cr.scale(1.0 / U_SCALE, 1.0 / U_SCALE)
    cr.translate(-U_CX, -U_Y0)


def to_underside(px: float, py: float) -> tuple[float, float]:
    """Inverse of :func:`underside_transform` for hit-testing."""
    return U_CX + (px - SIDE_U_CX) * U_SCALE, U_Y0 + (py - Y0) * U_SCALE


def hit_test(px: float, py: float, layout: str = "stacked") -> str | None:
    """Hit-test a point in design coordinates. More specific regions first."""
    import cairo

    surf = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)
    cr = cairo.Context(surf)
    for name in BUTTON_ORDER:
        add_button_path(cr, name)
        x, y = (to_underside(px, py) if (name == "dpi" and layout == "side") else (px, py))
        if name == "dpi":
            # a slightly larger target than the small button itself
            bx, by, bw, bh, _r = PURPLE
            if bx - 2 <= x <= bx + bw + 2 and by - 2 <= y <= by + bh + 2:
                return name
        if cr.in_fill(x, y):
            return name
    return None


def widget_hit(x: float, y: float, xf: tuple) -> str | None:
    scale, ox, oy = xf[:3]
    layout = xf[3] if len(xf) > 3 else "stacked"
    if scale <= 0:
        return None
    return hit_test((x - ox) / scale, (y - oy) / scale, layout)


# --- painting -------------------------------------------------------------

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
    top_label: str = "",
    layout: str = "stacked",
    fast: bool = False,  # noqa: ARG001 - vector drawing is cheap; same signature as svgdiagram.draw
) -> tuple[float, float, float, str]:
    """Paint the preview and return the transform used (for hit-testing).

    ``layout``: "stacked" (underside small, below) or "side" (underside the
    same size as the top view, to its right; labels under both views).
    """
    import cairo

    side = layout == "side"
    scale, ox, oy = fit(width, height, layout)
    cr.save()
    cr.translate(ox, oy)
    cr.scale(scale, scale)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    r, g, b = (c / 255 for c in led)
    fr, fg_, fb = fg.red, fg.green, fg.blue
    ar, ag, ab = accent.red, accent.green, accent.blue

    def source(rr, gg, bb, a):
        cr.set_source_rgba(rr, gg, bb, a)

    def stipple(path_fn, x0, y0, x1, y1, step, dot, alpha) -> None:
        cr.save()
        path_fn()
        cr.clip()
        source(fr, fg_, fb, alpha)
        row = 0
        y = y0
        while y < y1:
            x = x0 + (step / 2 if row % 2 else 0)
            while x < x1:
                cr.new_sub_path()
                cr.arc(x, y, dot, 0, 2 * math.pi)
                x += step
            y += step * 0.866
            row += 1
        cr.fill()
        cr.restore()

    lx, ly, lw, lh = LED

    # Soft LED wash under the palm, in the profile colour.
    cr.save()
    cr.translate(CX, ly + 30)
    cr.scale(1.0, 0.55)
    glow = cairo.RadialGradient(0, 0, 2, 0, 0, 110)
    glow.add_color_stop_rgba(0, r, g, b, 0.40)
    glow.add_color_stop_rgba(0.5, r, g, b, 0.11)
    glow.add_color_stop_rgba(1, r, g, b, 0.0)
    cr.set_source(glow)
    cr.arc(0, 0, 110, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # --- top view: shell ---
    add_body_path(cr)
    source(fr, fg_, fb, 0.07)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.50)
    cr.set_line_width(2.2)
    cr.stroke()

    # dotted grips on both sides
    for left in (True, False):
        pts = grip_points(left)

        def gp(pts=pts):
            cr.new_path()
            _add_poly(cr, pts)

        gp()
        source(fr, fg_, fb, 0.06)
        cr.fill_preserve()
        source(fr, fg_, fb, 0.22)
        cr.set_line_width(1.0)
        cr.stroke()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        stipple(gp, min(xs), min(ys), max(xs), max(ys), 2.6, 0.55, 0.30)

    # central channel (strip between the two main buttons)
    source(fr, fg_, fb, 0.035)
    t_ch = outline_samples(T_NOSE)
    y_top = next(_y(t) for t, hw in t_ch if hw > CHANNEL_HALF)
    rounded_rect(cr, CX - CHANNEL_HALF, y_top - 1.0, 2 * CHANNEL_HALF, SEAM_CENTER - y_top + 1.0, 2.0)
    cr.fill()

    # palm seam (curved)
    add_seam(cr)
    source(fr, fg_, fb, 0.40)
    cr.set_line_width(1.3)
    cr.stroke()

    # clickable buttons
    for name in ("left", "right", "forward", "back", "middle"):
        add_button_path(cr, name)
        if name == selected:
            source(ar, ag, ab, 0.55)
        elif name == hover:
            source(ar, ag, ab, 0.28)
        elif name == "middle":
            source(fr, fg_, fb, 0.16)
        else:
            source(fr, fg_, fb, 0.05)
        cr.fill_preserve()
        source(fr, fg_, fb, 0.80 if name == selected else 0.38)
        cr.set_line_width(1.3)
        cr.stroke()

    # wheel texture: staggered rubber studs
    wx, wy, ww, wh = WHEEL
    cr.save()
    add_button_path(cr, "middle")
    cr.clip()
    source(fr, fg_, fb, 0.30)
    rows = 8
    for i in range(rows):
        cy = wy + 4.0 + i * (wh - 8.0) / (rows - 1)
        cols = (-3.8, 0.0, 3.8) if i % 2 == 0 else (-1.9, 1.9)
        for dx in cols:
            cr.new_sub_path()
            cr.arc(CX + dx, cy, 1.15, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # LED slit in the profile colour, with a tight glow
    cr.save()
    cr.translate(lx + lw / 2, ly + lh / 2)
    cr.scale(1.0, 0.6)
    led_glow = cairo.RadialGradient(0, 0, 0.5, 0, 0, 18)
    led_glow.add_color_stop_rgba(0, r, g, b, 0.80)
    led_glow.add_color_stop_rgba(1, r, g, b, 0.0)
    cr.set_source(led_glow)
    cr.arc(0, 0, 18, 0, 2 * math.pi)
    cr.fill()
    cr.restore()
    source(r, g, b, 1)
    rounded_rect(cr, lx, ly, lw, lh, 1.5)
    cr.fill()

    # --- labels ---
    def label(text: str, x: float, y: float) -> None:
        cr.select_font_face("sans-serif")
        cr.set_font_size(10.5)
        source(fr, fg_, fb, 0.55)
        ext = cr.text_extents(text)
        cr.move_to(x - ext.width / 2, y)
        cr.show_text(text)

    if side:
        if top_label:
            label(top_label, CX, Y1 + LABEL_SPACE - 4)
        if underside_label:
            label(underside_label, SIDE_U_CX, Y1 + LABEL_SPACE - 4)

    # --- underside view ---
    cr.save()
    lwf = 1.0
    if side:
        underside_transform(cr)
        lwf = U_SCALE  # keep stroke widths like the top view
    else:
        if underside_label:
            cr.select_font_face("sans-serif")
            cr.set_font_size(11)
            source(fr, fg_, fb, 0.45)
            ext = cr.text_extents(underside_label)
            cr.move_to(U_CX - ext.width / 2, U_Y0 - 8)
            cr.show_text(underside_label)
        source(fr, fg_, fb, 0.12)
        cr.set_line_width(1.0)
        cr.move_to(CX, Y1 + 4)
        cr.line_to(U_CX, U_Y0 - (18 if underside_label else 6))
        cr.stroke()

    add_underside_body(cr)
    source(fr, fg_, fb, 0.06)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.50)
    cr.set_line_width(lwf * 2.2)
    cr.stroke()

    add_underside_rim(cr)
    source(fr, fg_, fb, 0.03)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.22)
    cr.set_line_width(lwf * 1.0)
    cr.stroke()

    # X frame: the open recesses between its arms
    for poly in frame_recesses():
        cr.new_path()
        _add_poly(cr, poly)
        source(fr, fg_, fb, 0.20)
        cr.fill_preserve()
        source(fr, fg_, fb, 0.34)
        cr.set_line_width(lwf * 1.0)
        cr.stroke()

    # PTFE feet
    for fn in (add_front_skate, add_rear_skate):
        cr.new_path()
        fn(cr)
        source(fr, fg_, fb, 0.10)
        cr.fill_preserve()
        source(fr, fg_, fb, 0.36)
        cr.set_line_width(lwf * 1.1)
        cr.stroke()

    # sensor housing + window
    cr.new_path()
    add_sensor(cr)
    source(fr, fg_, fb, 0.05)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.45)
    cr.set_line_width(lwf * 1.1)
    cr.stroke()
    cr.new_path()
    add_sensor_window(cr)
    source(fr, fg_, fb, 0.30)
    cr.fill()

    # purple multi-function button
    add_button_path(cr, "dpi")
    if selected == "dpi":
        source(ar, ag, ab, 0.60)
    elif hover == "dpi":
        source(ar, ag, ab, 0.32)
    else:
        source(*PURPLE_RGB, 0.92)
    cr.fill_preserve()
    source(fr, fg_, fb, 0.80 if selected == "dpi" else 0.45)
    cr.set_line_width(lwf * 1.2)
    cr.stroke()

    if "dpi" in changed:
        dx, dy = DOTS["dpi"]
        source(ar, ag, ab, 1)
        cr.arc(dx, dy, 4.5 * lwf, 0, 2 * math.pi)
        cr.fill()
    cr.restore()  # underside transform

    for name in ("left", "right", "middle", "forward", "back"):
        if name in changed:
            dx, dy = DOTS[name]
            source(ar, ag, ab, 1)
            cr.new_path()
            cr.arc(dx, dy, 4.5, 0, 2 * math.pi)
            cr.fill()

    cr.restore()
    return scale, ox, oy, layout


# Common preview API (shared with svgdiagram) --------------------------------
SIDE_MIN_WIDTH = SIDE_BOX[2] * 0.95 / 0.98


def available() -> bool:
    return True


def button_center(name: str, xf: tuple) -> tuple[float, float]:
    """Widget coordinates of a point inside button ``name``."""
    scale, ox, oy = xf[:3]
    layout = xf[3] if len(xf) > 3 else "stacked"
    if name == "middle":
        x, y = CX, WHEEL[1] + WHEEL[3] / 2
    elif name == "left":
        x, y = CX - 30, Y0 + 50
    elif name == "right":
        x, y = CX + 30, Y0 + 50
    elif name in ("forward", "back"):
        bx, by, bw, bh, _r = FORWARD if name == "forward" else BACK
        x, y = bx + bw / 2, by + bh / 2
    else:
        px, py, pw, ph, _r = PURPLE
        x, y = px + pw / 2, py + ph / 2
        if layout == "side":
            x = SIDE_U_CX + (x - U_CX) / U_SCALE
            y = Y0 + (y - U_Y0) / U_SCALE
    return ox + scale * x, oy + scale * y
