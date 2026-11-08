"""免安装入口。

    python src/run.py avatars                  # 默认 ORB 认 20 张局内真值
    python src/run.py avatars --method sift    # 换 SIFT
    python src/run.py avatars --method surf    # SURF（要有 opencv-contrib）
    python src/run.py avatars --method hist    # 旧的直方图方案，做对照
    python src/run.py avatars --list           # 看本机哪种算法能用
    python src/run.py avatars --truth lineup    # 只跑顶部阵容条
    python src/run.py avatars --assign          # 一人一角色的整体分配

    python src/run.py feed 录像.mp4 --name 昵称 --save-events events.json   # 找事件并存清单
    python src/run.py clip 录像.mp4 --events events.json --past 5 --future 5  # 前后各 5 秒切一段
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strinova_star import avatars, config, scoreboard  # noqa: E402
from strinova_star.legacy_hist import HalfbodyMatcher  # noqa: E402
from strinova_star.matchers import MATCHERS, describe, make  # noqa: E402

TRUTH_FOLDERS = {"lineup": "assets/characters/lineup", "roster": "assets/characters/roster"}
HIST = "hist"


def _format_for(name: str) -> tuple[str, float]:
    """不同算法的分数尺度不一样：内点是整数，直方图是 0~1。"""
    if name == HIST:
        return "{:.2f}", 0.45
    return "{:.0f}", 12.0


def feed_templates() -> Path:
    from strinova_star import feed
    return feed.TEMPLATES


def cmd_avatars(args: argparse.Namespace) -> int:
    if args.list:
        print("算法        本机可用  说明")
        for name, usable, note in describe():
            print(f"  {name:<10} {'可用' if usable else '不可用':<6}  {note}")
        print(f"  {HIST:<10} {'可用':<6}  旧的直方图方案（颜色 0.75 + 亮度 0.25），对照用")
        return 0

    fmt, min_score = _format_for(args.method)
    try:
        matcher = HalfbodyMatcher(args.templates) if args.method == HIST else make(
            args.method, folder=args.templates)
    except avatars.SurfUnavailable as exc:
        print(f"[SURF 不可用]\n{exc}")
        return 2
    print(f"模板：{matcher.folder.name}（{len(matcher)} 个角色，算法 {args.method}）")

    total = hits = 0
    elapsed = 0.0
    for key in args.truth:
        folder = avatars.ROOT / TRUTH_FOLDERS[key]
        samples = [p for p in sorted(folder.glob("*.png")) if not p.stem.startswith("_")]
        crops = [avatars.imread(path) for path in samples]
        assigned = avatars.assign(matcher, crops, min_score=min_score) if args.assign else [None] * len(crops)
        ok = 0
        print(f"\n[{key}] {len(samples)} 个局内真值裁片" + ("（一人一角色整体分配）" if args.assign else ""))
        for path, crop, pick in zip(samples, crops, assigned):
            started = time.perf_counter()
            ranked = matcher.scores(crop)
            elapsed += time.perf_counter() - started
            best = ranked[0]
            name, value = pick if pick else (best.character, best.score)
            hit = name == path.stem
            ok += hit
            second = ranked[1]
            margin = best.score - second.score
            mark = "OK " if hit else "错 "
            print(f"  {path.stem:<10} -> {name:<10} {fmt.format(value)} {mark} "
                  f"次：{second.character:<10} {fmt.format(second.score)}  差 {margin:+6.2f}")
        print(f"  命中 {ok}/{len(samples)}")
        total += len(samples)
        hits += ok
    if total:
        print(f"\n合计 {hits}/{total} = {hits / total:.0%}，平均 {elapsed / total * 1000:.0f} ms/张")
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    """一张计分板截图 → 10 条「昵称 + 角色」。"""
    profile = config.load_profile(args.profile)
    try:
        matcher = make(args.method)
    except avatars.SurfUnavailable as exc:
        print(f"[SURF 不可用]\n{exc}")
        return 2

    print(f"标定：{Path(profile['_path']).name}"
          f"（{profile['reference_size'][0]:.0f}×{profile['reference_size'][1]:.0f}）"
          f"  算法：{args.method}")
    for image_path in args.images:
        image = avatars.imread(image_path)
        started = time.perf_counter()
        board = scoreboard.read_scoreboard(image, profile, matcher,
                                           use_assign=not args.no_assign)
        elapsed = (time.perf_counter() - started) * 1000
        print(f"\n{Path(image_path).name}  {image.shape[1]}×{image.shape[0]}  "
              f"缩放 {board.scale[0]:.4f}  {elapsed:.0f} ms")
        print(scoreboard.format_table(board))
        if args.json:
            import json
            print(json.dumps(board.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    """扫录像的左上角播报，找出「我」的击倒/击杀，并保存那一帧图像。"""
    import cv2
    import json

    from strinova_star import feed, geometry, ocr

    profile = config.load_profile(args.profile)
    region = geometry.region(profile, args.region)
    icons = feed.load_icons(args.templates)
    recognize = None if args.no_ocr else ocr.recognizer()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"打不开视频：{args.video}")
        return 2
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    duration = total / fps if total else 0.0
    end = min(args.end or duration, duration)
    step = 1.0 / max(args.fps, 0.2)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"视频 {duration:.1f}s  播报区 {region}  {args.fps:g} fps  "
          f"本人 {args.name}  图标 {list(icons)}")

    events: list[dict] = []
    started = time.perf_counter()
    moment = args.start
    scanned = 0
    while moment <= end:
        cap.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        scanned += 1
        for row in feed.read_rows(frame, region, icons, recognize):
            if not feed.same_name(row.killer, args.name):
                continue
            for kind in row.kinds:
                if kind not in args.kinds:
                    continue
                dup = next((e for e in events
                            if e["kind"] == kind and feed.same_name(e["victim"], row.victim)
                            and abs(e["time"] - moment) <= args.dedupe), None)
                if dup:
                    continue
                events.append({"time": round(moment, 2), "kind": kind,
                               "victim": row.victim, "confidence": row.killer_score,
                               "y": round(row.y, 1)})
                print(f"  [{moment:7.2f}s] {kind:<9} → {row.victim or '(未读到名字)'}")
        moment += step
    cap.release()

    # 保存事件帧
    cap = cv2.VideoCapture(args.video)
    for index, event in enumerate(events, 1):
        cap.set(cv2.CAP_PROP_POS_MSEC, event["time"] * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        name = f"{index:02d}_{event['time']:07.2f}s_{event['kind']}_{event['victim'] or 'unknown'}.png"
        path = out_dir / name
        cv2.imencode(".png", frame)[1].tofile(str(path))
        event["frame"] = str(path)
    cap.close() if hasattr(cap, "close") else None
    cap.release()

    elapsed = time.perf_counter() - started
    print(f"\n扫了 {scanned} 帧，用时 {elapsed:.1f}s（{elapsed / max(scanned, 1) * 1000:.0f} ms/帧）")
    print(f"共 {len(events)} 个事件，帧图存在 {out_dir}")
    if args.save_events:
        path = Path(args.save_events)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"事件清单写到 {path}")
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    """按事件清单切片：前后各留几秒、重叠的合并，都能在命令行上调。"""
    import json

    from strinova_star import clipper, video as video_module

    # utf-8-sig：Windows 上记事本/PowerShell 存出来的 json 常带 BOM
    events = json.loads(Path(args.events).read_text(encoding="utf-8-sig"))
    if isinstance(events, dict):
        events = events.get("events", [])
    if not events:
        print("事件清单是空的，没什么可切")
        return 1

    duration = args.duration
    if duration is None:
        with video_module.Video(args.video) as video:
            duration = video.duration

    segments = clipper.plan(events, past=args.past, future=args.future,
                            duration=duration, join_gap=args.join_gap)
    merged = sum(1 for segment in segments if segment.count > 1)
    print(f"{Path(args.video).name}  {duration:.1f}s  {len(events)} 个事件 → {len(segments)} 段"
          f"（前 {args.past:g}s / 后 {args.future:g}s，合并容差 {args.join_gap:g}s）")
    for index, segment in enumerate(segments, start=1):
        print(f"  {index:02d}  {segment}")
    if merged:
        print(f"  其中 {merged} 段是多个事件并起来的")
    if args.dry_run:
        print("（--dry-run：只看窗口，不真的切）")
        return 0

    outputs = clipper.cut_all(args.video, segments, args.out, ffmpeg=args.ffmpeg or None,
                              accurate=args.accurate,
                              on_done=lambda path, index, total: print(f"  [{index}/{total}] {path.name}"))
    print(f"\n切出 {len(outputs)} 段，在 {Path(args.out)}")
    return 0


def cmd_lineup(args: argparse.Namespace) -> int:
    """不看 Tab 计分板：直接从正常画面顶部那条阵容条读角色。"""
    import json

    from strinova_star import lineup

    result = lineup.scan(args.video, samples=args.frames,
                         on_progress=lambda done, total, message: print(f"  [{done}/{total}] {message}"))
    print(lineup.format_text(result))
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.found else 1


def cmd_rounds(args: argparse.Namespace) -> int:
    """按回合切时间轴：读 HUD 比分，比分一变就是上一回合结束。"""
    import json

    from strinova_star import rounds

    result = rounds.scan(args.video, step=args.step,
                         on_progress=lambda done, total: print(f"\r  读比分 {done}/{total}", end="", flush=True))
    print()
    print(rounds.format_text(result))
    if result.match_end:
        print(f"（{result.match_end:.1f}s 之后是对局结算/回大厅）")
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.found else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="Strinova Star")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("avatars", help="角色识别自测")
    p.add_argument("--templates", default=str(avatars.HALFBODY),
                   help="模板目录，默认 assets/characters/halfbody（半身立绘）")
    p.add_argument("--method", default="orb", choices=sorted(MATCHERS) + [HIST],
                   help="orb=特征点+几何校验（默认）；sift；surf；hist=旧直方图")
    p.add_argument("--truth", nargs="+", default=list(TRUTH_FOLDERS),
                   choices=list(TRUTH_FOLDERS), help="真值样本目录")
    p.add_argument("--assign", action="store_true",
                   help="用整体唯一分配（一人一角色）而不是逐人取最大分")
    p.add_argument("--list", action="store_true", help="列出可用算法后退出")
    p.set_defaults(func=cmd_avatars)

    b = sub.add_parser("board", help="读一张 Tab 计分板截图：昵称 + 角色")
    b.add_argument("images", nargs="+", help="截图路径（可以多张）")
    b.add_argument("--profile", default=None, help="标定文件，默认 assets/profiles/strinova-2560x1600.json")
    b.add_argument("--method", default="orb", choices=sorted(MATCHERS), help="识别算法，默认 orb")
    b.add_argument("--no-assign", action="store_true", help="逐人取最大分，不做整体分配")
    b.add_argument("--json", action="store_true", help="额外打印 JSON")
    b.set_defaults(func=cmd_board)

    f = sub.add_parser("feed", help="扫录像的左上角播报，找本人的击倒/击杀并存帧图")
    f.add_argument("video", help="录像路径")
    f.add_argument("--name", required=True, help="本人的游戏昵称")
    f.add_argument("--profile", default=None, help="标定文件")
    f.add_argument("--region", default="kill_feed", help="播报区域名，默认 kill_feed")
    f.add_argument("--templates", default=str(feed_templates()), help="图标模板目录")
    f.add_argument("--fps", type=float, default=2.0, help="每秒抽几帧，默认 2")
    f.add_argument("--start", type=float, default=0.0)
    f.add_argument("--end", type=float, default=None)
    f.add_argument("--out", default=str(ROOT / "output" / "frames"), help="帧图输出目录")
    f.add_argument("--kinds", nargs="+", default=["kill", "knockdown"],
                   choices=["kill", "knockdown"], help="要哪些事件，默认两种都要")
    f.add_argument("--dedupe", type=float, default=8.0, help="同一受害者去重窗口（秒）")
    f.add_argument("--no-ocr", action="store_true", help="只出图标事件，不认名字（最快）")
    f.add_argument("--save-events", default=None, metavar="PATH",
                   help="把事件清单写成 json，喂给 clip 子命令")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_feed)

    c = sub.add_parser("clip", help="按事件清单切片（前后秒数可调，重叠的自动合并）")
    c.add_argument("video", help="录像路径")
    c.add_argument("--events", required=True,
                   help="事件清单 json（python src/run.py feed ... --save-events 生成）")
    c.add_argument("--past", type=float, default=5.0, help="事件前留几秒，默认 5")
    c.add_argument("--future", type=float, default=5.0, help="事件后留几秒，默认 5")
    c.add_argument("--join-gap", type=float, default=0.0,
                   help="两个窗口间隔不超过这个秒数也合并，默认 0（只并重叠/相接的）")
    c.add_argument("--duration", type=float, default=None, help="视频总长，不给就自己读")
    c.add_argument("--out", default=str(ROOT / "output" / "clips"), help="输出目录")
    c.add_argument("--ffmpeg", default=None, help="ffmpeg 路径")
    c.add_argument("--accurate", action="store_true",
                   help="输出侧 seek：起点准到包，但长录像会慢（默认按关键帧对齐）")
    c.add_argument("--dry-run", action="store_true", help="只打印窗口，不切片")
    c.set_defaults(func=cmd_clip)

    l = sub.add_parser("lineup", help="读正常画面顶部的阵容条：只要角色，不用按 Tab")
    l.add_argument("video", help="录像路径")
    l.add_argument("--frames", type=int, default=4, help="投票用几帧，默认 4")
    l.add_argument("--json", action="store_true", help="额外打印 JSON")
    l.set_defaults(func=cmd_lineup)

    r = sub.add_parser("rounds", help="按回合切时间轴（读 HUD 上的双方比分）")
    r.add_argument("video", help="录像路径")
    r.add_argument("--step", type=float, default=1.0, help="隔几秒看一帧，默认 1.0")
    r.add_argument("--json", action="store_true", help="额外打印 JSON")
    r.set_defaults(func=cmd_rounds)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
