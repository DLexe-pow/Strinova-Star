"""日志与导出：出问题时要能把栈和上下文一起交出去。"""

import atexit
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from strinova_star import logs


def temp_dir():
    path = tempfile.mkdtemp(prefix="strinova-log-")
    atexit.register(shutil.rmtree, path, True)
    return Path(path)


class FakeJob:
    def __init__(self):
        self.title = "识别双方阵容"
        self.state = "error"
        self.progress = 0.12
        self.message = "找计分板 7/399"
        self.error = "'list' object has no attribute 'strip'"
        self.traceback = "Traceback (most recent call last):\n  ... TypeError"


class FakeHub:
    def __init__(self):
        self.job = FakeJob()
        self.settings = {"video_dir": "D:/v", "self_name": "我"}
        self.log = [{"time": time.time(), "text": "开始识别阵容"}]


class LogsTest(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        logs.install(self.dir)

    def test_writes_file(self):
        logs.write("测试一行")
        text = logs.export_text()
        self.assertIn("测试一行", text)
        self.assertIn("Strinova Star", text)

    def test_exception_keeps_traceback(self):
        try:
            raise ValueError("炸了")
        except ValueError as exc:
            text = logs.exception("测试任务", exc)
        self.assertIn("ValueError", text)
        self.assertIn("炸了", logs.export_text())

    def test_export_has_job_and_settings(self):
        text = logs.export_text(FakeHub())
        self.assertIn("识别双方阵容", text)
        self.assertIn("list' object has no attribute 'strip'", text)
        self.assertIn("Traceback", text)
        self.assertIn("video_dir", text)
        self.assertIn("开始识别阵容", text)

    def test_rotate_drops_old_files(self):
        old = self.dir / "app-20200101.log"
        old.write_text("old", encoding="utf-8")
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        logs.rotate()
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
