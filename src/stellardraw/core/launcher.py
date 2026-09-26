"""外部程序启动器（对应界面上的「简洁视图」按钮）与系统目录打开。

对应需求：PRD F-09 / F-02c / E-04 / E-05 / §6.4 / AC-08 / AC-26

设计要点：
    「简洁视图」启动的外部程序与  StellarDraw **完全独立**：
        · 不传任何参数
        · 不共享任何数据或状态
        · 不建立进程间通信
        · 主程序不退出、不最小化

     StellarDraw 在这里只承担「启动器」角色。
    这个外部程序可以是任意普通 exe（老师自选、甚至不是本项目的一部分），
    因此实现上必须足够宽容 —— 路径失效只能提示，绝不能崩溃。

本模块所有函数都**不抛异常给调用方**（除 open_directory 明确说明外），
统一以 ``(是否成功, 提示文案)`` 的形式返回，便于 api 层直接透传给前端。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 提示文案（集中定义：与 PRD E-04 / E-05 的措辞保持一致，便于测试锁定）
# ---------------------------------------------------------------------------

MSG_NOT_SET = "尚未设置外部程序，请先在设置中指定路径"
MSG_NOT_FOUND = "未找到该程序，请重新设置路径"
MSG_OK = ""

# ---------------------------------------------------------------------------
# Windows 进程创建标志
# ---------------------------------------------------------------------------

# DETACHED_PROCESS (0x8)        —— 不继承父进程的控制台，避免弹出黑窗
# CREATE_NEW_PROCESS_GROUP(0x200) —— 使子进程不受父进程 Ctrl+C 影响
# 两者只在 Windows 上有效，非 Windows 平台传 0
CREATE_FLAGS = 0x00000008 | 0x00000200


def _creation_flags() -> int:
    """按当前平台返回 subprocess 的 creationflags（内部函数）。"""
    return CREATE_FLAGS if sys.platform == "win32" else 0


def normalize_path(path: str | os.PathLike[str] | None) -> str:
    """把用户输入的路径规整为去空白的字符串（内部工具，公开便于测试）。

    老师从资源管理器复制的路径常带首尾空格，或粘贴时带上英文双引号
    （Windows「复制文件地址」会给路径加引号），这里一并剥掉。
    """
    if path is None:
        return ""
    text = str(path).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return text


def is_valid_exe(path: str) -> bool:
    """判断给定路径是否指向一个存在的文件。

    对应需求：PRD E-04（未设置）/ E-05（路径失效）。

    参数：
        path: 用户设置的 exe 完整路径。

    返回：
        True 表示路径非空且文件存在。
    """
    text = normalize_path(path)
    if not text:
        return False
    return Path(text).is_file()


def launch(exe_path: str) -> tuple[bool, str]:
    """启动外部程序。

    对应需求：PRD F-09 / E-05 / §6.4。

    参数：
        exe_path: 外部程序的完整路径。

    返回：
        (是否成功, 提示信息)。失败时第二个元素为面向老师的友好提示，
        由上层直接显示；成功时第二个元素为空字符串。

    实现要点：
        · 路径为空   → 返回 (False, MSG_NOT_SET)（PRD E-04）
        · 文件不存在 → 返回 (False, MSG_NOT_FOUND)（PRD E-05）
        · 捕获所有启动异常，绝不向上抛出导致界面崩溃
    """
    text = normalize_path(exe_path)
    if not text:
        return False, MSG_NOT_SET
    if not Path(text).is_file():
        return False, MSG_NOT_FOUND
    try:
        _spawn(text)
    except Exception as exc:
        # 兜底：任何异常都只能提示，不能崩界面
        return False, f"{MSG_NOT_FOUND}（{exc}）"
    return True, MSG_OK


def _spawn(exe_path: str) -> subprocess.Popen:
    """以"完全独立"的方式启动目标程序（内部工具函数）。

    使用 subprocess.Popen 而非 os.startfile，是为了能捕获启动失败异常；
    使用 CREATE_FLAGS 使目标程序不随  StellarDraw 的退出而被连带关闭。

    工作目录设为该 exe 所在目录 —— 很多绿色版小工具依赖相对路径读取
    自己的配置文件，继承  StellarDraw 的工作目录会让它们找不到资源。
    """
    target = Path(exe_path)
    # 路径来自用户在设置面板中的显式选择，不是外部输入
    return subprocess.Popen(
        [str(target)],
        cwd=str(target.parent),
        creationflags=_creation_flags(),
    )


# ---------------------------------------------------------------------------
# 打开数据目录（PRD F-02c / AC-26）
# ---------------------------------------------------------------------------


def open_directory(path: str | os.PathLike[str]) -> Path:
    """用系统文件管理器打开指定目录（不存在时先自动创建）。

    对应需求：PRD F-02c / AC-26（TI-14 已确认）。

    参数：
        path: 目标目录路径。

    返回：
        实际打开的目录绝对路径。

    异常：
        OSError: 目录无法创建或系统拒绝打开时抛出，由 api 层转换为友好提示。
    """
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)

    if hasattr(os, "startfile"):
        # Windows：startfile 对目录会调起资源管理器
        os.startfile(str(target))
    else:  # pragma: no cover —— 目标平台是 Windows，此处仅为跨平台可读性
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(target)], creationflags=_creation_flags())
    return target
