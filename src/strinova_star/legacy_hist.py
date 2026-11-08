"""旧的「颜色/亮度直方图」方案，留作对照。

不做特征点，直接把头部窗缩到 64×64，比较两张图的颜色分布和亮度分布：

    分数 = 0.75 × (1 − H-S 二维直方图 Bhattacharyya 距离) + 0.25 × 亮度分布相关

实测也能 20/20，但它靠的是经验权重，换皮肤/换打光未必稳；ORB/SIFT/SURF 认的是
特征点之间的几何关系，对配色变化更鲁棒。保留它是为了 `--method hist` 能对比。
"""

from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass
from pathlib import Path

from .features import (
    HALFBODY,
    TEMPLATE_TOP,
    Candidate,
    center_square,
    head_window,
    imread,
)

FEATURE_SIZE = 64
HIST_BINS = (24, 8)            # H × S
HIST_WEIGHT = 0.75
BRIGHT_WEIGHT = 0.25
BACKGROUND_DELTA = 28          # 与边框中位色的距离阈值：超过才算前景
MIN_UPSCALE_SIDE = 80          # 小于这个边长的裁片先放大一倍再取特征

__all__ = ["HalfbodyMatcher", "Template", "compare", "query_features", "template_features"]


def _hs_hist(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, list(HIST_BINS), [0, 180, 0, 256]).astype(np.float32)
    hist /= hist.sum() + 1e-6
    return hist


def _bright_hist(gray: np.ndarray, mask: np.ndarray) -> np.ndarray:
    values = gray[mask > 0]
    if values.size < 50:
        values = gray.ravel()
    hist, _ = np.histogram(values, bins=32, range=(0, 256), density=True)
    return hist.astype(np.float32)


@dataclass
class Template:
    character: str
    hist: np.ndarray
    bright: np.ndarray
    mask: np.ndarray


def template_features(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    window = cv2.resize(head_window(rgba), (FEATURE_SIZE, FEATURE_SIZE), interpolation=cv2.INTER_AREA)
    alpha = (window[:, :, 3] > 40).astype(np.uint8)
    bgr = window[:, :, :3]
    if alpha.sum() < 200:                      # 没有 alpha 的图当作整幅可用
        alpha = np.ones((FEATURE_SIZE, FEATURE_SIZE), np.uint8)
    return _hs_hist(bgr, alpha), _bright_hist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), alpha), alpha


def query_features(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    image = image[:, :, :3] if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    # 计分板行的小头像只有 ~48×38，先放大一倍再取特征，细节才够（实测 19/20 → 20/20）
    if min(image.shape[:2]) < MIN_UPSCALE_SIDE:
        image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    square = center_square(image)
    square = cv2.resize(square, (FEATURE_SIZE, FEATURE_SIZE), interpolation=cv2.INTER_AREA)
    bgr = square[:, :, :3]
    ring = np.concatenate(
        [bgr[:2].reshape(-1, 3), bgr[-2:].reshape(-1, 3), bgr[:, :2].reshape(-1, 3), bgr[:, -2:].reshape(-1, 3)]
    )
    backdrop = np.median(ring, axis=0)
    delta = np.linalg.norm(bgr.astype(np.float32) - backdrop, axis=2)
    mask = (delta > BACKGROUND_DELTA).astype(np.uint8)
    if mask.sum() < 200:                       # 背景抠不出来就整幅算
        mask = np.ones((FEATURE_SIZE, FEATURE_SIZE), np.uint8)
    return _hs_hist(bgr, mask), _bright_hist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), mask), mask


def compare(query: tuple[np.ndarray, np.ndarray, np.ndarray], tpl: Template) -> float:
    q_hist, q_bright, q_mask = query
    overlap = (q_mask > 0) & (tpl.mask > 0)
    if overlap.sum() < 50:
        overlap = q_mask > 0
    colour = 1.0 - float(cv2.compareHist(tpl.hist, q_hist, cv2.HISTCMP_BHATTACHARYYA))
    brightness = float(np.corrcoef(tpl.bright, q_bright)[0, 1])
    if not np.isfinite(brightness):
        brightness = 0.0
    return HIST_WEIGHT * colour + BRIGHT_WEIGHT * (brightness + 1.0) / 2.0


class HalfbodyMatcher:
    """直方图版角色识别器（0~1 分，阈值 ``min_score=0.45``）。"""

    MIN_SCORE = 0.45
    MIN_MARGIN = 0.02

    def __init__(self, folder: str | Path = HALFBODY, top: float = TEMPLATE_TOP) -> None:
        self.folder = Path(folder)
        if not self.folder.is_dir():
            raise ValueError(f"模板目录不存在：{self.folder}")
        self.top = top
        self.templates: list[Template] = []
        for path in sorted(self.folder.glob("*.png")):
            if path.stem.startswith("_"):
                continue
            hist, bright, mask = template_features(imread(path))
            self.templates.append(Template(path.stem, hist, bright, mask))
        if not self.templates:
            raise ValueError(f"{self.folder} 里没有模板图")
        self.names = [t.character for t in self.templates]

    def __len__(self) -> int:
        return len(self.templates)

    def score_row(self, image: np.ndarray) -> np.ndarray:
        q = query_features(image)
        return np.array([compare(q, t) for t in self.templates], dtype=float)

    def scores(self, image: np.ndarray) -> list[Candidate]:
        row = self.score_row(image)
        order = np.argsort(-row)
        return [Candidate(self.names[i], float(row[i])) for i in order]

    def match(self, image: np.ndarray, min_score: float | None = None,
              min_margin: float | None = None) -> tuple[str, float]:
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
