"""ocr 结果归一化：各版本返回形状不同，这里保证不炸。"""

import unittest

from strinova_star import ocr


class PairTest(unittest.TestCase):
    def test_with_box(self):
        self.assertEqual(
            ocr._pair([[[0, 0], [10, 0], [10, 9], [0, 9]], "穗织剑圣星绘绘", "0.93"]),
            ("穗织剑圣星绘绘", 0.93),
        )

    def test_rec_only(self):
        self.assertEqual(ocr._pair(("若南缘", 0.8)), ("若南缘", 0.8))

    def test_strips_space(self):
        self.assertEqual(ocr._pair(("  abc  ", 0.5))[0], "abc")

    def test_bad_score_is_zero(self):
        self.assertEqual(ocr._pair([[[0, 0]], "abc", "oops"]), ("abc", 0.0))

    def test_unknown_shape(self):
        self.assertEqual(ocr._pair({"text": "abc"}), ("", 0.0))


class BestTextTest(unittest.TestCase):
    def test_picks_longest(self):
        result = ([[[[0, 0]], "ab", "0.9"], [[[0, 0]], "long name", "0.8"]], [0.01])
        self.assertEqual(ocr.best_text(result), ("long name", 0.8))

    def test_empty(self):
        self.assertEqual(ocr.best_text(None), ("", 0.0))
        self.assertEqual(ocr.best_text(([], [])), ("", 0.0))

    def test_ignores_blank(self):
        self.assertEqual(ocr.best_text(([["", 0.9]], [])), ("", 0.0))


if __name__ == "__main__":
    unittest.main()