"""OCR 薄封装（rapidocr-onnxruntime）。

统一输出成 dict 列表，坐标是**原图绝对像素**，这样上层不用关心引擎的 polygon 格式：

    {"text": "穗织剑圣星绘绘", "score": 0.99, "x1": 678, "y1": 501, "x2": 821, "y2": 525}

引擎第一次用到才初始化（模型加载约 0.5 s），也可以传入自己的 engine 便于测试。
"""

from __future__ import annotations

_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


_REC_ENGINE = None


def _pair(item) -> tuple[str, float]:
    """把引擎返回的一条结果归一成（文本, 置信度）。

    rapidocr 各版本返回的形状不一样，见过两种：
      * rec-only：``[("文本", 0.93), ...]``
      * 带框：   ``[[[x,y],...], "文本", "0.93"]``（注意置信度是**字符串**）
    所以这里不假设下标，按“哪一项是字符串”来取。
    """
    if not isinstance(item, (list, tuple)):
        return "", 0.0
    if len(item) >= 3 and isinstance(item[1], str):
        try:
            return item[1].strip(), float(item[2])
        except (TypeError, ValueError):
            return item[1].strip(), 0.0
    if len(item) == 2 and isinstance(item[0], str):
        try:
            return item[0].strip(), float(item[1])
        except (TypeError, ValueError):
            return item[0].strip(), 0.0
    return "", 0.0


def best_text(result) -> tuple[str, float]:
    """从引擎返回值里挑最长的一条文字：``(text, score)``。"""
    items = result[0] if isinstance(result, (list, tuple)) and result else None
    if not items:
        return "", 0.0
    pairs = [pair for pair in (_pair(item) for item in items) if pair[0]]
    if not pairs:
        return "", 0.0
    return max(pairs, key=lambda pair: len(pair[0]))


def recognizer():
    """只认字、不做检测的函数 ``(crop) -> (text, score)``。

    为什么用它：det 模型是瓶颈（这段播报整行 det+rec 要 852 ms），而 rec 只认一行字只要 15 ms。
    名字位置既然已经由图标模板给定，就不需要检测了。
    """
    global _REC_ENGINE
    if _REC_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _REC_ENGINE = RapidOCR(use_det=False, use_cls=False, use_rec=True)
        # 新版 rapidocr 不认 use_det 这个参数，只有这个开关真的关掉检测；
        # 不关的话每次调用都在跑检测模型（实测 511 ms/次，关了 13 ms）。
        if hasattr(_REC_ENGINE, "use_text_det"):
            _REC_ENGINE.use_text_det = False

    def recognize(crop) -> tuple[str, float]:
        if crop is None or getattr(crop, "size", 0) == 0:
            return "", 0.0
        # 直接调识别器最快（实测 10 ms）；有些版本没有这个入口，就退回 __call__
        if hasattr(_REC_ENGINE, "text_recognizer"):
            return best_text(_REC_ENGINE.text_recognizer([crop]))
        return best_text(_REC_ENGINE(crop))

    return recognize


def read_text(image, offset_x: int = 0, offset_y: int = 0, engine_=None) -> list[dict]:
    """对整幅图（或已裁好的区域）跑 OCR。offset 用来把区域坐标还原成整图坐标。"""
    if image is None or getattr(image, "size", 0) == 0:
        return []
    engine_ = engine_ or engine()
    result = engine_(image)
    lines = result[0] if result and result[0] else []
    out = []
    for item in lines:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        poly, text, score = item[0], item[1], float(item[2])
        text = (text or "").strip()
        if not text:
            continue
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        out.append({
            "text": text,
            "score": round(score, 4),
            "x1": int(min(xs)) + offset_x,
            "y1": int(min(ys)) + offset_y,
            "x2": int(max(xs)) + offset_x,
            "y2": int(max(ys)) + offset_y,
        })
    return out