"""core/storage.py 的单元测试。

对应需求：PRD F-03 / F-04 / F-10 / E-08 / NFR-S2 / AC-01⑤ / AC-03④ / AC-09④

本文件重点覆盖三类容易"静默出错"的行为：
    1. 配置文件损坏时必须能降级启动，而不是让程序打不开；
    2. 班级名可能包含 Windows 非法文件名字符，必须净化后才能落盘；
    3. 写入必须是原子的，避免断电导致名单被截断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stellardraw.core.roster import Roster, Student
from stellardraw.core.storage import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG,
    ROSTERS_SUBDIR,
    Storage,
    safe_stem,
)


class TestSafeStem:
    """班级名 → 文件名的净化规则。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("初一(3)班", "初一(3)班"),
            ("初一/3班", "初一_3班"),
            ('a:b*c?d"e<f>g|h', "a_b_c_d_e_f_g_h"),
            ("  三班  ", "三班"),
            ("三班...", "三班"),
            ("", "未命名班级"),
            ("   ", "未命名班级"),
            ("CON", "_CON"),
            ("nul", "_nul"),
        ],
    )
    def test_safe_stem(self, name: str, expected: str) -> None:
        """非法字符被替换、保留名被加前缀、空名有兜底。"""
        assert safe_stem(name) == expected


class TestConfig:
    """配置读写（PRD F-10 / AC-09④）。"""

    def test_paths_are_under_base_dir(self, storage: Storage, base_dir: Path) -> None:
        """配置必须落在注入的目录下，而不是项目真实 data/。"""
        assert storage.config_path == base_dir / "data" / CONFIG_FILENAME
        assert storage.data_root == base_dir / "data"
        assert storage.rosters_dir == base_dir / "data" / ROSTERS_SUBDIR

    def test_load_config_returns_defaults_when_missing(self, storage: Storage) -> None:
        """首次运行时没有配置文件，应返回一份完整默认值。"""
        assert storage.load_config() == DEFAULT_CONFIG
        # 必须是副本：改动返回值不应污染模块级默认值
        loaded = storage.load_config()
        loaded["dedupe"] = False
        assert DEFAULT_CONFIG["dedupe"] is True

    def test_save_and_load_roundtrip(self, storage: Storage) -> None:
        """保存后能原样读回，且中文字段不转义。"""
        storage.save_config({"dedupe": False, "muted": True, "volume": 0.35})
        config = storage.load_config()
        assert config["dedupe"] is False
        assert config["muted"] is True
        assert config["volume"] == pytest.approx(0.35)

        # 明文可读（NFR-S2）：文件里应当是真正的键名而不是 \uXXXX
        raw = storage.config_path.read_text(encoding="utf-8")
        assert "dedupe" in raw
        assert "\\u" not in raw

    def test_save_config_fills_missing_keys(self, storage: Storage) -> None:
        """只传部分字段时其余字段自动补默认值。"""
        saved = storage.save_config({"muted": True})
        assert saved["muted"] is True
        assert saved["dedupe"] == DEFAULT_CONFIG["dedupe"]
        assert set(saved) == set(DEFAULT_CONFIG)

    def test_update_config_merges_partially(self, storage: Storage) -> None:
        """部分更新不应丢掉其它字段。"""
        storage.save_config({"external_exe": "D:/tools/random.exe", "volume": 0.5})
        updated = storage.update_config({"volume": 0.9})
        assert updated["volume"] == pytest.approx(0.9)
        assert updated["external_exe"] == "D:/tools/random.exe"

    def test_update_config_rejects_non_dict(self, storage: Storage) -> None:
        """非法入参抛 TypeError。"""
        with pytest.raises(TypeError):
            storage.update_config(["muted"])  # type: ignore[arg-type]

    def test_corrupt_config_falls_back_to_defaults(self, storage: Storage) -> None:
        """配置文件损坏时必须降级启动，而不是抛异常让软件打不开。"""
        storage.config_path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        assert storage.load_config() == DEFAULT_CONFIG

    def test_sanitize_drops_unknown_keys(self, storage: Storage) -> None:
        """未知键被剔除，防止损坏的 JSON 注入无效字段。"""
        config = storage.sanitize_config({"dedupe": True, "乱入的键": 1})
        assert "乱入的键" not in config
        assert set(config) == set(DEFAULT_CONFIG)

    def test_sanitize_coerces_wrong_types(self, storage: Storage) -> None:
        """类型不符的值回退默认值，能转换的（字符串数字）则转换。"""
        config = storage.sanitize_config(
            {"dedupe": "yes", "volume": "0.5", "muted": 1, "mascot_enabled": "yes"}
        )
        assert config["dedupe"] is DEFAULT_CONFIG["dedupe"]
        assert config["muted"] is DEFAULT_CONFIG["muted"]
        assert config["mascot_enabled"] is DEFAULT_CONFIG["mascot_enabled"]
        assert config["volume"] == pytest.approx(0.5)

    def test_sanitize_handles_non_dict(self, storage: Storage) -> None:
        """整个配置不是 dict 时同样返回默认值。"""
        assert storage.sanitize_config("坏数据") == DEFAULT_CONFIG
        assert storage.sanitize_config(None) == DEFAULT_CONFIG

    def test_sound_default_on(self, storage: Storage) -> None:
        """TI-15：音效默认开启，配置文件里以 ``muted=False`` 表达。"""
        assert DEFAULT_CONFIG["muted"] is False
        assert storage.load_config()["muted"] is False

    def test_mascot_default_enabled(self, storage: Storage) -> None:
        """TI-16：看板娘开关默认开启。"""
        assert DEFAULT_CONFIG["mascot_enabled"] is True
        assert storage.load_config()["mascot_enabled"] is True

    def test_sound_and_mascot_toggles_persist(self, storage: Storage, base_dir: Path) -> None:
        """两个默认开关改动后重启（新实例）仍能读回 —— AC-09④。"""
        storage.update_config({"muted": True, "mascot_enabled": False})

        reopened = Storage(base_dir=base_dir)
        config = reopened.load_config()
        assert config["muted"] is True
        assert config["mascot_enabled"] is False
        # 只改这两项，其余字段不应被连带改动
        assert config["dedupe"] is True
        assert config["volume"] == pytest.approx(0.7)


