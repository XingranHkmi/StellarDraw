"""资源路径解析工具 —— 全项目**路径的唯一来源**。

本模块必须最先实现且不得绕过。原因见 PRD 附录 B 第 10、11 条：
PyInstaller 打包后，"静态资源"与"运行时数据"位于两个完全不同的位置，
一旦混用，轻则图片与前端文件加载失败，重则老师的班级名单在关闭软件后凭空消失。

两类路径的区别：

    ┌────────────────────┬──────────────────────────────┬────────────────────────────────┐
    │ 类型               │ 开发时                        │ 打包后（PyInstaller）           │
    ├────────────────────┼──────────────────────────────┼────────────────────────────────┤
    │ 静态资源（只读）    │ <项目根>/src/stellardraw/ui    │ <临时解包目录>/stellardraw/ui      │
    │ 运行时数据（读写）  │ <项目根>/data                │ <exe 所在目录>/data             │
    └────────────────────┴──────────────────────────────┴────────────────────────────────┘

因此本模块对外只提供两个取值函数：

    · resource_path(*parts)  读取**静态资源**，打包后跟随 sys._MEIPASS
    · data_dir() / data_file(*parts)  读写**运行时数据**，永远在 exe 旁边

【重要】任何模块都不允许自己用 __file__ 拼路径，一律通过本模块获取。
"""

from __future__ import annotations

import sys
from pathlib import Path

# PyInstaller 打包后会把资源解包到临时目录，并把该目录的绝对路径写入 sys._MEIPASS
# 开发环境下这个属性不存在，因此它同时也是"是否处于打包环境"的判定依据
_MEIPASS_ATTR = "_MEIPASS"

# 运行时数据目录名（与 exe 同级）
DATA_DIR_NAME = "data"

# 前端资源目录名（位于 stellardraw 包内）
UI_DIR_NAME = "ui"

# 打包配置中前端资源被映射到包内的目标路径前缀
# 对应 --add-data "src/stellardraw/ui;stellardraw/ui"
_FROZEN_UI_PREFIX = "stellardraw"


def is_frozen() -> bool:
    """判断当前是否运行在 PyInstaller 打包后的环境中。

    返回：
        True 表示已打包（资源在临时解包目录），False 表示开发环境。
    """
    return hasattr(sys, _MEIPASS_ATTR)


def project_root() -> Path:
    """返回项目根目录的绝对路径（仅在开发环境有意义）。

    本文件位于 <项目根>/src/stellardraw/paths.py，
    因此向上一级到 stellardraw、再上一级到 src、再上一级即项目根。
    """
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """获取**静态资源**的绝对路径（只读）。

    参数：
        *parts: 资源相对于 ui/ 目录的路径片段。
                例如 resource_path("img", "card_back.svg")、
                     resource_path("css", "base.css")。

    返回：
        资源的绝对路径。开发环境指向 src/stellardraw/ui/ 下；
        打包后指向 PyInstaller 的临时解包目录。

    注意：
        该函数只负责拼路径，不检查文件是否存在。
        调用方如需可自行 .exists() 判断。
    """
    if is_frozen():
        # 打包后：资源被解包到 sys._MEIPASS，按 --add-data 的映射路径组织
        base = Path(getattr(sys, _MEIPASS_ATTR))
        return base.joinpath(_FROZEN_UI_PREFIX, UI_DIR_NAME, *parts)

    # 开发环境：本文件所在目录就是 stellardraw 包目录
    return Path(__file__).resolve().parent.joinpath(UI_DIR_NAME, *parts)


def _data_base() -> Path:
    """返回运行时数据的**父目录**（内部函数）。

    打包后是 exe 所在目录（必须用 sys.executable，不能用 __file__）；
    开发环境是项目根目录。抽成独立函数是为了让 data_dir() 的意图更清晰。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def data_dir(*parts: str, create: bool = True) -> Path:
    """获取**运行时数据目录**（data/）的绝对路径。

    参数：
        *parts:  data/ 下的子目录片段，如 data_dir("rosters")。
        create:  是否自动创建目录（默认 True）。传 False 可用于"只查询不创建"的场景。

    返回：
        数据目录的绝对路径。

    【关键】该目录始终位于 exe（开发时为项目根）**同级**。
    绝不能指向 PyInstaller 的临时解包目录 —— 那是临时目录，
    程序退出即被清理，写进去的名单会在下次启动时凭空消失。
    """
    target = _data_base().joinpath(DATA_DIR_NAME, *parts)
    if create:
        # parents=True 允许一次性创建多级目录，exist_ok=True 避免已存在时报错
        target.mkdir(parents=True, exist_ok=True)
    return target


def data_file(*parts: str) -> Path:
    """获取 data/ 目录下某个**文件**的路径（不创建文件本身）。

    参数：
        *parts: 文件相对 data/ 的路径片段，如 data_file("config.json")、
                data_file("rosters", "初一3班.json")。

    返回：
        文件的绝对路径。其**父目录**会被自动创建，文件本身不会。
    """
    path = data_dir().joinpath(*parts)
    # 只创建父目录：文件内容由调用方（storage 模块）负责写入
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
