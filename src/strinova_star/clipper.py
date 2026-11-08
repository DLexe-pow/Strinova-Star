"""无损切片：把 `feed` 找到的时间点切成片段（-c copy，不重编码）。

一个事件切多长，由两个数决定：

    start = max(0, t - past)   # 击杀发生前留几秒
    end   = min(t + future, 时长)  # 击杀发生后留几秒

``past`` / ``future`` 是 :func:`plan` 的参数，也可以按事件单独覆盖
（``{"time": 12.3, "past": 5, "future": 8}``），所以「击倒留 4 秒、击杀留 6 秒」也写得出来。

多个事件撞在一起时按「窗口合并」处理：窗口按开始时间排序，后一个的开始只要不晚于
前一个的结束（再加 ``join_gap`` 的容差）就并成一段。::

    击杀① 10s、击杀② 15s，past = future = 5
    → [5, 15] 与 [10, 20] 重叠 5 秒 → 合并成 [5, 20]，只切一段

``join_gap=0`` 时「重叠或首尾相接」才合并；给个 0.5 就能把「隔半秒的两刀」也并起来。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_FFMPEG = (
    Path(r"D:\game\Apex Highlight Clipper\resources\tools\ffmpeg.exe"),
    Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
)


class ClipperError(RuntimeError):
    """找不到 ffmpeg，或切片失败。"""


@dataclass
class Segment:
    """一个待切的片段。

    ``label`` 用在文件名里，``events`` 是并进这一段的所有原始事件（没合并时只有一条）。
    """

    start: float
    end: float
    label: str = ""
    events: list[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def count(self) -> int:
        """这一段里装了几个击杀/击倒事件。"""
        return len(self.events)

    def __str__(self) -> str:
        return (f"{self.start:7.2f}s → {self.end:7.2f}s"
                f"（{self.duration:5.1f}s，{self.count} 个事件）{self.label}")


def find_ffmpeg(explicit: str | Path | None = None) -> Path:
    """按 显式 → 环境变量 → PATH → 已知位置 的顺序找 ffmpeg。"""
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
    # 便携版自带的那份优先：<app>/bin/ffmpeg.exe
    bundled = Path(__file__).resolve().parents[2] / "bin" / "ffmpeg.exe"
    if bundled.is_file():
        return bundled
    # 便携版自带的那份优先：<app>/bin/ffmpeg.exe
    bundled = Path(__file__).resolve().parents[2] / "bin" / "ffmpeg.exe"
    if bundled.is_file():
        return bundled
    env = __import__("os").environ.get("STRINOVA_FFMPEG")
    if env and Path(env).exists():
        return Path(env)
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    for candidate in KNOWN_FFMPEG:
        if candidate.exists():
            return candidate
    raise ClipperError("找不到 ffmpeg：装一个并加进 PATH，或设 STRINOVA_FFMPEG=<ffmpeg.exe 路径>")


def _seconds(value, name: str) -> float:
    """把 past / future / join_gap 统一成非负浮点数。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 得是数字，拿到 {value!r}") from None
    if seconds < 0:
        raise ValueError(f"{name} 不能是负数：{seconds}")
    return seconds


def _label_of(event: dict) -> str:
    for key in ("label", "kind", "victim"):
        text = str(event.get(key) or "").strip()
        if text:
            return text
    return ""


def _merge_label(events: list[dict]) -> str:
    """一段里的事件合成一个文件名用的标签：单事件照抄，同类计数，异类相加。"""
    labels: list[str] = []
    for event in events:
        text = _label_of(event)
        if text and text not in labels:
            labels.append(text)
    if not labels:
        return "clip"
    if len(events) <= 1:
        return labels[0]
    if len(labels) == 1:
        return f"{labels[0]}×{len(events)}"
    return "+".join(labels[:3])