class TestRosterPersistence:
    """班级名单的持久化（PRD F-03 / F-04 / AC-01⑤ / AC-03）。"""

    def test_list_rosters_empty(self, storage: Storage) -> None:
        """一个班级都没有时返回空列表。"""
        assert storage.list_rosters() == []

    def test_save_and_load_roundtrip(self, storage: Storage) -> None:
        """保存后重启（新 Storage 实例）仍能读回名单 —— AC-01⑤ 的核心。"""
        roster = Roster(name="初一(3)班", students=[Student("01", "张小雨"), Student("02", "李雷")])
        storage.save_roster(roster)

        reopened = Storage(base_dir=storage.data_root.parent)
        loaded = reopened.load_roster("初一(3)班")
        assert loaded.name == "初一(3)班"
        assert [s.to_dict() for s in loaded] == [
            {"num": "01", "name": "张小雨"},
            {"num": "02", "name": "李雷"},
        ]

    def test_roster_file_is_readable_json(self, storage: Storage) -> None:
        """落盘的是明文 JSON，便于老师自行备份（NFR-S2）。"""
        storage.save_roster(Roster(name="测试班", students=[Student("01", "张三")]))
        raw = json.loads(storage.roster_path("测试班").read_text(encoding="utf-8"))
        assert raw == {"name": "测试班", "students": [{"num": "01", "name": "张三"}]}

    def test_load_missing_roster_returns_empty(self, storage: Storage) -> None:
        """加载不存在的班级时返回空名单而非抛异常，保证界面总有可用对象。"""
        roster = storage.load_roster("不存在的班级")
        assert roster.name == "不存在的班级"
        assert roster.is_empty() is True

    def test_illegal_class_name_is_sanitized(self, storage: Storage) -> None:
        """含 Windows 非法字符的班级名不会导致写文件失败，且能读回原名。"""
        storage.save_roster(Roster(name="初一/3班", students=[Student("01", "张三")]))
        assert storage.roster_path("初一/3班").name == "初一_3班.json"
        assert storage.list_rosters() == ["初一/3班"]
        assert storage.load_roster("初一/3班").name == "初一/3班"

    def test_list_rosters_sorted(self, storage: Storage) -> None:
        """班级列表按名称排序，界面下拉框顺序稳定。"""
        names = ["初三(1)班", "初一(1)班", "初二(1)班"]
        for name in names:
            storage.save_roster(Roster(name=name))
        assert storage.list_rosters() == sorted(names)

    def test_save_roster_rejects_blank_name(self, storage: Storage) -> None:
        """班级名为空时拒绝保存。"""
        with pytest.raises(ValueError):
            storage.save_roster(Roster(name="   ", students=[Student("01", "张三")]))

    def test_roster_exists(self, storage: Storage) -> None:
        """班级存在性判定。"""
        assert storage.roster_exists("测试班") is False
        storage.save_roster(Roster(name="测试班"))
        assert storage.roster_exists("测试班") is True

    def test_corrupt_roster_file_degrades_gracefully(self, storage: Storage) -> None:
        """单个班级文件损坏时：读取返回空名单，列表仍能打开。"""
        broken = storage.rosters_dir / "坏班级.json"
        broken.write_text("{{{ 坏掉的 JSON", encoding="utf-8")

        assert storage.load_roster("坏班级").is_empty() is True
        assert "坏班级" in storage.list_rosters()

    def test_list_rosters_ignores_tmp_files(self, storage: Storage) -> None:
        """写入过程中的 .tmp 文件不应被当成班级。"""
        storage.save_roster(Roster(name="测试班"))
        (storage.rosters_dir / "测试班.json.tmp").write_text("{}", encoding="utf-8")
        assert storage.list_rosters() == ["测试班"]


