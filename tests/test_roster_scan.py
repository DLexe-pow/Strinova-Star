"""roster_scan 的多帧投票：不碰 OCR、不碰视频。"""

import unittest
from types import SimpleNamespace

from strinova_star import roster_scan

rounds_nickname = roster_scan.nickname_score


def board(*rows, scale=(1.3333, 1.3333)):
    """造一块假的计分板：每行 (side, nickname, character, inliers)。"""
    return SimpleNamespace(scale=list(scale), rows=[
        SimpleNamespace(nickname=nickname, side=side, character=character,
                        inliers=inliers, margin=inliers / 4)
        for side, nickname, character, inliers in rows
    ])


class NicknameScoreTest(unittest.TestCase):
    def test_one_char_variant_counts_as_same(self):
        # 同一块牌子读两遍，形近字飘一个：残之月冥 / 残之月寞
        self.assertEqual(rounds_nickname("残之月冥", "残之月寞"), 1.0)
        self.assertEqual(rounds_nickname("一阶收尾人罗兰", "一阶收尾人萝兰"), 1.0)
        self.assertLess(rounds_nickname("张三", "张四"), 1.0)

    def test_same_name(self):
        self.assertEqual(roster_scan.nickname_score("若南缘", "若南缘"), 1.0)

    def test_contained_name(self):
        self.assertEqual(roster_scan.nickname_score("若南缘", "若南缘123"), 1.0)

    def test_different_name(self):
        self.assertLess(roster_scan.nickname_score("若南缘", "地球大统领"), 0.5)

    def test_empty_name(self):
        self.assertEqual(roster_scan.nickname_score("", "若南缘"), 0.0)

    def test_ignores_spaces(self):
        self.assertEqual(roster_scan.nickname_score(" AA 猫猫", "AA猫猫"), 1.0)


class PickCandidatesTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(roster_scan.pick_candidates([]), [])

    def test_one_session_takes_both_ends(self):
        # 连着几秒都开着计分板：同一张图，只取首尾两帧，不浪费后面的识别时间
        picked = roster_scan.pick_candidates([10.0, 11.0, 12.0])
        self.assertEqual(picked, [10.0, 12.0])

    def test_two_sessions_each_get_one(self):
        picked = roster_scan.pick_candidates([10.0, 11.0, 60.0, 61.0])
        self.assertEqual(picked, [11.0, 61.0])

    def test_sessions_round_robin(self):
        # 两次开 Tab 只取各一帧；只有不短于 3 帧的那次才多给一帧（首帧）
        picked = roster_scan.pick_candidates([10.0, 10.5, 10.8, 60.0, 100.0])
        self.assertEqual(len(picked), 4)
        self.assertIn(60.0, picked)
        self.assertIn(100.0, picked)

    def test_respects_limit(self):
        picked = roster_scan.pick_candidates([10.0, 100.0, 200.0], max_candidates=2)
        self.assertEqual(len(picked), 2)

    def test_sorted_output(self):
        picked = roster_scan.pick_candidates([100.0, 10.0])
        self.assertEqual(picked, [10.0, 100.0])


class MergeBoardsTest(unittest.TestCase):
    def test_single_board_passes_through(self):
        slots, when = roster_scan.merge_boards([
            (12.0, board(("ally", "若南缘", "千代", 40), ("enemy", "地球大统领", "艾卡", 30)))])
        self.assertEqual(when, 12.0)
        self.assertEqual([slot.character for slot in slots], ["千代", "艾卡"])
        self.assertEqual(slots[0].inliers, 40)

    def test_votes_fill_missing_avatar(self):
        # 第一块红叉压住 若南缘，第二块压住 地球大统领 —— 合起来两个都认得出
        slots, _ = roster_scan.merge_boards([
            (10.0, board(("ally", "若南缘", "", 0), ("enemy", "地球大统领", "艾卡", 30))),
            (60.0, board(("ally", "若南缘", "千代", 50), ("enemy", "地球大统领", "", 0))),
        ])
        chars = {slot.nickname: slot.character for slot in slots}
        self.assertEqual(chars["若南缘"], "千代")
        self.assertEqual(chars["地球大统领"], "艾卡")
        self.assertEqual(len(slots), 2)

    def test_majority_wins(self):
        slots, _ = roster_scan.merge_boards([
            (10.0, board(("ally", "若南缘", "千代", 20))),
            (60.0, board(("ally", "若南缘", "千代", 20))),
            (100.0, board(("ally", "若南缘", "白墨", 90))),
        ])
        self.assertEqual(slots[0].character, "千代")

    def test_tie_goes_to_higher_inliers(self):
        slots, _ = roster_scan.merge_boards([
            (10.0, board(("ally", "若南缘", "千代", 20))),
            (60.0, board(("ally", "若南缘", "白墨", 60))),
        ])
        self.assertEqual(slots[0].character, "白墨")

    def test_ocr_variant_names_merged(self):
        # 同一块计分板在两次里 OCR 出了一点差异，也得算同一个人
        slots, _ = roster_scan.merge_boards([
            (10.0, board(("ally", "地球大统领", "艾卡", 20))),
            (60.0, board(("ally", "地球大统", "艾卡", 20))),
        ])
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].nickname, "地球大统领")

    def test_sides_never_merged(self):
        slots, _ = roster_scan.merge_boards([
            (10.0, board(("ally", "白墨", "白墨", 20), ("enemy", "白墨", "白墨", 20)))])
        self.assertEqual([slot.side for slot in slots], ["ally", "enemy"])

    def test_reference_time_is_clearest_board(self):
        _, when = roster_scan.merge_boards([
            (10.0, board(("ally", "甲", "", 0))),
            (60.0, board(("ally", "甲", "千代", 40))),
        ])
        self.assertEqual(when, 60.0)

    def test_average_inliers(self):
        slots, _ = roster_scan.merge_boards([
            (10.0, board(("ally", "若南缘", "千代", 40))),
            (60.0, board(("ally", "若南缘", "千代", 60))),
        ])
        self.assertEqual(slots[0].inliers, 50.0)


if __name__ == "__main__":
    unittest.main()
