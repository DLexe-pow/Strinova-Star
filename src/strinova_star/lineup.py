"""顶部阵容条：正常画面里一直亮着的 10 个小头像（我方 5 + 对方 5）。

计分板给的是「昵称 ↔ 角色」，但阵亡头像会被红叉压住、一局没按过 Tab 就完全没戏；
顶部那条阵容条不用按任何键，能直接读出「这一局是哪 10 个角色」。
它的局限是**没有昵称** —— 补的是角色名单，不是「谁玩谁」。

用法（模块级）：

    from strinova_star import lineup
    result = lineup.scan("录像.mp4")
    print(result.ally, result.enemy)

流程：在录像里挑若干帧（跳过片头片尾）→ 裁 10 个槽 → 每帧各投一票（内点够、
且第一名比第二名领先才投）→ 多数票当选。单帧认错带不偏结果。

实测（2560×1600，`Calabiyau ... 20.48.15.03.DVR.mp4`）：正常对局帧内点常年在
100～230，4 帧投票后 10/10；片头、击杀回放这类 HUD 藏起来的帧会被
`MIN_FRAME_SLOTS` 挡掉，不会把「没认出」当成「空位」。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config, geometry
from .matchers import make as make_matcher
from .video import Video

SIDE_SIZE = 5                 # 每边 5 个槽
SAMPLES = 4                   # 投几帧的票
MAX_ATTEMPTS = 16             # 最多试几帧找 HUD（片头/回放会被跳过）
MIN_FRAME_SLOTS = 6           # 一帧里至少认出 6 个，才拿这帧投票
MIN_SCORE = 12.0              # 单帧内点下限（和 matcher 默认一致）
MIN_MARGIN = 4.0              # 单帧第一名要比第二名高这么多分


@dataclass
class Slot:
    side: str                 # ally / enemy
    index: int                # 0..4
    character: str = ""
    inliers: float = 0.0      # 命中帧的平均内点
    margin: float = 0.0       # 命中帧的平均领先分
    votes: int = 0            # 几帧投了它

    def to_dict(self) -> dict:
        return {"side": self.side, "index": self.index, "character": self.character,
                "inliers": round(self.inliers, 1), "margin": round(self.margin, 1),
                "votes": self.votes}


@dataclass
class Result:
    video: str = ""
    found: bool = False       # 有没有看到阵容条（HUD 可见）
    time: float = 0.0         # 第一帧投票画面所在的秒数
    slots: list[Slot] = field(default_factory=list)
    frames: int = 0           # 实际投了几帧
    message: str = ""

    @property
    def ally(self) -> list[Slot]:
        return [slot for slot in self.slots if slot.side == "ally"]

    @property
    def enemy(self) -> list[Slot]:
        return [slot for slot in self.slots if slot.side == "enemy"]

    @property
    def recognized(self) -> int:
        return sum(1 for slot in self.slots if slot.character)

    def to_dict(self) -> dict:
        return {"video": self.video, "mode": "lineup", "found": self.found,
                "time": round(self.time, 1), "frames": self.frames,
                "recognized": self.recognized, "message": self.message,
                "ally": [slot.to_dict() for slot in self.ally],
                "enemy": [slot.to_dict() for slot in self.enemy]}


def slot_boxes(profile: dict, size: tuple[int, int]) -> list[tuple[str, int, tuple[int, int, int, int]]]:
    """10 个槽在当前分辨率下的像素框：[(side, index, box), ...]。"""
    adapted = geometry.for_size(profile, size)
    boxes = geometry.lineup_slots(adapted)
    out: list[tuple[str, int, tuple[int, int, int, int]]] = []
    for position, box_ in enumerate(boxes):
        side = "ally" if position < SIDE_SIZE else "enemy"
        out.append((side, position % SIDE_SIZE, geometry.clamp(box_, size)))
    return out


def read_frame(image: np.ndarray, profile: dict, matcher, min_score: float = MIN_SCORE,
               min_margin: float = MIN_MARGIN) -> list[dict]:
    """一帧 → 10 个槽各自认出谁（认不准就是空串）。"""
    rows: list[dict] = []
    height, width = image.shape[:2]
    for side, index, box_ in slot_boxes(profile, (width, height)):
        crop = geometry.crop(image, box_)
        if crop is None:
            rows.append({"side": side, "index": index, "character": "",
                         "score": 0.0, "runner": 0.0})
            continue
        scores = matcher.score_row(crop)
        order = np.argsort(-scores)
        best = float(scores[order[0]])
        second = float(scores[order[1]]) if len(order) > 1 else 0.0
        character = matcher.names[order[0]] if best >= min_score and best - second >= min_margin else ""
        rows.append({"side": side, "index": index, "character": character,
                     "score": best, "runner": second})
    return rows


def scan(video_path: str | Path, profile: dict | None = None, matcher=None,
         samples: int = SAMPLES, on_progress=None) -> Result:
    """在录像里找正常画面，读顶部阵容条。`on_progress(done, total, message)` 可选。"""
    profile = profile or config.load_profile()
    matcher = matcher or make_matcher()
    result = Result(video=str(video_path))

    votes = [Counter() for _ in range(2 * SIDE_SIZE)]
    totals = [Counter() for _ in range(2 * SIDE_SIZE)]
    margins = [Counter() for _ in range(2 * SIDE_SIZE)]

    with Video(video_path) as video:
        duration = video.duration
        if duration < 1.0:
            result.message = "录像太短，读不出阵容"
            return result
        # 片头 2 秒还没进对局、片尾可能是结算，都跳过
        start = min(2.0, duration * 0.1)
        end = max(start + 0.1, duration - 2.0)
        attempts = max(samples * 4, samples)
        moments = [round(start + (end - start) * i / max(1, attempts - 1), 2)
                   for i in range(attempts)]

        used = 0
        for moment in moments:
            if used >= samples:
                break
            image = video.frame(moment)
            if image is None:
                continue
            rows = read_frame(image, profile, matcher)
            good = sum(1 for row in rows if row["character"])
            if good < MIN_FRAME_SLOTS:
                continue          # HUD 没显示（片头/回放/结算）
            used += 1
            if not result.time:
                result.time = moment
            if on_progress:
                on_progress(used, samples, "读顶部阵容条")
            for position, row in enumerate(rows):
                if row["character"]:
                    votes[position][row["character"]] += 1
                    totals[position][row["character"]] += row["score"]
                    margins[position][row["character"]] += row["score"] - row["runner"]

    result.frames = used
    if not used:
        result.message = "这段录像里没看到顶部阵容条（可能没进对局，或 HUD 被关掉了）"
        return result

    result.found = True
    need = 1 if used == 1 else max(2, (used + 1) // 2)   # 过半票才算数
    for position, counter in enumerate(votes):
        side = "ally" if position < SIDE_SIZE else "enemy"
        slot = Slot(side=side, index=position % SIDE_SIZE)
        if counter:
            character, count = max(counter.items(), key=lambda item: (item[1], totals[position][item[0]]))
            if count >= need:
                slot.character = character
                slot.votes = count
                slot.inliers = totals[position][character] / count
                slot.margin = margins[position][character] / count
        result.slots.append(slot)

    missing = len(result.slots) - result.recognized
    if missing:
        result.message = f"阵容条上有 {missing} 个没认出来"
    return result


def fill_rows(rows, result: Result) -> list[tuple[str, str]]:
    """用阵容条把计分板上空着的角色补上（同侧排除法）。

    `rows` 是 ``scoreboard.RosterRow`` 列表（有 ``side`` / ``nickname`` / ``character``）。
    只在「该侧阵容条全认出来了」且「空位数量正好等于剩下的候选」时才动手，
    宁可不补也不瞎补。返回 ``[(昵称, 角色), ...]``。
    """
    filled: list[tuple[str, str]] = []
    for side in ("ally", "enemy"):
        line = [slot.character for slot in result.slots if slot.side == side]
        if not line or not all(line):
            continue
        side_rows = [row for row in rows if (row.side or "ally") == side]
        known = {row.character for row in side_rows if row.character}
        spare = [character for character in line if character not in known]
        empty = [row for row in side_rows if not row.character]
        if not spare or len(spare) != len(empty):
            continue
        for row, character in zip(empty, spare):
            row.character = character
            filled.append((row.nickname, character))
    return filled


def format_text(result: Result) -> str:
    """给人看的文本表。"""
    if not result.found:
        return result.message or "没有结果"
    lines = [f"顶部阵容条（{result.time:.1f}s 起的 {result.frames} 帧投票）"]
    for title, slots in (("我方", result.ally), ("对方", result.enemy)):
        names = "  ".join((slot.character or "未识别") for slot in slots)
        lines.append(f"  {title}  {names}")
    if result.message:
        lines.append(f"  {result.message}")
    return "\n".join(lines)
