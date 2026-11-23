"""SVG preview artwork: loads with librsvg, no logo/background, hotspots inside."""

from __future__ import annotations

import unittest

from nzxt_lift_elite.gui import svgdiagram as sd


class AssetTest(unittest.TestCase):
    def test_asset_clean(self):
        text = sd.ASSET.read_text(encoding="utf-8")
        self.assertNotIn("NZXT", text.upper().replace("LIFT ELITE", ""))
        self.assertNotIn('id="background"', text)
        self.assertNotIn("label-sotto", text)
        for ph in sd.LED_PLACEHOLDERS:
            self.assertEqual(text.count(ph), 1, ph)

    def test_led_recolour(self):
        text = sd.led_svg((0, 255, 0))
        for ph in sd.LED_PLACEHOLDERS:
            self.assertNotIn(ph, text)
        self.assertIn("#00ff00", text)

    def test_hotspots_inside_views(self):
        for name in sd.HOTSPOTS:
            x, y, w, h = sd.bbox(name)
            view = sd.VIEW_OF.get(name, "top")
            ix, iy, iw, ih = sd.TOP_INK if view == "top" else sd.BOT_INK
            self.assertGreaterEqual(x, ix - 3, name)
            self.assertLessEqual(x + w, ix + iw + 3, name)
            self.assertGreaterEqual(y, iy - 3, name)
            self.assertLessEqual(y + h, iy + ih + 3, name)

    def test_hit_round_trip_both_layouts(self):
        for layout, (w, h) in (("side", (300, 280)), ("stacked", (240, 400))):
            vw = sd.views(w, h, layout)
            xf = (1.0, 0.0, 0.0, layout, vw)
            for name in sd.HOTSPOTS:
                x, y = sd.button_center(name, xf)
                self.assertTrue(0 <= x <= w and 0 <= y <= h, (layout, name, x, y))
                self.assertEqual(sd.widget_hit(x, y, xf), name, (layout, name))
            # palm and LED are not buttons
            s, ox, oy = vw["top"]
            self.assertIsNone(sd.widget_hit(ox + 448 * s, oy + 560 * s, xf))
            self.assertIsNone(sd.widget_hit(ox + 448 * s, oy + 348 * s, xf))
            self.assertIsNone(sd.widget_hit(1, 1, xf))
        self.assertIsNone(sd.widget_hit(10, 10, (1.0, 0.0, 0.0)))  # before the first draw


@unittest.skipUnless(sd.available(), "librsvg (gi Rsvg) not available")
class RenderTest(unittest.TestCase):
    def test_renders_transparent_with_profile_led(self):
        import cairo

        class C:
            def __init__(self, r, g, b):
                self.red, self.green, self.blue = r, g, b

        w, h = 300, 280
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        xf = sd.draw(cr, w, h, led=(0, 255, 0), selected="left", hover=None, changed={"dpi"},
                     fg=C(0, 0, 0), accent=C(0.2, 0.5, 0.9), underside_label="Sotto", top_label="Sopra")
        surf.flush()
        data = surf.get_data()
        stride = surf.get_stride()

        def px(x, y):
            i = int(y) * stride + int(x) * 4
            b, g, r, a = data[i:i + 4]
            return r, g, b, a

        self.assertEqual(px(0, 0)[3], 0)  # transparent background
        s, ox, oy = xf[4]["top"]
        r, g, b, a = px(ox + 448 * s, oy + 348 * s)  # LED slit
        self.assertGreater(g, 150)
        self.assertLess(r, 120)
        r, g, b, a = px(ox + 448 * s, oy + 560 * s)  # palm is drawn
        self.assertGreater(a, 200)


if __name__ == "__main__":
    unittest.main()
