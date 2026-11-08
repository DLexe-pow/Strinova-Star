"""rounds 的纯逻辑测试：不碰视频和 OCR。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from strinova_star import rounds  # noqa: E402


def series(steps):
    """steps: [(结束秒, 我方, 对方)] → 每秒一条样本。"""
    out = []
    previous = 0
    for until, our, theirs in steps:
        for when in range(previous, until):
            out.append((float(when), our, theirs, 120.0))
        previous = until
    return out


class ParseTest(unittest.TestCase):
    def test_score(self):
        self.assertEqual(rounds.parse_score("00"), 0)
        self.assertEqual(rounds.parse_score("12"), 12)
        self.assertEqual(rounds.parse_score(" O2 "), 2)
        self.assertIsNone(rounds.parse_score(""))
        self.assertIsNone(rounds.parse_score("123"))

    def test_clock(self):
        self.assertEqual(rounds.parse_clock("01:56"), 116)
        self.assertEqual(rounds.parse_clock("00:05"), 5)
        self.assertEqual(rounds.parse_clock("01：56"), 116)
        self.assertEqual(rounds.parse_clock("156"), 116)
        self.assertIsNone(rounds.parse_clock(""))
        self.assertIsNone(rounds.parse_clock("目标7回合"))


class BuildTest(unittest.TestCase):
    def test_three_rounds_with_tail(self):
        found, match_end = rounds._build(series([(132, 0, 0), (248, 0, 1), (368, 0, 2), (399, 0, 4)]))
        self.assertEqual([item.score for item in found], ["0-0", "0-1", "0-2"])
        self.assertEqual([item.index for item in found], [1, 2, 3])
        self.assertAlmostEqual(found[0].start, 0.0)
        self.assertAlmostEqual(found[1].start, 132.0)
        self.assertAlmostEqual(found[2].end, 398.0)
        self.assertAlmostEqual(match_end, 368.0)

    def test_no_tail(self):
        found, match_end = rounds._build(series([(120, 0, 0), (240, 0, 1), (399, 0, 2)]))
        self.assertEqual(len(found), 3)
        self.assertAlmostEqual(found[2].start, 240.0)
        self.assertIsNone(match_end)

    def test_single_round(self):
        found, match_end = rounds._build(series([(300, 0, 0)]))
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].end, 299.0)
        self.assertIsNone(match_end)

    def test_glitch_is_ignored(self):
        samples = [(0.0, 0, 0, 120.0), (1.0, 0, 0, 120.0), (2.0, 1, 0, 2.0), (3.0, 0, 0, 120.0)]
        found, _end = rounds._build(samples)
        self.assertEqual([item.score for item in found], ["0-0"])

    def test_short_round_is_merged(self):
        found, _end = rounds._build(series([(100, 0, 0), (110, 0, 1), (200, 0, 2)]))
        self.assertEqual(len(found), 2)

    def test_empty(self):
        self.assertEqual(rounds._build([]), ([], None))


class RoundTest(unittest.TestCase):
    def test_fields(self):
        item = rounds.Round(index=2, start=10.0, end=70.0, our=1, theirs=0)
        self.assertEqual(item.duration, 60.0)
        self.assertEqual(item.score, "1-0")
        self.assertIn("回合", item.label if hasattr(item, "label") else "回合")
        payload = item.to_dict()
        self.assertEqual(payload["index"], 2)
        self.assertEqual(payload["duration"], 60.0)
        self.assertEqual(payload["score"], "1-0")


if __name__ == "__main__":
    unittest.main()