class TestRosterLifecycle:
    """班级的删除与重命名（PRD F-03 / AC-03③）。"""

    def test_delete_roster(self, storage: Storage) -> None:
        """删除已存在的班级返回 True，再删返回 False。"""
        storage.save_roster(Roster(name="测试班"))
        assert storage.delete_roster("测试班") is True
        assert storage.delete_roster("测试班") is False
        assert storage.list_rosters() == []

    def test_rename_roster(self, storage: Storage) -> None:
        """重命名后新名字可读、旧名字消失。"""
        storage.save_roster(Roster(name="旧班", students=[Student("01", "张三")]))
        renamed = storage.rename_roster("旧班", "新班")

        assert renamed.name == "新班"
        assert storage.list_rosters() == ["新班"]
        assert storage.load_roster("新班").students[0].name == "张三"
        assert storage.roster_exists("旧班") is False

    def test_rename_to_same_name_is_noop(self, storage: Storage) -> None:
        """重命名成同一个名字时直接返回，不误删数据。"""
        storage.save_roster(Roster(name="测试班", students=[Student("01", "张三")]))
        roster = storage.rename_roster("测试班", "测试班")
        assert roster.name == "测试班"
        assert storage.load_roster("测试班").students[0].name == "张三"

    def test_rename_to_blank_rejected(self, storage: Storage) -> None:
        """新名称为空时拒绝。"""
        with pytest.raises(ValueError):
            storage.rename_roster("测试班", "   ")

    def test_rename_to_existing_rejected(self, storage: Storage) -> None:
        """新名称与已有班级冲突时拒绝，避免静默覆盖别人班的名单。"""
        storage.save_roster(Roster(name="甲班"))
        storage.save_roster(Roster(name="乙班"))
        with pytest.raises(ValueError, match="已存在"):
            storage.rename_roster("甲班", "乙班")
        # 两个班级都还在，数据没有被破坏
        assert set(storage.list_rosters()) == {"甲班", "乙班"}


