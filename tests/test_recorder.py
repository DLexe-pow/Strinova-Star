"""录制模块的单测：只看命令拼得对不对、状态算得对不对，不真的开 ffmpeg。"""

import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

import atexit
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strinova_star import recorder  # noqa: E402


def make_config(**overrides):
    raw = {"ffmpeg": _fake_ffmpeg()}
    raw.update(overrides)
    return recorder.settings_from(raw)


def _fake_ffmpeg():
    return sys.executable



def temp_dir() -> Path:
    """建一个临时目录，进程退出时自己删。

    这台机器的 TEMP 有时不可写（沙箱/权限），tempfile 会退回到当前目录建
    `tmpXXXX`，跑一次测试就在项目根目录留一堆空目录 —— 所以统一走这里，
    并且用 atexit 兜底清理。
    """
    path = Path(tempfile.mkdtemp(prefix="strinova-test-"))
    atexit.register(shutil.rmtree, path, True)
    return path

class BuildCommandTest(unittest.TestCase):
    def test_defaults_are_120fps_cq26(self):
        config = make_config()
        self.assertEqual(config["fps"], 120)
        self.assertEqual(config["cq"], 26)
        command = recorder.build_command("ffmpeg", Path("D:/rec"), config)
        self.assertEqual(command[command.index("-cq") + 1], "26")
        self.assertIn("ddagrab=0:framerate=120", " ".join(command))

    def test_segment_list_and_mkv(self):
        command = recorder.build_command("ffmpeg", Path("D:/rec"), make_config())
        self.assertIn("-f", command)
        self.assertEqual(command[command.index("-segment_list") + 1], str(Path("D:/rec") / "seg.csv"))
        self.assertEqual(command[command.index("-reset_timestamps") + 1], "0")
        self.assertTrue(command[-1].endswith("seg-%04d.mkv"))

    def test_maps_every_audio_device(self):
        config = make_config(record_audio_devices=["麦克风", "CABLE Output"])
        command = recorder.build_command("ffmpeg", Path("D:/rec"), config)
        self.assertEqual(command.count("-map"), 3)          # 视频 + 2 条音轨
        self.assertIn("audio=麦克风", command)
        self.assertIn("audio=CABLE Output", command)
        self.assertIn("title=麦克风", command)

    def test_no_audio_means_only_video_map(self):
        command = recorder.build_command("ffmpeg", Path("D:/rec"), make_config())
        self.assertEqual(command.count("-map"), 1)
        self.assertEqual(command[command.index("-map") + 1], "0:v")

    def test_desktop_audio_pipe_becomes_first_track(self):
        config = make_config(record_desktop_audio=True,
                             desktop_audio_pipe=r"\\.\pipe\test-audio")
        command = recorder.build_command("ffmpeg", Path("D:/rec"), config)
        self.assertEqual(command[command.index("-f") + 1], "lavfi")
        self.assertIn("-ac", command)
        self.assertEqual(command[command.index("-ac") + 1], "2")
        self.assertIn(r"\\.\pipe\test-audio", command)
        self.assertEqual(command.count("-map"), 2)
        self.assertEqual(command[command.index("-map") + 1], "0:v")
        self.assertEqual(command[command.index("-map", command.index("-map") + 1) + 1], "1:a")
        self.assertIn("title=桌面声音", command)

    def test_desktop_audio_then_microphones_number_inputs(self):
        config = make_config(record_desktop_audio=True, desktop_audio_pipe=r"\\.\pipe\a",
                             record_audio_devices=["麦克风"])
        command = recorder.build_command("ffmpeg", Path("D:/rec"), config)
        self.assertEqual(command.count("-map"), 3)
        maps = [command[index + 1] for index, item in enumerate(command) if item == "-map"]
        self.assertEqual(maps, ["0:v", "1:a", "2:a"])
        self.assertIn("title=桌面声音", command)
        self.assertIn("title=麦克风", command)

    def test_pipe_ignored_when_desktop_audio_off(self):
        config = make_config(record_desktop_audio=False, desktop_audio_pipe=r"\\.\pipe\a")
        command = recorder.build_command("ffmpeg", Path("D:/rec"), config)
        self.assertEqual(command.count("-map"), 1)
        self.assertNotIn(r"\\.\pipe\a", command)

    def test_keyframe_follows_fps_and_seconds(self):
        command = recorder.build_command("ffmpeg", Path("D:/rec"),
                                         make_config(record_fps=60, record_keyframe=2))
        self.assertEqual(command[command.index("-g") + 1], "120")

    def test_fixed_resolution_adds_scale(self):
        command = recorder.build_command("ffmpeg", Path("D:/rec"),
                                         make_config(record_resolution="1920x1080"))
        self.assertEqual(command[command.index("-vf") + 1], "scale=1920:1080")

    def test_auto_resolution_has_no_scale(self):
        command = recorder.build_command("ffmpeg", Path("D:/rec"), make_config())
        self.assertNotIn("-vf", command)

    def test_vbr_uses_target_bitrate_not_cq(self):
        command = recorder.build_command(
            "ffmpeg", Path("D:/rec"),
            make_config(record_rate_control="vbr", record_bitrate=60))
        self.assertIn("-b:v", command)
        self.assertNotIn("-cq", command)
        self.assertEqual(command[command.index("-b:v") + 1], "60M")


