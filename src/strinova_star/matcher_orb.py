"""ORB 匹配（默认算法）。

ORB = oFAST 关键点 + rBRIEF 二进制描述子，匹配用 Hamming 距离。
优点：开源无专利、OpenCV 主包自带、快。

参数是照着实测表定的（20 张局内真值，24 个模板 × 3 尺度）：

    nfeatures / patchSize   命中    耗时/张    平均第一二名内点差
    800  / 21               20/20    63 ms       69
    1200 / 31               20/20    98 ms       93
    2000 / 31               20/20   171 ms      114   ← 现在用的

关键点数越多、patch 越大，内点差拉得越开（认得更稳），代价是慢。
171 ms 对「一局读一次计分板（10 张）」是可以接受的（约 1.7 s），
如果要逐帧实时识别，把 ``NFEATURES``/``PATCH_SIZE`` 调回 800/21。

提醒：参考文献（cnblogs 那篇）用的是 ``crossCheck=True`` + 按距离取前 60 + findHomography。
实测同样的 20 张：命中也是 20/20，但**每张 2.1~2.4 s（慢 30 倍）**，内点差反而只有 31~40
——因为 crossCheck 会强制全对比，好匹配里混进大量离群点。所以这里仍用
``knnMatch(k=2) + Lowe ratio``，靠 ratio 先把离群点滤掉，再 RANSAC 数内点。
"""

from __future__ import annotations

import cv2

from .features import FeatureMatcher

__all__ = ["OrbMatcher"]

NFEATURES = 2000
PATCH_SIZE = 31


class OrbMatcher(FeatureMatcher):
    SCALES = (1.0, 1.5, 2.0)      # 立绘 224×320 vs 局内贴图 48×38，尺度差太大
    QUERY_UPSCALE = 4.0
    MIN_QUERY_SIDE = 120
    RATIO = 0.75
    RANSAC_THRESHOLD = 3.0
    MIN_SCORE = 12
    MIN_MARGIN = 4

    def create_detector(self):
        return cv2.ORB_create(nfeatures=NFEATURES, fastThreshold=10, edgeThreshold=6,
                              patchSize=PATCH_SIZE, scaleFactor=1.2)

    def create_matcher(self):
        return cv2.BFMatcher(cv2.NORM_HAMMING)
