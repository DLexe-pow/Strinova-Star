"""SIFT 匹配。

SIFT = 高斯差分金字塔找尺度空间极值 + 128 维浮点梯度方向直方图描述子。
相对 ORB 的优势是**真正的尺度不变**（金字塔内自动定尺度），对亮度变化也更稳；
代价是慢，且描述子是 float，必须用 L2 距离。

实测（20 张局内真值，24 模板 × 3 尺度）：

    nfeatures / contrast / 查询放大    lineup   roster   合计    耗时/张
    600  / 0.02  / 4×                 10/10     7/10   17/20    30 ms
    1000 / 0.01  / 6×                 10/10     8/10   18/20    47 ms   ← 现在用的
    1500 / 0.005 / 6×                 10/10     4/10   14/20    67 ms

结论：**SIFT 认顶部阵容条（85×52）很好，认 Tab 计分板那一行（48×38）不行。**
原因很直白——48×38 放大 6 倍靠的是插值，DoG 金字塔在这种糊图上找不到有意义的极值，
关键点只有个位数；ORB 的 FAST 是像素邻域比较，对糊图宽容得多。
所以默认仍是 ORB，SIFT 作为「阵容条专用」或对照留在这里。
"""

from __future__ import annotations

import cv2

from .features import FeatureMatcher

__all__ = ["SiftMatcher"]


class SiftMatcher(FeatureMatcher):
    SCALES = (1.0, 1.5, 2.0)
    QUERY_UPSCALE = 6.0
    MIN_QUERY_SIDE = 120
    RATIO = 0.75
    RANSAC_THRESHOLD = 3.0
    MIN_SCORE = 6           # SIFT 关键点少，内点阈值跟着降
    MIN_MARGIN = 3

    def create_detector(self):
        return cv2.SIFT_create(nfeatures=1000, contrastThreshold=0.01, edgeThreshold=10, sigma=1.6)

    def create_matcher(self):
        return cv2.BFMatcher(cv2.NORM_L2)
