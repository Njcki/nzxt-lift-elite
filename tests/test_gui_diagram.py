"""Hit-testing for the Lift Elite preview (Cairo only, no display needed)."""

from __future__ import annotations

import unittest

from nzxt_lift_elite.gui import diagram


class DiagramHitTest(unittest.TestCase):
    def test_buttons_are_distinct_and_clickable(self):
        cx, y0 = diagram.CX, diagram.Y0
        wx, wy, ww, wh = diagram.WHEEL
        self.assertEqual(diagram.hit_test(cx, wy + wh / 2), "middle")
        self.assertEqual(diagram.hit_test(cx - 30, y0 + 50), "left")
        self.assertEqual(diagram.hit_test(cx + 30, y0 + 50), "right")
        fx, fy, fw, fh, _ = diagram.FORWARD
        self.assertEqual(diagram.hit_test(fx + fw / 2, fy + fh / 2), "forward")
        bx, by, bw, bh, _ = diagram.BACK
        self.assertEqual(diagram.hit_test(bx + bw / 2, by + bh / 2), "back")
        px, py, pw, ph, _ = diagram.PURPLE
        self.assertEqual(diagram.hit_test(px + pw / 2, py + ph / 2), "dpi")
        # Palm rest and the status LED are not buttons.
        self.assertIsNone(diagram.hit_test(cx, y0 + 160))
        lx, ly, lw, lh = diagram.LED
        self.assertIsNone(diagram.hit_test(lx + lw / 2, ly + lh / 2))

    def test_fit_and_widget_hit_round_trip(self):
        scale, ox, oy = diagram.fit(440, 744)
        self.assertGreater(scale, 0)
        wx, wy, ww, wh = diagram.WHEEL
        x = ox + scale * (diagram.CX)
        y = oy + scale * (wy + wh / 2)
        self.assertEqual(diagram.widget_hit(x, y, (scale, ox, oy)), "middle")

    def test_side_layout_hit_and_same_size(self):
        # purple button: its underside-inset centre mapped to the side position
        px, py, pw, ph, _ = diagram.PURPLE
        ux, uy = px + pw / 2, py + ph / 2
        sx = diagram.SIDE_U_CX + (ux - diagram.U_CX) / diagram.U_SCALE
        sy = diagram.Y0 + (uy - diagram.U_Y0) / diagram.U_SCALE
        self.assertEqual(diagram.to_underside(sx, sy), (ux, uy))
        self.assertEqual(diagram.hit_test(sx, sy, "side"), "dpi")
        self.assertIsNone(diagram.hit_test(ux, uy, "side"))  # old stacked spot is empty now
        # top view buttons unchanged in the side layout
        wx, wy, ww, wh = diagram.WHEEL
        scale, ox, oy = diagram.fit(320, 300, "side")
        xf = (scale, ox, oy, "side")
        self.assertEqual(diagram.widget_hit(ox + scale * diagram.CX, oy + scale * (wy + wh / 2), xf), "middle")
        self.assertEqual(diagram.widget_hit(ox + scale * sx, oy + scale * sy, xf), "dpi")
        # same size: the underside spans the same length as the top view
        self.assertAlmostEqual(diagram.U_LEN / diagram.U_SCALE, diagram.LENGTH)
        bx, by, bw, bh = diagram.SIDE_BOX
        self.assertGreater(bx + bw, diagram.SIDE_U_CX + diagram._MAX_HALF)
        self.assertEqual(diagram.choose_layout(320, 300), "side")
        self.assertEqual(diagram.choose_layout(200, 700), "stacked")

    def test_rounded_nose_and_buttons_inside(self):
        import cairo

        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
        pts = diagram.outline_samples(diagram.T_NOSE)
        self.assertEqual(pts[0], (0.0, 0.0))
        # smooth: the tip is flat-ish (small slope) and the width grows monotonically
        widths = [hw for _t, hw in pts]
        self.assertEqual(widths, sorted(widths))
        t1, hw1 = pts[1]
        self.assertLess(t1 * diagram.LENGTH, 0.2 * hw1)
        # no protrusion: every vertex of both main buttons is inside the shell
        for name in ("left", "right"):
            diagram.add_button_path(cr, name)
            verts = [pt for kind, pt in cr.copy_path() if kind in (cairo.PATH_MOVE_TO, cairo.PATH_LINE_TO)]
            diagram.add_body_path(cr)
            for x, y in verts:
                self.assertTrue(cr.in_fill(x, y), (name, x, y))


if __name__ == "__main__":
    unittest.main()