def plan(events, past: float = 8.0, future: float = 2.0, duration: float | None = None,
         join_gap: float = 0.0) -> list[Segment]:
    """事件 → 片段（事件前 `past` 秒、后 `future` 秒，重叠/相邻的合并）。

    :param events: ``[{"time": 12.3, "label": "击杀", ...}]``，可带 ``past`` / ``future`` 覆盖默认值
    :param past: 默认向前留几秒（≥0）
    :param future: 默认向后留几秒（≥0）
    :param duration: 视频总长，给了就把尾巴夹住，超出片尾的事件直接丢掉
    :param join_gap: 两个窗口之间最多允许空几秒仍然合并（默认 0 = 只合并重叠或相接的）
    """
    default_before = _seconds(past, "past")
    default_after = _seconds(future, "future")
    gap = _seconds(join_gap, "join_gap")
    limit = float(duration) if duration else None

    windows: list[Segment] = []
    for event in events:
        moment = float(event.get("time", 0.0))
        if limit is not None and moment > limit:   # 事件在片尾之外，切不出来
            continue
        before = _seconds(event.get("past", default_before), "past")
        after = _seconds(event.get("future", default_after), "future")
        start = max(0.0, moment - before)
        end = moment + after
        if limit is not None:
            end = min(end, limit)
        if end <= start:          # 事件落在片尾之外，或者窗口长度是 0
            continue
        windows.append(Segment(start=start, end=end, events=[dict(event)]))

    windows.sort(key=lambda segment: segment.start)
    merged: list[Segment] = []
    for window in windows:
        tail = merged[-1] if merged else None
        if tail is not None and window.start <= tail.end + gap:
            tail.end = max(tail.end, window.end)
            tail.events.extend(window.events)
            tail.label = _merge_label(tail.events)
        else:
            window.label = _merge_label(window.events)
            merged.append(window)
    return merged


def _safe(text: str) -> str:
    for bad in '\\/:*?"<>|':
        text = text.replace(bad, "_")
    return text.strip() or "clip"


def cut(video: str | Path, segment: Segment, out_path: str | Path,
        ffmpeg: str | Path | None = None, overwrite: bool = True,
        accurate: bool = False) -> Path:
    """切一段。

    默认「输入侧 seek + 流拷贝」：快，但起点会落到前一个关键帧（通常差 1~2 秒）。
    ``accurate=True`` 改成「输出侧 seek」：从文件头读过去、按包丢弃，起点准，长录像会慢。
    """
    exe = find_ffmpeg(ffmpeg)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{segment.start:.3f}"]
    # `-map 0` 不能省：录屏会有多条音轨（游戏 / 麦克风 / 语音），
    # 不写 map 时 ffmpeg 只挑各类型的第一条，切完就只剩一条音轨了。
    span = ["-t", f"{segment.duration:.3f}", "-map", "0", "-c", "copy",
            "-avoid_negative_ts", "make_zero", "-y" if overwrite else "-n"]
    if accurate:
        command = [str(exe), "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-i", str(video), *seek, *span, str(target)]
    else:
        command = [str(exe), "-hide_banner", "-loglevel", "error", "-nostdin",
                   *seek, "-i", str(video), *span, str(target)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise ClipperError(f"ffmpeg 失败：{done.stderr.strip()[:300]}")
    return target


def cut_all(video: str | Path, segments: list[Segment], out_dir: str | Path,
            prefix: str = "clip", ffmpeg: str | Path | None = None,
            dry_run: bool = False, accurate: bool = False,
            on_done=None) -> list[Path]:
    """连续切多段，返回产出的文件列表。`on_done(path, index, total)` 可选。

    文件名带起止时间，合并过的那几段一眼能看出来：``clip-001_击杀×2_5.0-20.0s.mp4``。
    """
    out_dir = Path(out_dir)
    results: list[Path] = []
    for index, segment in enumerate(segments, start=1):
        name = (f"{prefix}-{index:03d}_{_safe(segment.label)}"
                f"_{segment.start:.1f}-{segment.end:.1f}s.mp4")
        target = out_dir / name
        if not dry_run:
            cut(video, segment, target, ffmpeg, accurate=accurate)
        results.append(target)
        if on_done:
            on_done(target, index, len(segments))
    return results
