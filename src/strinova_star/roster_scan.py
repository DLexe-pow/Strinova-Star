"""在录像里找 Tab 计分板，并读出双方阵容（昵称 + 角色）。

用法（模块级）：

    from strinova_star import roster_scan
    result = roster_scan.scan("录像.mp4")
    print(roster_scan.format_text(result))

流程：按 `step` 秒抽样 → 只 OCR 表头细条判断「这一帧有没有计分板」→
对候选帧跑完整识别 → 按昵称把多块计分板的结果投票合并。
阵亡玩家的头像会被红叉压住，所以不能见到计分板就直接用：一局里可能开好几次 Tab，
每次被红叉压住的不是同一批人，合起来才认得更全。
"""

from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config, geometry, ocr, scoreboard
from .matchers import make as make_matcher
from .video import Video

# 表头细条（基准坐标：1920x1080）——「击败/阵亡/助攻 绝招 成长等级」那一行
HEADER_BOX = (560, 250, 960, 325)
HEADER_HINTS = ("击败", "阵亡", "输出分", "成长等级")
HEADER_CHARS = "击败阵亡助攻输出成长等级"
MAX_CANDIDATES = 8
# 两次计分板之间隔多久算「另一次开 Tab」——同一次开着的几秒里画面几乎一样，投票没意义
SESSION_GAP = 4.0
NICKNAME_FUZZY = 0.8


@dataclass
class Slot:
    nickname: str
    side: str
    character: str = ""
    inliers: float = 0.0
    margin: float = 0.0

    def to_dict(self) -> dict:
        return {"nickname": self.nickname, "side": self.side, "character": self.character,
                "inliers": round(self.inliers, 1), "margin": round(self.margin, 1)}


@dataclass
class Result:
    video: str = ""
    found: bool = False
    time: float = 0.0
    scale: float = 0.0
    slots: list[Slot] = field(default_factory=list)
    candidates: list[float] = field(default_factory=list)
    boards: int = 0
    hits: list[float] = field(default_factory=list)   # 扫到计分板的所有时刻（含没挑去读的那些）
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
        return {"video": self.video, "found": self.found, "time": round(self.time, 1),
                "scale": round(self.scale, 4), "recognized": self.recognized,
                "boards": self.boards, "message": self.message,
                "ally": [slot.to_dict() for slot in self.ally],
                "enemy": [slot.to_dict() for slot in self.enemy]}


