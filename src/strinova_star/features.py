"""角色识别的公共地基。

三种算法（ORB / SIFT / SURF）共用这里的东西：读图、取头部窗、读模板目录、
「好匹配 → RANSAC 内点」这套打分，以及一人一角色的指派（``assign``）。

算法本身在各自文件里：
    matcher_orb.py    ORB  —— 二进制描述子 + Hamming，最快，默认
    matcher_sift.py   SIFT —— 浮点描述子 + L2，稳但慢一些
    matcher_surf.py   SURF —— 浮点描述子 + L2，需要 opencv-contrib

三者都继承 :class:`FeatureMatcher`，接口一致：
``names`` / ``score_row(image)`` / ``scores(image)`` / ``match(image)``。
"""

from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AVATARS = ROOT / "assets" / "characters" / "avatar"
HALFBODY = ROOT / "assets" / "characters" / "halfbody"
PORTRAITS = ROOT / "assets" / "characters" / "portraits"

TEMPLATE_ASPECT = 85 / 52      # 局内小头像的宽高比
TEMPLATE_TOP = 0.12            # 头部窗的顶部偏移（占立绘高度）


def imread(path: str | Path) -> np.ndarray:
    """读图（保留 alpha）。Windows 上 cv2.imread 不认中文路径，必须走 imdecode。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"读不到图片：{path}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 3:
        alpha = np.full(image.shape[:2], 255, np.uint8)
        image = np.dstack([image, alpha])
    return image


def center_square(image: np.ndarray) -> np.ndarray:
    """取中心方窗：模板和查询都要先做这一步，两边才有可比性。"""
    h, w = image.shape[:2]
    side = min(h, w)
    top = (h - side) // 2
    left = (w - side) // 2
    return image[top:top + side, left:left + side]


def head_window(rgba: np.ndarray, top: float = TEMPLATE_TOP, aspect: float = TEMPLATE_ASPECT) -> np.ndarray:
    """从半身立绘里取「头部」那块：整幅宽、按 85:52 定高、顶部往下偏 ``top``。"""
    h, w = rgba.shape[:2]
    width = w
    height = int(round(width / aspect))
    if height > h:
        height = h
        width = int(round(height * aspect))
    y0 = min(int(round(h * top)), max(0, h - height))
    x0 = (w - width) // 2
    return rgba[y0:y0 + height, x0:x0 + width]


@dataclass
class Candidate:
    character: str
    score: float


class FeatureMatcher:
    """特征点匹配的公共实现，子类只提供「怎么造检测器」和几个参数。"""

    # 子类覆盖
    SCALES: tuple[float, ...] = (1.0, 1.5, 2.0)
    QUERY_UPSCALE = 4.0
    MIN_QUERY_SIDE = 120
    RATIO = 0.75
    RANSAC_THRESHOLD = 3.0
    MIN_SCORE = 12
    MIN_MARGIN = 4

    def __init__(self, folder: str | Path = HALFBODY, top: float = TEMPLATE_TOP) -> None:
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise ValueError(f"模板目录不存在：{self.folder}")
        self.top = top
        self.detector = self.create_detector()
        self.matcher = self.create_matcher()
        self.names: list[str] = []
        self._templates: list[list[tuple[tuple, np.ndarray | None]]] = []
        for path in sorted(self.folder.glob("*.png")):
            if path.stem.startswith("_"):
                continue
            art = imread(path)
            self.names.append(path.stem)
            self._templates.append([self._describe(art, scale) for scale in self.SCALES])
        if not self.names:
            raise ValueError(f"{self.folder} 里没有模板图")

    # --- 子类实现 -------------------------------------------------------
    def create_detector(self):
        raise NotImplementedError

    def create_matcher(self):
        raise NotImplementedError

    # --- 公共流程 -------------------------------------------------------
    def __len__(self) -> int:
        return len(self.names)

    def _describe(self, rgba: np.ndarray, scale: float) -> tuple[tuple, np.ndarray | None]:
        """模板侧：取头部窗 → 按尺度放大 → 透明区涂黑并作掩膜 → 提特征。"""
        window = head_window(rgba, top=self.top)
        size = (max(1, int(round(window.shape[1] * scale))), max(1, int(round(window.shape[0] * scale))))
        bgr = cv2.resize(window[:, :, :3], size, interpolation=cv2.INTER_CUBIC)
        alpha = cv2.resize(window[:, :, 3], size, interpolation=cv2.INTER_NEAREST)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray[alpha < 40] = 0
        return self.detector.detectAndCompute(gray, (alpha > 40).astype(np.uint8) * 255)

    def _query(self, image: np.ndarray) -> tuple[tuple, np.ndarray | None]:
        """查询侧：中心方窗（太小先放大）→ 提特征。"""
        bgr = image[:, :, :3] if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        square = center_square(bgr)
        if min(square.shape[:2]) < self.MIN_QUERY_SIDE:
            square = cv2.resize(square, None, fx=self.QUERY_UPSCALE, fy=self.QUERY_UPSCALE,
                                interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(square, cv2.COLOR_BGR2GRAY)
        return self.detector.detectAndCompute(gray, None)

    def _inliers(self, query, template) -> int:
        """好匹配（Lowe ratio）→ 用 RANSAC 估一个仿射变换 → 数内点。

        只用「好匹配数」不做几何校验实测只有 16/20（令→蕾欧娜、绯莎→玛德蕾娜 这类），
        数内点后才 20/20 —— 这一步不能省。
        """
        keypoints, descriptors = query
        tpl_keypoints, tpl_descriptors = template
        if descriptors is None or tpl_descriptors is None:
            return 0
        if len(descriptors) < 4 or len(tpl_descriptors) < 4:
            return 0
        pairs = self.matcher.knnMatch(descriptors, tpl_descriptors, k=2)
        good = [a for a, b in (pair for pair in pairs if len(pair) == 2) if a.distance < self.RATIO * b.distance]
        if len(good) < 4:
            return 0
        src = np.float32([keypoints[a.queryIdx].pt for a in good]).reshape(-1, 1, 2)
        dst = np.float32([tpl_keypoints[a.trainIdx].pt for a in good]).reshape(-1, 1, 2)
        _, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                              ransacReprojThreshold=self.RANSAC_THRESHOLD)
        return int(mask.sum()) if mask is not None else 0

    def score_row(self, image: np.ndarray) -> np.ndarray:
        """每个角色的分数（列序与 ``names`` 一致，``assign`` 依赖它）。"""
        query = self._query(image)
        return np.array([max(self._inliers(query, t) for t in templates) for templates in self._templates],
                        dtype=float)

    def scores(self, image: np.ndarray) -> list[Candidate]:
        row = self.score_row(image)
        order = np.argsort(-row)
        return [Candidate(self.names[i], float(row[i])) for i in order]

    def match(self, image: np.ndarray, min_score: float | None = None,
              min_margin: float | None = None) -> tuple[str, float]:
        """单人识别；分数不够或前两名太近就返回空串（宁可不认，不认错）。"""
        min_score = self.MIN_SCORE if min_score is None else min_score
        min_margin = self.MIN_MARGIN if min_margin is None else min_margin
        row = self.score_row(image)
        order = np.argsort(-row)
        best = float(row[order[0]])
        if best < min_score:
            return "", best
        if len(order) > 1 and best - float(row[order[1]]) < min_margin:
            return "", best
        return self.names[order[0]], best


def assign(matcher: FeatureMatcher, crops: list[np.ndarray], min_score: float | None = None,
           matrix: np.ndarray | None = None) -> list[tuple[str, float]]:
    """一批裁片 → 角色，一人一个（用于「先认全 5+5 个角色再配昵称」）。

    矩阵列序必须与 ``matcher.names`` 一致，用 :meth:`FeatureMatcher.score_row`。
    已经算过 ``score_row`` 的调用方可以把结果从 ``matrix`` 传进来，省一次重算。
    """
    if not crops:
        return []
    min_score = matcher.MIN_SCORE if min_score is None else min_score
    if matrix is None:
        matrix = np.array([matcher.score_row(crop) for crop in crops], dtype=float)
    else:
        matrix = np.asarray(matrix, dtype=float)
    picks = solve(matrix, maximize=True)
    out: list[tuple[str, float]] = []
    for row, col in enumerate(picks):
        if col < 0:
            out.append(("", 0.0))
            continue
        value = float(matrix[row, col])
        out.append((matcher.names[col] if value >= min_score else "", value))
    return out


def solve(matrix: np.ndarray, maximize: bool = True) -> list[int]:
    """指派问题（匈牙利/JV 算法）。返回**每一行**对应的列号，-1 表示未分配。

    返回长度恒等于 ``matrix.shape[0]``。行比列多时内部转置求解，再把结果翻回来
    （这一步以前写反过，导致 ``assign`` 返回 24 个值、还抛过 IndexError）。
    """
    m = np.asarray(matrix, dtype=float)
    if m.size == 0:
        return []
    n_rows, n_cols = m.shape
    if n_rows == 0 or n_cols == 0:
        return [-1] * n_rows
    weight = -m if maximize else m
    transposed = n_rows > n_cols
    if transposed:
        weight = weight.T
        a, b = n_cols, n_rows          # a = 行数（≤ b）
    else:
        a, b = n_rows, n_cols
    u = np.zeros(a + 1)
    v = np.zeros(b + 1)
    p = np.zeros(b + 1, dtype=int)     # p[j] = 第 j 列匹配到的行（1 起）
    way = np.zeros(b + 1, dtype=int)
    for i in range(1, a + 1):
        p[0] = i
        j0 = 0
        minv = np.full(b + 1, np.inf)
        used = np.zeros(b + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = np.inf
            j1 = 0
            for j in range(1, b + 1):
                if used[j]:
                    continue
                cur = weight[i0 - 1, j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(b + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    column_of_row = [-1] * a           # 行下标 → 列下标（均以 weight 为准）
    for j in range(1, b + 1):
        if p[j]:
            column_of_row[p[j] - 1] = j - 1
    if not transposed:
        return column_of_row
    out = [-1] * n_rows                # 翻回来：weight 的行 = 原矩阵的列
    for w_row, w_col in enumerate(column_of_row):
        if w_col >= 0:
            out[w_col] = w_row
    return out
