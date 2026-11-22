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


if __name__ == "__main__":
    unittest.main()
