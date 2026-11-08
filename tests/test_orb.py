"""ORB 特征点匹配的回归测试。

几个数字来自实机标定（D:\\game\\Strinova Star 的 20 张局内真值裁片）：
阵容条 10/10、计分板 10/10，平均 62 ms/张，平均第一/第二名差 63 个内点。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from strinova_star.avatars import ROOT as ASSET_ROOT  # noqa: E402
from strinova_star.avatars import OrbMatcher, assign, imread  # noqa: E402


class OrbMatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = OrbMatcher()

    def test_has_all_characters(self):
        self.assertEqual(len(self.matcher), 24)

    def test_template_descriptors_built_for_every_scale(self):
        for templates in self.matcher._templates:
            self.assertEqual(len(templates), len(OrbMatcher.SCALES))
            for _keypoints, descriptors in templates:
                self.assertIsNotNone(descriptors)

    def test_baimo_does_not_collapse_into_michele(self):
        """绿发 vs 金发曾经互串，这条钉住它（用局内真值裁片，不是立绘自识）。"""
        crop = imread(ASSET_ROOT / "assets/characters/lineup/白墨.png")
        name, score = self.matcher.match(crop)
        self.assertEqual(name, "白墨")
        self.assertGreater(score, 12)

    def test_small_scoreboard_row_still_recognised(self):
        """计分板行只有 48×38，得靠放大 4 倍才提得出特征点。"""
        crop = imread(ASSET_ROOT / "assets/characters/roster/令.png")
        name, score = self.matcher.match(crop)
        self.assertEqual(name, "令")
        self.assertGreater(score, 12)

    def test_blank_crop_is_rejected(self):
        blank = np.full((52, 85, 3), 128, np.uint8)
        name, _score = self.matcher.match(blank)
        self.assertEqual(name, "")

    def test_self_recognition_of_artwork(self):
        """24 张立绘自己认自己，不能有例外。"""
        for name in self.matcher.names:
            crop = imread(self.matcher.folder / f"{name}.png")
            self.assertEqual(self.matcher.match(crop)[0], name, name)


class OrbAssignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = OrbMatcher()

    def test_assign_keeps_matcher_order(self):
        names = self.matcher.names[:5]
        crops = [imread(self.matcher.folder / f"{name}.png") for name in names]
        picks = assign(self.matcher, crops)
        self.assertEqual([p[0] for p in picks], names)

    def test_assign_rejects_blank(self):
        blank = np.full((52, 85, 3), 128, np.uint8)
        picks = assign(self.matcher, [blank], min_score=12)
        self.assertEqual(picks[0][0], "")


if __name__ == "__main__":
    unittest.main()
