"""左上角击杀播报（feed）识别。

图标语义（用户口径，别搞反）：

- **人物倒地剪影** = 击倒（knockdown），被打倒的人还能被拉起来
- **粗箭头 →** = 击杀（kill）
- 准星 ⊕ = 「精准」修饰，可能出现在任意事件前面
- 一行里可能有多个图标，也可能前后有多行（击倒后再终结＝两行）

速度策略（实测，这台机器）：

| 做法 | 耗时 |
|---|---|
| 整行 OCR（det+rec） | 852 ms |
| 整区模板匹配（双图标） | 5.6 ms |
| rec-only 认一段名字 | 15 ms |
| seek+解码一帧 | 92 ms |

所以绝不全帧 OCR：**先用图标模板找事件（5 ms），再只对那一行的两段名字做 rec-only（30 ms）**。
图标位置还顺带告出「击杀者名字到哪结束」，名字裁切不用猜宽度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "assets" / "templates"

# 图标 → 事件类型。文件名沿用最早起的名字，语义以注释为准：
#   down.png      = 人物倒地剪影 → 击倒
#   knockdown.png = 粗箭头       → 击杀
ICON_KIND = {"down": "knockdown", "knockdown": "kill"}
SCALES = (0.85, 0.9, 0.95, 1.0, 1.05, 1.1)
ICON_SCORE = 0.72
ROW_TOLERANCE = 18            # 同一行的 y 容差
NAME_FUZZY = 0.75             # 名字相似度阈值

# 播报行最左边的击杀者头像：位置固定（不随昵称长短变），基准是 1920×1080
PORTRAIT_DX = 40.0            # 相对播报区左边缘
PORTRAIT_DY = -12.0           # 相对行中心线
PORTRAIT_SIDE = 36.0
PORTRAIT_MIN_SCORE = 30.0
PORTRAIT_MIN_MARGIN = 10.0


@dataclass
class FeedIcon:
    kind: str                 # "kill" / "knockdown"
    x: int
    y: int
    w: int
    h: int
    score: float


@dataclass
class FeedRow:
    y: float
    icons: list[FeedIcon] = field(default_factory=list)
    killer: str = ""
    victim: str = ""
    killer_score: float = 0.0
    victim_score: float = 0.0
    killer_raw: str = ""          # OCR 原样读到的字，没对上昵称表时保留着给人看
    victim_raw: str = ""
    killer_matched: bool = False  # 是否对上了已知昵称（有昵称表时才可能为 True）
    victim_matched: bool = False
    killer_character: str = ""    # 播报行左边那个头像认出来的角色（计分板缺人时用它补）
    victim_character: str = ""
    character_inliers: float = 0.0

    @property
    def kinds(self) -> list[str]:
        seen = []
        for icon in self.icons:
            if icon.kind not in seen:
                seen.append(icon.kind)
        return seen

    def to_dict(self) -> dict:
        return {
            "y": round(self.y, 1),
            "kinds": self.kinds,
            "killer": self.killer,
            "victim": self.victim,
            "killer_raw": self.killer_raw,
            "victim_raw": self.victim_raw,
            "killer_matched": self.killer_matched,
            "victim_matched": self.victim_matched,
            "killer_character": self.killer_character,
            "victim_character": self.victim_character,
            "killer_score": round(self.killer_score, 3),
            "victim_score": round(self.victim_score, 3),
        }


def load_icons(folder: str | Path = TEMPLATES) -> dict[str, np.ndarray]:
    folder = Path(folder)
    icons = {}
    for stem in ICON_KIND:
        path = folder / f"{stem}.png"
        if not path.exists():
            continue
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            icons[stem] = image
    if not icons:
        raise FileNotFoundError(f"{folder} 里没有图标模板（需要 down.png / knockdown.png）")
    return icons


def find_icons(gray: np.ndarray, icons: dict[str, np.ndarray],
               min_score: float = ICON_SCORE, max_x: int | None = None) -> list[FeedIcon]:
    """在灰度区域里找事件图标。多个尺度取最大分，再做一次简单去重。

    ``max_x`` 用来挡掉画面右半部分的误匹配（角色立绘上也有类似剪影的图案，
    一旦被当成图标，「击杀者名字」的裁剪范围就会一路延到受害者名字里）。
    """
    found: list[FeedIcon] = []
    limit = gray.shape[1] if max_x is None else min(max_x, gray.shape[1])
    for stem, template in icons.items():
        best = np.full(gray.shape[:2], -1.0, dtype=np.float32)
        best_size = template.shape[:2]
        for scale in SCALES:
            tpl = cv2.resize(template, None, fx=scale, fy=scale) if scale != 1.0 else template
            th, tw = tpl.shape[:2]
            if th > gray.shape[0] or tw > limit:
                continue
            scores = cv2.matchTemplate(gray[:, :limit], tpl, cv2.TM_CCOEFF_NORMED)
            pad_y, pad_x = scores.shape
            region = best[:pad_y, :pad_x]
            np.maximum(region, scores, out=region)
        candidates = np.argwhere(best >= min_score)
        for y, x in candidates:
            found.append(FeedIcon(ICON_KIND[stem], int(x), int(y),
                                  int(best_size[1]), int(best_size[0]),
                                  float(best[y, x])))
    # 同一图标会被邻近像素重复命中，按 (kind, y/18, x/18) 去重，保留最高分
    deduped: dict[tuple, FeedIcon] = {}
    for icon in found:
        key = (icon.kind, icon.y // ROW_TOLERANCE, icon.x // ROW_TOLERANCE)
        if key not in deduped or icon.score > deduped[key].score:
            deduped[key] = icon
    return sorted(deduped.values(), key=lambda item: (item.y, item.x))


def rows_from_icons(icons: list[FeedIcon]) -> list[FeedRow]:
    """按 y 把图标聚成行。"""
    rows: list[FeedRow] = []
    for icon in sorted(icons, key=lambda item: item.y):
        for row in rows:
            if abs(row.y - icon.y) <= ROW_TOLERANCE:
                row.icons.append(icon)
                row.y = (row.y + icon.y) / 2
                break
        else:
            rows.append(FeedRow(y=float(icon.y), icons=[icon]))
    for row in rows:
        row.icons.sort(key=lambda item: item.x)
    return rows


def _name_crop(frame, x1: int, x2: int, y_center: int, half: int = 26):
    x1, x2 = max(0, int(x1)), int(x2)
    top, bottom = int(y_center - half), int(y_center + half)
    if x2 - x1 < 12 or bottom - top < 12:
        return None
    return frame[max(0, top):bottom, x1:x2]


def read_rows(frame, region: tuple[int, int, int, int], icons: dict[str, np.ndarray],
              recognizer=None, min_score: float = ICON_SCORE,
              max_icon_x: int = 620,
              known: list[str] | None = None,
              wanted: str | None = None) -> list[FeedRow]:
    """一帧 → 若干行（图标 + 击杀者名 + 受害者名）。

    ``known`` 是已知昵称表（来自计分板/顶部队列）：给了就只认表里的名字，
    对不上的保留 OCR 原文并标记 ``killer_matched=False``，让调用方决定收不收。
    播报里的名字只有十几个像素高，光看 OCR 经常错一两个字，对着昵称表反而稳。

    ``wanted`` 是"只关心谁的击杀"：给了就先只认击杀者名字，不是这个人的行
    直接跳过、不认受害者名字 —— 一次 OCR 大约 150 ms，省一半就是省一半。
    """
    x1, y1, x2, y2 = region
    patch = frame[y1:y2, x1:x2]
    if patch.size == 0:
        return []
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    icons_found = find_icons(gray, icons, min_score, max_x=int(max_icon_x - x1))
    if not icons_found:
        return []
    rows = rows_from_icons(icons_found)

    if recognizer is None:
        return rows

    for row in rows:
        first, last = row.icons[0], row.icons[-1]
        killer_box = _name_crop(patch, 6, first.x - 6, int(row.y))
        _fill_name(row, "killer", recognizer, killer_box, known)
        if wanted and not same_name(row.killer_raw or row.killer, wanted):
            continue
        victim_box = _name_crop(patch, last.x + last.w + 6, patch.shape[1], int(row.y))
        _fill_name(row, "victim", recognizer, victim_box, known)
    return rows


def _fill_name(row: FeedRow, attr: str, recognizer, box, known: list[str] | None) -> None:
    """认一格名字，顺手对着昵称表校正。"""
    if box is None:
        return
    text, score = recognizer(box)
    setattr(row, f"{attr}_raw", text)
    setattr(row, f"{attr}_score", score)
    matched = match_name(text, known) if known else ""
    setattr(row, attr, matched or text)
    setattr(row, f"{attr}_matched", bool(matched))


def portrait_box(row_y: float, scale: float) -> tuple[int, int, int, int]:
    """击杀者头像在播报区里的像素框（坐标都是播报区内的）。"""
    side = max(8, int(round(PORTRAIT_SIDE * scale)))
    x1 = int(round(PORTRAIT_DX * scale))
    y1 = int(round(row_y + PORTRAIT_DY * scale))
    return x1, y1, x1 + side, y1 + side


def read_character(patch: np.ndarray, row: FeedRow, scale: float, matcher,
                   min_score: float = PORTRAIT_MIN_SCORE,
                   min_margin: float = PORTRAIT_MIN_MARGIN) -> tuple[str, float]:
    """从播报行左边那个头像认角色 →（角色名, 内点）。

    播报里的小头像和计分板头像是同一套 128×128 图（`assets/characters/portraits`），
    认得出就是「谁杀了谁」的直接证据，计分板被红叉压住的人正好从这儿补。
    """
    x1, y1, x2, y2 = portrait_box(row.y, scale)
    top, bottom = max(0, y1), min(patch.shape[0], y2)
    left, right = max(0, x1), min(patch.shape[1], x2)
    if bottom - top < 8 or right - left < 8:      # 行滑出播报区时别拿半截图瞎认
        return "", 0.0
    return matcher.match(patch[top:bottom, left:right], min_score=min_score, min_margin=min_margin)


def name_score(left: str, right: str) -> float:
    """两段昵称的相似度：完全一样或互相包含算 1.0，其余用编辑距离比。"""
    left, right = (left or "").strip(), (right or "").strip()
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # 只有两边都够长（≥3 字）才算“互相包含就是同一个人”：
    # 否则播报里的一两个字会命中一堆长昵称（拉 → 拉薇）
    if min(len(left), len(right)) >= 3 and (left in right or right in left):
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _name_limit(left: str, right: str, threshold: float) -> float:
    # 短昵称（≤3 字）可靠字符少，阈值放宽：否则 爱菜菜/爱丫菜 这种同一个人的抖动会被当成两个人
    return 0.6 if min(len(left), len(right)) <= 3 else threshold


_PLAIN = re.compile(r"[\d\s.\-_|丨]+")
_CJK = re.compile(r"[^\u4e00-\u9fff]+")


def _cjk(text: str) -> str:
    """只留汉字：播报里的英文/数字常常是 OCR 噪声，比较汉字部分最稳。"""
    return _CJK.sub("", text or "")


def _one_off(left: str, right: str) -> bool:
    """同长度、只差一个字（薪膜 / 蒜膜，冥 / 寞）：形近字读错，当同一个人。"""
    return len(left) == len(right) >= 2 and sum(1 for a, b in zip(left, right) if a != b) <= 1


def _plain(text: str) -> str:
    return _PLAIN.sub("", text or "")


def subsequence_score(text: str, name: str) -> float:
    """播报里常把长昵称截短（香草之帆 / 香草一之濑帆波）：整段字按顺序都在就算认得出。

    返回 len(text)/len(name)，越接近 1 越像“只截了一点点”；不是按顺序的子序列返回 0。
    """
    text, name = text or "", name or ""
    if len(text) < 2 or len(text) >= len(name):
        return 0.0
    remaining = iter(name)
    if all(char in remaining for char in text):
        return len(text) / len(name)
    return 0.0


_TRAIL = ("击败", "击倒", "阵亡", "助攻", "存活", "得分", "输出", "成长")


def _trim(text: str) -> str:
    """播报行 OCR 常把右边的「击败/助攻」之类粘到名字后面，切掉。"""
    cut = len(text)
    for marker in _TRAIL:
        index = text.find(marker)
        if index >= 2:
            cut = min(cut, index)
    return text[:cut].strip()


def match_name(text: str, names: list[str], threshold: float = NAME_FUZZY) -> str:
    """把 OCR 的小段字对到昵称表里，返回表里的写法；对不上返回空串。"""
    text = _trim((text or "").strip())
    if not text or not names:
        return ""
    # 昵称里常混进 OCR 的尾随数字（爱丫菜菜71 / 爱菜菜7），去掉数字再比一次
    plain = _plain(text)
    if len(plain) < 2:            # 一个字认不出是谁
        return ""
    scores = []
    for name in names:
        if not name:
            continue
        plain_name = _plain(name)
        score = max(name_score(text, name), name_score(plain, plain_name))
        # 汉字部分单独比一次：英文昵称常有大小写/字母抖动，汉字反而稳
        left_cjk, right_cjk = _cjk(plain), _cjk(plain_name)
        if left_cjk and right_cjk:
            score = max(score, SequenceMatcher(None, left_cjk, right_cjk).ratio())
        # 只差一个字的短名（薪膜 / 蒜膜）
        if _one_off(plain, plain_name):
            score = 1.0
        scores.append((name, score))
    if not scores:
        return ""
    scores.sort(key=lambda item: item[1], reverse=True)
    best, best_score = scores[0]
    if best_score < _name_limit(text, best, threshold) and best_score >= 0.6:
        # 没那么像，但表里只有这一个人勉强对得上（Lbili小绘都oN → bilibili小绘郁），
        # 且第二名明显更差，认了比留个错字强
        runner = scores[1][1] if len(scores) > 1 else 0.0
        if runner <= 0.45:
            return best
    if best_score >= _name_limit(text, best, threshold):
        # 除非跟昵称一字不差，否则有人并列接近就别猜（香草一 → 香草一二 / 香草一三）
        exact = text == best or plain == _plain(best)
        if not exact and len(scores) > 1 and scores[1][1] >= best_score - 0.05:
            return ""
        return best
    # 截断的昵称：整段按顺序出现在某个昵称里，而且表里只有一个这样的人，才敢认
    hits = [name for name in names if name and subsequence_score(plain, _plain(name)) > 0]
    if len(hits) == 1:
        return hits[0]
    # 最后再试「最长公共前缀」：OCR 往往在名字后面粘了一串界面文字
    best_prefix, best_len, ambiguous = "", 0, False
    for name in names:
        if not name:
            continue
        size = 0
        while size < min(len(text), len(name)) and text[size] == name[size]:
            size += 1
        if size > best_len:
            best_prefix, best_len, ambiguous = name, size, False
        elif size == best_len and size > 0:
            ambiguous = True
    if best_len >= 3 and not ambiguous and best_len >= len(text) - 2:
        return best_prefix
    return ""


def canonicalize_events(events: list[dict], names: list[str]) -> int:
    """用昵称表把播报里的击杀者/受害者名字改回正写法，返回改了几处。"""
    if not events or not names:
        return 0
    changed = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        for field in ("killer", "victim"):
            raw = (event.get(field) or "").strip()
            if not raw:
                continue
            better = match_name(raw, names)
            if better and better != raw:
                event[field] = better
                event[f"{field}_raw"] = raw
                changed += 1
    return changed


def same_name(left: str, right: str, threshold: float = NAME_FUZZY) -> bool:
    """昵称比对：OCR 常错一两个字，用相似度兜。"""
    left, right = (left or "").strip(), (right or "").strip()
    if not left or not right:
        return False
    limit = _name_limit(left, right, threshold)
    if name_score(left, right) >= limit:
        return True
    # 昵称里常混进 OCR 的尾随数字（爱丫菜菜71 / 爱菜菜7），去掉数字再比一次
    strip = lambda text: re.sub(r"[\d\s.\-_|丨]+", "", text)
    return name_score(strip(left), strip(right)) >= limit
