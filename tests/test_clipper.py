"""单测：切片窗口（前后各留几秒、重叠合并、片尾夹取）。

跑法：python src/run_tests.py
"""

from __future__ import annotations

import unittest

from strinova_star import clipper


def event(time, label="击杀", **extra):
    return {"time": time, "label": label, **extra}


class PlanTest(unittest.TestCase):
    def test_single_event_uses_default_window(self):
        [segment] = clipper.plan([event(100)], past=5, future=2)
        self.assertAlmostEqual(segment.start, 95)
        self.assertAlmostEqual(segment.end, 102)

    def test_custom_lengths_are_respected(self):
        [segment] = clipper.plan([event(100)], past=1.5, future=0.5)
        self.assertAlmostEqual(segment.start, 98.5)
        self.assertAlmostEqual(segment.end, 100.5)

    def test_start_is_clamped_to_zero(self):
        [segment] = clipper.plan([event(2)], past=8, future=2)
        self.assertAlmostEqual(segment.start, 0)
        self.assertAlmostEqual(segment.end, 4)

    def test_end_is_clamped_to_video_duration(self):
        [segment] = clipper.plan([event(8)], past=5, future=5, duration=10)
        self.assertAlmostEqual(segment.start, 3)
        self.assertAlmostEqual(segment.end, 10)

    def test_event_past_video_end_is_dropped(self):
        self.assertEqual(clipper.plan([event(12)], past=5, future=5, duration=10), [])

    def test_zero_length_window_is_dropped(self):
        self.assertEqual(clipper.plan([event(10)], past=0, future=0), [])

    def test_per_event_override(self):
        windows = clipper.plan([event(100, past=1, future=1), event(200)], past=5, future=2)
        self.assertAlmostEqual(windows[0].start, 99)
        self.assertAlmostEqual(windows[0].end, 101)
        self.assertAlmostEqual(windows[1].start, 195)
        self.assertAlmostEqual(windows[1].end, 202)

    def test_negative_length_is_rejected(self):
        with self.assertRaises(ValueError):
            clipper.plan([event(10)], past=-1, future=2)
        with self.assertRaises(ValueError):
            clipper.plan([event(10)], past=1, future=2, join_gap=-1)

    def test_non_numeric_length_is_rejected(self):
        with self.assertRaises(ValueError):
            clipper.plan([event(10)], past="五秒", future=2)

    def test_events_are_sorted_by_start(self):
        windows = clipper.plan([event(60), event(10)], past=1, future=1)
        self.assertEqual([round(segment.start) for segment in windows], [9, 59])


class MergeTest(unittest.TestCase):
    def test_overlapping_windows_merge(self):
        # 用户给的例子：击杀① 10s、击杀② 15s，前后各 5 秒 → 一段 5~20
        windows = clipper.plan([event(10), event(15)], past=5, future=5)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].start, 5)
        self.assertAlmostEqual(windows[0].end, 20)
        self.assertEqual(windows[0].count, 2)

    def test_touching_windows_merge(self):
        # [5,10] 与 [10,15] 首尾相接，也算一段
        windows = clipper.plan([event(10), event(15)], past=5, future=0)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].start, 5)
        self.assertAlmostEqual(windows[0].end, 15)

    def test_small_gap_stays_separate_by_default(self):
        # 10s 事件窗口 [5,10]，12s 事件窗口 [7,12]……这里用 future=0 造 1 秒空隙
        windows = clipper.plan([event(10), event(16)], past=5, future=0)
        self.assertEqual(len(windows), 2)

    def test_join_gap_bridges_small_gap(self):
        windows = clipper.plan([event(10), event(16)], past=5, future=0, join_gap=1.0)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].start, 5)
        self.assertAlmostEqual(windows[0].end, 16)

    def test_merge_keeps_the_longest_end(self):
        # 三个事件：中间那个的尾巴最长，合并后不能被吞掉
        windows = clipper.plan([event(10, future=20), event(12), event(14)], past=5, future=2)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].end, 30)

    def test_merge_respects_video_duration(self):
        windows = clipper.plan([event(9), event(11)], past=5, future=5, duration=12)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].start, 4)
        self.assertAlmostEqual(windows[0].end, 12)

    def test_three_events_merge_into_one(self):
        windows = clipper.plan([event(10), event(14), event(18)], past=5, future=5)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].count, 3)


