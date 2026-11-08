"""顶部阵容条读角色的单测：不碰视频、不跑真 matcher。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from strinova_star import lineup  # noqa: E402


class FakeMatcher:
    """给每张裁片返回固定分数行，用来测阈值/领先分逻辑。"""

    names = ["甲", "乙", "丙"]

    def __init__(self, row):
        self.row = np.array(row, dtype=float)
        self.calls = 0

    def score_row(self, image):
        self.calls += 1
        return self.row


def fake_profile(size=(2560, 1600)):
    return {
        "reference_size": [size[0], size[1]],
        "reference_base": [1920, 1080],
        "hud_scale": [min(size[0] / 1920, size[1] / 1080)] * 2,
        "regions": {},
        "lineup": {
            "anchor": ["center", "top"],
            "scale_mode": "height",
            "slots": [
                [443.6 + i * 101.6, 77.0, 501.0 + i * 101.6, 112.1]
                for i in range(5)
            ] + [
                [1344.9 + i * 101.6, 77.0, 1402.3 + i * 101.6, 112.1]
                for i in range(5)
            ],
        },
    }


class SlotBoxTest(unittest.TestCase):
    def test_ten_slots_five_each_side(self):
        boxes = lineup.slot_boxes(fake_profile(), (2560, 1600))
        self.assertEqual(len(boxes), 10)
        self.assertEqual([side for side, _i, _b in boxes].count("ally"), 5)
        self.assertEqual([side for side, _i, _b in boxes].count("enemy"), 5)
        self.assertEqual([i for _s, i, _b in boxes], [0, 1, 2, 3, 4] * 2)

    def test_slots_scaled_by_height(self):
        boxes = lineup.slot_boxes(fake_profile(), (2560, 1600))
        x1, y1, x2, y2 = boxes[0][2]
        self.assertAlmostEqual(x2 - x1, 85, delta=2)     # 57.4 × 1.4815
        self.assertAlmostEqual(y2 - y1, 52, delta=2)     # 35.1 × 1.4815
        self.assertLess(y1, y2)

    def test_slots_clamped_inside_frame(self):
        for _side, _i, (x1, y1, x2, y2) in lineup.slot_boxes(fake_profile(), (2560, 1600)):
            self.assertGreaterEqual(x1, 0)
            self.assertGreaterEqual(y1, 0)
            self.assertLessEqual(x2, 2560)
            self.assertLessEqual(y2, 1600)


class ReadFrameTest(unittest.TestCase):
    def _image(self, size=(2560, 1600)):
        return np.zeros((size[1], size[0], 3), dtype=np.uint8)

    def test_confident_match(self):
        matcher = FakeMatcher([50, 20, 5])
        rows = lineup.read_frame(self._image(), fake_profile(), matcher)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["character"], "甲")
        self.assertEqual(rows[0]["score"], 50.0)
        self.assertEqual(rows[0]["runner"], 20.0)

    def test_low_score_is_blank(self):
        rows = lineup.read_frame(self._image(), fake_profile(), FakeMatcher([10, 2, 1]))
        self.assertEqual(rows[0]["character"], "")

    def test_small_margin_is_blank(self):
        # 第一名够分，但和第二名只差 2 分（< MIN_MARGIN=4）→ 宁可不认
        rows = lineup.read_frame(self._image(), fake_profile(), FakeMatcher([20, 18, 1]))
        self.assertEqual(rows[0]["character"], "")

    def test_tiny_frame_yields_blank_slots(self):
        rows = lineup.read_frame(self._image((2, 2)), fake_profile(), FakeMatcher([50, 20, 5]))
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["character"] == "" for row in rows))


def make_result(slots):
    """slots: [("ally", 0, "甲"), ...]"""
    result = lineup.Result(found=True, time=2.0, frames=4)
    for side, index, character in slots:
        result.slots.append(lineup.Slot(side=side, index=index, character=character, votes=4))
    return result


def make_rows(spec):
    """spec: [("ally", "昵称", "角色"), ...]"""
    return [SimpleNamespace(side=side, nickname=nickname, character=character,
                            inliers=0.0, margin=0.0) for side, nickname, character in spec]


class FillRowsTest(unittest.TestCase):
    def test_fills_single_missing_on_ally_side(self):
        rows = make_rows([("ally", "a", "千代"), ("ally", "b", ""), ("ally", "c", "星绘"),
                          ("ally", "d", "忧雾"), ("ally", "e", "米雪儿·李"),
                          ("enemy", "f", "香奈美")])
        result = make_result([("ally", i, c) for i, c in enumerate(["千代", "白墨", "星绘", "忧雾", "米雪儿·李"])]
                             + [("enemy", i, c) for i, c in enumerate(["香奈美", "白墨", "诺诺", "梅瑞狄斯", "珐格兰丝"])])
        filled = lineup.fill_rows(rows, result)
        self.assertEqual(filled, [("b", "白墨")])
        self.assertEqual(rows[1].character, "白墨")

    def test_does_not_fill_when_counts_disagree(self):
        rows = make_rows([("ally", "a", "千代"), ("ally", "b", ""), ("ally", "d", "")])
        result = make_result([("ally", i, c) for i, c in enumerate(["千代", "星绘", "忧雾", "米雪儿·李", "白墨"])])
        self.assertEqual(lineup.fill_rows(rows, result), [])
        self.assertEqual(rows[1].character, "")

    def test_skips_incomplete_lineup_side(self):
        rows = make_rows([("ally", "a", ""), ("enemy", "c", "香奈美")])
        result = make_result([("ally", 0, "千代"), ("ally", 1, "")])
        self.assertEqual(lineup.fill_rows(rows, result), [])

    def test_nothing_to_do_when_roster_complete(self):
        rows = make_rows([("ally", "a", "千代")])
        result = make_result([("ally", 0, "千代")])
        self.assertEqual(lineup.fill_rows(rows, result), [])

    def test_ignores_character_already_used(self):
        # 阵容条这一侧是 千代/白墨，但计分板已经认成 千代 + 空 → 白墨 补上
        rows = make_rows([("ally", "a", "千代"), ("ally", "b", "")])
        result = make_result([("ally", 0, "千代"), ("ally", 1, "白墨")])
        self.assertEqual(lineup.fill_rows(rows, result), [("b", "白墨")])


class ResultTest(unittest.TestCase):
    def test_counts(self):
        result = make_result([("ally", 0, "千代"), ("ally", 1, ""), ("enemy", 0, "香奈美")])
        self.assertEqual(result.recognized, 2)
        self.assertEqual(len(result.ally), 2)
        self.assertEqual(len(result.enemy), 1)
        payload = result.to_dict()
        self.assertEqual(payload["mode"], "lineup")
        self.assertEqual(payload["recognized"], 2)


if __name__ == "__main__":
    unittest.main()