class TestDefaultPaths:
    """未注入 base_dir 时的真实 data/ 路径解析（生产环境走这条分支）。"""

    def test_default_storage_follows_paths_module(self) -> None:
        """默认 Storage 必须与 paths 模块给出同一份路径，不能各拼各的。"""
        from stellardraw import paths

        storage = Storage()
        assert storage.config_path == paths.data_file(CONFIG_FILENAME)
        assert storage.data_root == paths.data_dir()
        assert storage.rosters_dir == paths.data_dir(ROSTERS_SUBDIR)
        # 只做路径比对，不写入任何文件，避免污染项目真实的 data/
        assert storage.config_path.parent.is_dir()

    def test_file_path_default_branch(self) -> None:
        """未注入 base_dir 时 file_path 同样走 paths 模块。"""
        from stellardraw import paths

        assert Storage().file_path("demo.csv") == paths.data_file("demo.csv")


class TestConfigCoercion:
    """_coerce 的类型规整（覆盖 int / 未知类型等分支）。"""

    def test_bool_default(self) -> None:
        """bool 默认值只接受真正的布尔。"""
        assert Storage._coerce(True, False) is True
        assert Storage._coerce("true", False) is False

    def test_float_default(self) -> None:
        """float 默认值可接受数字与数字字符串，无法转换时回退。"""
        assert Storage._coerce(1, 0.5) == pytest.approx(1.0)
        assert Storage._coerce("0.25", 0.5) == pytest.approx(0.25)
        assert Storage._coerce("很大声", 0.5) == pytest.approx(0.5)
        assert Storage._coerce(None, 0.5) == pytest.approx(0.5)

    def test_int_default(self) -> None:
        """int 默认值同样支持数字字符串。"""
        assert Storage._coerce("7", 0) == 7
        assert Storage._coerce("七", 0) == 0

    def test_str_default(self) -> None:
        """str 默认值只接受字符串。"""
        assert Storage._coerce("D:/a.exe", "") == "D:/a.exe"
        assert Storage._coerce(123, "") == ""

    def test_unknown_default_type_passes_through(self) -> None:
        """默认值类型未被覆盖时（如 None）原样返回，不做猜测。"""
        assert Storage._coerce("x", None) == "x"
        assert Storage._coerce([1, 2], None) == [1, 2]


class TestRosterDegradation:
    """名单文件的容错读取（应对不规范 / 被手改过的数据）。"""

    def test_roster_without_name_field_falls_back_to_arg(self, storage: Storage) -> None:
        """JSON 里没有 name 字段时，用调用方传入的班级名补上。"""
        (storage.rosters_dir / "某班.json").write_text(
            '{"students": [{"num": "01", "name": "张三"}]}', encoding="utf-8"
        )
        roster = storage.load_roster("某班")
        assert roster.name == "某班"
        assert len(roster) == 1

    def test_directory_named_like_roster_is_ignored(self, storage: Storage) -> None:
        """与班级同名的**目录**不应被当成班级档案（防止列表里冒出幽灵班级）。"""
        (storage.rosters_dir / "幽灵班.json").mkdir()
        storage.save_roster(Roster(name="真实班"))
        assert storage.list_rosters() == ["真实班"]


class TestAtomicWrite:
    """原子写入与辅助路径（PRD F-04 的可靠性要求）。"""

    def test_no_tmp_file_left_behind(self, storage: Storage) -> None:
        """原子替换完成后不应残留临时文件。"""
        storage.save_config({"muted": True})
        leftovers = list(storage.data_root.glob("*.tmp"))
        assert leftovers == []

    def test_save_config_overwrites_existing(self, storage: Storage) -> None:
        """连续保存应覆盖旧内容而不是追加。"""
        storage.save_config({"volume": 0.1})
        storage.save_config({"volume": 0.8})
        assert storage.load_config()["volume"] == pytest.approx(0.8)
        assert storage.config_path.read_text(encoding="utf-8").count('"volume"') == 1

    def test_file_path_creates_parent_dirs(self, storage: Storage) -> None:
        """file_path 会自动补齐父目录（供 api 层定位 CSV 模板）。"""
        target = storage.file_path("模板", "roster_template.csv")
        assert target.parent.is_dir()
        assert not target.exists()

    def test_config_path_creates_data_dir(self, storage: Storage) -> None:
        """访问 config_path 本身就会把 data/ 目录建出来。"""
        assert storage.config_path.parent.is_dir()