class LabelTest(unittest.TestCase):
    def test_single_label(self):
        [segment] = clipper.plan([event(10, "击倒")], past=1, future=1)
        self.assertEqual(segment.label, "击倒")

    def test_same_kind_counts(self):
        [segment] = clipper.plan([event(10, "击杀"), event(12, "击杀")], past=5, future=5)
        self.assertEqual(segment.label, "击杀×2")

    def test_mixed_kinds_add_up(self):
        [segment] = clipper.plan([event(10, "击倒"), event(12, "击杀")], past=5, future=5)
        self.assertEqual(segment.label, "击倒+击杀")

    def test_falls_back_to_kind_then_victim(self):
        [segment] = clipper.plan([{"time": 10, "kind": "knockdown", "victim": "若南缘"}],
                                 past=1, future=1)
        self.assertEqual(segment.label, "knockdown")

    def test_empty_events_give_clip(self):
        [segment] = clipper.plan([{"time": 10}], past=1, future=1)
        self.assertEqual(segment.label, "clip")


class SegmentTest(unittest.TestCase):
    def test_duration_and_count(self):
        segment = clipper.Segment(start=5, end=12, events=[{}, {}])
        self.assertAlmostEqual(segment.duration, 7)
        self.assertEqual(segment.count, 2)

    def test_negative_duration_is_clamped(self):
        self.assertEqual(clipper.Segment(start=12, end=5).duration, 0)

    def test_str_mentions_span_and_count(self):
        text = str(clipper.Segment(start=5, end=20, label="击杀×2", events=[{}, {}]))
        self.assertIn("5.00s", text)
        self.assertIn("20.00s", text)
        self.assertIn("2 个事件", text)


class CutCommandTest(unittest.TestCase):
    """不真的跑 ffmpeg，只看命令拼得对不对。"""

    def test_input_seek_is_the_default(self):
        seen = _run_fake_cut(accurate=False)
        self.assertLess(seen.index("-ss"), seen.index("-i"))

    def test_accurate_mode_seeks_on_output(self):
        seen = _run_fake_cut(accurate=True)
        self.assertGreater(seen.index("-ss"), seen.index("-i"))

    def test_duration_flag_matches_segment(self):
        seen = _run_fake_cut(accurate=False)
        self.assertEqual(seen[seen.index("-t") + 1], "7.500")

    def test_keeps_every_stream(self):
        # 录屏有多条音轨（游戏 / 麦克风），不写 -map 0 切完就只剩第一条。
        seen = _run_fake_cut(accurate=False)
        self.assertIn("-map", seen)
        self.assertEqual(seen[seen.index("-map") + 1], "0")


def _run_fake_cut(accurate: bool) -> list[str]:
    """把 clipper 里的 subprocess 换成假实现，拿到拼好的命令参数（不碰真模块）。"""
    import types

    captured: dict[str, list[str]] = {}

    class _Done:
        returncode = 0
        stderr = ""

    def fake_run(command, **_kwargs):
        captured["command"] = [str(part) for part in command]
        return _Done()

    original = clipper.subprocess
    clipper.subprocess = types.SimpleNamespace(run=fake_run)
    try:
        clipper.cut("in.mp4", clipper.Segment(start=10, end=17.5),
                    "out.mp4", ffmpeg=_fake_ffmpeg(), accurate=accurate)
    finally:
        clipper.subprocess = original
    return captured["command"]


def _fake_ffmpeg():
    """随便找一个存在的东西当 ffmpeg，绕开 find_ffmpeg。"""
    return __import__("sys").executable


if __name__ == "__main__":
    unittest.main()
