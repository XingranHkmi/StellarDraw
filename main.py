"""StellarDraw 课堂抽号机 —— 启动入口（薄壳）。

本文件只做一件事：
    把 ``src`` 目录加入 Python 的模块搜索路径，
    然后调用真正的应用入口 ``stellardraw.main.main()``。

为什么要这样写：
    本项目遵循 AGENTS.md 第六章的工作区约定——源码统一放在 ``src/`` 下。
    但 PyCharm 与 ``uv run`` 默认以**项目根目录**为工作目录，
    此时 ``stellardraw`` 包并不在搜索路径中，直接 import 会失败。
    在此注入一次路径后：
        · 可以直接 ``uv run main.py`` 启动；
        · 可以直接在 PyCharm 中配置运行 / 调试本文件；
        · 打包时同样可用本文件作为 PyInstaller 的入口。

注意：
    本文件不含任何业务逻辑。所有实现位于 ``src/stellardraw/`` 下，
    修改功能请去那里，不要把代码写进本文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 项目源码根目录：<项目根>/src
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    # 放在最前面，确保优先于任何同名已安装包
    sys.path.insert(0, str(_SRC_DIR))

# 路径注入必须发生在导入之前，因此这里刻意不放在文件顶部，禁用 E402 检查
from stellardraw.main import main  # noqa: E402


if __name__ == "__main__":
    main()
