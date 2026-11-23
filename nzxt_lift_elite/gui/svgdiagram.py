"""Lift Elite preview drawn from the bundled SVG artwork (librsvg).

``data/lift-elite.svg`` holds the top view and the underside stacked (as
in the original artwork). Here they are rendered as two separate views,
side by side at the same length (or stacked when the panel is narrow),
with the GUI's own "Sopra"/"Sotto" labels, a transparent background, the
LED recoloured to the profile colour and clickable hotspots taken from
the SVG's own button shapes. Falls back to :mod:`diagram` when librsvg
(gi ``Rsvg``) is not available.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

ASSET = Path(__file__).with_name("data") / "lift-elite.svg"
SVG_W, SVG_H = 896.0, 1200.0
NS = "{http://www.w3.org/2000/svg}"

# Placeholder LED colours in the asset → shade factors of the profile colour.
LED_PLACEHOLDERS = {"#fe0001": 1.0, "#fe0002": 0.86, "#fe0003": 0.55, "#fe0004": 0.45, "#fe0005": -0.55}
LED_CENTER = (448.0, 348.0)

# Source rectangles (SVG units) of the two views, with room for the shadows.
TOP_INK = (270.0, 54.0, 356.0, 661.0)
BOT_INK = (351.0, 783.0, 196.0, 375.0)
TOP_SRC = (256.0, 44.0, 384.0, 700.0)
BOT_SRC = (338.0, 775.0, 222.0, 400.0)
BOT_K = TOP_INK[3] / BOT_INK[3]  # underside drawn at the same length as the top
GAP = 8.0
LABEL_SPACE = 44.0  # SVG units under the views (side) / above the underside (stacked)

# Layout boxes in "layout units" (top-view SVG units).
SIDE_BOX = (0.0, 0.0, TOP_SRC[2] + GAP + BOT_SRC[2] * BOT_K, TOP_SRC[3] + LABEL_SPACE)
STACKED_BOX = (0.0, 0.0, TOP_SRC[2], TOP_SRC[3] + LABEL_SPACE + BOT_SRC[3])
SIDE_MIN_WIDTH = 250  # px: below this the views are stacked

HOTSPOTS = {
    "middle": "scroll-wheel",
    "dpi": "purple-dpi-button",
    "forward": "side-btn-forward",
    "back": "side-btn-back",
    "left": "lmb",
    "right": "rmb",
}
BUTTON_ORDER = ("middle", "dpi", "forward", "back", "left", "right")
VIEW_OF = {"dpi": "bottom"}  # everything else is on the top view

# Changed-indicator dots (SVG units).
DOTS = {
    "left": (340.0, 200.0),
    "right": (556.0, 200.0),
    "middle": (448.0, 104.0),
    "forward": (262.0, 305.0),
    "back": (268.0, 445.0),
    "dpi": (494.0, 1016.0),
}


def available() -> bool:
    try:
        import gi

        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg  # noqa: F401
    except (ImportError, ValueError):
        return False
    return ASSET.is_file()


# --- SVG path data → simple command list ---------------------------------

_TOKEN = re.compile(r"[MLHVCQZmlhvcqz]|-?\d*\.?\d+(?:e-?\d+)?")


def parse_path(d: str) -> list[tuple[str, tuple[float, ...]]]:
    """Absolute M/L/C/Z commands (supports M L H V C Q Z, abs and rel)."""
    toks = _TOKEN.findall(d)
    out: list[tuple[str, tuple[float, ...]]] = []
    i = 0
    cmd = ""
    x = y = sx = sy = 0.0
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                out.append(("Z", ()))
                x, y = sx, sy
                continue
        rel = cmd.islower()
        c = cmd.upper()
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4}[c]
        v = [float(t) for t in toks[i:i + n]]
        i += n
        if c == "H":
            v = [v[0] + (x if rel else 0), y]
            c, rel = "L", False
        elif c == "V":
            v = [x, v[0] + (y if rel else 0)]
            c, rel = "L", False
        if rel:
            v = [val + (x if k % 2 == 0 else y) for k, val in enumerate(v)]
        if c == "Q":  # quadratic → cubic
            qx, qy, ex, ey = v
            v = [x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), ex + 2 / 3 * (qx - ex), ey + 2 / 3 * (qy - ey), ex, ey]
            c = "C"
        out.append((c, tuple(v)))
        x, y = v[-2], v[-1]
        if c == "M":
            sx, sy = x, y
            cmd = "l" if rel else "L"  # implicit lineto after moveto
    return out


def _rect_cmds(x, y, w, h, r) -> list:
    k = 0.5522847498307936 * r
    return [
        ("M", (x + r, y)), ("L", (x + w - r, y)),
        ("C", (x + w - r + k, y, x + w, y + r - k, x + w, y + r)), ("L", (x + w, y + h - r)),
        ("C", (x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h)), ("L", (x + r, y + h)),
        ("C", (x + r - k, y + h, x, y + h - r + k, x, y + h - r)), ("L", (x, y + r)),
        ("C", (x, y + r - k, x + r - k, y, x + r, y)), ("Z", ()),
    ]


@lru_cache(maxsize=1)
def shapes() -> dict[str, list]:
    """Hotspot outlines + the two shell outlines, parsed from the asset."""
    root = ET.fromstring(ASSET.read_text(encoding="utf-8").split("?>", 1)[-1])
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    out: dict[str, list] = {}

    def first_shape(el):
        if el.tag == NS + "path":
            return parse_path(el.get("d"))
        if el.tag == NS + "rect":
            f = lambda k, d=0.0: float(el.get(k, d))  # noqa: E731
            return _rect_cmds(f("x"), f("y"), f("width"), f("height"), f("rx"))
        for child in el:
            got = first_shape(child)
            if got:
                return got
        return None

    for name, sid in HOTSPOTS.items():
        out[name] = first_shape(by_id[sid])
    out["top-shell"] = first_shape(by_id["top-shell-base"])
    out["bottom-shell"] = first_shape(by_id["bottom-shell-base"])
    return out


def add_path(cr, cmds) -> None:
    cr.new_path()
    for c, v in cmds:
        if c == "M":
            cr.move_to(*v)
        elif c == "L":
            cr.line_to(*v)
        elif c == "C":
            cr.curve_to(*v)
        elif c == "Z":
            cr.close_path()


def bbox(name: str) -> tuple[float, float, float, float]:
    pts = [(v[k], v[k + 1]) for _c, v in shapes()[name] for k in range(0, len(v), 2)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


# --- layout -----------------------------------------------------------------

def layout_box(layout: str):
    return SIDE_BOX if layout == "side" else STACKED_BOX


def side_content_height(width: float) -> int:
    _x, _y, bw, bh = SIDE_BOX
    return int(math.ceil(width / bw * bh / 0.98)) + 2


def views(width: float, height: float, layout: str) -> dict[str, tuple[float, float, float]]:
    """Per view: (scale, ox, oy) mapping SVG units → widget pixels."""
    _x, _y, bw, bh = layout_box(layout)
    s = min(width / bw, height / bh) * 0.98
    x0 = (width - bw * s) / 2
    y0 = (height - bh * s) / 2
    top = (s, x0 - TOP_SRC[0] * s, y0 - TOP_SRC[1] * s)
    if layout == "side":
        sb = s * BOT_K
        bx = x0 + (TOP_SRC[2] + GAP) * s
        bot = (sb, bx - BOT_SRC[0] * sb, y0 - BOT_SRC[1] * sb)
    else:
        by = y0 + (TOP_SRC[3] + LABEL_SPACE) * s
        bot = (s, x0 + (TOP_SRC[2] - BOT_SRC[2]) / 2 * s - BOT_SRC[0] * s, by - BOT_SRC[1] * s)
    return {"top": top, "bottom": bot}


def _to_svg(x: float, y: float, xf) -> tuple[float, float]:
    s, ox, oy = xf
    return (x - ox) / s, (y - oy) / s


def hit_svg(name_view: str, px: float, py: float) -> str | None:
    """Hit-test SVG coords on one view ("top" / "bottom")."""
    import cairo

    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
    sh = shapes()
    shell = "top-shell" if name_view == "top" else "bottom-shell"
    add_path(cr, sh[shell])
    if not cr.in_fill(px, py) and name_view == "top":
        # side buttons sit on the edge of the shell
        for name in ("forward", "back"):
            add_path(cr, sh[name])
            if cr.in_fill(px, py):
                return name
        return None
    for name in BUTTON_ORDER:
        if VIEW_OF.get(name, "top") != name_view:
            continue
        add_path(cr, sh[name])
        if cr.in_fill(px, py):
            return name
        if name == "dpi":  # small button: slightly larger target
            x, y, w, h = bbox(name)
            if x - 3 <= px <= x + w + 3 and y - 3 <= py <= y + h + 3:
                return name
    return None


def widget_hit(x: float, y: float, xf: tuple) -> str | None:
    """``xf`` = (1, dx, dy, layout, views) as returned by :func:`draw`."""
    if len(xf) < 5:
        return None  # not drawn yet
    _s, dx, dy, _layout, vw = xf
    x, y = x - dx, y - dy
    for view in ("bottom", "top"):
        s, ox, oy = vw[view]
        src = BOT_SRC if view == "bottom" else TOP_SRC
        px, py = _to_svg(x, y, vw[view])
        if src[0] <= px <= src[0] + src[2] and src[1] <= py <= src[1] + src[3]:
            hit = hit_svg(view, px, py)
            if hit:
                return hit
    return None


def button_center(name: str, xf: tuple) -> tuple[float, float]:
    """Widget coordinates of a point inside button ``name`` (tests, a11y)."""
    _s, dx, dy, _layout, vw = xf
    x, y, w, h = bbox(name)
    cx, cy = x + w / 2, y + h / 2
    if name == "left":
        cx = x + w * 0.55
    if name == "right":
        cx = x + w * 0.45
    if name in ("forward", "back"):
        cx = x + w * 0.6
    s, ox, oy = vw[VIEW_OF.get(name, "top")]
    return ox + cx * s + dx, oy + cy * s + dy


# --- rendering ---------------------------------------------------------------

def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def led_svg(led: tuple[int, int, int]) -> str:
    """Asset text with the LED placeholders replaced by shades of ``led``."""
    text = ASSET.read_text(encoding="utf-8")
    for ph, f in LED_PLACEHOLDERS.items():
        if f >= 0:
            col = [c * f for c in led]
        else:  # highlight: mix toward white
            col = [c + (255 - c) * -f for c in led]
        text = text.replace(ph, _hex(col))
    return text


@lru_cache(maxsize=8)
def _handle(led: tuple[int, int, int]):
    import gi

    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg

    return Rsvg.Handle.new_from_data(led_svg(led).encode("utf-8"))


_cache: dict = {}


def _base_surface(width: int, height: int, layout: str, led, vw):
    """The two views rendered once per size/colour (librsvg filters are slow)."""
    import cairo
    import gi

    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg

    key = (width, height, layout, tuple(led))
    surf = _cache.get(key)
    if surf is not None:
        return surf
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, width), max(1, height))
    cr = cairo.Context(surf)
    handle = _handle(tuple(led))
    vp = Rsvg.Rectangle()
    vp.x, vp.y, vp.width, vp.height = 0, 0, SVG_W, SVG_H
    for view, src in (("top", TOP_SRC), ("bottom", BOT_SRC)):
        s, ox, oy = vw[view]
        cr.save()
        cr.translate(ox, oy)
        cr.scale(s, s)
        cr.rectangle(*src)
        cr.clip()
        handle.render_document(cr, vp)
        cr.restore()
    surf.flush()
    if len(_cache) > 6:
        _cache.clear()
    _cache[key] = surf
    _last[(layout, tuple(led))] = (surf, vw["top"])
    return surf


# Last crisp render per (layout, colour), reused scaled while the preview is
# being resized (librsvg with filters is far too slow for every frame).
_last: dict = {}


def _paint_base(cr, width: float, height: float, layout: str, led, vw, fast: bool) -> None:
    prev = _last.get((layout, tuple(led))) if fast else None
    if prev is None:
        cr.set_source_surface(_base_surface(int(width), int(height), layout, led, vw), 0, 0)
        cr.paint()
        return
    import cairo

    surf, (s0, ox0, oy0) = prev
    s, ox, oy = vw["top"]
    k = s / s0
    # every view maps SVG → pixels as offset + s·f(u) with the same offset, so
    # one translate + uniform scale maps the old render onto the new geometry
    cr.save()
    cr.translate(ox - k * ox0, oy - k * oy0)
    cr.scale(k, k)
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    cr.paint()
    cr.restore()


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
    layout: str = "side",
    fast: bool = False,
):
    """Paint the preview. ``fast``: reuse the last crisp render, scaled (live resize)."""
    import cairo

    vw = views(width, height, layout)
    cr.save()
    _paint_base(cr, width, height, layout, led, vw, fast)

    r, g, b = (c / 255 for c in led)
    ar, ag, ab = accent.red, accent.green, accent.blue
    sh = shapes()

    # LED glow in the profile colour, around the slit
    s, ox, oy = vw["top"]
    cr.save()
    cr.translate(ox, oy)
    cr.scale(s, s)
    lx, ly = LED_CENTER
    glow = cairo.RadialGradient(0, 0, 4, 0, 0, 150)
    glow.add_color_stop_rgba(0, r, g, b, 0.55)
    glow.add_color_stop_rgba(0.35, r, g, b, 0.16)
    glow.add_color_stop_rgba(1, r, g, b, 0.0)
    add_path(cr, sh["top-shell"])
    cr.save()
    cr.clip()
    cr.translate(lx, ly + 6)
    cr.scale(1.0, 0.55)
    cr.set_source(glow)
    cr.arc(0, 0, 150, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # hover / selection overlay on the top-view buttons
    for name in ("left", "right", "middle", "forward", "back"):
        if name not in (selected, hover):
            continue
        cr.save()
        if name in ("left", "right"):
            add_path(cr, sh["top-shell"])
            cr.clip()
        add_path(cr, sh[name])
        cr.set_source_rgba(ar, ag, ab, 0.45 if name == selected else 0.25)
        cr.fill_preserve()
        if name == selected:
            cr.set_source_rgba(ar, ag, ab, 0.95)
            cr.set_line_width(3.0)
            cr.stroke()
        cr.new_path()
        cr.restore()
    for name in ("left", "right", "middle", "forward", "back"):
        if name in changed:
            cr.set_source_rgba(ar, ag, ab, 1)
            cr.new_path()
            cr.arc(*DOTS[name], 9.0, 0, 2 * math.pi)
            cr.fill()
    cr.restore()

    # underside: purple button
    s, ox, oy = vw["bottom"]
    cr.save()
    cr.translate(ox, oy)
    cr.scale(s, s)
    if "dpi" in (selected, hover):
        add_path(cr, sh["dpi"])
        cr.set_source_rgba(ar, ag, ab, 0.55 if selected == "dpi" else 0.30)
        cr.fill_preserve()
        if selected == "dpi":
            cr.set_source_rgba(ar, ag, ab, 0.95)
            cr.set_line_width(2.0)
            cr.stroke()
        cr.new_path()
    if "dpi" in changed:
        cr.set_source_rgba(ar, ag, ab, 1)
        cr.arc(*DOTS["dpi"], 6.0, 0, 2 * math.pi)
        cr.fill()
    cr.restore()

    # labels (GUI's own, translated)
    fr, fgc, fb = fg.red, fg.green, fg.blue
    cr.select_font_face("sans-serif")
    cr.set_font_size(11.0)
    cr.set_source_rgba(fr, fgc, fb, 0.6)

    def label(text, view, src_x_center, svg_y):
        if not text:
            return
        s, ox, oy = vw[view]
        ext = cr.text_extents(text)
        cr.move_to(ox + src_x_center * s - ext.width / 2, oy + svg_y * s)
        cr.show_text(text)

    ts, tox, toy = vw["top"]
    if layout == "side":
        y_lab = toy + (TOP_SRC[1] + TOP_SRC[3] + LABEL_SPACE * 0.55) * ts
        for text, view, xc in ((top_label, "top", 448.0), (underside_label, "bottom", 448.0)):
            if text:
                s, ox, _oy = vw[view]
                ext = cr.text_extents(text)
                cr.move_to(ox + xc * s - ext.width / 2, y_lab)
                cr.show_text(text)
    else:
        bs, box_, boy = vw["bottom"]
        if underside_label:
            ext = cr.text_extents(underside_label)
            cr.move_to(box_ + 448.0 * bs - ext.width / 2, boy + (BOT_SRC[1] - LABEL_SPACE * 0.3) * bs)
            cr.show_text(underside_label)
    cr.restore()
    return (1.0, 0.0, 0.0, layout, vw)
