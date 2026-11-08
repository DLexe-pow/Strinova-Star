"""播报解析里不依赖 OCR / 视频的部分：昵称比对、头像框、头像认角色。"""

import unittest

import numpy as np

from strinova_star import feed


class FakeMatcher:
    """假识别器：只关心拿到没拿到裁片，返回固定结果。"""

    def __init__(self, name="甲", score=40.0):
        self.name = name
        self.score = score
        self.crops = []

    def match(self, image, min_score=None, min_margin=None):
        if image is None or image.size == 0:
            return "", 0.0
        self.crops.append((image.shape, min_score, min_margin))
        return (self.name, self.score) if self.score >= (min_score or 0) else ("", self.score)


def row(y=100.0):
    return feed.FeedRow(y=y, icons=[feed.FeedIcon("kill", 200, int(y), 20, 20, 0.9)])


class NameScoreTest(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(feed.name_score("若南缘", "若南缘"), 1.0)

    def test_contained(self):
        self.assertEqual(feed.name_score("若南缘", "若南缘123"), 1.0)

    def test_unrelated(self):
        self.assertLess(feed.name_score("若南缘", "地球大统领"), 0.5)

    def test_empty(self):
        self.assertEqual(feed.name_score("", "若南缘"), 0.0)


class MatchNameTest(unittest.TestCase):
    def test_one_char_variant_matches(self):
        # 同一块牌子读两遍：薪膜 / 蒜膜
        names = ["薪膜", "超糖猫猫", "九月不可爱吗"]
        self.assertEqual(feed.match_name("蒜膜", names), "薪膜")

    def test_messy_english_name_matches(self):
        # 英文部分抖成 Lbili…oN，汉字「小绘都/小绘郁」只差一个
        names = ["bilibili小绘郁", "超糖猫猫", "欺软怕硬"]
        self.assertEqual(feed.match_name("Lbili小绘都oN", names), "bilibili小绘郁")

    def test_unique_weak_match_still_rejects_garbage(self):
        names = ["薪膜", "超糖猫猫", "九月不可爱吗"]
        self.assertEqual(feed.match_name("随便一个人", names), "")

    NAMES = ["地球大统领", "若南缘", "AAA猫猫侠代购喵"]

    def test_exact(self):
        self.assertEqual(feed.match_name("若南缘", self.NAMES), "若南缘")

    def test_ocr_noise(self):
        self.assertEqual(feed.match_name("若南", self.NAMES), "若南缘")

    def test_trailing_digits_ignored(self):
        self.assertEqual(feed.match_name("地球大统领·", self.NAMES), "地球大统领")

    def test_unknown_returns_empty(self):
        self.assertEqual(feed.match_name("完全不相干的名字", self.NAMES), "")

    def test_no_table_returns_empty(self):
        self.assertEqual(feed.match_name("若南缘", []), "")


class SameNameTest(unittest.TestCase):
    def test_contains(self):
        self.assertTrue(feed.same_name("若南缘", "若南缘"))
        self.assertTrue(feed.same_name("若南", "若南缘"))

    def test_short_name_tolerance(self):
        self.assertTrue(feed.same_name("若南缘", "若楠缘"))

    def test_different(self):
        self.assertFalse(feed.same_name("若南缘", "地球大统领"))


class PortraitBoxTest(unittest.TestCase):
    def test_box_is_square_and_left(self):
        x1, y1, x2, y2 = feed.portrait_box(100.0, 1.0)
        self.assertEqual(x2 - x1, int(feed.PORTRAIT_SIDE))
        self.assertEqual(y2 - y1, x2 - x1)
        self.assertLess(x1, 60)

    def test_box_scales_with_frame_height(self):
        small = feed.portrait_box(100.0, 1.0)
        big = feed.portrait_box(100.0, 1.5)
        self.assertGreater(big[2] - big[0], small[2] - small[0])
        self.assertGreater(big[0], small[0])

    def test_box_follows_row(self):
        self.assertLess(feed.portrait_box(100.0, 1.0)[1], feed.portrait_box(200.0, 1.0)[1])


class ReadCharacterTest(unittest.TestCase):
    def test_reads_killer_portrait(self):
        patch = np.zeros((400, 600, 3), np.uint8)
        matcher = FakeMatcher("艾卡", 64.0)
        self.assertEqual(feed.read_character(patch, row(100.0), 1.0, matcher), ("艾卡", 64.0))
        self.assertEqual(len(matcher.crops), 1)

    def test_crop_sits_at_left_of_row(self):
        patch = np.zeros((400, 600, 3), np.uint8)
        matcher = FakeMatcher()
        feed.read_character(patch, row(100.0), 1.0, matcher)
        (height, width, _), _, _ = matcher.crops[0]
        self.assertEqual(width, height)

    def test_too_slow_crop_gives_nothing(self):
        patch = np.zeros((400, 600, 3), np.uint8)
        # 行在画面外（比如刚滑进来的第一行），裁不到东西就不能瞎认
        self.assertEqual(feed.read_character(patch, row(-200.0), 1.0, FakeMatcher()), ("", 0.0))

    def test_low_score_rejected(self):
        patch = np.zeros((400, 600, 3), np.uint8)
        self.assertEqual(feed.read_character(patch, row(100.0), 1.0, FakeMatcher(score=5.0)),
                         ("", 5.0))


class FeedRowDictTest(unittest.TestCase):
    def test_character_fields_present(self):
        data = feed.FeedRow(y=1.0).to_dict()
        self.assertIn("killer_character", data)
        self.assertIn("victim_character", data)
        self.assertIn("killer_matched", data)


class TruncatedNameTest(unittest.TestCase):
    names = ["香草一之濑帆波", "何人在弹奏春日影", "腰射大王赫卡忒", "磨莫", "白墨", "拉薇"]

    def test_short_read_maps_back_to_full_name(self):
        self.assertEqual(feed.match_name("香草之帆", self.names), "香草一之濑帆波")

    def test_prefix_read_maps_back(self):
        self.assertEqual(feed.match_name("何人在弹奏", self.names), "何人在弹奏春日影")

    def test_exact_name_stays(self):
        self.assertEqual(feed.match_name("腰射大王赫卡忒", self.names), "腰射大王赫卡忒")

    def test_single_character_is_not_a_name(self):
        self.assertEqual(feed.match_name("拉", self.names), "")

    def test_unknown_text_gives_nothing(self):
        self.assertEqual(feed.match_name("完全不存在", self.names), "")

    def test_hud_text_glued_after_name_is_trimmed(self):
        names = ["腰射大王赫卡忒", "香草一之濑帆波"]
        self.assertEqual(feed.match_name("腰射大王欧泊击败阵亡/助攻绝", names), "腰射大王赫卡忒")

    def test_short_read_of_a_long_name_is_recovered(self):
        names = ["辰星star", "磨莫", "腰射大王赫卡忒"]
        self.assertEqual(feed.match_name("星a", names), "辰星star")

    def test_trimmed_prefix_tie_is_still_refused(self):
        names = ["香草一二", "香草一三"]
        self.assertEqual(feed.match_name("香草一击败阵亡", names), "")

    def test_ambiguous_truncation_is_refused(self):
        names = ["香草一二", "香草一三"]
        self.assertEqual(feed.match_name("香草一", names), "")


class CanonicalizeTest(unittest.TestCase):
    def test_rewrites_victim_and_keeps_raw(self):
        events = [{"time": 111.0, "label": "击倒", "victim": "香草之帆", "killer": "磨莫"}]
        changed = feed.canonicalize_events(events, ["香草一之濑帆波", "磨莫"])
        self.assertEqual(changed, 1)
        self.assertEqual(events[0]["victim"], "香草一之濑帆波")
        self.assertEqual(events[0]["victim_raw"], "香草之帆")
        self.assertEqual(events[0]["killer"], "磨莫")

    def test_no_names_means_no_change(self):
        events = [{"victim": "香草之帆"}]
        self.assertEqual(feed.canonicalize_events(events, []), 0)
        self.assertEqual(events[0]["victim"], "香草之帆")

    def test_rerun_is_idempotent(self):
        events = [{"victim": "香草之帆"}]
        names = ["香草一之濑帆波"]
        feed.canonicalize_events(events, names)
        self.assertEqual(feed.canonicalize_events(events, names), 0)



if __name__ == "__main__":
    unittest.main()
