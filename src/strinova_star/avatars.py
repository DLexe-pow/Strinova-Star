"""兼容层：老的 ``from strinova_star.avatars import OrbMatcher`` 仍然可用。

真正的实现拆到了这几个文件，按算法一种一个：

    features.py       公共地基（读图、头部窗、RANSAC 打分、指派）
    matcher_orb.py    ORB   —— 默认
    matcher_sift.py   SIFT
    matcher_surf.py   SURF  （要 opencv-contrib）
    matchers.py       注册表 + 工厂 ``make("orb"|"sift"|"surf")``
    legacy_hist.py    旧的直方图方案（对照用）

新代码请直接从 :mod:`strinova_star.matchers` 取，
例如 ``make("sift")``。
"""

from __future__ import annotations

from .features import (  # noqa: F401
    AVATARS,
    HALFBODY,
    PORTRAITS,
    ROOT,
    TEMPLATE_ASPECT,
    TEMPLATE_TOP,
    Candidate,
    FeatureMatcher,
    assign,
    center_square,
    head_window,
    imread,
    solve,
)
from .legacy_hist import (  # noqa: F401
    BACKGROUND_DELTA,
    BRIGHT_WEIGHT,
    FEATURE_SIZE,
    HIST_BINS,
    HIST_WEIGHT,
    HalfbodyMatcher,
    Template,
    compare,
    query_features,
    template_features,
)
from .matcher_orb import OrbMatcher  # noqa: F401
from .matcher_sift import SiftMatcher  # noqa: F401
from .matcher_surf import SurfMatcher, SurfUnavailable, is_available as surf_available  # noqa: F401
from .matchers import DEFAULT, MATCHERS, build, describe, make  # noqa: F401

__all__ = [
    "AVATARS",
    "BACKGROUND_DELTA",
    "BRIGHT_WEIGHT",
    "Candidate",
    "DEFAULT",
    "FEATURE_SIZE",
    "FeatureMatcher",
    "HALFBODY",
    "HIST_BINS",
    "HIST_WEIGHT",
    "HalfbodyMatcher",
    "MATCHERS",
    "OrbMatcher",
    "PORTRAITS",
    "ROOT",
    "SiftMatcher",
    "SurfMatcher",
    "SurfUnavailable",
    "TEMPLATE_ASPECT",
    "TEMPLATE_TOP",
    "Template",
    "assign",
    "build",
    "center_square",
    "compare",
    "describe",
    "head_window",
    "imread",
    "make",
    "query_features",
    "solve",
    "surf_available",
    "template_features",
]
