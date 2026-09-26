"""本地数据持久化。

对应需求：PRD F-03 / F-04 / F-10 / NFR-S2 / NFR-D5 / AC-03④ / AC-09④ / §9.4

数据文件（全部为 UTF-8 明文的 JSON，便于老师自行查看与备份）：

    data/config.json              全局配置
    data/rosters/<班级名>.json     各班级名单
    data/roster_template.csv      CSV 名单模板（由 importer 生成）

【关键】所有路径一律通过 ``paths.data_dir()`` / ``paths.data_file()`` 获取，
绝不能自己用 ``__file__`` 拼接 —— 否则打包后数据会被写进 PyInstaller 的
临时解包目录，软件一关闭名单就丢了（见 PRD 附录 B 第 11 条）。

为了便于 pytest 隔离测试（NFR-M4），本模块支持在构造时注入一个替代根目录
（``Storage(base_dir=tmp_path)``）；不注入时才走 ``paths`` 的真实 data/ 目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from stellardraw import paths
from stellardraw.core.roster import Roster

# 配置文件名
CONFIG_FILENAME = "config.json"

# 名单子目录名
ROSTERS_SUBDIR = "rosters"

# 名单文件后缀
ROSTER_SUFFIX = ".json"

# 默认配置：新增字段时在此处给出默认值，避免各处散落硬编码
DEFAULT_CONFIG: dict[str, Any] = {
    "dedupe": True,  # 去重模式开关（PRD F-05）
    "muted": False,  # 音效总开关：True=静音、False=开启（PRD F-15 / TI-15，默认开启音效）
    "volume": 0.7,  # 音量 0.0 ~ 1.0（PRD "外观音效"页）
    "mascot_enabled": True,  # 看板娘显隐开关（PRD F-14 / TI-16，默认开启）
    "external_exe": "",  # 「简洁视图」外部程序路径（PRD F-09）
    "current_roster": "",  # 上次选中的班级，用于重启后恢复（PRD F-03）
}

# Windows 文件名非法字符（班级名由老师自由输入，必须做净化）
_INVALID_FILENAME_CHARS = '<>:"/\\|?*'

# Windows 保留设备名：这些名字不能作为主文件名
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# 班级名为空或净化后为空时的兜底文件名
_FALLBACK_STEM = "未命名班级"


def safe_stem(name: str) -> str:
    """把班级名称转换成可安全用作 Windows 文件名的字符串。

    参数：
        name: 老师输入的班级名，例如 "初一(3)班"。

    返回：
        净化后的文件名主干。非法字符替换为下划线；结尾的空白与点会被去除
        （Windows 不允许文件名以点或空格结尾）；命中系统保留名时加前缀。

    注意：
        净化是**多对一**的：若两个班级名净化后相同（如 ``初一/3班`` 与
        ``初一:3班``），后保存的会覆盖前一个。界面上应对重名做提示，
        这里只保证"不会因为非法字符直接写文件失败"。
    """
    cleaned = "".join("_" if ch in _INVALID_FILENAME_CHARS else ch for ch in str(name).strip())
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        return _FALLBACK_STEM
    if cleaned.upper() in _RESERVED_STEMS:
        return f"_{cleaned}"
    return cleaned


class Storage:
    """本地数据读写。

    参数：
        base_dir: 可选的数据根目录，最终数据落在 ``<base_dir>/data/`` 下。
                  传入即为测试隔离用；不传则使用程序同级的 ``data/``。
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def _dir(self, *parts: str, create: bool = True) -> Path:
        """返回数据目录（或子目录）路径（内部函数）。"""
        if self._base_dir is None:
            return paths.data_dir(*parts, create=create)
        target = self._base_dir.joinpath(paths.DATA_DIR_NAME, *parts)
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def _file(self, *parts: str) -> Path:
        """返回数据文件路径，并保证其父目录存在（内部函数）。"""
        if self._base_dir is None:
            return paths.data_file(*parts)
        path = self._dir(*parts[:-1]).joinpath(parts[-1])
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def config_path(self) -> Path:
        """配置文件的完整路径。"""
        return self._file(CONFIG_FILENAME)

    @property
    def data_root(self) -> Path:
        """数据根目录（``<程序同级>/data/``）的完整路径，必要时自动创建。

        界面上的「打开 data 目录」按钮直接用这个路径（PRD F-02c / AC-26）。
        """
        return self._dir()

    def file_path(self, *parts: str) -> Path:
        """获取数据目录下任意文件的路径（父目录自动创建）。

        供 api 层定位 CSV 模板等辅助文件，避免绕过本模块自行拼路径。
        """
        return self._file(*parts)

    @property
    def rosters_dir(self) -> Path:
        """班级名单目录的完整路径（必要时自动创建）。"""
        return self._dir(ROSTERS_SUBDIR)

    def roster_path(self, name: str) -> Path:
        """返回指定班级的名单文件路径。"""
        return self.rosters_dir.joinpath(f"{safe_stem(name)}{ROSTER_SUFFIX}")

    # ------------------------------------------------------------------
    # 通用 JSON 读写（原子写入）
    # ------------------------------------------------------------------

    @staticmethod
    def _read_json(path: Path) -> Any:
        """读取 JSON 文件；文件不存在时返回 None（内部函数）。

        读取时显式使用 utf-8 与 newline=""，避免 Windows 换行被翻译两次。

        异常：
            json.JSONDecodeError: 文件内容损坏时向上抛出，由调用方决定降级策略。
        """
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8", newline="") as fh:
            return json.load(fh)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        """把数据原子地写入 JSON 文件（内部函数）。

        实现方式：先写同目录下的临时文件，再 ``os.replace`` 覆盖目标。
        ``os.replace`` 在同一卷内是原子操作，可避免写入中途断电 / 强杀进程
        导致配置文件被截断成半截 JSON —— 那会让老师下次启动时丢失全部设置。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            # ensure_ascii=False 让中文班级名在文件里保持可读（NFR-S2）
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)

    # ------------------------------------------------------------------
    # 配置（PRD F-10 / AC-09④）
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce(value: Any, default: Any) -> Any:
        """按默认值的类型规整配置项，类型不符时回退到默认值（内部函数）。"""
        if isinstance(default, bool):
            return value if isinstance(value, bool) else default
        if isinstance(default, float):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        if isinstance(default, int):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        if isinstance(default, str):
            return value if isinstance(value, str) else default
        return value

    def sanitize_config(self, raw: Any) -> dict[str, Any]:
        """把任意输入规整成完整的配置字典。

        · 只保留 DEFAULT_CONFIG 中声明过的键，防止损坏的 JSON 注入无效字段
        · 缺失的键用默认值补齐，调用方无需到处写 ``.get(key, default)``
        · 类型不符的值回退到默认值，保证界面拿到的一定是可用类型
        """
        config = dict(DEFAULT_CONFIG)
        if not isinstance(raw, dict):
            return config
        for key, default in DEFAULT_CONFIG.items():
            if key in raw:
                config[key] = self._coerce(raw[key], default)
        return config

    def load_config(self) -> dict[str, Any]:
        """读取配置；文件不存在或损坏时返回默认配置。

        返回：
            完整的配置字典（键集合与 DEFAULT_CONFIG 一致）。
        """
        try:
            raw = self._read_json(self.config_path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # 配置损坏不应导致程序无法启动：退回默认值，
            # 下一次 save_config 会把文件覆盖成合法内容
            return dict(DEFAULT_CONFIG)
        return self.sanitize_config(raw)

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """写入配置，返回实际落盘的完整配置。

        参数：
            config: 配置字典。未声明的键会被忽略，缺失的键用默认值补齐。
        """
        sanitized = self.sanitize_config(config)
        self._write_json(self.config_path, sanitized)
        return sanitized

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        """按部分字段更新配置（对应 api.save_settings 的语义）。

        参数：
            patch: 只包含需要变更的键，例如 ``{"muted": True}``。

        返回：
            更新后的完整配置字典。
        """
        if not isinstance(patch, dict):
            raise TypeError("配置更新内容必须是字典")
        config = self.load_config()
        merged = self.sanitize_config({**config, **patch})
        return self.save_config(merged)

    # ------------------------------------------------------------------
    # 班级名单（PRD F-03 / F-04 / AC-03）
    # ------------------------------------------------------------------

    def list_rosters(self) -> list[str]:
        """列出全部班级名称，按名称排序。

        对应需求：PRD F-03 / AC-03。

        实现说明：文件名做了安全净化，因此**不能**直接用文件名回显给老师
        （例如 "初一/3班" 会被写成 "初一_3班.json"）。这里优先读取
        JSON 内部的 ``name`` 字段，读不到才退回文件名主干。
        """
        directory = self.rosters_dir
        names: list[str] = []
        for item in directory.glob(f"*{ROSTER_SUFFIX}"):
            if not item.is_file():
                continue
            title = ""
            try:
                raw = self._read_json(item)
                if isinstance(raw, dict):
                    title = str(raw.get("name") or "").strip()
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                # 单个班级文件损坏不应让整个列表打不开
                title = ""
            names.append(title or item.stem)
        return sorted(set(names))

    def roster_exists(self, name: str) -> bool:
        """判断指定班级是否已存在档案。"""
        return self.roster_path(name).is_file()

    def load_roster(self, name: str) -> Roster:
        """载入指定班级的名单。

        对应需求：PRD F-03 / F-04。

        参数：
            name: 班级名称。

        返回：
            Roster 对象。文件不存在或内容损坏时返回**仅带班级名的空名单**
            （而非抛异常），保证界面永远能拿到一个可用对象。
        """
        path = self.roster_path(name)
        try:
            raw = self._read_json(path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return Roster(name=str(name).strip())
        if raw is None:
            return Roster(name=str(name).strip())
        roster = Roster.from_dict(raw)
        # 文件里的 name 字段优先（应对文件名被净化过的情况）
        if not roster.name:
            roster.name = str(name).strip()
        return roster

    def save_roster(self, roster: Roster) -> Path:
        """保存班级名单，返回写入的文件路径。

        对应需求：PRD F-04 / AC-01⑤（重启后名单仍在）。
        """
        name = str(roster.name).strip()
        if not name:
            raise ValueError("班级名称不能为空")
        roster.name = name
        path = self.roster_path(name)
        self._write_json(path, roster.to_dict())
        return path

    def delete_roster(self, name: str) -> bool:
        """删除班级档案。

        对应需求：PRD F-03 / AC-03③。

        返回：
            True 表示确实删掉了一个文件；False 表示该班级本来就不存在
            （不算错误，界面可据此给出不同提示）。
        """
        path = self.roster_path(name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def rename_roster(self, old_name: str, new_name: str) -> Roster:
        """重命名班级档案。

        对应需求：PRD F-03 / AC-03③。

        参数：
            old_name: 原班级名。
            new_name: 新班级名。

        返回：
            重命名后的 Roster 对象。

        异常：
            ValueError: 新名称为空，或新名称已被另一个班级占用时抛出。
        """
        cleaned = str(new_name).strip()
        if not cleaned:
            raise ValueError("班级名称不能为空")
        if cleaned == str(old_name).strip():
            return self.load_roster(old_name)
        if self.roster_exists(cleaned):
            raise ValueError(f"已存在名为「{cleaned}」的班级，请换一个名称")

        roster = self.load_roster(old_name)
        roster.rename(cleaned)
        self.save_roster(roster)
        self.delete_roster(old_name)
        return roster
