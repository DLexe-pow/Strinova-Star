"""旁路采样里能脱离屏幕和 ffmpeg 测的部分。"""

import unittest

from strinova_star import livefeed


class ParseMatchIdTest(unittest.TestCase):
    def test_reads_the_leading_number(self):
        text = "765071518099047823 C1.18.6.11 S1.18.6.12"
        self.assertEqual(livefeed.parse_match_id(text), "765071518099047823")

    def test_trims_to_eighteen_digits(self):
        self.assertEqual(livefeed.parse_match_id("765071518099047823999"),
                         "765071518099047823")

    def test_skips_short_numbers(self):
        self.assertEqual(livefeed.parse_match_id("C1.18.6.11 S1.18.6.12"), "")

    def test_ignores_sixteen_digit_reads(self):
        # 实测计分板上认成过 16 位，不能当成合法编号
        self.assertEqual(livefeed.parse_match_id("7643635531885455 1.186.1"), "")

    def test_same_id_tolerates_one_bad_character(self):
        self.assertTrue(livefeed.same_id("111111111111111111", "111111111111111112"))
        self.assertFalse(livefeed.same_id("111111111111111111", "222222222222222222"))
        self.assertFalse(livefeed.same_id("111111111111111111", ""))

    def test_skips_garbage(self):
        self.assertEqual(livefeed.parse_match_id("765071518099047823x").count("x"), 0)
        self.assertEqual(livefeed.parse_match_id("765071518099047823"), "765071518099047823")


class CropBoxTest(unittest.TestCase):
    def test_scales_by_height(self):
        # 2560×1600 是标定分辨率，缩放 1.4815
        self.assertEqual(livefeed.crop_box((2560, 1600)), (39, 81, 344, 130))

    def test_1080p_is_identity(self):
        self.assertEqual(livefeed.crop_box((1920, 1080)), (26, 55, 232, 88))

    def test_box_inside_frame(self):
        x1, y1, x2, y2 = livefeed.crop_box((1280, 800))
        self.assertGreaterEqual(x1, 0)
        self.assertLessEqual(x2, 1280)
        self.assertLessEqual(y2, 800)


class SamplerCommandTest(unittest.TestCase):
    def test_frame_size_matches_the_filter(self):
        size = (2560, 1600)
        command = livefeed.sampler_command("ffmpeg", size)
        width, height = livefeed.frame_size(size)
        vf = command[command.index("-vf") + 1]
        crop = next(part for part in vf.split(",") if part.startswith("crop="))
        box_width, box_height = [int(value) for value in crop.split("=")[1].split(":")[:2]]
        self.assertEqual(box_width * livefeed.SCALE, width)
        self.assertEqual(box_height * livefeed.SCALE, height)

    def test_scale_is_applied_in_the_filter(self):
        size = (2560, 1600)
        command = livefeed.sampler_command("ffmpeg", size)
        width, height = livefeed.frame_size(size)
        vf = command[command.index("-vf") + 1]
        self.assertIn(f"scale={width}:{height}", vf)
        # 显卡帧得先下载到内存，否则 crop/scale 接不上
        self.assertTrue(vf.startswith("hwdownload"))
        self.assertIn("format=bgr24", vf)

    def test_output_is_rawvideo(self):
        command = livefeed.sampler_command("ffmpeg", (1920, 1080))
        self.assertIn("rawvideo", command)
        self.assertIn("bgr24", command)
        self.assertEqual(command[-1], "-")


class MatchWatchTest(unittest.TestCase):
    def test_first_number_starts_a_match(self):
        watch = livefeed.MatchWatch()
        changes = watch.feed(2.0, "111111111111111111 C1.0")
        self.assertEqual([(c.kind, c.match) for c in changes], [("start", "111111111111111111")])

    def test_new_number_ends_and_starts(self):
        watch = livefeed.MatchWatch()
        watch.feed(1.0, "111111111111111111")
        # 换局要连认两帧才切，防止一帧读错就造出假对局
        self.assertEqual(watch.feed(10.0, "222222222222222222"), [])
        changes = watch.feed(11.0, "222222222222222222")
        self.assertEqual([c.kind for c in changes], ["end", "start"])
        self.assertEqual(changes[0].at, 11.0)

    def test_single_wrong_digit_keeps_the_match(self):
        watch = livefeed.MatchWatch()
        watch.feed(1.0, "111111111111111111")
        self.assertEqual(watch.feed(5.0, "111111111111111112"), [])
        self.assertEqual(watch.current, "111111111111111111")

    def test_short_read_is_ignored(self):
        watch = livefeed.MatchWatch(idle_seconds=10)
        watch.feed(1.0, "111111111111111111")
        watch.feed(5.0, "2222222222222222")       # 只读出 16 位
        self.assertEqual(watch.current, "111111111111111111")
        self.assertEqual(len(watch.changes), 1)

    def test_same_number_is_not_a_change(self):
        watch = livefeed.MatchWatch()
        watch.feed(1.0, "111111111111111111")
        self.assertEqual(watch.feed(5.0, "111111111111111111"), [])

    def test_blank_keeps_previous_until_idle(self):
        watch = livefeed.MatchWatch(idle_seconds=30)
        watch.feed(1.0, "111111111111111111")
        self.assertEqual(watch.feed(20.0, ""), [])
        changes = watch.feed(40.0, "")
        self.assertEqual([c.kind for c in changes], ["end"])

    def test_close_ends_open_match(self):
        watch = livefeed.MatchWatch()
        watch.feed(1.0, "111111111111111111")
        self.assertEqual([c.kind for c in watch.close(99.0)], ["end"])

    def test_close_without_match_is_noop(self):
        self.assertEqual(livefeed.MatchWatch().close(5.0), [])

    def test_spans_pair_start_and_end(self):
        watch = livefeed.MatchWatch()
        watch.feed(1.0, "111111111111111111")
        watch.feed(10.0, "222222222222222222")
        watch.feed(11.0, "222222222222222222")   # 连认两帧才确认换局
        watch.close(30.0)
        spans = watch.spans()
        self.assertEqual(len(spans), 2)
        # 上一局结束在「新编号被确认」的那一刻
        self.assertEqual(spans[0], {"match": "111111111111111111", "start": 1.0, "end": 11.0})
        self.assertEqual(spans[1]["match"], "222222222222222222")

    def test_one_span_per_match_even_if_it_drops_out(self):
        # 大对局中间读不到编号（回放/观察视角）会断一下，但编号没变就是同一局
        watch = livefeed.MatchWatch(idle_seconds=10)
        watch.feed(1.0, "111111111111111111")
        watch.feed(20.0, "")          # 空档超过 10 秒 → end
        watch.feed(40.0, "111111111111111111")   # 同一个编号又出现 → start
        watch.close(50.0)
        spans = watch.spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["start"], 1.0)
        self.assertEqual(spans[0]["end"], 50.0)

    def test_change_serialises_for_jsonl(self):
        change = livefeed.Change(3.14159, "start", "111111111111111111")
        self.assertEqual(change.to_dict(), {"at": 3.14, "kind": "start",
                                           "match": "111111111111111111"})


if __name__ == "__main__":
    unittest.main()
