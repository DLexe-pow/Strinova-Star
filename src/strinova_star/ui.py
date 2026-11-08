"""图形界面的后端：本地 HTTP + 静态页，所有功能都通过 API 调用。

主程序（app.py）只做一件事——把这一层跑起来；具体动作都在前端按需触发：
    找阵容  → roster_scan.scan()，缺头像时用 lineup.scan() 补
    找高光  → feed 扫描击杀/击倒播报
    切片    → clipper.cut_all()

任务都是后台线程执行，前端轮询 /api/job 拿进度，避免请求超时。
"""

from __future__ import annotations

import json
import platform
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import clipper, config, features, feed, geometry, hotkey, lineup, logs, loopback, ocr, picker, recorder, roster_scan, rounds, version, watcher as watcher_module
from .matchers import make as make_matcher
from .video import Video

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "ui" / "web"
# 数据目录：打包成桌面版后放在 exe 旁边的 data\| 下（安装目录可能是只读的），
# 开发时还是项目里的 output\|。
import os as _os
OUTPUT = Path(_os.environ.get("STRINOVA_OUTPUT") or (ROOT / "output"))
DEFAULT_VIDEO_DIR = Path(r"C:\Users\enger\Videos\NVIDIA\Calabiyau-Win64-Shipping")
SETTINGS_PATH = OUTPUT / "ui-settings.json"
ROSTER_CACHE = OUTPUT / "roster-cache.json"
RECORD_DIR = OUTPUT / "recordings"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov")
_PICK_LOCK = threading.Lock()      # 同一时间只允许开一个系统选择窗口


