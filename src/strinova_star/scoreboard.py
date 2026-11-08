"""Tab 计分板：一帧截图 → 10 条「昵称 + 角色」。

设计要点（都是实机踩出来的）：

1. **行位置从 OCR 反推，不写死 y**。同一台机器两次截图，我方首行一个在 501、一个在 517，
   面板会整体上下浮动，固定坐标必然错位。
2. **头像裁在昵称行首的左边**，而不是用绝对 x。行首 x 是天然锚点，换分辨率、换 UI 缩放都跟着走。
3. **10 个人一人一角色**用 `features.assign()` 做整体最优分配，避免「同一个人被认两次」
   这种各自取最大值才会犯的错。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import geometry, ocr
from .features import Candidate, assign, imread as _default_imread

# 计分板表头与杂项，绝不能当成人名
HEADER_WORDS = (
    "击败", "阵亡", "助攻", "绝招", "成长等级", "主武器", "输出分", "技能分", "协作分",
    "PING", "ping", "回合", "准备完毕", "准备阶段", "我方", "对方", "玩家", "对战报告",
    "对手", "剪刀手", "欧泊", "乌尔比诺", "隐藏", "显示", "切换鼠标", "目标", "准备", "准备完毕", "XX",
)

REGION_NAME = "names"          # profile.regions.names：昵称列的粗框
PORTRAIT_W = 56                # 头像宽度（2560×1600 实测 52，留点余量）
PORTRAIT_GAP = 4               # 头像右缘与昵称左缘之间的空隙
ROW_TOLERANCE = 14             # 同一行的 y 容差
MIN_NAME_SCORE = 0.5


@dataclass
class RosterRow:
    nickname: str
    y: float
    side: str = ""             # "ally" / "enemy" / ""
    character: str = ""
    inliers: float = 0.0
    margin: float = 0.0
    portrait_box: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict:
        return {
            "nickname": self.nickname,
            "y": round(self.y, 1),
            "side": self.side,
            "character": self.character,
            "inliers": round(float(self.inliers), 1),
            "margin": round(float(self.margin), 1),
        }


@dataclass
class Scoreboard:
    rows: list[RosterRow] = field(default_factory=list)
    scale: tuple[float, float] = (1.0, 1.0)
    column: tuple[int, int, int, int] | None = None

    @property
    def ally(self) -> list[RosterRow]:
        return [r for r in self.rows if r.side == "ally"]

    @property
    def enemy(self) -> list[RosterRow]:
        return [r for r in self.rows if r.side == "enemy"]

    def to_dict(self) -> dict:
        return {
            "scale": [round(self.scale[0], 4), round(self.scale[1], 4)],
            "column": list(self.column) if self.column else None,
            "count": len(self.rows),
            "rows": [r.to_dict() for r in self.rows],
        }


def is_name(text: str) -> bool:
    """粗判一行文字像不像昵称：不是表头、不是比分/KDA、至少 2 个字符。"""
    text = (text or "").strip()
    if len(text) < 2:
        return False
    for word in HEADER_WORDS:
        if word in text:
            return False
    # 纯数字/斜杠/百分号（KDA、比分、时间、伤害）
    if all(ch.isdigit() or ch in "/.%:+- " for ch in text):
        return False
    # 数字占比过高也不像昵称（如 "69岁扶墙电竞" 只占 2 位，仍可通过）
    digits = sum(ch.isdigit() for ch in text)
    if digits and digits / len(text) > 0.7:
        return False
    return True


def rows_from_lines(lines: list[dict], column: tuple[int, int, int, int] | None = None,
                    min_score: float = MIN_NAME_SCORE) -> list[dict]:
    """从 OCR 结果里挑出昵称行：按 x 落在昵称列、按 y 去重。"""
    picked = []
    for line in lines:
        if line.get("score", 0) < min_score:
            continue
        if not is_name(line.get("text", "")):
            continue
        if column is not None:
            x1, y1, x2, y2 = column
            if not (x1 <= line["x1"] <= x2 and y1 <= line["y1"] <= y2):
                continue
        picked.append(line)

    picked.sort(key=lambda item: ((item["y1"] + item["y2"]) / 2, item["x1"]))
    deduped: list[dict] = []
    for line in picked:
        center = (line["y1"] + line["y2"]) / 2
        for kept in deduped:
            if abs((kept["y1"] + kept["y2"]) / 2 - center) <= ROW_TOLERANCE:
                if line["score"] > kept["score"]:
                    kept.update(line)
                break
        else:
            deduped.append(dict(line))
    return deduped


def split_sides(rows: list[RosterRow]) -> None:
    """按 y 最大空隙切成两半（我方 / 对方）。空隙不明显就都不分。"""
    if len(rows) < 2:
        return
    ordered = sorted(rows, key=lambda r: r.y)
    gaps = [(ordered[i + 1].y - ordered[i].y, i) for i in range(len(ordered) - 1)]
    biggest, index = max(gaps)
    typical = sorted(g for g, _ in gaps)[len(gaps) // 2]
    if typical <= 0 or biggest < typical * 1.5:
        return
    for row in ordered[: index + 1]:
        row.side = "ally"
    for row in ordered[index + 1:]:
        row.side = "enemy"


def portrait_box(row: dict) -> tuple[int, int, int, int]:
    """头像框：贴在昵称行首左边。"""
    center_y = (row["y1"] + row["y2"]) / 2
    half = max(12.0, (row["y2"] - row["y1"]) * 0.9)
    x2 = row["x1"] - PORTRAIT_GAP
    return (int(x2 - PORTRAIT_W), int(center_y - half), int(x2), int(center_y + half))


def read_scoreboard(image, profile: dict, matcher, engine=None, column=None,
                    use_assign: bool = True, min_score: float = MIN_NAME_SCORE) -> Scoreboard:
    """一张计分板截图 → 10 条昵称/角色。matcher 是 features.FeatureMatcher。"""
    profile = geometry.for_size(profile, (image.shape[1], image.shape[0]))
    if column is None:
        try:
            column = geometry.region(profile, REGION_NAME)
        except geometry.GeometryError:
            column = None

    if column is not None:
        crop_box = geometry.clamp(column, (image.shape[1], image.shape[0]))
        patch = image[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]]
        lines = ocr.read_text(patch, crop_box[0], crop_box[1], engine)
    else:
        lines = ocr.read_text(image, 0, 0, engine)

    rows = [RosterRow(nickname=item["text"], y=(item["y1"] + item["y2"]) / 2)
            for item in rows_from_lines(lines, column, min_score)]
    if not rows:
        return Scoreboard(rows=[], scale=geometry.scale_for(profile, "height"), column=column)

    split_sides(rows)

    # 头像 → 角色：先各自打分，再整体分配
    keep: list[tuple[RosterRow, "object"]] = []
    for row in rows:
        source = next((item for item in lines if item["text"] == row.nickname), None)
        if source is None:
            continue
        row.portrait_box = portrait_box(source)
        crop = geometry.crop(image, row.portrait_box)
        if crop is None:
            continue
        keep.append((row, crop))

    if keep:
        valid_rows = [row for row, _ in keep]
        valid_crops = [crop for _, crop in keep]
        # 每张裁片只算一遍分数：既给「第二名称差」用，也给指派用
        matrix = np.array([matcher.score_row(crop) for crop in valid_crops], dtype=float)
        ranked = [[Candidate(matcher.names[index], float(matrix[row, index]))
                   for index in np.argsort(-matrix[row])] for row in range(len(valid_crops))]
        if use_assign:
            picked = assign(matcher, valid_crops, matrix=matrix)
        else:
            picked = [matcher.match(crop) for crop in valid_crops]
        for row, (character, score), score_list in zip(valid_rows, picked, ranked):
            row.character = character
            row.inliers = float(score)
            if len(score_list) >= 2:
                row.margin = float(score_list[0].score - score_list[1].score)

    return Scoreboard(rows=rows, scale=geometry.scale_for(profile, "height"), column=column)


def format_table(scoreboard: Scoreboard) -> str:
    """给命令行打印用。"""
    if not scoreboard.rows:
        return "（没读到昵称行）"
    side_label = {"ally": "我方", "enemy": "对方", "": "——"}
    width = max(len(row.nickname) for row in scoreboard.rows)
    lines = []
    for row in scoreboard.rows:
        lines.append(
            f"{side_label.get(row.side, '——')}  {row.nickname:<{width}}  "
            f"{row.character or '未识别':<8} 内点 {row.inliers:>4.0f} 差 {row.margin:>4.0f}"
        )
    return "\n".join(lines)