class SettingsTest(unittest.TestCase):
    def test_clamps_out_of_range_values(self):
        config = make_config(record_fps=1000, record_cq=99, record_segment=0.1)
        self.assertEqual(config["fps"], 240)
        self.assertEqual(config["cq"], 40)
        self.assertEqual(config["segment"], 2)

    def test_unknown_encoder_falls_back(self):
        self.assertEqual(make_config(record_encoder="nope")["encoder"], "hevc_nvenc")

    def test_bad_numbers_use_defaults(self):
        config = make_config(record_fps="很快")
        self.assertEqual(config["fps"], 120)


class SegmentListTest(unittest.TestCase):
    def setUp(self):
        self.folder = temp_dir()
        (self.folder / "seg.csv").write_text(
            "seg-0000.mkv,0.000000,9.990000\nseg-0001.mkv,9.990000,19.980000\n",
            encoding="utf-8")

    def test_reads_both_segments(self):
        rows = recorder.read_segments(self.folder)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["name"], "seg-0001.mkv")
        self.assertAlmostEqual(rows[1]["end"], 19.98)

    def test_missing_list_is_empty(self):
        empty = temp_dir()
        self.assertEqual(recorder.read_segments(empty), [])

    def test_broken_lines_are_skipped(self):
        (self.folder / "seg.csv").write_text("seg-0000.mkv,oops\nfine.mkv,1,2\n", encoding="utf-8")
        self.assertEqual([row["name"] for row in recorder.read_segments(self.folder)], ["fine.mkv"])

    def test_folder_size_counts_mkv_only(self):
        (self.folder / "a.mkv").write_bytes(b"x" * 100)
        (self.folder / "b.txt").write_bytes(b"y" * 50)
        self.assertEqual(recorder.folder_size(self.folder), 100)


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()

    def _session(self, name, age_hours):
        folder = self.root / name
        folder.mkdir()
        stamp = time.time() - age_hours * 3600
        os.utime(folder, (stamp, stamp))
        return folder

    def test_drops_sessions_older_than_keep_hours(self):
        old = self._session("old", 30)
        fresh = self._session("fresh", 1)
        removed = recorder.cleanup(self.root, keep_hours=10, min_free_gb=0)
        self.assertEqual(removed, ["old"])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_protect_keeps_current_session(self):
        old = self._session("old", 30)
        removed = recorder.cleanup(self.root, keep_hours=10, min_free_gb=0, protect=old)
        self.assertEqual(removed, [])
        self.assertTrue(old.exists())

    def test_zero_keep_hours_means_keep_forever(self):
        self._session("old", 999)
        self.assertEqual(recorder.cleanup(self.root, keep_hours=0, min_free_gb=0), [])

    def test_default_keeps_everything(self):
        # 默认不再按时长删：只有磁盘快满时才动旧文件
        self.assertEqual(recorder.DEFAULT_KEEP_HOURS, 0.0)
        self._session("old", 5000)
        self.assertEqual(recorder.cleanup(self.root, min_free_gb=0), [])

    def test_cleanup_writes_a_log(self):
        self._session("old", 30)
        log = self.root / "cleanup.log"
        removed = recorder.cleanup(self.root, keep_hours=10, min_free_gb=0, log_path=log)
        self.assertEqual(removed, ["old"])
        text = log.read_text(encoding="utf-8")
        self.assertIn("old", text)
        self.assertIn("超过保留时长", text)

    def test_cleanup_without_log_still_works(self):
        self._session("old", 30)
        self.assertEqual(recorder.cleanup(self.root, keep_hours=10, min_free_gb=0), ["old"])


