"""后台监视录像文件夹：新录像「写完」后自动识别击杀/击倒。

实时性的做法（和 Outplayed 一个思路，但不 hook 游戏）：
  1. 每 `interval` 秒看一眼录像文件夹，记下每个文件的 (大小, 修改时间)；
  2. 文件大小连续 `stable` 秒不再变化，才算「录完了」（NVIDIA 回放是整段落盘的）；
  3. 对录完的新文件跑一次 `feed` 扫描，把事件回调出去；
  4. 已经处理过的文件记在 `state_path` 里，重启不会重复扫。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov")


class Watcher:
    """轮询式文件监视（不依赖 watchdog，少一个依赖）。"""

    def __init__(self, folder: str | Path, interval: float = 5.0, stable: float = 6.0,
                 state_path: str | Path | None = None):
        self.folder = Path(folder)
        self.interval = interval
        self.stable = stable
        self.state_path = Path(state_path) if state_path else Path(".watcher-state.json")
        self.processed: set[str] = self._load_state()
        self._sizes: dict[str, tuple[int, float]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.on_video = None        # 录完时的回调：(Path) -> None
        self.on_status = None       # 状态回调：(str) -> None

    # --- 状态持久化 ---
    def _load_state(self) -> set[str]:
        if self.state_path.exists():
            try:
                return set(json.loads(self.state_path.read_text(encoding="utf-8")))
            except Exception:
                return set()
        return set()

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(json.dumps(sorted(self.processed), ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        except Exception:
            pass

    # --- 扫描 ---
    def finished_videos(self) -> list[Path]:
        """本次轮询里「刚好录完」的文件。"""
        if not self.folder.exists():
            return []
        ready: list[Path] = []
        now = time.time()
        seen: set[str] = set()
        for path in self.folder.iterdir():
            if path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            key = str(path)
            seen.add(key)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            previous = self._sizes.get(key)
            if previous and previous[0] == size:
                if now - previous[1] >= self.stable and key not in self.processed:
                    ready.append(path)
            else:
                self._sizes[key] = (size, now)
        for key in list(self._sizes):
            if key not in seen:
                self._sizes.pop(key, None)
        return ready

    def mark_processed(self, path: str | Path) -> None:
        self.processed.add(str(path))
        self._save_state()

    # --- 线程 ---
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for path in self.finished_videos():
                    if self.on_status:
                        self.on_status(f"新录像：{path.name}")
                    if self.on_video:
                        self.on_video(path)
                    self.mark_processed(path)
            except Exception as exc:  # 监视线程不能因为单次失败退出
                if self.on_status:
                    self.on_status(f"监视出错：{exc}")
            self._stop.wait(self.interval)
