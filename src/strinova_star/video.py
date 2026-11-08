"""视频取帧：只负责「某个时间点的一帧」，不关心识别。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class VideoError(RuntimeError):
    """打不开视频或取不到帧。"""


class Video:
    """一个录像文件的随机访问句柄（基于 OpenCV，够用且无额外依赖）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise VideoError(f"找不到录像：{self.path}")
        self._capture = cv2.VideoCapture(str(self.path))
        if not self._capture.isOpened():
            raise VideoError(f"打不开录像：{self.path}")
        self.width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = float(self._capture.get(cv2.CAP_PROP_FPS)) or 30.0
        frames = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        self.duration = frames / self.fps if frames else 0.0

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def frame(self, seconds: float) -> np.ndarray | None:
        """取 `seconds` 处的整帧（BGR）。越界会被夹到片长内。"""
        moment = max(0.0, min(float(seconds), max(0.0, self.duration - 1e-3)))
        self._capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000.0)
        ok, image = self._capture.read()
        return image if ok else None

    def frames(self, start: float = 0.0, end: float | None = None, step: float = 1.0):
        """按固定间隔逐帧产出 `(时间, 帧)`。"""
        stop = self.duration if end is None else min(end, self.duration)
        moment = max(0.0, start)
        while moment < stop:
            image = self.frame(moment)
            if image is None:
                break
            yield moment, image
            moment += step

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "Video":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
