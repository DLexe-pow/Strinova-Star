"""跑全部单测（不用安装包，也不需要游戏）。

    python src/run_tests.py

不用 unittest discover：这台机器上的 Python 是嵌入式发行版，
会忽略 PYTHONPATH，discovery 的路径推断经常拿不到包，
所以这里显式把 tests/*.py 逐个导入再收集用例。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


def load_module(path: Path):
    name = f"tests.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    files = sorted(p for p in (ROOT / "tests").glob("test_*.py"))
    if not files:
        print("没找到 tests/test_*.py")
        return 1
    for path in files:
        module = load_module(path)
        suite.addTests(loader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