def header_box(profile: dict, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """表头细条在当前分辨率下的像素框。

    计分板按「画面高度」缩放（2304×1440 → 1.3333），不是按等比适配（那个会算成 1.2），
    所以这里显式把 hud_scale 换成高度缩放再换算坐标。
    """
    adapted = geometry.for_size(profile, size)
    adapted = dict(adapted)
    adapted["hud_scale"] = list(geometry.height_scale(adapted))
    return geometry.clamp(geometry.box(adapted, HEADER_BOX, "left", "top"), size)


def looks_like_scoreboard(image: np.ndarray, profile: dict, engine=None, recognizer=None) -> bool:
    """只看表头细条，判断这一帧是否开着计分板。

    先用「只认字」的模式过一遍（约 15 ms）——整条表头带检测的 OCR 要 1 秒左右，
    播报扫描每帧都判一次根本扛不住。只认字读出来的是带噪点的整行（"阵亡"常被读成
    "降亡"），所以按单字命中数判断，而不是整词。
    """
    box = header_box(profile, (image.shape[1], image.shape[0]))
    strip = image[box[1]:box[3], box[0]:box[2]]
    if strip.size == 0:
        return False
    if recognizer is not None:
        text, _ = recognizer(strip)
        return bool(text) and sum(1 for char in HEADER_CHARS if char in text) >= 3
    text = "".join(item["text"] for item in ocr.read_text(strip, box[0], box[1], engine))
    return any(hint in text for hint in HEADER_HINTS)


def read_roster(image: np.ndarray, profile: dict, matcher=None, engine=None) -> scoreboard.Scoreboard:
    """一帧 → 10 条昵称/角色（内部已处理分辨率换算）。"""
    if matcher is None:
        matcher = make_matcher()
    return scoreboard.read_scoreboard(image, profile, matcher, engine)


def nickname_score(left: str, right: str) -> float:
    """两段昵称有多像（0~1）：完全一样或互相包含算 1.0，其余按编辑距离比。

    四五个字的昵称，只要**只有一个字不一样**就按同一个人算（1.0）：
    同一块牌子 OCR 两遍，「冥 / 寞」这种形近字几乎每次都会飘一个字，
    按字符比例只有 0.75，会被当成两个人，把阵容撑成 11 个。
    """
    left = "".join((left or "").split())
    right = "".join((right or "").split())
    if not left or not right:
        return 0.0
    if left == right or left in right or right in left:
        return 1.0
    if len(left) == len(right) >= 3:
        diff = sum(1 for a, b in zip(left, right) if a != b)
        if diff <= 1:
            return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def pick_candidates(moments: list[float], max_candidates: int = MAX_CANDIDATES,
                    session_gap: float = SESSION_GAP) -> list[float]:
    """把「开着计分板的时刻」挑成几组，每组取一帧。

    连着好几秒都开着计分板时，这些帧几乎是同一张图，全拿去投票只是白花时间；
    跨过 `session_gap` 才算新的一次开 Tab。多组交替取（先每组一帧，再每组第二帧），
    这样一局开了三次 Tab 也能各取到、而不是被开头那一次占满。
    """
    moments = sorted(float(moment) for moment in moments)
    if not moments:
        return []
    sessions: list[list[float]] = [[moments[0]]]
    for moment in moments[1:]:
        if moment - sessions[-1][-1] > session_gap:
            sessions.append([moment])
        else:
            sessions[-1].append(moment)

    # 每次开 Tab 里挑两帧最不一样的：最后那帧（计分板已淡入完成）和第一帧
    pools = [[session[-1], session[0]] if len(session) >= 3 else [session[-1]]
             for session in sessions]
    picked: list[float] = []
    depth = 0
    while len(picked) < max_candidates:
        added = False
        for pool in pools:
            if len(picked) >= max_candidates:
                break
            if depth < len(pool):
                picked.append(pool[depth])
                added = True
        if not added:
            break
        depth += 1
    return sorted(set(picked))


def merge_boards(boards: list[tuple[float, "scoreboard.Scoreboard"]],
                 fuzzy: float = NICKNAME_FUZZY) -> tuple[list[Slot], float]:
    """多块计分板按昵称投票合并 →（阵容, 参考时刻）。

    同一个人在每一块计分板里都可能因为阵亡红叉认不出角色，把这些帧对起来投票，
    票多的角色胜出；票数一样就看内点总和（更清晰的那几张更可信）。
    """
    players: list[dict] = []
    for moment, board in boards:
        for row in board.rows:
            side = row.side or ""
            target, best = None, 0.0
            for player in players:
                if player["side"] != side:
                    continue
                score = nickname_score(player["nickname"], row.nickname)
                if score >= fuzzy and score > best:
                    target, best = player, score
            if target is None:
                target = {"side": side, "sides": Counter(), "nickname": row.nickname,
                          "names": Counter(), "votes": Counter(), "inliers": Counter(),
                          "margins": Counter()}
                players.append(target)
            target["sides"][side] += 1
            target["names"][row.nickname] += 1
            if row.character:
                target["votes"][row.character] += 1
                target["inliers"][row.character] += row.inliers
                target["margins"][row.character] += row.margin

    slots: list[Slot] = []
    for player in players:
        character, inliers, margin = "", 0.0, 0.0
        votes = player["votes"]
        if votes:
            character = max(votes, key=lambda name: (votes[name], player["inliers"][name]))
            count = votes[character]
            inliers = player["inliers"][character] / count
            margin = player["margins"][character] / count
        nickname = player["names"].most_common(1)[0][0]
        side = player["sides"].most_common(1)[0][0] if player["sides"] else player["side"]
        slots.append(Slot(nickname=nickname, side=side, character=character,
                          inliers=inliers, margin=margin))

    reference = max(boards, key=lambda item: sum(1 for row in item[1].rows if row.character))[0]
    return slots, reference


def scan(video_path: str | Path, profile: dict | None = None, matcher=None,
         step: float = 1.0, on_progress=None, max_candidates: int = MAX_CANDIDATES) -> Result:
    """在录像里定位计分板并识别阵容。`on_progress(done, total, message)` 可选。"""
    profile = profile or config.load_profile()
    engine = ocr.engine()
    recognizer = ocr.recognizer()   # 逐帧判断用「只认字」模式，比带检测的 OCR 快几十倍
    result = Result(video=str(video_path))

    with Video(video_path) as video:
        total = max(1, int(video.duration / step))
        found: list[float] = []
        done = 0
        for moment, image in video.frames(step=step):
            done += 1
            if on_progress:
                on_progress(done, total, "找计分板")
            if looks_like_scoreboard(image, profile, engine, recognizer=recognizer):
                found.append(moment)
                # 一局可能开好几次 Tab，所以多记一些时刻，后面按「每次开 Tab」挑帧
                if len(found) >= max_candidates * 4:
                    break

        candidates = pick_candidates(found, max_candidates)
        result.candidates = candidates
        result.hits = found          # 所有命中时刻，找击杀时按时间跳过这几秒
        if not candidates:
            result.message = "这段录像里没找到计分板（可能是没按过 Tab）"
            return result

        boards: list[tuple[float, scoreboard.Scoreboard]] = []
        for index, moment in enumerate(candidates):
            if on_progress:
                on_progress(index + 1, len(candidates), "识别阵容")
            image = video.frame(moment)
            if image is None:
                continue
            boards.append((moment, read_roster(image, profile, matcher, engine)))

    if not boards:
        result.message = "找到计分板但读不出内容"
        return result

    slots, moment = merge_boards(boards)
    result.found = True
    result.boards = len(boards)
    result.time = moment
    result.scale = boards[0][1].scale[0] if boards[0][1].scale else 0.0
    result.slots = slots
    missing = len(result.slots) - result.recognized
    prefix = (f"合并了 {len(boards)} 块计分板" if len(boards) > 1
              else "这局只扫到 1 块计分板")
    if missing > 0:
        result.message = f"{prefix}，还有 {missing} 个角色没认出来（可能被阵亡标压住）"
    else:
        result.message = f"{prefix}，{len(result.slots)} 个角色全认出来了"
    return result


def format_text(result: Result) -> str:
    """给人看的文本表。"""
    if not result.found:
        return result.message or "没有结果"
    lines = [f"对局阵容（{result.time:.1f}s 的计分板，缩放 {result.scale:.4f}）"]
    for title, slots in (("我方", result.ally), ("对方", result.enemy)):
        lines.append(f"  {title}")
        for slot in slots:
            character = slot.character or "未识别"
            lines.append(f"    {slot.nickname:<14} {character:<8} 内点 {slot.inliers:>4.0f}")
    return "\n".join(lines)
