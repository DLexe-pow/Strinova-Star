"""写日志 + 一键导出。

排查问题时最值钱的不是"点了什么按钮"，而是**出错那一刻的上下文**：异常栈、当时在跑什么任务、
软件版本、Python 版本、设置快照。这些都落盘，界面上点「导出日志」就把整份东西下载下来。
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

APP_NAME = "Strinova Star"
KEEP_DAYS = 7

_DIR: Path | None = None
_LOCK = threading.Lock()
_installed = False


def install(directory) -> Path:
    """指定日志目录并挂上全局异常钩子。多次调用只挂一次钩子。"""
    global _DIR, _installed
    _DIR = Path(directory)
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not _installed:
        sys.excepthook = _excepthook
        threading.excepthook = _thread_hook
        _installed = True
        write(f"=== 启动 {APP_NAME} {app_info()['version']} ===")
    return _DIR


def directory() -> Path | None:
    return _DIR


def _file() -> Path | None:
    if _DIR is None:
        return None
    return _DIR / f"app-{time.strftime('%Y%m%d')}.log"


def write(text: str, level: str = "info") -> None:
    """写一行。写不进去就算了，日志不能把程序拖死。"""
    stamp = time.strftime("%H:%M:%S")
    line = f"{stamp} [{level}] {text}"
    print(line)
    path = _file()
    if path is None:
        return
    with _LOCK:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def exception(context: str, exc: BaseException | None = None) -> str:
    """记一条异常（带栈），返回栈文本，方便同时塞进 job 里给界面。"""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else traceback.format_exc()
    write(f"{context} 出错：{exc}\n{text}", "error")
    return text


def _excepthook(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    write(f"未捕获异常：{exc}\n{text}", "error")
    sys.__excepthook__(exc_type, exc, tb)


def _thread_hook(args) -> None:
    if args.exc_type is SystemExit:
        return
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    write(f"线程 {getattr(args.thread, 'name', '?')} 炸了：{args.exc_value}\n{text}", "error")


def app_info() -> dict:
    info = {
        "app": APP_NAME,
        "version": _version(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cwd": os.getcwd(),
        "log_dir": str(_DIR) if _DIR else "",
    }
    try:
        import cv2
        info["opencv"] = cv2.__version__
    except Exception:
        pass
    try:
        import numpy
        info["numpy"] = numpy.__version__
    except Exception:
        pass
    return info


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:
        return "?"


def rotate(keep_days: int = KEEP_DAYS) -> None:
    """只留最近几天的日志文件。"""
    if _DIR is None:
        return
    cutoff = time.time() - keep_days * 86400
    for path in _DIR.glob("app-*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _tail(path: Path, limit: int = 400) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def export_text(hub=None) -> str:
    """拼出一份能直接发给开发者看的文本。"""
    info = app_info()
    lines = ["=" * 64, f"{APP_NAME} 诊断日志", "=" * 64]
    for key, value in info.items():
        lines.append(f"{key:8}：{value}")
    lines.append("")

    if hub is not None:
        lines.append("-" * 64)
        lines.append("【当前任务】")
        job = getattr(hub, "job", None)
        if job is None:
            lines.append("（没有任务）")
        else:
            lines.append(f"任务：{job.title}")
            lines.append(f"状态：{job.state}  进度：{job.progress:.0%}")
            if job.message:
                lines.append(f"进度文案：{job.message}")
            if job.error:
                lines.append(f"错误：{job.error}")
            if getattr(job, "traceback", ""):
                lines.append("栈：")
                lines.append(job.traceback)
        lines.append("")

        lines.append("-" * 64)
        lines.append("【设置快照】")
        try:
            snapshot = {k: v for k, v in dict(hub.settings).items() if k != "ffmpeg"}
            lines.append(json.dumps(snapshot, ensure_ascii=False, indent=2))
        except Exception:
            lines.append("（读设置失败）")
        lines.append("")

        if getattr(hub, "log", None):
            lines.append("-" * 64)
            lines.append("【最近操作】")
            for entry in hub.log[-200:]:
                stamp = time.strftime("%H:%M:%S", time.localtime(entry.get("time", 0)))
                lines.append(f"{stamp}  {entry.get('text', '')}")
            lines.append("")

    path = _file()
    if path is not None:
        lines.append("-" * 64)
        lines.append(f"【程序日志 {path.name}】")
        lines.extend(_tail(path))
    return "\n".join(lines) + "\n"