def _guess_video_dir() -> Path:
    """第一次运行时挑一个真的装了录像的文件夹（默认那个常常是空的）。"""
    parents = [Path.home() / "Videos" / "NVIDIA", Path("D:/game/NVIDIA")]
    candidates: list[Path] = [DEFAULT_VIDEO_DIR]
    for parent in parents:
        try:
            if parent.is_dir():
                candidates.extend(p for p in sorted(parent.iterdir()) if p.is_dir())
        except OSError:
            continue
    best, best_count = DEFAULT_VIDEO_DIR, -1
    for folder in candidates:
        try:
            count = sum(1 for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
        except OSError:
            continue
        if count > best_count:
            best, best_count = folder, count
    return best


def load_settings() -> dict:
    settings = {"video_dir": str(_guess_video_dir()), "ffmpeg": "", "self_name": "穗织剑圣星绘绘",
                "fps": 2.0, "past": 8.0, "future": 2.0, "join_gap": 0.0, "method": "orb",
                "mode": "user",
        "theme": "xinghui",
                # 录制（见 docs/录制方案.md §4）
                "record_mode": "manual", "record_fps": 120, "record_cq": 26,
                "record_encoder": "hevc_nvenc", "record_preset": "p4",
                "record_rate_control": "cq", "record_bitrate": 40,
                "record_segment": 10, "record_keyframe": 2,
                "record_resolution": "auto", "record_draw_mouse": False,
                "record_keep_segments": False, "record_keep_hours": 0,
                "record_min_free_gb": 10, "record_audio_devices": [],
                "record_desktop_audio": True, "record_desktop_audio_device": "",
                "record_hotkey": "F9"}
    if SETTINGS_PATH.exists():
        try:
            settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return settings


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_roster_cache() -> dict[str, dict]:
    """每段录像认出来的昵称和角色，找击杀时拿来校对播报里的小字、也能反过来补阵容。"""
    try:
        data = json.loads(ROSTER_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    cache: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(value, list):          # 老版本只存昵称列表
            names, characters = value, {}
        elif isinstance(value, dict):
            names = value.get("names") or []
            characters = value.get("characters") or {}
        else:
            continue
        cache[str(key)] = {
            "names": [str(name) for name in names if name],
            "characters": {str(name): str(character) for name, character in characters.items()
                           if name and character},
            "boards": sorted({round(float(when), 1) for when in
                              (value.get("boards") if isinstance(value, dict) else []) or []}),
        }
        if isinstance(value, dict):
            # 整段识别结果（阵容 / 击杀 / 切片）原样带回来，重开软件也能直接显示
            for section in ("roster", "events", "clips", "rounds"):
                if value.get(section) is not None:
                    cache[str(key)][section] = value[section]
    return cache


def save_roster_cache(cache: dict[str, dict]) -> None:
    try:
        ROSTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ROSTER_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class Job:
    """一个后台任务：只有进度 + 结果，前端轮询即可。"""

    def __init__(self, title: str):
        self.title = title
        self.state = "running"
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = ""
        self.traceback = ""
        self.started = time.time()

    def to_dict(self) -> dict:
        return {"title": self.title, "state": self.state, "progress": round(self.progress, 3),
                "message": self.message, "result": self.result, "error": self.error,
                "traceback": self.traceback,
                "started": round(self.started, 3),
                "seconds": round(time.time() - self.started, 1)}


class Hub:
    """保存设置与后台任务。"""

    def __init__(self):
        self.settings = load_settings()
        self.job: Job | None = None
        self._lock = threading.Lock()
        self.log: list[dict] = []          # 给 SSE 推的轻量事件
        self.watcher = None                # watcher_module.Watcher，按需启动
        self.roster_cache = load_roster_cache()
        self.recorder = recorder.Recorder(RECORD_DIR, self.settings)
        self.hotkey = None

    # --- 一局一局的记忆：昵称 ↔ 角色 ---
    def _entry(self, video_path: str) -> dict:
        entry = self.roster_cache.setdefault(str(video_path), {"names": [], "characters": {}, "boards": []})
        entry.setdefault("names", [])
        entry.setdefault("characters", {})
        entry.setdefault("boards", [])
        return entry

    def known_names(self, video_path: str) -> list[str]:
        return list(self._entry(video_path)["names"])

    def known_characters(self, video_path: str) -> dict[str, str]:
        return dict(self._entry(video_path)["characters"])

    def known_boards(self, video_path: str) -> list[float]:
        """认阵容时扫到计分板的时刻；找击杀时这些秒数直接跳过播报识别。"""
        return [float(when) for when in self._entry(video_path)["boards"]]

    def remember_roster(self, video_path: str, names, characters: dict | None = None,
                        boards: list[float] | None = None) -> int:
        entry = self._entry(video_path)
        for name in names:
            if name and name not in entry["names"]:
                entry["names"].append(name)
        for name, character in (characters or {}).items():
            if name and character:
                entry["characters"][name] = character
        if boards:
            known = {round(float(when), 1) for when in entry["boards"]}
            known.update(round(float(when), 1) for when in boards)
            entry["boards"] = sorted(known)
        fixed = self.fix_events(video_path)
        save_roster_cache(self.roster_cache)
        return fixed

    def fix_events(self, video_path: str) -> int:
        """昵称表变了（比如刚认完阵容）就拿它把以前的击杀播报名字订正一遍。"""
        entry = self._entry(video_path)
        payload = entry.get("events")
        names = list(entry.get("names") or [])
        if not isinstance(payload, dict) or not names:
            return 0
        events = payload.get("events") or []
        changed = feed.canonicalize_events(events, names)
        # 切片里也带着自己那份事件，一并订正，免得切片页的时间轴还是错名
        for clip in entry.get("clips") or []:
            if isinstance(clip, dict) and clip.get("events"):
                changed += feed.canonicalize_events(clip["events"], names)
        return changed

    # --- 录像列表 ---
    def remember_result(self, result) -> None:
        """把一次识别的结果记到录像名下，下次打开同一段录像直接读出来。"""
        if not isinstance(result, dict):
            return
        video = result.get("video")
        if not video:
            return
        entry = self._entry(video)
        if "ally" in result or "enemy" in result:
            entry["roster"] = result
        if "events" in result:
            entry["events"] = result
        if result.get("clips") is not None:
            entry["clips"] = result["clips"]
        elif result.get("files") is not None:
            entry["clips"] = result["files"]
        save_roster_cache(self.roster_cache)

    def videos(self) -> list[dict]:
        folder = Path(self.settings["video_dir"])
        if not folder.exists():
            return []
        items = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            items.append({"name": path.name, "path": str(path),
                          "size": path.stat().st_size})
        return items

    # --- 任务调度 ---
    def start(self, title: str, run) -> Job:
        with self._lock:
            if self.job and self.job.state == "running":
                raise RuntimeError("还有一个任务在跑，等它跑完")
            job = Job(title)
            self.job = job

        def worker():
            try:
                job.result = run(job)
                job.state = "done"
                job.progress = 1.0
                try:
                    self.remember_result(job.result)
                except Exception:  # 存缓存失败不能影响任务结果
                    traceback.print_exc()
            except Exception as exc:  # 任务里的异常要回给界面，而不是让服务崩掉
                job.state = "error"
                job.error = f"{exc}"
                job.traceback = logs.exception(f"任务「{title}」", exc)

        threading.Thread(target=worker, daemon=True).start()
        return job

    # --- 实时监视 ---
    def push(self, text: str) -> None:
        self.log.append({"time": time.time(), "text": text})
        del self.log[:-200]
        logs.write(text)

    def watch_start(self) -> dict:
        if self.watcher and self.watcher.running:
            return {"running": True, "folder": str(self.watcher.folder)}
        hub = self

        def on_video(path: Path) -> None:
            hub.push(f"发现新录像：{path.name}，开始识别")
            try:
                hub.scan_highlights(str(path), hub.settings.get("self_name", ""))
            except Exception as exc:
                hub.push(f"自动识别失败：{exc}")

        self.watcher = watcher_module.Watcher(Path(self.settings["video_dir"]),
                                             state_path=OUTPUT / ".watcher-state.json")
        self.watcher.on_video = on_video
        self.watcher.on_status = self.push
        self.watcher.start()
        self.push(f"开始监视 {self.settings['video_dir']}")
        return {"running": True, "folder": self.settings["video_dir"]}

    def watch_stop(self) -> dict:
        if self.watcher:
            self.watcher.stop()
            self.push("已停止监视")
        return {"running": False}

    # --- 录制（全局 / 手动） ---
    def record_status(self) -> dict:
        self.recorder.config = recorder.settings_from(self.settings)
        return self.recorder.status().to_dict()

    def record_start(self, mode: str | None = None) -> dict:
        mode = mode or self.settings.get("record_mode") or "manual"
        if mode not in recorder.READY_MODES:
            raise RuntimeError(f"{recorder.MODES.get(mode, mode)}还没做，先用手动或全局模式")
        self.recorder.config = recorder.settings_from(self.settings)
        folder = self.recorder.start(mode)
        for line in self.recorder.take_log():
            self.push(line)
        self.push(f"开始录制（{recorder.MODES.get(mode, mode)}）：{folder.name}")
        return self.record_status()

    def record_stop(self) -> dict:
        target = self.recorder.stop()
        if target:
            self.push(f"录制已保存：{target}")
        return self.record_status()

    def record_toggle(self) -> dict:
        if self.recorder.running:
            return self.record_stop()
        return self.record_start()

    def record_hotkey(self) -> dict:
        """按 F9：想录就开、正在录就停。"""
        if self.recorder.running:
            return self.record_stop()
        return self.record_start()

    def install_hotkey(self) -> bool:
        name = str(self.settings.get("record_hotkey") or "F9")
        if self.hotkey and self.hotkey.name == name.upper() and self.hotkey.registered:
            return True
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = hotkey.Hotkey(name, callback=self._on_hotkey)
        ok = self.hotkey.start()
        if ok:
            self.push(f"热键 {name} 已就绪：按一下开始/停止录制")
        else:
            self.push(f"热键没装上：{self.hotkey.error}")
        return ok

    def _on_hotkey(self) -> None:
        try:
            status = self.record_hotkey()
        except Exception as exc:
            self.push(f"热键触发失败：{exc}")
            return
        if status.get("running"):
            self.push("（热键）开始录制")
        else:
            self.push("（热键）已停止录制")

    def audio_devices(self) -> dict:
        """麦克风（dshow）和可环回采集的播放设备，给界面下拉用。"""
        microphones: list[str] = []
        try:
            ffmpeg = clipper.find_ffmpeg(self.settings.get("ffmpeg") or None)
            microphones, _all = hotkey.list_devices(str(ffmpeg))
        except Exception as exc:
            self.push(f"列麦克风失败：{exc}")
        desktop: list[str] = []
        try:
            desktop = [item["name"] for item in loopback.devices()]
        except Exception as exc:
            self.push(f"列播放设备失败：{exc}")
        return {"microphones": microphones, "desktop": desktop,
                "desktop_default": str(self.settings.get("record_desktop_audio_device") or "")}

    def hotkey_status(self) -> dict:
        key = self.hotkey
        return {"name": str(self.settings.get("record_hotkey") or "F9"),
                "registered": bool(key and key.registered),
                "error": (key.error if key else "")}

    # --- 具体动作 ---
    def scan_roster(self, video_path: str) -> Job:
        def run(job: Job):
            profile = config.load_profile()
            matcher = make_matcher(self.settings.get("method", "orb"))

            def on_progress(done: int, total: int, message: str) -> None:
                # 两个阶段各占一段进度条，不然「认阵容 1/6」会把 90% 拉回 15%
                ratio = done / max(1, total)
                share = 0.6 * ratio if message.startswith("找计分板") else 0.6 + 0.3 * ratio
                job.progress = min(0.9, share)
                job.message = f"{message} {done}/{total}"

            result = roster_scan.scan(video_path, profile=profile, matcher=matcher,
                                      step=1.0, on_progress=on_progress)

            if not result.found:
                # 没按过 Tab（或计分板一帧都没露）：改读顶部阵容条，只有角色没有昵称
                job.message = "没找到计分板，改读顶部阵容条"
                job.progress = 0.9
                line = lineup.scan(video_path, profile=profile, matcher=matcher)
                if line.found and line.recognized:
                    payload = line.to_dict()
                    payload["message"] = ("这段录像里没找到计分板（可能整局没按 Tab），"
                                          "下面是顶部阵容条认出的角色")
                    return payload
                return result.to_dict()

            # 记下这局的昵称 ↔ 角色，之后找击杀时拿它校对播报里的小字
            fixed_events = self.remember_roster(video_path, [slot.nickname for slot in result.slots],
                                 {slot.nickname: slot.character for slot in result.slots if slot.character},
                                 boards=list(result.hits or result.candidates or []))

            missing = len(result.slots) - result.recognized
            if missing:
                # 先看这场对局之前从播报头像里认出来的人
                captured = self.known_characters(video_path)
                for slot in result.slots:
                    if slot.character or not captured:
                        continue
                    for name, character in captured.items():
                        if feed.same_name(slot.nickname, name):
                            slot.character = character
                            break
                missing = len(result.slots) - result.recognized

            filled: list[tuple[str, str]] = []
            if missing:
                # 还有被阵亡标压住的：用顶部阵容条按「排除法」补
                job.message = "计分板缺头像，读顶部阵容条补"
                job.progress = 0.9
                line = lineup.scan(video_path, profile=profile, matcher=matcher)
                filled = lineup.fill_rows(result.slots, line) if line.found else []
                if filled:
                    fixed_events += self.remember_roster(video_path, [n for n, _ in filled], dict(filled))

            payload = result.to_dict()
            payload["mode"] = "scoreboard"
            if filled:
                payload["filled"] = [{"nickname": nickname, "character": character}
                                     for nickname, character in filled]

            missing = len(result.slots) - result.recognized
            notes = []
            if result.boards > 1:
                notes.append(f"合并了 {result.boards} 块计分板一起投票")
            if missing:
                notes.append(f"还有 {missing} 个角色没认出来")
            elif result.slots:
                notes.append(f"{len(result.slots)} 个角色全认出来了")
            if filled:
                notes.append("其中 " + str(len(filled)) + " 个是按顶部阵容条补的："
                             + "、".join(f"{n}→{c}" for n, c in filled))
            if fixed_events:
                payload["fixed_events"] = fixed_events
                notes.append(f"顺手用昵称表订正了 {fixed_events} 条播报名字")
            if notes:
                payload["message"] = "，".join(notes)
            return payload
        return self.start("识别双方阵容", run)

    def scan_rounds(self, video_path: str) -> Job:
        """按回合切时间轴：读 HUD 上的比分，比分一变就是一个回合结束。"""
        def run(job: Job):
            def on_progress(done: int, total: int) -> None:
                job.progress = min(0.9, done / max(1, total))
                job.message = f"读回合比分 {done}/{total}"

            result = rounds.scan(video_path, on_progress=on_progress)
            payload = result.to_dict()
            if result.found:
                entry = self._entry(video_path)
                entry["rounds"] = {"rounds": payload["rounds"], "match_end": payload.get("match_end")}
                save_roster_cache(self.roster_cache)
                payload["message"] = f"分好了 {result.count} 个回合"
            return payload
        return self.start("分回合", run)
    def scan_highlights(self, video_path: str, self_name: str) -> Job:
        def run(job: Job):
            profile = config.load_profile()
            engine = ocr.engine()
            recognizer = ocr.recognizer()
            icons = feed.load_icons()
            known = self.known_names(video_path)          # 这局玩家的昵称，用来校对播报里的小字
            characters = self.known_characters(video_path)
            boards = self.known_boards(video_path)
            portraits = None
            captured = 0
            events: list[dict] = []
            skipped = 0
            with Video(video_path) as video:
                adapted = geometry.for_size(profile, (video.width, video.height))
                region = geometry.region(adapted, "kill_feed")
                scale = geometry.height_scale(adapted)[1]
                step = 1.0 / max(0.5, float(self.settings.get("fps", 2.0)))
                total = max(1, int(video.duration / step) + 1)
                for index, (moment, image) in enumerate(video.frames(step=step), start=1):
                    job.progress = min(0.95, index / total)
                    job.message = f"识别播报 {min(index, total)}/{total}"
                    rows = feed.read_rows(image, region, icons, recognizer,
                                          known=known or None, wanted=self_name)
                    if not rows:
                        continue
                    if boards:
                        # 认阵容时已经知道哪几秒开着计分板，直接按时间跳过，省掉每帧一次 OCR
                        if any(abs(moment - when) <= 1.5 for when in boards):
                            skipped += 1
                            continue
                    elif roster_scan.looks_like_scoreboard(image, profile, engine,
                                                          recognizer=recognizer):
                        # 计分板会盖住播报，这时候的「没播报」不代表没发生
                        skipped += 1
                        continue
                    patch = image[region[1]:region[3], region[0]:region[2]]
                    for row in rows:
                        name = row.killer if row.killer_matched else ""
                        if name and name not in characters and captured < 16:
                            if portraits is None:
                                portraits = make_matcher("orb", features.PORTRAITS)
                            character, score = feed.read_character(patch, row, scale, portraits)
                            if character:
                                characters[name] = character
                                captured += 1
                        if known and not row.killer_matched:
                            continue          # 对不上昵称表，多半是 OCR 读花了
                        if not feed.same_name(row.killer, self_name) or row.killer_score < 0.5:
                            continue
                        for kind in row.kinds:
                            # 播报行会在屏幕上停好几秒，同一次击杀会被读到好几次；
                            # 受害者名字 OCR 常错一两个字（腰射大赫 / 腰射大王赫卡忒），
                            # 所以按 0.6 的相似度去重，比 same_name 的默认阈值松一点
                            if any(item["kind"] == kind and abs(item["time"] - moment) <= 8
                                   and feed.name_score(item.get("victim", ""), row.victim) >= 0.6
                                   for item in events):
                                continue
                            events.append({"time": round(moment, 1), "kind": kind,
                                           "victim": row.victim, "confidence": row.killer_score})
            events.sort(key=lambda item: item["time"])
            for item in events:
                item["label"] = {"knockdown": "击倒", "kill": "击杀"}.get(item["kind"], item["kind"])
            if characters:
                self.remember_roster(video_path, [], characters)
            payload = {"video": video_path, "events": events, "characters": characters,
                       "known_names": len(known)}
            if skipped:
                payload["skipped_scoreboard"] = skipped
            return payload
        return self.start("识别击杀/击倒", run)

    def cut(self, video_path: str, events: list[dict], out_dir: str | None = None) -> Job:
        def run(job: Job):
            video = Video(video_path)
            try:
                duration = video.duration
            finally:
                video.close()
            segments = clipper.plan(events, past=float(self.settings.get("past", 8.0)),
                                    future=float(self.settings.get("future", 2.0)),
                                    duration=duration,
                                    join_gap=float(self.settings.get("join_gap", 0.0)))
            target = Path(out_dir or (OUTPUT / "clips"))
            outputs = clipper.cut_all(video_path, segments, target,
                                      ffmpeg=self.settings.get("ffmpeg") or None,
                                      on_done=lambda path, index, total: (
                                          setattr(job, "progress", index / max(1, total)),
                                          setattr(job, "message", f"切片 {index}/{total}：{path.name}")))
            return {"video": str(video_path), "out_dir": str(target),
                    "files": [str(path) for path in outputs],
                    "clips": [{"path": str(path), "name": path.name,
                               "start": round(segment.start, 3), "end": round(segment.end, 3),
                               "label": segment.label,
                               "events": [{"time": round(float(item.get("time", 0.0)), 2),
                                           "label": item.get("label", ""),
                                           "kind": item.get("kind", ""),
                                           "victim": item.get("victim", "")}
                                          for item in segment.events]}
                              for path, segment in zip(outputs, segments)],
                    "segments": [{"start": s.start, "end": s.end, "label": s.label,
                                  "count": s.count} for s in segments]}
        return self.start("裁剪高光", run)


HUB = Hub()


def _about() -> dict:
    """给界面「关于」卡片用的版本信息。"""
    try:
        ffmpeg = str(clipper.find_ffmpeg(None))
    except Exception as exc:  # 找不到 ffmpeg 也别让接口挂掉
        ffmpeg = f"没找到（{exc}）"
    return {
        "version": version.VERSION,
        "build": version.BUILD,
        "label": version.label(),
        "python": platform.python_version(),
        "ffmpeg": ffmpeg,
        "data": str(OUTPUT),
    }


def _changelog() -> str:
    """读根目录的 CHANGELOG.md（开发时在项目根，打包后在 resources/app 里）。"""
    for candidate in (ROOT / "CHANGELOG.md", ROOT.parent / "CHANGELOG.md"):
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return "还没有更新日志。"


class Handler(BaseHTTPRequestHandler):
    server_version = "StrinovaStar/0.1"

    def log_message(self, *_args):  # 安静一点，别把每个请求都打到控制台
        pass

    # --- 工具 ---
    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _text(self, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8", "replace")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path: Path) -> None:
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        suffix = path.suffix.lower()
        kind = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".png": "image/png"}.get(suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _portrait(self, name: str) -> None:
        """角色小头像。只认 portraits 目录下的「<角色名>.png」，别的一律 404。"""
        clean = (name or "").strip()
        if not clean or any(char in clean for char in '\\/:*?"<>|') or clean.startswith("."):
            self._json({"error": "bad name"}, 400)
            return
        target = features.PORTRAITS / f"{clean}.png"
        if not target.is_file():
            self._json({"error": "no portrait"}, 404)
            return
        self._static(target)

    def _media(self, path: Path) -> None:
        """视频播放：支持 Range，拖动进度条才不卡。"""
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            piece = header[len("bytes="):].split("-")
            try:
                start = int(piece[0]) if piece[0] else 0
                end = int(piece[1]) if len(piece) > 1 and piece[1] else size - 1
            except ValueError:
                start, end = 0, size - 1
        end = min(end, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if header else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        remaining = length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(2 * 1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # --- 路由 ---
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._static(WEB / "index.html")
            return
        if parsed.path.startswith("/web/"):
            self._static(WEB / parsed.path[len("/web/"):])
            return
        if parsed.path == "/api/media":
            target = parse_qs(parsed.query).get("path", [""])[0]
            self._media(Path(target))
            return
        if parsed.path == "/api/portrait":
            # 阵容表里显示的小头像：按角色名取 assets/characters/portraits/<角色>.png
            name = parse_qs(parsed.query).get("name", [""])[0]
            self._portrait(name)
            return
        if parsed.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            seen = 0
            try:
                while True:
                    entries = HUB.log[seen:]
                    for item in entries:
                        payload = json.dumps(item, ensure_ascii=False)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    if entries:
                        seen = len(HUB.log)
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError):
                return
        if parsed.path == "/api/state":
            self._json({"settings": HUB.settings, "videos": HUB.videos(),
                        "job": HUB.job.to_dict() if HUB.job else None,
                        "record": HUB.record_status(),
                        "hotkey": HUB.hotkey_status(),
                        "version": version.VERSION,
                        "watch": bool(HUB.watcher and HUB.watcher.running)})
            return
        if parsed.path == "/api/about":
            self._json(_about())
            return
        if parsed.path == "/api/changelog":
            self._text(_changelog())
            return
        if parsed.path == "/api/record":
            self._json({"record": HUB.record_status()})
            return
        if parsed.path == "/api/audio-devices":
            try:
                self._json(HUB.audio_devices())
            except Exception as exc:
                self._json({"microphones": [], "desktop": [], "error": str(exc)})
            return
        if parsed.path == "/api/cache":
            query = parse_qs(parsed.query)
            video = (query.get("path") or [""])[0]
            entry = HUB.roster_cache.get(video, {}) if video else {}
            if video and HUB.fix_events(video):
                # 老缓存里的名字可能还是错字/截断，用已知昵称表就地订正
                save_roster_cache(HUB.roster_cache)
            self._json({"roster": entry.get("roster"), "events": entry.get("events"),
                        "clips": entry.get("clips"), "rounds": entry.get("rounds")})
            return
        if parsed.path == "/api/log/export":
            body = logs.export_text(HUB).encode("utf-8", "replace")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="strinova-log-%s.txt"' % stamp)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/job":
            # 前端每 700ms 轮询这里；没有它就点了按钮界面永远不动
            self._json({"job": HUB.job.to_dict() if HUB.job else None,
                        "record": HUB.record_status()})
            return
        self._json({"error": "unknown"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
        except Exception:
            self._json({"error": "bad json"}, 400)
            return
        try:
            if parsed.path == "/api/settings":
                HUB.settings.update(payload)
                save_settings(HUB.settings)
                self._json({"settings": HUB.settings})
                return
            if parsed.path == "/api/rounds":
                job = HUB.scan_rounds(payload.get("video") or "")
                self._json({"job": job.to_dict()})
                return
            if parsed.path == "/api/roster":
                job = HUB.scan_roster(payload.get("video") or "")
                self._json({"job": job.to_dict()})
                return
            if parsed.path == "/api/highlights":
                job = HUB.scan_highlights(payload.get("video") or "", payload.get("self_name") or "")
                self._json({"job": job.to_dict()})
                return
            if parsed.path == "/api/clip":
                job = HUB.cut(payload.get("video") or "", payload.get("events") or [],
                              payload.get("out_dir"))
                self._json({"job": job.to_dict()})
                return
            if parsed.path == "/api/watch":
                if payload.get("on"):
                    self._json({"watch": HUB.watch_start()})
                else:
                    self._json({"watch": HUB.watch_stop()})
                return
            if parsed.path == "/api/pick-folder":
                # 弹系统「选择文件夹」窗口：这个请求会一直挂到用户选完（或点取消）
                if not _PICK_LOCK.acquire(blocking=False):
                    raise RuntimeError("已经有一个选择窗口开着")
                try:
                    path = picker.pick_folder("选录像文件夹", payload.get("initial") or None)
                finally:
                    _PICK_LOCK.release()
                self._json({"path": path})
                return
            if parsed.path == "/api/record":
                action = str(payload.get("action") or "").lower()
                if action == "start":
                    self._json({"record": HUB.record_start(payload.get("mode"))})
                elif action == "stop":
                    self._json({"record": HUB.record_stop()})
                elif action == "toggle":
                    self._json({"record": HUB.record_toggle()})
                else:
                    self._json({"error": "action 只能是 start / stop / toggle"}, 400)
                return
            if parsed.path == "/api/job":
                self._json({"job": HUB.job.to_dict() if HUB.job else None})
                return
        except Exception as exc:
            self._json({"error": str(exc)}, 400)
            return
        self._json({"error": "unknown"}, 404)


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    log_dir = logs.install(OUTPUT / "logs")
    logs.rotate()
    logs.write(f"服务启动：http://{host}:{port}，数据目录 {OUTPUT}")
    """把界面跑起来（阻塞）。"""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"界面已启动：{url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        HUB.install_hotkey()
    except Exception as exc:      # 热键装不上不影响别的功能
        print(f"热键没装上：{exc}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