class GroupSegmentsTest(unittest.TestCase):
    """单局分割：一局一个文件，选人/加载画面归到它后面那一局。"""

    def seg(self, start, end, name="seg.mkv"):
        return {"name": name, "start": start, "end": end}

    def test_uses_the_middle_of_each_segment(self):
        rows = [self.seg(0, 10), self.seg(10, 20)]
        spans = [{"match": "b", "start": 9.0, "end": 100.0}]
        groups = recorder.group_segments(rows, spans)
        self.assertEqual([(match, len(items)) for match, items in groups], [("b", 2)])

    def test_pregame_screen_goes_to_the_next_match(self):
        rows = [self.seg(0, 10), self.seg(10, 20), self.seg(20, 30)]
        spans = [{"match": "b", "start": 18.0, "end": 100.0}]
        groups = recorder.group_segments(rows, spans)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], "b")
        self.assertEqual(len(groups[0][1]), 3)

    def test_between_matches_goes_to_the_next_one(self):
        rows = [self.seg(0, 10), self.seg(10, 20), self.seg(20, 30), self.seg(30, 40)]
        spans = [{"match": "a", "start": 1.0, "end": 15.0},
                 {"match": "b", "start": 35.0, "end": 100.0}]
        groups = recorder.group_segments(rows, spans)
        self.assertEqual([match for match, _ in groups], ["a", "b"])
        self.assertEqual([len(items) for _, items in groups], [1, 3])

    def test_trailing_segments_stay_with_the_last_match(self):
        rows = [self.seg(0, 10), self.seg(10, 20)]
        spans = [{"match": "a", "start": 1.0, "end": 8.0}]
        groups = recorder.group_segments(rows, spans)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0][1]), 2)

    def test_far_away_preroll_is_not_glued_to_the_match(self):
        # 在菜单里挂了十分钟才开打，那段不该算进这一局
        rows = [self.seg(0, 10), self.seg(600, 610)]
        spans = [{"match": "a", "start": 608.0, "end": 1000.0}]
        groups = recorder.group_segments(rows, spans)
        self.assertEqual([match for match, _ in groups], ["", "a"])

    def test_long_tail_after_last_match_is_separate(self):
        rows = [self.seg(0, 10), self.seg(500, 510)]
        spans = [{"match": "a", "start": 1.0, "end": 8.0}]
        groups = recorder.group_segments(rows, spans)
        self.assertEqual([match for match, _ in groups], ["a", ""])

    def test_without_spans_everything_is_one_group(self):
        rows = [self.seg(0, 10)]
        self.assertEqual(recorder.group_segments(rows, []), [("", rows)])

    def test_no_segments(self):
        self.assertEqual(recorder.group_segments([], [{"match": "a", "start": 0, "end": 1}]), [])


class StopTest(unittest.TestCase):
    """停止录制：拼接命令必须 -map 0，拼完按设置删不删段。"""

    def setUp(self):
        self.folder = temp_dir()
        for index in range(2):
            (self.folder / f"seg-{index:04d}.mkv").write_bytes(b"fake")
        (self.folder / "seg.csv").write_text(
            "seg-0000.mkv,0,10\nseg-0001.mkv,10,20\n", encoding="utf-8")
        self.calls = []
        self.listings = []

    def _run(self, keep_segments):
        captured = self.calls

        class _Done:
            returncode = 0
            stderr = ""

        def fake_run(command, **_kwargs):
            parts = [str(part) for part in command]
            captured.append(parts)
            if "-i" in parts:                 # concat 列表读出来留着断言
                listing = Path(parts[parts.index("-i") + 1])
                if listing.exists():
                    self.listings.append(listing.read_text(encoding="utf-8"))
            (self.folder / "full.mkv").write_bytes(b"x" * 10)
            return _Done()

        original = recorder.subprocess
        recorder.subprocess = types.SimpleNamespace(
            run=fake_run, Popen=original.Popen, CREATE_NO_WINDOW=0)
        # Recorder 收的是界面原始设置（record_* 这些键），不是归一化后的配置
        raw = {"record_keep_segments": keep_segments, "record_fps": 30}
        try:
            machine = recorder.Recorder(self.folder, raw)
            machine.folder = self.folder
            machine.started = time.time() - 20
            machine.process = _FakeProcess()
            target = machine.stop()
        finally:
            recorder.subprocess = original
        return target

    def test_concat_keeps_every_stream(self):
        self._run(keep_segments=True)
        command = self.calls[0]
        self.assertEqual(command[command.index("-map") + 1], "0")
        self.assertEqual(command[command.index("-f") + 1], "concat")

    def test_concat_writes_durations(self):
        # 段文件带全局时间戳，不写 duration 时拼接结果会翻倍
        self._run(keep_segments=True)
        listing = self.listings[0]
        self.assertIn("duration 10.000", listing)
        self.assertEqual(listing.count("file '"), 2)

    def test_segments_are_deleted_by_default(self):
        self._run(keep_segments=False)
        self.assertFalse((self.folder / "seg-0000.mkv").exists())
        self.assertTrue((self.folder / "full.mkv").exists())

    def test_keep_segments_option_keeps_them(self):
        self._run(keep_segments=True)
        self.assertTrue((self.folder / "seg-0000.mkv").exists())


class _FakeProcess:
    """假装 ffmpeg 还在跑：stop() 只需要 stdin 能写、wait 能返回。"""

    stdin = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


if __name__ == "__main__":
    unittest.main()
