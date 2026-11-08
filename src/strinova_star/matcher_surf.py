"""SURF 匹配（需要 opencv-contrib）。

SURF 用积分图上的 Hessian 行列式定关键点、Haar 小波响应做描述子，是同家族里
比 SIFT 快的近似版。它属于「non-free」模块，**不在 opencv-python 主包里**：

    pip install opencv-contrib-python

装好之后 ``cv2.xfeatures2d.SURF_create``（OpenCV 4.4 及更早）或
``cv2.SURF_create``（部分发行版）就能用。本机当前环境（OpenCV 5.0.0 主包）
两者都没有，所以这里做了运行时探测：能造出检测器就用，
造不出来就抛 :class:`SurfUnavailable`，由调用方决定跳过还是提示。
"""

from __future__ import annotations

import cv2

from .features import FeatureMatcher

__all__ = ["SurfMatcher", "SurfUnavailable", "is_available"]


class SurfUnavailable(RuntimeError):
    """当前 OpenCV 构建里没有 SURF。"""


def _factory():
    for module in ("xfeatures2d",):
        holder = getattr(cv2, module, None)
        if holder is not None and hasattr(holder, "SURF_create"):
            return holder.SURF_create
    if hasattr(cv2, "SURF_create"):
        return cv2.SURF_create
    return None


def is_available() -> bool:
    return _factory() is not None


class SurfMatcher(FeatureMatcher):
    SCALES = (1.0, 1.5, 2.0)
    QUERY_UPSCALE = 4.0
    MIN_QUERY_SIDE = 120
    RATIO = 0.8           # SURF 描述子维度低，比率阈值放宽一点
    RANSAC_THRESHOLD = 3.0
    MIN_SCORE = 10
    MIN_MARGIN = 4

    def create_detector(self):
        factory = _factory()
        if factory is None:
            raise SurfUnavailable(
                "当前 OpenCV 构建没有 SURF（属于 contrib/non-free 模块）。\n"
                f"  当前版本：{cv2.__version__}\n"
                "  解决：pip install opencv-contrib-python，然后重跑\n"
                "  临时替代：--method orb 或 --method sift"
            )
        # extended=False → 64 维、upright=True → 不估方向，比默认快一倍
        return factory(hessianThreshold=400, extended=False, upright=False)

    def create_matcher(self):
        # 非 extended 的 SURF 描述子是 64 维 float，走 L2
        return cv2.BFMatcher(cv2.NORM_L2)
