"""按回合找切点。

一局（大对局）内部的「小回合」时间轴要在界面上能直接看到、也能按回合切片，
所以这里只做一件事：扫一遍录像，读左上角那条 HUD 上的**双方回合比分**和**回合倒计时**，
比分一变就是一个回合结束，再往后找倒计时回到满值的那一帧，就是下一个回合真正开始。

为什么用比分而不是别的：

* 结算横幅（「失败 / 全员被击败」）只有一两秒，按 1 秒采样容易错过；
* 计分板 / 商店画面每次都不一样（有抗锯齿、有动效），模板匹配不稳；
* 比分区是固定坐标的一小块，识别一次约 10 ms，整局（40 分钟）也就几十秒。

比分只增不减，掉下来基本是切场景时读到了别的数字（比如过场里的 0），
所以往下掉要先「持续几帧」才认。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config, geometry, ocr, video

# 1920×1080 基准下的坐标（实测 2560×1600 → 除以 1600/1080 得来）
LEFT_SCORE = (733.0, 108.0, 776.0, 132.0)
RIGHT_SCORE = (950.0, 108.0, 993.0, 132.0)
TIMER = (821.0, 105.0, 880.0, 134.0)

SCAN_STEP = 1.0
"""默认每秒看一帧。比分变化到下一回合开始有十几秒的空档，不必更密。"""

FULL_TIME = 105.0
"""倒计时回到这个秒数以上，说明是新回合开始了（回合时长约 120 秒）。"""

MIN_ROUND = 20.0

TAIL_MIN = 60.0
"""最后一小段短于这个时长，就当它是结算画面，不算回合。"""
"""短于这个时长的「回合」多半是结算画面里读到的噪声，直接并进前一段。"""

_DIGITS = re.compile(r"\d+")


@dataclass
class Round:
    index: int
    start: float
    end: float
    our: int = 0
    theirs: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def score(self) -> str:
        return f"{self.our}-{self.theirs}"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "score": self.score,
            "label": f"回合 {self.index}",
        }

    def __str__(self) -> str:
        return f"回合 {self.index:>2}  {self.start:7.1f}s → {self.end:7.1f}s（{self.duration:5.1f}s）{self.score}"


@dataclass
class Result:
    video: str = ""
    found: bool = False
    rounds: list[Round] = field(default_factory=list)
    seconds: float = 0.0
    message: str = ""
    match_end: float | None = None
    """最后一个回合结束（结算画面开始）的时刻；对局没打完就是 None。"""

    @property
    def count(self) -> int:
        return len(self.rounds)

    def to_dict(self) -> dict:
        return {
            "video": self.video,
            "found": self.found,
            "count": self.count,
            "rounds": [item.to_dict() for item in self.rounds],
            "message": self.message,
            "match_end": None if self.match_end is None else round(self.match_end, 2),
        }


def _scale_for(size: tuple[int, int]) -> float:
    return max(1.0, float(size[1])) / 1080.0


def _box(box: tuple[float, float, float, float], scale: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    x1, y1, x2, y2 = (int(round(value * scale)) for value in box)
    return (
        max(0, min(width, x1)),
        max(0, min(height, y1)),
        max(0, min(width, x2)),
        max(0, min(height, y2)),
    )


def parse_score(text: str) -> int | None:
    """比分：只保留数字，「00」→0。"""
    digits = "".join(_DIGITS.findall(text or ""))
    if not digits or len(digits) > 2:
        return None
    return int(digits)


def parse_clock(text: str) -> float | None:
    """倒计时：`01:56` → 116.0；`00:05` → 5.0。"""
    text = (text or "").strip()
    match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    digits = "".join(_DIGITS.findall(text))
    if len(digits) == 3:
        return int(digits[:1]) * 60 + int(digits[1:])
    return None


def read_state(image, scale: float, recognizer=None) -> tuple[int | None, int | None, float | None]:
    """读一帧的（我方比分、对方比分、倒计时）。读不到的项返回 None。"""
    recognizer = recognizer or ocr.recognizer()
    height, width = image.shape[:2]
    size = (width, height)

    def read(box) -> str:
        x1, y1, x2, y2 = _box(box, scale, size)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return ""
        frame = image[y1:y2, x1:x2]
        if frame.size == 0:
            return ""
        text, _score = recognizer(frame)
        return text

    return parse_score(read(LEFT_SCORE)), parse_score(read(RIGHT_SCORE)), parse_clock(read(TIMER))


def _frame_at(source: video.Video, when: float) -> np.ndarray:
    return source.frame(when)


def scan(
    video_path: str | Path,
    profile: dict | None = None,
    step: float = SCAN_STEP,
    recognizer=None,
    on_progress=None,
) -> Result:
    """扫整段录像，返回按回合切好的时间轴。"""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到录像：{path}")

    profile = profile or config.load_profile()
    recognizer = recognizer or ocr.recognizer()
    started = time.time()

    with video.Video(path) as source:
        scale = _scale_for((source.width, source.height))
        total = max(1, int(source.duration / max(0.2, step)) + 1)
        samples: list[tuple[float, int, int, float | None]] = []
        left = right = 0
        seen = False
        for index, (when, frame) in enumerate(source.frames(step=step)):
            our, theirs, clock = read_state(frame, scale, recognizer)
            if our is not None and theirs is not None:
                left, right = our, theirs
                seen = True
            samples.append((when, left, right, clock))
            if on_progress:
                on_progress(min(index + 1, total), total)
    result = Result(video=str(path), seconds=round(time.time() - started, 1))
    if not seen:
        result.message = "这段录像里没读到回合比分（可能不是对局画面）"
        return result

    rounds, match_end = _build(samples)
    if not rounds:
        result.message = "只读到一帧比分，切不出回合"
        return result
    result.found = True
    result.rounds = rounds
    result.match_end = match_end
    result.message = f"{len(rounds)} 个回合"
    return result


def _build(samples: list[tuple[float, int, int, float | None]]) -> tuple[list[Round], float | None]:
    """把逐帧样本切成回合。

    比分区只会往上涨（赢一回合我方 +1，输一回合对方 +1），所以：
    先取比分的历史最大值当作「已确认的比分」，只有超过它才算一次变化；
    同一个分数要在下一帧还在，才算真的（防止切场景时读到别的数字）。
    """
    if not samples:
        return [], None

    marks: list[tuple[float, int, int]] = [(samples[0][0], samples[0][1], samples[0][2])]
    best_our, best_theirs = samples[0][1], samples[0][2]
    pending: tuple[float, int, int] | None = None

    for when, our, theirs, _clock in samples[1:]:
        if our > best_our or theirs > best_theirs:
            if pending is None:
                pending = (when, our, theirs)
            elif pending[1] == our and pending[2] == theirs:
                marks.append(pending)
                best_our, best_theirs = our, theirs
                pending = None
            else:
                pending = (when, our, theirs)
        else:
            pending = None

    rounds: list[Round] = []
    for index, (when, our, theirs) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else samples[-1][0]
        rounds.append(Round(index=index + 1, start=when, end=end, our=our, theirs=theirs))

    merged: list[Round] = []
    for item in rounds:
        if merged and (item.duration < MIN_ROUND or item.score == merged[-1].score):
            merged[-1].end = max(merged[-1].end, item.end)
            continue
        merged.append(item)
    match_end: float | None = None
    if len(merged) >= 2 and merged[-1].duration < TAIL_MIN:
        # 最后一小段是结算/回大厅，不算一个回合：并进前一个回合，只留个结束点
        tail = merged.pop()
        match_end = tail.start
        if merged:
            merged[-1].end = samples[-1][0]
    for index, item in enumerate(merged, start=1):
        item.index = index
    return merged, match_end

def _round_start(samples: list[tuple[float, int, int, float | None]], after: float) -> float:
    """比分刚变之后，倒计时回到满值的时刻就是新回合真正开始。"""
    for when, _our, _theirs, clock in samples:
        if when < after:
            continue
        if clock is not None and clock >= FULL_TIME:
            return when
    return after


def format_text(result: Result) -> str:
    lines = [f"{result.video}（{result.seconds:.1f}s）{result.message}"]
    for item in result.rounds:
        lines.append(str(item))
    return "\n".join(lines)


__all__ = [
    "Round",
    "Result",
    "scan",
    "read_state",
    "parse_score",
    "parse_clock",
    "format_text",
    "LEFT_SCORE",
    "RIGHT_SCORE",
    "TIMER",
]
