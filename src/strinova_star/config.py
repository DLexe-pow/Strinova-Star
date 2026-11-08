"""标定文件读取。

profile 里的区域坐标都是 **1920×1080 基准**，运行时按实际画面换算（见 geometry.py）。
这样换分辨率只要换一个 profile，代码不用动。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT / "assets" / "profiles" / "strinova-2560x1600.json"


class ProfileError(ValueError):
    """profile 文件缺字段或结构不对。"""


def load_profile(path: str | Path | None = None) -> dict:
    """读 profile 并做基本校验，返回 dict（不做任何换算）。"""
    target = Path(path) if path else DEFAULT_PROFILE
    if not target.exists():
        raise ProfileError(f"找不到标定文件：{target}")
    try:
        profile = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{target.name} 不是合法 JSON：{exc}") from exc

    for key in ("reference_size", "reference_base", "regions"):
        if key not in profile:
            raise ProfileError(f"{target.name} 缺少必需字段 {key!r}")

    for name, region in profile["regions"].items():
        box = region.get("box")
        if not (isinstance(box, list) and len(box) == 4):
            raise ProfileError(f"区域 {name!r} 的 box 必须是 4 个数字，现在是 {box!r}")
        anchor = region.get("anchor", ["left", "top"])
        if not (isinstance(anchor, list) and len(anchor) == 2):
            raise ProfileError(f"区域 {name!r} 的 anchor 必须是 2 个元素，现在是 {anchor!r}")

    # 数字型字段统一转 float，方便后面直接算
    profile["reference_size"] = [float(v) for v in profile["reference_size"]]
    profile["reference_base"] = [float(v) for v in profile["reference_base"]]
    profile.setdefault("_path", str(target))
    return profile


def region_names(profile: dict) -> list[str]:
    return list(profile["regions"])


def has_region(profile: dict, name: str) -> bool:
    return name in profile["regions"]
