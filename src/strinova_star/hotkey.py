"""全局热键（Windows）。

录制时用户全屏在游戏里，没法切回浏览器点按钮，所以「开始/停止录制」要能用一个
全局热键触发。实现是 `RegisterHotKey` + 一个只做消息循环的线程，不注入、不 hook。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000

KEYS = {"F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B}


class HotkeyError(RuntimeError):
    """注册失败：多半是热键被游戏或别的软件占了。"""


class Hotkey:
    """一个全局热键；线程里跑 GetMessage 循环，按一下回调一次。"""

    def __init__(self, name: str = "F9", callback=None):
        self.name = name.upper()
        self.vk = KEYS.get(self.name)
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.registered = False
        self.error = ""
        self._ready = threading.Event()

    @property
    def available(self) -> bool:
        return self.vk is not None

    def start(self, timeout: float = 3.0) -> bool:
        if not self.available:
            self.error = f"不认识这个键：{self.name}"
            return False
        if self.thread and self.thread.is_alive():
            return True
        self._ready.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="hotkey")
        self.thread.start()
        self._ready.wait(timeout)
        return self.registered

    def stop(self) -> None:
        if not self.thread or not self.thread.is_alive():
            return
        if self.thread_id:
            _user32.PostThreadMessageW(wintypes.DWORD(self.thread_id), WM_QUIT, 0, 0)
        self.thread.join(timeout=2)

    # --- 线程内部 ---
    def _loop(self) -> None:
        self.thread_id = int(_kernel32.GetCurrentThreadId())
        if not _user32.RegisterHotKey(None, 1, MOD_NOREPEAT, wintypes.UINT(self.vk)):
            self.error = f"{self.name} 被别的程序占用了"
            self._ready.set()
            return
        self.registered = True
        self._ready.set()
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and self.callback:
                    try:
                        self.callback()
                    except Exception:      # 回调出错不能把热键线程弄死
                        pass
        finally:
            _user32.UnregisterHotKey(None, 1)
            self.registered = False


def list_devices(ffmpeg: str) -> tuple[list[str], list[str]]:
    """列出 DirectShow 的声音输入设备（用来在界面里勾选音轨）。

    返回 (麦克风类设备, 所有设备)，顺序和 ffmpeg 输出一致。
    """
    import subprocess

    command = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return [], []
    text = (done.stderr or "") + (done.stdout or "")
    audio: list[str] = []
    for line in text.splitlines():
        if "(audio)" not in line:
            continue
        if '"' not in line:
            continue
        name = line.split('"')[1]
        if name and name not in audio:
            audio.append(name)
    return audio, audio
