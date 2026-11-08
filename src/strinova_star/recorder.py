"""屏幕录制：一个 ffmpeg 进程分段落盘，停止时无损拼成整段。

设计要点（对应 `docs/录制方案.md`）：

* 采集走 `ddagrab`（DXGI 桌面复制），无边框全屏下直接拿到游戏画面；
* 写 mkv 不写 mp4 —— mp4 要 moov 收尾，进程被强杀就废了；
* 每 N 秒切一个段，段边界从 `seg.csv` 读，打点和切片共用同一条时间轴；
* 停止时用 concat 把段拼起来（`-c copy`，秒级，不重编码）。

这里只负责「录」，判断什么时候开始/停止由上层（`ui.Hub`、热键）决定。
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .clipper import find_ffmpeg

DEFAULT_FPS = 120
DEFAULT_CQ = 26
DEFAULT_PRESET = "p4"
DEFAULT_ENCODER = "hevc_nvenc"
DEFAULT_SEGMENT = 10.0
DEFAULT_KEYFRAME = 2.0
DEFAULT_KEEP_HOURS = 0.0        # 0 = 不按时长删（只按剩余空间兜底）
DEFAULT_MIN_FREE_GB = 10.0

MODES = {
    "global": "全局录像",
    "manual": "手动录像",
    "single": "单局分割",
    "highlight": "高光录制",
}
READY_MODES = ("global", "manual", "single")


class RecorderError(RuntimeError):
    """录制相关的错误：找不到 ffmpeg、启动失败、拼接失败。"""


@dataclass
class Status:
    """给界面看的录制状态。"""

    running: bool = False
    mode: str = ""
    folder: str = ""
    started: float = 0.0               # unix 时间戳
    seconds: float = 0.0
    size: int = 0                      # 已落盘字节
    segments: int = 0
    full: str = ""                     # 停止后拼出来的完整文件
    files: list[str] = field(default_factory=list)   # 单局分割时一局一个
    match: str = ""                    # 正在录的这一局的编号
    free_gb: float = 0.0
    message: str = ""
    error: str = ""
    audio: dict = field(default_factory=dict)   # 系统声音采集状态

    def to_dict(self) -> dict:
        return {"running": self.running, "mode": self.mode,
                "mode_label": MODES.get(self.mode, self.mode),
                "folder": self.folder, "seconds": round(self.seconds, 1),
                "size": self.size, "segments": self.segments,
                "full": self.full, "files": self.files, "match": self.match,
                "free_gb": round(self.free_gb, 1),
                "message": self.message, "error": self.error,
                "audio": self.audio}


def settings_from(config: dict) -> dict:
    """从界面设置里取录制参数，缺的用默认值补。"""
    def number(key, default, low, high):
        try:
            value = float(config.get(key, default))
        except (TypeError, ValueError):
            return default
        return min(high, max(low, value))

    encoder = str(config.get("record_encoder") or DEFAULT_ENCODER)
    if encoder not in ("hevc_nvenc", "h264_nvenc", "av1_nvenc", "libx264"):
        encoder = DEFAULT_ENCODER
    return {
        "fps": int(number("record_fps", DEFAULT_FPS, 30, 240)),
        "cq": int(number("record_cq", DEFAULT_CQ, 10, 40)),
        "bitrate": int(number("record_bitrate", 40, 5, 200)),
        "rate_control": str(config.get("record_rate_control") or "cq"),
        "preset": str(config.get("record_preset") or DEFAULT_PRESET),
        "encoder": encoder,
        "segment": number("record_segment", DEFAULT_SEGMENT, 2, 60),
        "keyframe": number("record_keyframe", DEFAULT_KEYFRAME, 0.5, 10),
        "resolution": str(config.get("record_resolution") or "auto"),
        "draw_mouse": bool(config.get("record_draw_mouse", False)),
        "keep_segments": bool(config.get("record_keep_segments", False)),
        "keep_hours": number("record_keep_hours", DEFAULT_KEEP_HOURS, 0.0, 1000),
        "min_free_gb": number("record_min_free_gb", DEFAULT_MIN_FREE_GB, 1, 500),
        "audio_devices": list(config.get("record_audio_devices") or []),
        "desktop_audio": bool(config.get("record_desktop_audio", True)),
        "desktop_audio_device": str(config.get("record_desktop_audio_device") or "").strip(),
        "desktop_audio_pipe": str(config.get("desktop_audio_pipe") or ""),
        "folder": str(config.get("record_dir") or ""),
        "ffmpeg": str(config.get("ffmpeg") or ""),
    }


def _video_args(config: dict) -> list[str]:
    """编码参数。CQ 和固定码率二选一。"""
    fps = config["fps"]
    args = ["-c:v", config["encoder"]]
    if config["encoder"].endswith("_nvenc"):
        args += ["-preset", config["preset"]]
        if config["rate_control"] == "cq":
            args += ["-cq", str(config["cq"])]
        elif config["rate_control"] == "cbr":
            args += ["-rc", "cbr", "-b:v", f"{config['bitrate']}M",
                     "-maxrate", f"{config['bitrate']}M", "-bufsize", f"{config['bitrate'] * 2}M"]
        else:
            args += ["-rc", "vbr", "-b:v", f"{config['bitrate']}M"]
    else:  # libx264 之类走 CRF
        args += ["-preset", "veryfast", "-crf", str(config["cq"])]
    keyframe = max(1, int(round(fps * float(config["keyframe"]))))
    args += ["-g", str(keyframe), "-forced-idr", "1"]
    if config["resolution"] != "auto":
        width, _, height = config["resolution"].partition("x")
        args = ["-vf", f"scale={width}:{height}"] + args
    return args


def build_command(ffmpeg: str | Path, folder: Path, config: dict) -> list[str]:
    """拼出录制命令。测试里直接检查这个列表。"""
    capture = f"ddagrab=0:framerate={config['fps']}:draw_mouse={1 if config['draw_mouse'] else 0}"
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", capture]
    track_titles: list[str] = []
    track_inputs: list[int] = []
    next_input = 1
    # 系统声音：Python 侧采集 WASAPI 环回，通过命名管道喂进来（ffmpeg 这个构建没有 wasapi 输入）
    pipe = str(config.get("desktop_audio_pipe") or "")
    if config.get("desktop_audio") and pipe:
        command += ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", pipe]
        track_inputs.append(next_input)
        track_titles.append("桌面声音")
        next_input += 1
    audio = [str(name) for name in config.get("audio_devices") or [] if str(name).strip()]
    for name in audio:
        command += ["-f", "dshow", "-i", f"audio={name}"]
        track_inputs.append(next_input)
        track_titles.append(name)
        next_input += 1
    command += ["-map", "0:v"]
    for where in track_inputs:
        command += ["-map", f"{where}:a"]
    command += _video_args(config)
    if track_titles:
        command += ["-c:a", "aac", "-b:a", "192k"]
        for position, title in enumerate(track_titles):
            command += ["-metadata:s:a:%d" % position, f"title={title}"]
    command += ["-f", "segment", "-segment_time", f"{config['segment']:g}",
                "-reset_timestamps", "0",
                "-segment_list", str(folder / "seg.csv"),
                "-segment_list_type", "csv",
                "-y", str(folder / "seg-%04d.mkv")]
    return command


def read_segments(folder: Path) -> list[dict]:
    """读 ffmpeg 写的 seg.csv：每行是 文件名,开始秒,结束秒。"""
    listing = folder / "seg.csv"
    if not listing.exists():
        return []
    rows: list[dict] = []
    try:
        with listing.open("r", encoding="utf-8", errors="replace") as handle:
            for row in csv.reader(handle):
                if len(row) < 3:
                    continue
                try:
                    rows.append({"name": row[0], "start": float(row[1]), "end": float(row[2])})
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def folder_size(folder: Path) -> int:
    total = 0
    if not folder.exists():
        return 0
    for path in folder.glob("*.mkv"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def free_gb(folder: Path) -> float:
    try:
        usage = shutil.disk_usage(str(folder if folder.exists() else folder.parent))
    except OSError:
        return 0.0
    return usage.free / (1024 ** 3)


def cleanup(root: Path, keep_hours: float = DEFAULT_KEEP_HOURS,
            min_free_gb: float = DEFAULT_MIN_FREE_GB,
            protect: Path | None = None,
            log_path: Path | None = None) -> list[str]:
    """删旧会话：先按保留时长，再按剩余空间。返回删掉的目录名。

    默认 `keep_hours=0` 表示不按时长删，只有磁盘快满时才会删最旧的。
    删之前会往 `log_path`（一般是 `recordings/cleanup.log`）记一行，方便事后查。
    """
    if not root.exists():
        return []
    sessions = [path for path in root.iterdir() if path.is_dir()]
    sessions.sort(key=lambda path: path.stat().st_mtime)
    removed: list[tuple[str, str]] = []
    now = time.time()

    def drop(path: Path, reason: str) -> None:
        if protect is not None and path.resolve() == protect.resolve():
            return
        if not path.is_dir():
            return
        shutil.rmtree(path, ignore_errors=True)
        removed.append((path.name, reason))

    for path in list(sessions):
        if keep_hours > 0:
            age_hours = (now - path.stat().st_mtime) / 3600
            if age_hours > keep_hours:
                drop(path, f"超过保留时长 {keep_hours:g} 小时")
    if min_free_gb > 0:
        for path in list(sessions):
            if free_gb(root) >= min_free_gb:
                break
            drop(path, "磁盘快满了")
    if removed:
        _log_removed(log_path, removed)
    return [name for name, _reason in removed]


def group_segments(segments: list[dict], spans: list[dict],
                   preroll: float = 180.0, tail: float = 120.0) -> list[tuple[str, list[dict]]]:
    """把分段按**大对局**分组，返回 [(对局编号, 分段列表)]，按时间排序。

    边界只看对局编号：编号变了才是新的一局。一局中间的「换边重新选人」「加载」
    编号不变，所以要并回同一局（`MatchWatch.spans()` 已经按编号合并过）。

    开打之前的选人界面、过场加载（VS）读不到编号，但它们属于**后面那一局**，
    所以落在两局之间的段归给下一局；只有离下一局太远（超过 `preroll` 秒）才
    单独留成 `""`（比如在菜单里挂了十分钟），免得把大厅录像也粘进对局里。
    最后一局打完之后的结算画面最多再跟 `tail` 秒，再往后也就归 `""`。
    """
    if not spans:
        return [("", list(segments))] if segments else []
    ordered = sorted(spans, key=lambda span: span["start"])
    groups: dict[str, list[dict]] = {}
    for row in segments:
        middle = (row["start"] + row["end"]) / 2.0
        match = ""
        following = next((span for span in ordered if span["start"] > middle), None)
        for span in ordered:
            if span["start"] <= middle < span["end"]:
                match = span["match"]
                break
        else:
            if following is not None:
                if following["start"] - middle <= preroll:
                    match = following["match"]
            else:
                last = ordered[-1]
                if middle - last["end"] <= tail:
                    match = last["match"]
        groups.setdefault(match, []).append(row)
    keyed = sorted(groups.items(),
                   key=lambda item: min(r["start"] for r in item[1]))
    return keyed


def _log_removed(log_path: Path | None, removed: list[tuple[str, str]]) -> None:
    """把「删了什么、为什么删」写进日志；写不了就算了，别影响录制。"""
    if log_path is None:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"{stamp} 删除 {name}（{reason}）" for name, reason in removed]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


class Recorder:
    """一次只录一路。开始 → 分段落盘；停止 → 拼接。"""

    def __init__(self, root: Path, config: dict | None = None):
        self.root = Path(root)
        self.config = settings_from(config or {})
        self.process: subprocess.Popen | None = None
        self.folder: Path | None = None
        self.mode = ""
        self.started = 0.0
        self.last = Status()
        self.loopback = None            # 系统声音采集（loopback.Loopback）
        self.sampler = None             # 旁路采样，读对局编号（livefeed.Sampler）
        self._log: list[str] = []

    # --- 状态 ---
    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def status(self) -> Status:
        if self.folder is None:
            status = Status(free_gb=free_gb(self.root))
            status.error = self.last.error
            status.full = self.last.full
            status.audio = self.last.audio
            return status
        segments = read_segments(self.folder)
        elapsed = time.time() - self.started if self.running else self.last.seconds
        status = Status(running=self.running, mode=self.mode,
                        folder=str(self.folder), started=self.started,
                        seconds=elapsed, size=folder_size(self.folder),
                        segments=len(segments), full=self.last.full,
                        free_gb=free_gb(self.root),
                        message=self.last.message, error=self.last.error,
                        audio=self.loopback.status() if self.loopback else self.last.audio,
                        match=self.sampler.status()["match"] if self.sampler else "")
        return status

    # --- 起停 ---
    def _desktop_audio(self, config: dict) -> str:
        """起系统声音采集，返回提示文字（管道地址会写进 config）。"""
        config["desktop_audio_pipe"] = ""
        if not config.get("desktop_audio"):
            return ""
        try:
            from . import loopback as loopback_module
        except Exception as exc:            # 非 Windows / 文件被删
            return f"系统声音没接上：{exc}"
        if not loopback_module.available():
            return "系统声音没接上：vendor/ 里没有 pyaudiowpatch"
        try:
            self.loopback = loopback_module.Loopback(
                device=config.get("desktop_audio_device") or "")
            config["desktop_audio_pipe"] = self.loopback.start(timeout=10.0)
        except Exception as exc:
            self.loopback = None
            return f"系统声音没接上：{exc}"
        return f"系统声音取自「{self.loopback.device}」"

    def start(self, mode: str = "global", extra: str = "") -> Path:
        if self.running:
            raise RecorderError("已经在录了")
        if mode not in READY_MODES:
            raise RecorderError(f"{MODES.get(mode, mode)}还没做，先用手动/全局模式")
        base = Path(self.config["folder"]) if self.config["folder"] else self.root
        self._cleanup()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"{stamp}{extra}_{mode}" if extra else f"{stamp}_{mode}"
        folder = base / name
        folder.mkdir(parents=True, exist_ok=True)
        config = dict(self.config)
        note = self._desktop_audio(config)
        ffmpeg = find_ffmpeg(self.config.get("ffmpeg") or None)
        command = build_command(ffmpeg, folder, config)
        (folder / "command.json").write_text(
            json.dumps({"command": command, "started": time.time(), "note": note},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._stop_audio()
            raise RecorderError(f"起不来 ffmpeg：{exc}") from exc
        time.sleep(0.5)
        if self.process.poll() is not None:
            message = (self.process.stderr.read() or b"").decode("utf-8", "replace")[-400:]
            self.process = None
            self._stop_audio()
            shutil.rmtree(folder, ignore_errors=True)
            raise RecorderError(f"ffmpeg 立刻退出了：{message.strip()}")
        if self.loopback is not None and not self.loopback.wait_connected(8.0):
            note = (note + "；ffmpeg 没连上音频管道，这段可能没声音").lstrip("；")
        self.folder = folder
        self.mode = mode
        self.started = time.time()
        self.last = Status(running=True, mode=mode, folder=str(folder), started=self.started,
                           message=note)
        if mode == "single":
            self._start_sampler(folder)
        return folder

    def _start_sampler(self, folder: Path) -> None:
        """单局分割要用：另开一路采样读左上角的对局编号。出错只记日志，不影响录制。"""
        try:
            from . import livefeed, ocr
            ffmpeg = find_ffmpeg(self.config.get("ffmpeg") or None)
            size = livefeed.probe_size(str(ffmpeg))
            recognizer = ocr.recognizer()
            sampler = livefeed.Sampler(str(ffmpeg), size,
                                       lambda frame: recognizer(frame)[0])
            sampler.start()
            self.sampler = sampler
        except Exception as exc:
            self._log.append(f"对局编号没读上：{exc}")

    def _stop_audio(self) -> None:
        if self.loopback is None:
            return
        try:
            self.last.audio = self.loopback.status()
            self.last.audio["error"] = self.last.audio.get("error") or self.loopback.error
        except Exception:
            pass
        try:
            self.loopback.stop()
        finally:
            self.loopback = None

    def _stop_sampler(self) -> list[dict]:
        """停采样，返回按对局分段用的 spans（顺便把 matches.jsonl 写进当前目录）。"""
        sampler = self.sampler
        self.sampler = None
        if sampler is None:
            return []
        try:
            sampler.stop()
            sampler.watch.close(time.time() - self.started)
        except Exception:
            pass
        spans = sampler.watch.spans()
        folder = self.folder
        if folder is not None:
            try:
                (folder / "matches.jsonl").write_text(
                    "\n".join(json.dumps(change.to_dict(), ensure_ascii=False)
                              for change in sampler.watch.changes) + "\n",
                    encoding="utf-8")
            except OSError:
                pass
        return spans

    def stop(self) -> Path | None:
        """停止录制并拼接。返回完整文件的路径（没段就返回 None）。"""
        if not self.process:
            return None
        folder = self.folder
        process = self.process
        self.last.seconds = time.time() - self.started
        spans = self._stop_sampler()
        if self.loopback is not None:
            # 音轨补到和画面一样长，否则最后零点几秒只有画面没有声
            try:
                self.loopback.pad_to(self.last.seconds)
            except Exception:
                pass
        try:
            if process.stdin:
                process.stdin.write(b"q")
                process.stdin.flush()
            process.wait(timeout=15)
        except (subprocess.TimeoutExpired, OSError):
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        finally:
            self.process = None
            self._stop_audio()
        if folder is None:
            return None
        segments = read_segments(folder)
        if not segments:
            self.last.message = "没录到画面（检查是不是独占全屏，或显卡不支持 ddagrab）"
            return None
        if self.mode == "single" and spans:
            targets = self._split_by_match(folder, segments, spans)
            if targets:
                return targets[0]
        target = folder / "full.mkv"
        try:
            self._concat(folder, target, segments)
        except RecorderError as exc:
            self.last.error = str(exc)
            return None
        self.last.full = str(target)
        self.last.message = f"已保存 {target.name}（{len(segments)} 段，{target.stat().st_size / 2**20:.0f} MB）"
        if not self.config["keep_segments"]:
            for row in segments:
                (folder / row["name"]).unlink(missing_ok=True)
        return target

    def _split_by_match(self, folder: Path, segments: list[dict],
                        spans: list[dict]) -> list[Path]:
        """单局分割：一局一个文件。返回写成功的文件列表。"""
        groups = group_segments(segments, spans)
        if not groups or not any(rows for _, rows in groups):
            return []
        if len(groups) == 1 and not groups[0][0]:
            return []                       # 一帧编号都没认出来 → 退回整段
        stamp = folder.name.rsplit("_", 1)[0]
        targets: list[Path] = []
        detail: list[str] = []
        for match, rows in groups:
            if not rows:
                continue
            name = f"{stamp}_match-{match}.mkv" if match else f"{stamp}_no-match.mkv"
            target = folder / name
            try:
                self._concat(folder, target, rows)
            except RecorderError as exc:
                self.last.error = str(exc)
                continue
            targets.append(target)
            detail.append(f"{name}（{len(rows)} 段）")
        if not targets:
            return []
        self.last.files = [str(path) for path in targets]
        self.last.full = str(targets[0])
        self.last.message = f"已按对局分成 {len(targets)} 个文件：" + "、".join(detail)
        if not self.config["keep_segments"]:
            for row in segments:
                (folder / row["name"]).unlink(missing_ok=True)
        return targets

    def _cleanup(self) -> None:
        """开录之前清一次旧的：不在停止时清，免得刚录完的那段被自己删掉。"""
        base = Path(self.config["folder"]) if self.config["folder"] else self.root
        try:
            removed = cleanup(base, self.config["keep_hours"], self.config["min_free_gb"],
                              log_path=base / "cleanup.log")
        except Exception:
            return
        if removed:
            self._log.extend(f"清理旧录像：{name}" for name in removed)

    def take_log(self) -> list[str]:
        """取走待播报的日志（清理提示这些），取完清空。"""
        items, self._log = self._log, []
        return items

    def _concat(self, folder: Path, target: Path, segments: list[dict]) -> None:
        ffmpeg = find_ffmpeg(self.config.get("ffmpeg") or None)
        listing = folder / "concat.txt"
        lines = []
        for row in segments:
            name = row["name"].replace("'", "'\\''")
            lines.append(f"file '{name}'")
            # 段文件带着全局时间戳（reset_timestamps 0），容器里的「时长」是结束时刻，
            # 直接 concat 会被当成 4s+8s+8s，拼接结果翻倍。显式写 duration 才对得上。
            lines.append(f"duration {max(0.0, row['end'] - row['start']):.3f}")
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-f", "concat", "-safe", "0", "-i", str(listing),
                   "-map", "0", "-c", "copy", "-y", str(target)]
        done = subprocess.run(command, capture_output=True, text=True, timeout=1800,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode != 0:
            raise RecorderError(f"拼接失败：{done.stderr.strip()[:300]}")
        listing.unlink(missing_ok=True)
