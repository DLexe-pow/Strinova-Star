"""三种角色识别算法的注册表。

    from strinova_star.matchers import MATCHERS, describe, make

    matcher = make("orb")          # 默认
    matcher = make("sift")
    matcher = make("surf")         # 没装 opencv-contrib 会抛 SurfUnavailable

也保留旧的直方图那套（``hist``）做对照，实现在 :mod:`strinova_star.legacy_hist`。
"""

from __future__ import annotations

from .features import FeatureMatcher
from .matcher_orb import OrbMatcher
from .matcher_sift import SiftMatcher
from .matcher_surf import SurfMatcher, SurfUnavailable, is_available as surf_available

__all__ = [
    "MATCHERS",
    "DEFAULT",
    "FeatureMatcher",
    "SurfUnavailable",
    "build",
    "describe",
    "make",
    "surf_available",
]

MATCHERS: dict[str, type[FeatureMatcher]] = {
    "orb": OrbMatcher,
    "sift": SiftMatcher,
    "surf": SurfMatcher,
}

DEFAULT = "orb"

NOTES = {
    "orb": "二进制描述子 + Hamming，默认（2000 特征点，约 175 ms/张）",
    "sift": "浮点 128 维 + L2，尺度不变更好，慢一些",
    "surf": "浮点 64 维 + L2，需要 opencv-contrib",
}


def make(name: str = DEFAULT, folder=None, top: float | None = None) -> FeatureMatcher:
    """按名字造一个匹配器。``folder`` 省略就用 assets/characters/halfbody。"""
    key = (name or DEFAULT).lower()
    if key not in MATCHERS:
        raise KeyError(f"未知算法 {name!r}，可选：{', '.join(sorted(MATCHERS))}")
    cls = MATCHERS[key]
    kwargs = {}
    if folder is not None:
        kwargs["folder"] = folder
    if top is not None:
        kwargs["top"] = top
    return cls(**kwargs)


def build(name: str = DEFAULT, folder=None) -> FeatureMatcher:
    return make(name, folder)


def describe() -> list[tuple[str, bool, str]]:
    """给命令行列算法用：名字 / 本机能不能用 / 一句说明。"""
    rows = []
    for key, cls in MATCHERS.items():
        usable = True
        if key == "surf":
            usable = surf_available()
        rows.append((key, usable, NOTES.get(key, cls.__doc__ or "")))
    return rows
