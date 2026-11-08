"""系统声音采集里能脱离设备测的那部分：补静音算法和状态汇总。"""

import unittest

from strinova_star import loopback


class SilenceTest(unittest.TestCase):
    """按墙钟补静音：写进去的字节数要一直等于已经过去的时间。"""

    def test_keeping_up_needs_nothing(self):
        # 48kHz 立体声 16bit：1 秒 = 48000 * 2 声道 * 2 字节
        self.assertEqual(loopback.silence_needed(192000, 1.0), 0)

    def test_fills_a_full_gap(self):
        self.assertEqual(loopback.silence_needed(0, 1.0), 192000)

    def test_fills_only_the_missing_half(self):
        self.assertEqual(loopback.silence_needed(96000, 1.0), 96000)

    def test_never_negative(self):
        # 写多了不该倒扣，否则会被当成「往回补」
        self.assertEqual(loopback.silence_needed(500000, 1.0), 0)

    def test_shorter_gap(self):
        self.assertEqual(loopback.silence_needed(0, 0.02), 3840)

    def test_other_rate_and_channels(self):
        self.assertEqual(loopback.silence_needed(0, 0.5, rate=16000, channels=1), 16000)

    def test_negative_elapsed_is_zero(self):
        self.assertEqual(loopback.silence_needed(0, -1.0), 0)


class StatusTest(unittest.TestCase):
    """status() 的秒数要把补的静音算进去，否则界面上的音轨会比画面短。"""

    def setUp(self):
        self.device = loopback.Loopback()
        self.device.rate = 48000
        self.device.channels = 2

    def test_seconds_include_silence(self):
        self.device.bytes = 96000
        self.device.silence = 96000
        status = self.device.status()
        self.assertEqual(status["seconds"], 1.0)
        self.assertEqual(status["silence_seconds"], 0.5)

    def test_not_running_without_thread(self):
        self.assertFalse(self.device.status()["running"])

    def test_chunks_and_error_are_reported(self):
        self.device.chunks = 7
        self.device.error = "采集中断：设备没了"
        status = self.device.status()
        self.assertEqual(status["chunks"], 7)
        self.assertIn("设备没了", status["error"])

    def test_reopens_are_reported(self):
        self.device.reopens = 2
        self.assertEqual(self.device.status()["reopens"], 2)


class FakeAudio:
    """假的 PyAudio：只够测重开流程，不碰真设备。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.opens = 0
        self.terminated = False

    def open(self, **_kwargs):
        self.opens += 1
        if self.fail:
            raise OSError(-9999, "Unanticipated host error")
        return FakeStream()

    def terminate(self):
        self.terminated = True


class FakeStream:
    def __init__(self):
        self.stopped = False
        self.closed = False

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakePyAudio:
    paInt16 = 8


class ReopenTest(unittest.TestCase):
    """设备断了要能重开；重不开也不能把采集线程搞崩。"""

    def setUp(self):
        self.device = loopback.Loopback()
        self.device.rate = 48000
        self.device.channels = 2
        self.device._pyaudio = FakePyAudio()
        self.device._index = 0

    def test_reopen_succeeds(self):
        audio = FakeAudio()
        self.device._audio = audio
        self.assertTrue(self.device._reopen())
        self.assertEqual(audio.opens, 1)
        self.assertIsNotNone(self.device._stream)

    def test_reopen_gives_up_when_device_is_gone(self):
        audio = FakeAudio(fail=True)
        self.device._audio = audio
        self.assertFalse(self.device._reopen())
        self.assertEqual(audio.opens, 3)

    def test_reopen_does_nothing_while_stopping(self):
        audio = FakeAudio()
        self.device._audio = audio
        self.device._stopping.set()
        self.assertFalse(self.device._reopen())
        self.assertEqual(audio.opens, 0)

    def test_close_stream_marks_it_closed(self):
        stream = FakeStream()
        self.device._stream = stream
        self.device._close_stream()
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertIsNone(self.device._stream)


if __name__ == "__main__":
    unittest.main()
