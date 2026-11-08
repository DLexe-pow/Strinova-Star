"""Strinova Star 核心包。

链路：标定（config / geometry）→ 取帧（video）→ OCR 昵称（ocr）
→ 认头像（features + matcher_orb / sift / surf）→ 计分板（scoreboard / roster_scan）
→ 击杀播报（feed）→ 切片（clipper）→ 界面后端（ui）。
"""

from .version import BUILD, VERSION  # noqa: F401  （版本号只在 version.py 里改）`n`n__version__ = VERSION
