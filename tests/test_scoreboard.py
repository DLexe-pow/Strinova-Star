"""scoreboard 的单测：不碰 OCR，用假数据测「挑行 / 分边 / 裁头像」三件事。"""

import unittest

from strinova_star import scoreboard


def line(text, x1, y1, x2=None, y2=None, score=0.95):
    return {
        "text": text,
        "score": score,
        "x1": x1,
        "y1": y1,
        "x2": x2 if x2 is not None else x1 + 120,
        "y2": y2 if y2 is not None else y1 + 24,
    }


class IsNameTest(unittest.TestCase):
    def test_normal_names(self):
        for name in ("穗织剑圣星绘绘", "z林籁泉韵z", "69岁扶墙电竞", "伊蕾Na", "依伴"):
            self.assertTrue(scoreboard.is_name(name), name)

    def test_headers_rejected(self):
        for text in ("击败/阵亡/助攻", "准备完毕", "成长等级", "主武器", "输出分", "我方", "剪刀手"):
            self.assertFalse(scoreboard.is_name(text), text)

    def test_numbers_rejected(self):
        for text in ("2/4/0", "10/0/9", "56", "0", "12:30", "100%"):
            self.assertFalse(scoreboard.is_name(text), text)

    def test_too_short(self):
        self.assertFalse(scoreboard.is_name("`"))
        self.assertFalse(scoreboard.is_name(""))


class RowsFromLinesTest(unittest.TestCase):
    column = (600, 400, 1100, 1300)

    def test_picks_name_rows_only(self):
        lines = [
            line("穗织忍者茉子", 680, 501),
            line("2/4/0", 954, 504),
            line("准备完毕", 1111, 550),
            line("想要得到彼此", 680, 552),
        ]
        rows = scoreboard.rows_from_lines(lines, self.column)
        self.assertEqual([r["text"] for r in rows], ["穗织忍者茉子", "想要得到彼此"])

    def test_outside_column_ignored(self):
        rows = scoreboard.rows_from_lines([line("场外文字", 100, 501)], self.column)
        self.assertEqual(rows, [])

    def test_low_confidence_ignored(self):
        rows = scoreboard.rows_from_lines([line("某个人名", 680, 501, score=0.3)], self.column)
        self.assertEqual(rows, [])

    def test_same_row_keeps_better_score(self):
        lines = [line("穗织忍者茉子", 680, 501, score=0.6), line("穗织忍者茉子", 679, 505, score=0.9)]
        rows = scoreboard.rows_from_lines(lines, self.column)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["score"], 0.9)

    def test_rows_sorted_by_y(self):
        lines = [line("乙乙", 680, 600), line("甲甲", 680, 501)]
        rows = scoreboard.rows_from_lines(lines, self.column)
        self.assertEqual([r["text"] for r in rows], ["甲甲", "乙乙"])


class SplitSidesTest(unittest.TestCase):
    def test_splits_on_big_gap(self):
        rows = [scoreboard.RosterRow(f"我方{i}", 500 + i * 48) for i in range(5)]
        rows += [scoreboard.RosterRow(f"对方{i}", 926 + i * 48) for i in range(5)]
        scoreboard.split_sides(rows)
        self.assertEqual(sum(r.side == "ally" for r in rows), 5)
        self.assertEqual(sum(r.side == "enemy" for r in rows), 5)

    def test_single_team_not_split(self):
        rows = [scoreboard.RosterRow(f"人{i}", 500 + i * 48) for i in range(5)]
        scoreboard.split_sides(rows)
        self.assertTrue(all(r.side == "" for r in rows))

    def test_single_row_does_nothing(self):
        rows = [scoreboard.RosterRow("孤独", 500)]
        scoreboard.split_sides(rows)
        self.assertEqual(rows[0].side, "")


class PortraitBoxTest(unittest.TestCase):
    def test_box_sits_left_of_name(self):
        row = line("穗织忍者茉子", 680, 501, 821, 525)
        x1, y1, x2, y2 = scoreboard.portrait_box(row)
        self.assertEqual(x2, 680 - scoreboard.PORTRAIT_GAP)
        self.assertEqual(x2 - x1, scoreboard.PORTRAIT_W)
        self.assertLess(y1, 513)
        self.assertGreater(y2, 513)

    def test_box_grows_with_line_height(self):
        small = scoreboard.portrait_box(line("甲", 680, 500, 700, 510))
        big = scoreboard.portrait_box(line("甲", 680, 500, 700, 560))
        self.assertGreater(big[3] - big[1], small[3] - small[1])


class ScoreboardTest(unittest.TestCase):
    def test_ally_enemy_properties(self):
        board = scoreboard.Scoreboard(rows=[
            scoreboard.RosterRow("甲", 500, "ally", "心夏", 30, 20),
            scoreboard.RosterRow("乙", 600, "enemy", "令", 25, 10),
        ])
        self.assertEqual([r.nickname for r in board.ally], ["甲"])
        self.assertEqual([r.nickname for r in board.enemy], ["乙"])

    def test_to_dict(self):
        board = scoreboard.Scoreboard(rows=[scoreboard.RosterRow("甲", 500.4, "ally", "心夏", 30, 20)])
        data = board.to_dict()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["rows"][0]["character"], "心夏")
        self.assertEqual(data["rows"][0]["y"], 500.4)


if __name__ == "__main__":
    unittest.main()
