"""录制时的旁路采样：另开一个 ffmpeg 低频抓帧，读左上角的对局编号。

为什么要另开一个进程：主录制那个 ffmpeg 的输出全写进磁盘，**不能**再分给 Python 读；
但 `ddagrab` 允许多路同时抓同一块屏幕（实测两个进程并发没问题），所以采样单独跑一路：

    ffmpeg -f lavfi -i ddagrab=0:framerate=2  -vf crop=…,scale=…  -f rawvideo -pix_fmt bgr24 -

一帧只有几十 KB，2 fps 也就几百 KB/s，几乎不占资源。

对局编号认出来以后交给 `MatchWatch`：编号变了就是新的一局，记一条「开局」；
编号消失超过 `idle_seconds` 就是离开对局，记一条「结束」。这些记录写进
`matches.jsonl`，停止录制时按它把分段拼成「一局一个文件」。

本模块只做「读 + 记」，不碰录制进程，方便单独测。
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

MATCH_ID_BOX = (26.0, 55.0, 232.0, 88.0)   # 1920×1080 基准，只框前 18 位数字
SAMPLE_FPS = 2.0
IDLE_SECONDS = 30.0                        # 编号读不到这么久才算离开对局
SCALE = 2                                  # 裁出来的小条放大两倍再认，字太小认不准

_RUN = re.compile(r"\d{6,}")
_DIGITS = re.compile(r"\D+")


def parse_match_id(text: str) -> str:
    """从「765071518099047823 C1.18.6.11 S1.18.6.12」里取出 18 位对局编号。

    只认够 18 位的数字串：认字漏掉一两位时（实测有过只读出 16 位的），宁可当没读到
    沿用上一个编号，也不能凭空造出一个「新对局」。
    """
    for run in _RUN.findall(text or ""):
        if len(run) >= 18:
            return run[:18]
    return ""


def same_id(left: str, right: str) -> bool:
    """两个编号算不算同一局：完全相同，或者只差一个字符（认字的抖动）。"""
    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) != len(right):
        return False
    return sum(1 for a, b in zip(left, right) if a != b) <= 1


def crop_box(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """按实际画面尺寸换算编号框（按高度缩放，和标定档一致）。"""
    width, height = size
    scale = height / 1080.0
    x1, y1, x2, y2 = MATCH_ID_BOX
    return (int(round(x1 * scale)), int(round(y1 * scale)),
            int(round(x2 * scale)), int(round(y2 * scale)))


def sampler_command(ffmpeg: str, size: tuple[int, int], fps: float = SAMPLE_FPS,
                    scale: int = SCALE) -> list[str]:
    """只把编号那一小条裁出来、放大，输出 bgr24 原始帧到 stdout。"""
    width, height = size
    box_scale = height / 1080.0
    x1 = int(round(MATCH_ID_BOX[0] * box_scale))
    y1 = int(round(MATCH_ID_BOX[1] * box_scale))
    box_width = int(round((MATCH_ID_BOX[2] - MATCH_ID_BOX[0]) * box_scale / 2)) * 2
    box_height = int(round((MATCH_ID_BOX[3] - MATCH_ID_BOX[1]) * box_scale / 2)) * 2
    box_width = max(2, min(box_width, width))
    box_height = max(2, min(box_height, height))
    x1 = max(0, min(x1, width - box_width))
    y1 = max(0, min(y1, height - box_height))
    # ddagrab 出来的是显卡里的 d3d11 帧，得先 hwdownload 到内存，否则后面的
    # crop/scale 会和 auto_scale 打架（实测报 "Impossible to convert between
    # the formats supported by the filter 'Parsed_crop_0'"）。
    inner = (f"hwdownload,format=bgra,"
             f"crop={box_width}:{box_height}:{x1}:{y1},"
             f"scale={box_width * scale}:{box_height * scale}:flags=lanczos,"
             f"format=bgr24")
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"ddagrab=0:framerate={fps:g}:draw_mouse=0",
            "-vf", inner, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


def frame_size(size: tuple[int, int], scale: int = SCALE) -> tuple[int, int]:
    """采样帧的宽高（要和 sampler_command 里的 crop/scale 对得上）。"""
    height_scale = size[1] / 1080.0
    box_width = int(round((MATCH_ID_BOX[2] - MATCH_ID_BOX[0]) * height_scale / 2)) * 2
    box_height = int(round((MATCH_ID_BOX[3] - MATCH_ID_BOX[1]) * height_scale / 2)) * 2
    return (max(2, box_width) * scale, max(2, box_height) * scale)


def probe_size(ffmpeg: str, timeout: float = 20.0) -> tuple[int, int]:
    """问 ffmpeg 屏幕到底多大（`showinfo` 会把 `s:2560x1600` 打在日志里）。

    以前是抓一帧 png 回来解码，但 ddagrab 的 d3d11 帧喂 png 编码器会失败
    （`Terminating thread with return code -22`），所以改成只让它报尺寸。
    """
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "info",
               "-f", "lavfi", "-i", "ddagrab=0:framerate=1:draw_mouse=0",
               "-frames:v", "1", "-vf", "showinfo", "-f", "null", "-"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = (done.stderr or "") + (done.stdout or "")
    sizes = re.findall(r"s:(\d+)x(\d+)", log)
    if not sizes:
        raise RuntimeError(f"问不出屏幕尺寸：{log.strip()[-200:]}")
    width, height = sizes[-1]
    return (int(width), int(height))


@dataclass
class Change:
    """一次对局编号的变化。"""

    at: float                 # 相对录制开始的秒数
    kind: str                 # start / end
    match: str = ""

    def to_dict(self) -> dict:
        return {"at": round(self.at, 2), "kind": self.kind, "match": self.match}


class MatchWatch:
    """把一串「某时刻读到的编号」变成开局/结束事件。

    读不到编号（回放、过场、水印盖住）时先沿用上一个编号，超过 `idle_seconds`
    才认为离开了对局 —— 不然一进回放就会被拆成两局。
    """

    def __init__(self, idle_seconds: float = IDLE_SECONDS):
        self.idle_seconds = idle_seconds
        self.current = ""
        self.last_seen = 0.0
        self.started_at = 0.0
        self.pending = ""
        self.pending_hits = 0
        self.changes: list[Change] = []

    def feed(self, when: float, text: str) -> list[Change]:
        found = parse_match_id(text)
        out: list[Change] = []
        if found:
            self.last_seen = when
            if self.current and same_id(found, self.current):
                self.pending, self.pending_hits = "", 0
            elif not self.current:
                self.current = found
                self.started_at = when
                out.append(Change(when, "start", found))
            else:
                # 换局要连着认到两次才算数，免得一帧读错就切出一个假对局
                if self.pending and same_id(found, self.pending):
                    self.pending_hits += 1
                else:
                    self.pending, self.pending_hits = found, 1
                if self.pending_hits >= 2:
                    out.append(Change(when, "end", self.current))
                    self.current = found
                    self.started_at = when
                    out.append(Change(when, "start", found))
                    self.pending, self.pending_hits = "", 0
        elif self.current and when - self.last_seen >= self.idle_seconds:
            out.append(Change(when, "end", self.current))
            self.current = ""
        self.changes.extend(out)
        return out

    def close(self, when: float) -> list[Change]:
        """录制结束时补一个「结束」。"""
        if not self.current:
            return []
        change = Change(when, "end", self.current)
        self.current = ""
        self.changes.append(change)
        return [change]

    def spans(self) -> list[dict]:
        """整理成 [{"match":…, "start":…, "end":…}]，方便切片。

        同一个编号只留一段：一个大对局里有很多回合，中间进回放、切观察视角、
        读不到编号都会有短暂空档，但**对局编号不变**就是同一局，不能按回合切。
        """
        merged: dict[str, dict] = {}
        for change in self.changes:
            if change.kind == "start":
                span = merged.setdefault(change.match,
                                         {"match": change.match, "start": change.at,
                                          "end": change.at})
                span["start"] = min(span["start"], change.at)
            elif change.kind == "end" and change.match in merged:
                span = merged[change.match]
                if change.at >= span["start"]:
                    span["end"] = max(span["end"], change.at)
        return sorted(merged.values(), key=lambda item: item["start"])


class Sampler:
    """低频抓帧 + 认编号。出错只记 `error`，不影响录制。"""

    def __init__(self, ffmpeg: str, size: tuple[int, int], recognize,
                 fps: float = SAMPLE_FPS, scale: int = SCALE):
        self.ffmpeg = str(ffmpeg)
        self.size = size
        self.recognize = recognize          # 回调：crop(np.ndarray) -> 文本
        self.fps = fps
        self.scale = scale
        self.process: subprocess.Popen | None = None
        self.error = ""
        self.frames = 0
        self.watch = MatchWatch()
        self.started = 0.0
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def start(self) -> None:
        import numpy as np

        width, height = frame_size(self.size, self.scale)
        self._frame_bytes = width * height * 3
        self._shape = (height, width, 3)
        command = sampler_command(self.ffmpeg, self.size, self.fps, self.scale)
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.started = time.time()
        self._stopping.clear()
        self._np = np
        self._thread = threading.Thread(target=self._pump, daemon=True, name="sampler")
        self._thread.start()

    def _pump(self) -> None:
        stream = self.process.stdout if self.process else None
        if stream is None:
            return
        try:
            while not self._stopping.is_set():
                buffer = stream.read(self._frame_bytes)
                if not buffer or len(buffer) < self._frame_bytes:
                    break
                frame = self._np.frombuffer(buffer, dtype=self._np.uint8).reshape(self._shape)
                self.frames += 1
                text = ""
                try:
                    text = self.recognize(frame) or ""
                except Exception as exc:      # 认字失败不该停采样
                    if not self.error:
                        self.error = f"认编号失败：{exc}"
                self.watch.feed(time.time() - self.started, text)
        except Exception as exc:
            if not self._stopping.is_set():
                self.error = f"采样线程出错：{exc}"
        finally:
            self.process = None

    def stop(self) -> None:
        self._stopping.set()
        process = self.process
        if process is not None:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.wait(timeout=5)
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        self.process = None

    def status(self) -> dict:
        return {"running": self.running, "frames": self.frames,
                "match": self.watch.current, "error": self.error}
