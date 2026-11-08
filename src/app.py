"""Strinova Star 主程序：只负责把界面跑起来。

所有功能（找计分板、识别阵容、识别击杀/击倒、裁剪高光）都在界面里点按钮触发，
后端逻辑各自在 strinova_star 下的独立模块里：

    video.py       取帧
    roster_scan.py 找计分板 → 双方阵容（昵称 + 角色）
    scoreboard.py  单帧计分板解析
    feed.py        击杀/击倒播报
    clipper.py     无损切片
    ui.py          HTTP + 静态页（界面后端）

用法：
    python src/app.py            # 起界面并自动开浏览器
    python src/app.py --no-open --port 8765
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from strinova_star import ui  # noqa: E402  （要先补 sys.path 才能导入）


def main() -> int:
    parser = argparse.ArgumentParser(description="Strinova Star 图形界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    ui.serve(host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
