"""版本号与构建信息。

要发新版本，只改这里的 **两个常量**，然后在 `CHANGELOG.md` 最上面加一节：

    VERSION = "0.1.1"
    BUILD = "2026-11-20"

`ui/web/index.html` 标题栏的版本小标签、设置页「关于」卡片、以及导出日志里的
版本信息都从这里取，不要在别处再写死版本号。
"""

from __future__ import annotations

VERSION = "0.1.0"
BUILD = "2026-11-08"

#: 给界面和日志用的一行文字，例如 ``v1.0.0（2026-11-08）``
def label() -> str:
    return f"v{VERSION}（{BUILD}）" if BUILD else f"v{VERSION}"
