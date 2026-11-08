"""基准坐标（1920×1080）→ 实际像素的换算。

实测结论，别再猜：
- 计分板、顶部阵容条：按 **画面高度** 缩放，`s = height / 1080`（2560×1600 → 1.4815）
- 计数器、账号/队友区：按 **等比适配** 缩放，`fit = min(width/1920, height/1080)`（2560×1600 → 1.3333）

所以每个区域都可以用 `scale_mode` 指定用哪套：「height」或「fit」（默认取 profile.hud_scale，
也就是 fit 那套，兼容老标定）。
"""

from __future__ import annotations

HORIZONTAL = ("left", "center", "right")
VERTICAL = ("top", "center", "bottom")


class GeometryError(ValueError):
    pass


def fit_scale(profile: dict) -> tuple[float, float]:
    """等比适配缩放：3200×1080 这种超宽屏也不会把元素撑出画面。"""
    width, height = profile["reference_size"]
    base_width, base_height = profile["reference_base"]
    s = min(width / base_width, height / base_height)
    return (s, s)


def height_scale(profile: dict) -> tuple[float, float]:
    """按画面高度缩放（计分板/阵容条用这套）。"""
    _width, height = profile["reference_size"]
    _base_width, base_height = profile["reference_base"]
    s = height / base_height
    return (s, s)


def scale_for(profile: dict, scale_mode: str | None = None,
              override: tuple[float, float] | list[float] | None = None) -> tuple[float, float]:
    if override is not None:
        sx, sy = override
        return (float(sx), float(sy))
    if scale_mode == "height":
        return height_scale(profile)
    if scale_mode in (None, "fit"):
        hud = profile.get("hud_scale")
        if hud:
            return (float(hud[0]), float(hud[1]))
        return fit_scale(profile)
    raise GeometryError(f"未知的 scale_mode: {scale_mode!r}（只支持 height / fit）")


def point(profile: dict, x: float, y: float, horizontal: str = "left", vertical: str = "top",
          scale: tuple[float, float] | None = None, scale_mode: str | None = None) -> tuple[float, float]:
    """基准坐标的一个点 → 实际像素。锚点决定「从哪边量」。"""
    if horizontal not in HORIZONTAL:
        raise GeometryError(f"horizontal 只能是 {HORIZONTAL}，收到 {horizontal!r}")
    if vertical not in VERTICAL:
        raise GeometryError(f"vertical 只能是 {VERTICAL}，收到 {vertical!r}")

    width, height = profile["reference_size"]
    base_width, base_height = profile["reference_base"]
    sx, sy = scale_for(profile, scale_mode, scale)

    if horizontal == "left":
        px = x * sx
    elif horizontal == "right":
        px = width - (base_width - x) * sx
    else:
        px = width / 2 + (x - base_width / 2) * sx

    if vertical == "top":
        py = y * sy
    elif vertical == "bottom":
        py = height - (base_height - y) * sy
    else:
        py = height / 2 + (y - base_height / 2) * sy

    return (px, py)


def box(profile: dict, values, horizontal: str = "left", vertical: str = "top",
        scale: tuple[float, float] | None = None, scale_mode: str | None = None) -> tuple[int, int, int, int]:
    """基准坐标的 [x1,y1,x2,y2] → 实际像素框（四舍五入成整数）。"""
    if len(values) != 4:
        raise GeometryError(f"box 需要 4 个数，收到 {values!r}")
    x1, y1 = point(profile, values[0], values[1], horizontal, vertical, scale, scale_mode)
    x2, y2 = point(profile, values[2], values[3], horizontal, vertical, scale, scale_mode)
    return (int(round(min(x1, x2))), int(round(min(y1, y2))),
            int(round(max(x1, x2))), int(round(max(y1, y2))))


def region(profile: dict, name: str) -> tuple[int, int, int, int]:
    """按 profile 里该区域的 anchor / scale_mode 换算成像素框。"""
    try:
        spec = profile["regions"][name]
    except KeyError as exc:
        raise GeometryError(f"profile 里没有区域 {name!r}，现有：{sorted(profile['regions'])}") from exc
    anchor = spec.get("anchor", ["left", "top"])
    return box(profile, spec["box"], anchor[0], anchor[1], scale_mode=spec.get("scale_mode"))


def regions(profile: dict) -> dict[str, tuple[int, int, int, int]]:
    return {name: region(profile, name) for name in profile["regions"]}


def lineup_slots(profile: dict) -> list[tuple[int, int, int, int]]:
    """顶部阵容条 10 个槽（我方 5 + 对方 5）。"""
    spec = profile.get("lineup") or {}
    slots = spec.get("slots") or []
    anchor = spec.get("anchor", ["center", "top"])
    return [box(profile, slot, anchor[0], anchor[1], scale_mode=spec.get("scale_mode", "height"))
            for slot in slots]


def clamp(box_: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """把框夹进画面内，越界的部分削掉。"""
    width, height = size
    x1, y1, x2, y2 = box_
    return (max(0, x1), max(0, y1), min(width, x2), min(height, y2))


def offset(box_: tuple[int, int, int, int], dx: int = 0, dy: int = 0) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box_
    return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


def crop(image, box_: tuple[int, int, int, int]):
    """按框裁图；越界自动夹紧，框为空返回 None。"""
    x1, y1, x2, y2 = clamp(box_, (image.shape[1], image.shape[0]))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return image[y1:y2, x1:x2]


def for_size(profile: dict, size: tuple[int, int]) -> dict:
    """把标定档「换算」到目标分辨率：只改 reference_size 与 hud_scale，区域框仍是基准坐标。

    标定档里的 reference_size 是标定时的画面尺寸（2560x1600），直接拿它去读
    2304x1440 的截图会算出 1.4815 的缩放（实际应是 1.3333），所有框都会偏。
    """
    width, height = int(size[0]), int(size[1])
    if [width, height] == [int(v) for v in profile["reference_size"]]:
        return profile
    adapted = dict(profile)
    adapted["reference_size"] = [width, height]
    adapted["hud_scale"] = list(fit_scale(adapted))
    return adapted
