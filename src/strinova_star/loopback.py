"""系统声音采集：WASAPI 环回 → 命名管道 → ffmpeg。

为什么要这么绕：

* 我们这个 ffmpeg 构建没有 wasapi 输入设备（`-devices` 里只有 dshow / gdigrab /
  openal / vfwcap / lavfi），DirectShow 也看不到「立体声混音」；
* 但 Windows 从 Vista 起就允许对**播放设备**做环回采集（WASAPI loopback），
  不用装虚拟声卡、也不用改系统默认输出；
* Python 这边用 `pyaudiowpatch`（PyAudio 的 WASAPI 分支，放在 `vendor/` 里，
  不走 pip）抓 48kHz 立体声 16bit PCM，再通过命名管道喂给正在录制的 ffmpeg。

ffmpeg 那边一样编码：`-f s16le -ar 48000 -ac 2 -i <命名管道 strinova-star-desktop>`。
管道是字节流，写多快 ffmpeg 读多快；我们按真实速度写，所以时间轴和画面自然对齐。

设备中途拔掉、切默认输出、驱动打个嗝，WASAPI 都会把读流打断（`paUnanticipatedHostError`
之类）。这时候不能直接退出 —— 音轨会比画面短，后面整段都错位。做法是：

1. 读失败就重开流，最多重试几次；
2. 重开的空档**按墙钟补静音**，保证「写进管道的字节数」始终等于「已经过去的时间」；
3. 补不回来（设备彻底没了）就一直补静音，让音轨长度继续跟着画面走。

`recorder.Recorder` 负责起停这个类，`ui.Hub` 只读 `status()` 给界面看。
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor"

SAMPLE_RATE = 48000
CHANNELS = 2
CHUNK = 960                     # 20 ms 一块，48000 / 50
PIPE_NAME = "strinova-star-desktop"

_PIPE_ACCESS_OUTBOUND = 0x00000002
_FILE_FLAG_OVERLAPPED = 0x40000000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_ERROR_NO_DATA = 232
_WAIT_OBJECT_0 = 0
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class LoopbackError(RuntimeError):
    """环回采集起不来。"""


_MAX_REOPEN = 5                  # 读流最多重开几次
_REOPEN_BACKOFF = 0.4            # 重开失败后的退避，翻倍到 2 秒
_SILENCE_BLOCK = 960             # 补静音时一次写多少帧


def silence_needed(written: int, elapsed: float,
                   rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> int:
    """按墙钟算「现在应该已经写进去多少字节」，还差多少就补多少静音。"""
    target = int(max(0.0, elapsed) * rate * channels * 2)
    return max(0, target - int(written))


# --- Win32 管道 -------------------------------------------------------------

class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p),
                ("InternalHigh", ctypes.c_void_p),
                ("Pointer", ctypes.c_void_p),
                ("hEvent", wintypes.HANDLE)]


def _load_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.DWORD, ctypes.c_void_p]
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_Overlapped)]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped),
                                             ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
    return kernel32


_KERNEL32 = None


def _kernel():
    global _KERNEL32
    if _KERNEL32 is None:
        _KERNEL32 = _load_kernel32()
    return _KERNEL32


def _create_pipe(name: str) -> int:
    handle = _kernel().CreateNamedPipeW(
        rf"\\.\pipe\{name}",
        _PIPE_ACCESS_OUTBOUND | _FILE_FLAG_OVERLAPPED,
        _PIPE_TYPE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
        1, 1 << 16, 1 << 16, 0, None)
    if not handle or handle == _INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), f"建不了管道 \\\\.\\pipe\\{name}")
    return handle


def _connect(handle: int, timeout: float) -> bool:
    """等 ffmpeg 连上来。超时返回 False。"""
    kernel = _kernel()
    event = kernel.CreateEventW(None, True, False, None)
    overlapped = _Overlapped()
    overlapped.hEvent = event
    try:
        ok = kernel.ConnectNamedPipe(handle, ctypes.byref(overlapped))
        if ok:
            return True
        error = ctypes.get_last_error()
        if error == _ERROR_PIPE_CONNECTED:
            return True
        if error != _ERROR_IO_PENDING:
            raise OSError(error, "ConnectNamedPipe 失败")
        if kernel.WaitForSingleObject(event, int(max(0.2, timeout) * 1000)) != _WAIT_OBJECT_0:
            return False
        written = wintypes.DWORD()
        if not kernel.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(written), False):
            raise OSError(ctypes.get_last_error(), "连接管道失败")
        return True
    finally:
        kernel.CloseHandle(event)


def _write(handle: int, data: bytes, timeout: float = 5.0) -> None:
    kernel = _kernel()
    event = kernel.CreateEventW(None, True, False, None)
    overlapped = _Overlapped()
    overlapped.hEvent = event
    written = wintypes.DWORD()
    buffer = ctypes.create_string_buffer(data, len(data))
    try:
        ok = kernel.WriteFile(handle, buffer, len(data), ctypes.byref(written), ctypes.byref(overlapped))
        if not ok:
            error = ctypes.get_last_error()
            if error != _ERROR_IO_PENDING:
                raise OSError(error, "写音频管道失败")
            if kernel.WaitForSingleObject(event, int(max(0.2, timeout) * 1000)) != _WAIT_OBJECT_0:
                raise OSError(0, "写音频管道超时")
            if not kernel.GetOverlappedResult(handle, ctypes.byref(overlapped),
                                              ctypes.byref(written), False):
                raise OSError(ctypes.get_last_error(), "写音频管道失败")
        if written.value < len(data):
            raise OSError(0, f"只写进去 {written.value}/{len(data)} 字节")
    finally:
        kernel.CloseHandle(event)


# --- pyaudiowpatch ---------------------------------------------------------

def _pyaudio():
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    try:
        import pyaudiowpatch
    except Exception as exc:  # pragma: no cover - 没装就是没装
        raise LoopbackError(f"没有 pyaudiowpatch：{exc}") from exc
    return pyaudiowpatch


def available() -> bool:
    try:
        _pyaudio()
    except LoopbackError:
        return False
    return True


def devices() -> list[dict]:
    """列出可以环回采集的播放设备（默认播放设备排第一）。"""
    pyaudio = _pyaudio()
    audio = pyaudio.PyAudio()
    found: list[dict] = []
    try:
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        default = ""
        try:
            default = audio.get_default_wasapi_loopback()["name"]
        except Exception:
            pass
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if info["hostApi"] != wasapi["index"] or not info.get("isLoopbackDevice"):
                continue
            found.append({"name": info["name"],
                          "channels": int(info["maxInputChannels"]),
                          "rate": int(info["defaultSampleRate"]),
                          "default": info["name"] == default})
    except Exception as exc:
        raise LoopbackError(f"列不出播放设备：{exc}") from exc
    finally:
        audio.terminate()
    found.sort(key=lambda item: not item["default"])
    return found


def default_device() -> str:
    pyaudio = _pyaudio()
    audio = pyaudio.PyAudio()
    try:
        return str(audio.get_default_wasapi_loopback()["name"])
    except Exception as exc:
        raise LoopbackError(f"找不到默认播放设备：{exc}") from exc
    finally:
        audio.terminate()


class Loopback:
    """一路环回采集 + 一条命名管道。一次只服务一个 ffmpeg。"""

    def __init__(self, device: str = "", pipe: str = PIPE_NAME,
                 chunk: int = CHUNK, rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.wanted = (device or "").strip()
        self.pipe = pipe or PIPE_NAME
        self.chunk = chunk
        self.rate = rate
        self.channels = channels
        self.device = ""
        self.error = ""
        self.bytes = 0
        self.chunks = 0
        self.silence = 0           # 中途补进去的静音字节数
        self.reopens = 0           # 读流重开的次数
        self._stream = None
        self._audio = None
        self._pyaudio = None
        self._handle = None
        self._index = -1
        self._thread: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._queue: queue.Queue | None = None
        self._stopping = threading.Event()
        self._connected = threading.Event()
        self._write_lock = threading.Lock()
        self._started = 0.0

    @property
    def path(self) -> str:
        return rf"\\.\pipe\{self.pipe}"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stopping.is_set())

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self, timeout: float = 10.0) -> str:
        """开采集线程，返回管道路径。ffmpeg 起来后自己会连上来。"""
        if self.running:
            raise LoopbackError("系统声音已经在采了")
        pyaudio = _pyaudio()
        audio = pyaudio.PyAudio()
        self._pyaudio = pyaudio
        try:
            info = self._pick(audio, pyaudio)
            self._index = int(info["index"])
            self.rate = int(info["defaultSampleRate"])
            self.channels = int(info["maxInputChannels"]) or 1
            stream = self._open_stream(audio)
        except LoopbackError:
            audio.terminate()
            raise
        except Exception as exc:
            audio.terminate()
            raise LoopbackError(f"打不开环回设备：{exc}") from exc
        self._audio = audio
        self._stream = stream
        self.device = str(info["name"])
        self.bytes = 0
        self.chunks = 0
        self.silence = 0
        self.reopens = 0
        self.error = ""
        self._stopping.clear()
        self._connected.clear()
        self._started = time.time()
        self._queue = queue.Queue(maxsize=64)
        self._reader = threading.Thread(target=self._reader_loop, daemon=True,
                                         name="loopback-read")
        self._reader.start()
        self._thread = threading.Thread(target=self._pump, args=(timeout,), daemon=True,
                                        name="loopback-pipe")
        self._thread.start()
        return self.path

    def _open_stream(self, audio):
        return audio.open(format=self._pyaudio.paInt16, channels=self.channels, rate=self.rate,
                          frames_per_buffer=self.chunk, input=True,
                          input_device_index=self._index)

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _reopen(self) -> bool:
        """读流断了就重开；设备没了就返回 False，让调用方补静音撑着。"""
        if self._stopping.is_set() or self._audio is None:
            return False
        self._close_stream()
        delay = _REOPEN_BACKOFF
        for attempt in range(3):
            if self._stopping.is_set():
                return False
            try:
                self._stream = self._open_stream(self._audio)
                return True
            except Exception:
                if attempt < 2:
                    time.sleep(delay)
                    delay = min(2.0, delay * 2)
        return False

    def _pick(self, audio, pyaudio) -> dict:
        if self.wanted:
            for index in range(audio.get_device_count()):
                info = audio.get_device_info_by_index(index)
                if info.get("isLoopbackDevice") and info["name"] == self.wanted:
                    return dict(info, index=index)
            raise LoopbackError(f"找不到环回设备「{self.wanted}」")
        try:
            info = audio.get_default_wasapi_loopback()
        except Exception as exc:
            raise LoopbackError(f"找不到默认播放设备：{exc}") from exc
        return dict(info, index=info["index"])

    def wait_connected(self, timeout: float = 8.0) -> bool:
        return self._connected.wait(timeout)

    def _pad_locked(self, handle: int, anchor: float) -> int:
        """（调用方已持锁）把「应该写到的字节数」和「已经写的」之间的差补成静音。"""
        written = self.bytes + self.silence
        fill = silence_needed(written, time.time() - anchor, self.rate, self.channels)
        if fill <= 0:
            return 0
        block = b"\x00" * (_SILENCE_BLOCK * self.channels * 2)
        sent = 0
        while sent < fill:
            piece = block[: min(len(block), fill - sent)]
            _write(handle, piece)
            sent += len(piece)
        self.silence += sent
        return sent

    def pad_to(self, seconds: float) -> int:
        """收尾时把音轨补到指定长度，免得比画面短一截。返回补了多少字节。"""
        handle = self._handle
        if not handle or self._stopping.is_set():
            return 0
        with self._write_lock:
            written = self.bytes + self.silence
            fill = silence_needed(written, max(0.0, seconds), self.rate, self.channels)
            if fill <= 0:
                return 0
            block = b"\x00" * (_SILENCE_BLOCK * self.channels * 2)
            sent = 0
            try:
                while sent < fill:
                    piece = block[: min(len(block), fill - sent)]
                    _write(handle, piece)
                    sent += len(piece)
            except OSError:
                return sent
            self.silence += sent
            return sent

    def _reader_loop(self) -> None:
        """只管读，往环形缓冲里塞。读不到数据（系统没在放声音）就一直等着。

        WASAPI 环回在「没有音频在播放」时不会吐数据，`read()` 会一直卡住；
        以前是在这个线程里同时读+写管道，卡住就整套录制都停住。现在把它拆开：
        管道线程按墙钟补静音，音轨始终跟画面一样长。
        """
        while not self._stopping.is_set():
            stream = self._stream
            if stream is None:
                if not self._reopen():
                    time.sleep(0.1)
                continue
            try:
                data = stream.read(self.chunk, exception_on_overflow=False) or b""
            except Exception as exc:
                if self._stopping.is_set():
                    return
                if not self.error:
                    self.error = f"采集中断：{exc}"
                if self.reopens < _MAX_REOPEN:
                    self.reopens += 1
                    self._reopen()
                else:
                    time.sleep(0.2)
                continue
            if not data:
                continue
            try:
                self._queue.put(data, timeout=0.5)
            except queue.Full:
                pass            # ffmpeg 那边慢了，这一块丢掉就行

    def _pump(self, timeout: float) -> None:
        handle = None
        try:
            handle = _create_pipe(self.pipe)
            self._handle = handle
            if not _connect(handle, timeout):
                self.error = "ffmpeg 没连上音频管道"
                return
            self._connected.set()
            anchor = time.time()
            while not self._stopping.is_set():
                try:
                    data = self._queue.get(timeout=0.02)
                except queue.Empty:
                    data = b""
                try:
                    with self._write_lock:
                        # 先按墙钟补齐落后的部分，再写新采到的，音轨就不会比画面短
                        self._pad_locked(handle, anchor)
                        if data:
                            _write(handle, data)
                            self.bytes += len(data)
                            self.chunks += 1
                except OSError as exc:
                    # 232 = ERROR_NO_DATA：ffmpeg 已经录完退出，管道那头关了，正常收尾
                    code = getattr(exc, "winerror", None) or (exc.args[0] if exc.args else None)
                    if code == _ERROR_NO_DATA:
                        return
                    if not self._stopping.is_set():
                        self.error = f"写管道失败：{exc}"
                    return
        except OSError as exc:
            if not self._stopping.is_set():
                self.error = f"管道出错：{exc}"
        except Exception as exc:  # 线程里别静默死掉
            if not self._stopping.is_set():
                self.error = f"采集线程崩了：{type(exc).__name__}: {exc}"
        finally:
            if handle:
                try:
                    _kernel().DisconnectNamedPipe(handle)
                except Exception:
                    pass
                _kernel().CloseHandle(handle)
            self._handle = None
            self._connected.clear()

    def status(self) -> dict:
        seconds = (self.bytes + self.silence) / max(1, self.rate * self.channels * 2)
        return {"running": self.running, "device": self.device,
                "connected": self.connected, "seconds": round(seconds, 1),
                "chunks": self.chunks, "silence_seconds": round(
                    self.silence / max(1, self.rate * self.channels * 2), 1),
                "reopens": self.reopens, "error": self.error}

    def stop(self) -> None:
        """停采集。

        坑：从别的线程关流 / terminate，而读线程正卡在 `read()` 里，会让 PortAudio
        直接 0xC0000005 崩进程。所以这里只置标志、等线程自己退；读线程要是还卡在
        `read()`（系统没在放声音时就会这样），就放弃这套 PyAudio，不去动它。
        """
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        reader, self._reader = self._reader, None
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.5)
        stuck = bool(reader is not None and reader.is_alive())
        if stuck:
            self.error = self.error or "读线程卡在驱动里（没声音在放时常这样），这次先放掉这套流"
            self._audio = None
            self._stream = None
            return
        self._close_stream()
        audio = self._audio
        self._audio = None
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass