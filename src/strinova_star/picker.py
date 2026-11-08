"""调 Windows 自带的「选择文件夹」窗口（IFileOpenDialog）。

为什么不自己画一个：浏览器拿不到绝对路径，而嵌入式 Python 又没带 tkinter。
所以这里用 ctypes 直接走 COM，弹出系统对话框——左边就是「此电脑」和所有盘符，
和资源管理器里看到的一样，不会卡在某个盘里出不来。

用法：
    from strinova_star.picker import pick_folder
    path = pick_folder("选录像文件夹", r"D:\\录像")   # 用户点取消时返回 ""
"""

from __future__ import annotations

import threading
import time


import ctypes
from ctypes import wintypes
import uuid
from ctypes import POINTER, byref, c_uint, c_void_p, c_wchar_p

_HRESULT = ctypes.c_long
_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_FOS_PICKFOLDERS = 0x00000020
_FOS_FORCEFILESYSTEM = 0x00000040
_FOS_PATHMUSTEXIST = 0x00000800
_SIGDN_FILESYSPATH = 0x80058000
_CANCELLED = -2147023673          # HRESULT_FROM_WIN32(ERROR_CANCELLED)，用户点了取消


class PickerError(RuntimeError):
    """对话框没起来，或者拿不到选中的路径。"""


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


_CLSID_FILE_OPEN_DIALOG = _GUID.of("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
_IID_IFILE_OPEN_DIALOG = _GUID.of("D57C7288-D4AD-4768-BE02-9D969532D960")
_IID_ISHELL_ITEM = _GUID.of("43826D1E-E718-42EE-BC55-A1E261C37BFE")

_ole32 = ctypes.WinDLL("ole32")
_shell32 = ctypes.WinDLL("shell32")

_ole32.CoInitializeEx.argtypes = [c_void_p, ctypes.c_ulong]
_ole32.CoInitializeEx.restype = _HRESULT
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [POINTER(_GUID), c_void_p, ctypes.c_ulong,
                                    POINTER(_GUID), POINTER(c_void_p)]
_ole32.CoCreateInstance.restype = _HRESULT
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoTaskMemFree.restype = None
_shell32.SHCreateItemFromParsingName.argtypes = [c_wchar_p, c_void_p,
                                                POINTER(_GUID), POINTER(c_void_p)]
_shell32.SHCreateItemFromParsingName.restype = _HRESULT


def _call(obj: c_void_p, index: int, argtypes: list, *args):
    """按虚表下标调 COM 方法（ctypes 没有接口声明，只能自己走虚表）。"""
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(_HRESULT, c_void_p, *argtypes)
    return proto(table[index])(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj and obj.value:
        _call(obj, 2, [])


def available() -> bool:
    """这台机器上能不能弹系统对话框（非 Windows 一律 False）。"""
    return hasattr(ctypes, "WinDLL") and hasattr(ctypes, "WINFUNCTYPE")



_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
for _name, _args in {
    "FindWindowW": [c_wchar_p, c_wchar_p],
    "GetForegroundWindow": [],
    "SetForegroundWindow": [c_void_p],
    "BringWindowToTop": [c_void_p],
    "SetWindowPos": [c_void_p, c_void_p, wintypes.INT, wintypes.INT, wintypes.INT, wintypes.INT, c_uint],
    "GetWindowThreadProcessId": [c_void_p, c_void_p],
    "AttachThreadInput": [c_uint, c_uint, wintypes.BOOL],
    "SetFocus": [c_void_p],
}.items():
    _fn = getattr(_user32, _name)
    _fn.argtypes = _args
    _fn.restype = c_void_p if _name in ("FindWindowW", "GetForegroundWindow") else wintypes.INT

_HWND_TOPMOST, _HWND_NOTOPMOST = -1, -2
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40


def _raise_window(hwnd) -> None:
    """把窗口提到最前（含「临时置顶再取消」这一招，绕开系统的前台锁）。"""
    if not hwnd:
        return
    fg = _user32.GetForegroundWindow()
    tid_fg = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = _kernel32.GetCurrentThreadId()
    attached = _user32.AttachThreadInput(tid_me, tid_fg, 1) if tid_fg else 0
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        _user32.SetFocus(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(tid_me, tid_fg, 0)
    _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                         _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
    _user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0,
                         _SWP_NOSIZE | _SWP_NOMOVE | _SWP_SHOWWINDOW)


def _watch_window(title: str, timeout: float = 5.0) -> None:
    """对话框是另起的线程画的，Show 之前先派个人在这儿等它出现，出现就顶到最前。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _user32.FindWindowW(None, title)
        if hwnd:
            _raise_window(hwnd)
            return
        time.sleep(0.05)


def pick_folder(title: str = "选择文件夹", initial: str | None = None) -> str:
    """弹出系统「选择文件夹」窗口，返回绝对路径；用户取消时返回空字符串。"""
    if not available():
        raise PickerError("这个系统没有系统文件夹选择框")

    # 每个线程都要自己 CoInitialize；HTTP 请求线程不是 STA，这里补上
    hr = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    initialized = hr in (0, 1)          # S_OK / S_FALSE；RPC_E_CHANGED_MODE 就沿用线程已有的
    dialog = c_void_p()
    try:
        hr = _ole32.CoCreateInstance(byref(_CLSID_FILE_OPEN_DIALOG), None,
                                     _CLSCTX_INPROC_SERVER,
                                     byref(_IID_IFILE_OPEN_DIALOG), byref(dialog))
        if hr < 0:
            raise PickerError(f"创建选择窗口失败（0x{hr & 0xFFFFFFFF:08X}）")

        options = c_uint(0)
        _call(dialog, 10, [POINTER(c_uint)], byref(options))       # GetOptions
        _call(dialog, 9, [c_uint],                                  # SetOptions
              options.value | _FOS_PICKFOLDERS | _FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST)
        _call(dialog, 17, [c_wchar_p], title)                       # SetTitle

        folder = c_void_p()
        if initial:
            hr = _shell32.SHCreateItemFromParsingName(str(initial), None,
                                                      byref(_IID_ISHELL_ITEM), byref(folder))
            if hr >= 0:
                _call(dialog, 12, [c_void_p], folder)               # SetFolder
                _release(folder)

        # 不给 owner（跨进程的 HWND 当 owner 会被 COM 拒成 E_FAIL），
        # 改派一个线程等窗口出现、立刻顶到最前，否则它常常藏在主窗口后面
        threading.Thread(target=_watch_window, args=(title,), daemon=True).start()
        hr = _call(dialog, 3, [c_void_p], None)                     # Show(owner=None)
        if hr == _CANCELLED:
            return ""
        if hr < 0:
            raise PickerError(f"选择窗口出错（0x{hr & 0xFFFFFFFF:08X}）")

        item = c_void_p()
        hr = _call(dialog, 20, [POINTER(c_void_p)], byref(item))    # GetResult
        if hr < 0:
            raise PickerError("没拿到选中的文件夹")
        name = c_wchar_p()
        _call(item, 5, [c_uint, POINTER(c_wchar_p)],                # GetDisplayName
              _SIGDN_FILESYSPATH, byref(name))
        path = name.value or ""
        if name:
            _ole32.CoTaskMemFree(name)
        _release(item)
        return path
    finally:
        _release(dialog)
        if initialized:
            _ole32.CoUninitialize()
