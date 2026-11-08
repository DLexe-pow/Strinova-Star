"""geometry 的单测：基准坐标 → 像素。"""

import unittest

from strinova_star import geometry


def profile(width=2560, height=1600, hud=(1.3333, 1.3333)):
    return {
        "reference_size": [width, height],
        "reference_base": [1920, 1080],
        "hud_scale": list(hud),
        "regions": {
            "names": {"box": [420, 300, 700, 790], "anchor": ["left", "top"],
                      "scale_mode": "height"},
            "weapon": {"box": [1780, 900, 1900, 1010], "anchor": ["right", "bottom"]},
        },
        "lineup": {
            "anchor": ["center", "top"],
            "scale_mode": "height",
            "slots": [[443.6, 77.0, 501.0, 112.1], [511.8, 77.0, 569.2, 112.1]],
        },
    }


class ScaleTest(unittest.TestCase):
    def test_height_scale(self):
        self.assertAlmostEqual(geometry.height_scale(profile())[0], 1600 / 1080, places=4)

    def test_fit_scale_uses_min(self):
        # 2560x1600：宽比 1.333、高比 1.4815 → 取小的 1.333
        sx, sy = geometry.fit_scale(profile())
        self.assertAlmostEqual(sx, 4 / 3, places=4)
        self.assertAlmostEqual(sy, 4 / 3, places=4)

    def test_ultrawide_does_not_overflow(self):
        # 3440x1440：宽比 1.79、高比 1.333 → 仍取 1.333
        sx, _ = geometry.fit_scale(profile(3440, 1440))
        self.assertAlmostEqual(sx, 4 / 3, places=4)

    def test_scale_mode_override(self):
        sx, _ = geometry.scale_for(profile(), "height", override=(2.0, 2.0))
        self.assertEqual(sx, 2.0)
        with self.assertRaises(geometry.GeometryError):
            geometry.scale_for(profile(), "diagonal")


class PointTest(unittest.TestCase):
    def test_left_top(self):
        p = geometry.point(profile(), 100, 100, "left", "top", scale_mode="height")
        self.assertAlmostEqual(p[0], 100 * 1600 / 1080, places=3)
        self.assertAlmostEqual(p[1], 100 * 1600 / 1080, places=3)

    def test_right_anchor_keeps_margin(self):
        # 距右边缘 (1920-x) 个单位，换成像素后仍是这个距离 × 缩放
        p = geometry.point(profile(), 1820, 0, "right", "top", scale_mode="height")
        self.assertAlmostEqual(p[0], 2560 - 100 * 1600 / 1080, places=3)

    def test_center_is_symmetric(self):
        left = geometry.point(profile(), 860, 0, "center", "top", scale_mode="height")[0]
        right = geometry.point(profile(), 1060, 0, "center", "top", scale_mode="height")[0]
        self.assertAlmostEqual(1280 - left, right - 1280, places=3)

    def test_bottom_anchor(self):
        p = geometry.point(profile(), 0, 1080, "left", "bottom", scale_mode="height")
        self.assertAlmostEqual(p[1], 1600, places=3)

    def test_bad_anchor_raises(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.point(profile(), 0, 0, "middle", "top")


class BoxTest(unittest.TestCase):
    def test_box_is_sorted(self):
        # 从右锚点给的框，x 会自然倒序，box 要负责排好
        b = geometry.box(profile(), [1900, 900, 1780, 1010], "right", "bottom")
        self.assertLess(b[0], b[2])
        self.assertLess(b[1], b[3])

    def test_box_needs_four_values(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.box(profile(), [1, 2, 3])

    def test_region_uses_its_scale_mode(self):
        b = geometry.region(profile(), "names")
        self.assertAlmostEqual(b[0], round(420 * 1600 / 1080), delta=1)
        self.assertEqual(b[1], round(300 * 1600 / 1080))

    def test_region_unknown_name(self):
        with self.assertRaises(geometry.GeometryError):
            geometry.region(profile(), "nope")

    def test_lineup_slots(self):
        slots = geometry.lineup_slots(profile())
        self.assertEqual(len(slots), 2)
        self.assertLess(slots[0][0], slots[0][2])


class UtilTest(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(geometry.clamp((-10, -10, 3000, 2000), (2560, 1600)), (0, 0, 2560, 1600))

    def test_offset(self):
        self.assertEqual(geometry.offset((1, 2, 3, 4), 5, -1), (6, 1, 8, 3))

    def test_crop_empty(self):
        class Fake:
            shape = (100, 100, 3)

        self.assertIsNone(geometry.crop(Fake(), (10, 10, 11, 11)))


if __name__ == "__main__":
    unittest.main()
