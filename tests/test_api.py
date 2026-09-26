"""api.py（JS Bridge 接口层）的单元测试。

对应需求：PRD F-01 ~ F-10 / E-01 ~ E-08 / AC-01 ~ AC-10

接口层是"前端唯一能碰到的东西"，因此本文件按**验收标准的编号顺序**组织用例：
    班级档案 → 名单保存 → CSV 导入 → 模板 → 抽取 → 设置 → 外部程序 → 数据目录
每个用例都同时断言 `ok` 结果与 `code` 错误码，因为前端正是靠 code 决定
弹什么提示、要不要把用户引导到设置面板。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stellardraw import __version__
from stellardraw.api import (
    ABOUT_PLACEHOLDER,
    APP_NAME,
    DEFAULT_ROSTER_NAME,
    MSG_EMPTY_ROSTER,
    MSG_NEW_ROUND,
)
from stellardraw.core import launcher
from stellardraw.core.importer import TEMPLATE_FILENAME
from stellardraw.core.storage import CONFIG_FILENAME


def write_csv(path: Path, text: str, encoding: str = "utf-8-sig") -> Path:
    """把文本按指定编码写成 CSV 文件，返回路径。"""
    path.write_bytes(text.encode(encoding))
    return path


class TestBridgeBasics:
    """连通性与"关于"页信息。"""

    def test_ping(self, api) -> None:
        """ping 用于前端确认 JS Bridge 已就绪。"""
        result = api.ping()
        assert result["ok"] is True
        assert result["version"] == __version__

    def test_get_app_info(self, api) -> None:
        """AC-09③：关于页所需的名称、版本、技术说明、占位文案一应俱全。"""
        info = api.get_app_info()
        assert info["ok"] is True
        assert info["name"] == APP_NAME
        assert info["version"] == __version__
        assert info["placeholder"] == ABOUT_PLACEHOLDER
        assert Path(info["data_dir"]).is_dir()

    def test_api_never_raises_on_bad_input(self, api) -> None:
        """接口层必须永不抛异常：异常穿过 JS Bridge 会让前端拿到 undefined。"""
        assert api.draw("不是数字")["ok"] is False
        assert api.load_roster(None)["ok"] is False
        assert api.save_settings("不是字典")["ok"] is False
        assert api.apply_import("乱写")["ok"] is False


class TestRosterLifecycle:
    """班级档案的增删改查与恢复（PRD F-03 / AC-03）。"""

    def test_initially_empty(self, api) -> None:
        """首次运行时没有任何班级，当前班级为空。"""
        result = api.list_rosters()
        assert result["ok"] is True
        assert result["rosters"] == []
        assert result["current"] == ""

    def test_create_roster(self, api) -> None:
        """AC-03①：能新建班级并切换过去。"""
        result = api.create_roster("初一(3)班")
        assert result["ok"] is True
        assert result["current"] == "初一(3)班"
        assert result["rosters"] == ["初一(3)班"]
        assert api.list_rosters()["current"] == "初一(3)班"

    def test_create_roster_trims_name(self, api) -> None:
        """班级名首尾空白被清理。"""
        assert api.create_roster("  初一(3)班  ")["current"] == "初一(3)班"

    @pytest.mark.parametrize("bad_name", ["", "   ", None])
    def test_create_roster_rejects_blank(self, api, bad_name) -> None:
        """班级名不能为空。"""
        result = api.create_roster(bad_name)
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"

    def test_create_duplicate_rejected(self, api) -> None:
        """重名班级被拒绝，避免覆盖已有名单。"""
        api.create_roster("初一(3)班")
        result = api.create_roster("初一(3)班")
        assert result["ok"] is False
        assert "已存在" in result["message"]

    def test_switch_between_rosters(self, api, students_payload) -> None:
        """AC-03②：切换班级后名单正确变更。"""
        api.create_roster("甲班")
        api.save_roster("甲班", students_payload(3, "甲"))
        api.create_roster("乙班")
        api.save_roster("乙班", students_payload(5, "乙"))

        loaded = api.load_roster("甲班")
        assert loaded["ok"] is True
        assert loaded["count"] == 3
        assert loaded["roster"]["students"][0]["name"] == "甲1"

        assert api.load_roster("乙班")["count"] == 5

    def test_load_unknown_roster_fails(self, api) -> None:
        """加载不存在的班级给出明确错误，而不是凭空创建空班级。"""
        result = api.load_roster("不存在的班")
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"

    def test_current_roster_restored_after_restart(self, make_api) -> None:
        """AC-03④：重启程序后自动恢复上次选中的班级。"""
        first = make_api()
        first.create_roster("初一(3)班")
        first.save_roster("初一(3)班", [{"num": "01", "name": "张小雨"}])
        first.create_roster("初二(1)班")
        first.load_roster("初一(3)班")

        # 模拟"关掉软件再打开"：基于同一份数据目录重新构造 Api
        reopened = make_api()
        assert reopened.list_rosters()["current"] == "初一(3)班"
        assert reopened.load_roster("初一(3)班")["count"] == 1

    def test_deleted_current_roster_is_forgotten_on_restart(self, make_api) -> None:
        """上次选中的班级已被删除时，启动不应崩溃，当前班级回到空。"""
        first = make_api()
        first.create_roster("临时班")
        # 绕过 api 直接删文件，模拟老师在资源管理器里手动删除班级档案
        data_dir = Path(first.get_app_info()["data_dir"])
        (data_dir / "rosters" / "临时班.json").unlink()

        reopened = make_api()
        assert reopened.list_rosters()["current"] == ""
        assert reopened.get_settings()["settings"]["current_roster"] == ""

    def test_rename_roster(self, api) -> None:
        """AC-03③：能重命名班级。"""
        api.create_roster("旧班名")
        result = api.rename_roster("旧班名", "新班名")
        assert result["ok"] is True
        assert result["rosters"] == ["新班名"]
        assert api.list_rosters()["current"] == "新班名"

    def test_rename_current_updates_config(self, make_api) -> None:
        """重命名当前班级后，重启恢复的应是新名字。"""
        first = make_api()
        first.create_roster("旧班名")
        first.rename_roster("旧班名", "新班名")

        assert make_api().list_rosters()["current"] == "新班名"

    def test_rename_to_existing_rejected(self, api) -> None:
        """重名时拒绝，且两个班的数据都不受影响。"""
        api.create_roster("甲班")
        api.create_roster("乙班")
        result = api.rename_roster("甲班", "乙班")
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"
        assert set(api.list_rosters()["rosters"]) == {"甲班", "乙班"}

    def test_delete_roster_switches_to_remaining(self, api, students_payload) -> None:
        """删除当前班级后自动切换到剩余班级中的第一个。"""
        api.create_roster("甲班")
        api.save_roster("甲班", students_payload(2, "甲"))
        api.create_roster("乙班")

        result = api.delete_roster("乙班")
        assert result["ok"] is True
        assert result["current"] == "甲班"
        assert result["rosters"] == ["甲班"]

    def test_delete_last_roster_returns_to_empty(self, api) -> None:
        """删掉最后一个班级后回到"无班级"状态，界面据此走空名单引导。"""
        api.create_roster("唯一的班")
        result = api.delete_roster("唯一的班")
        assert result["ok"] is True
        assert result["current"] == ""
        assert result["rosters"] == []

    def test_delete_unknown_roster_fails(self, api) -> None:
        """删除不存在的班级给出错误。"""
        result = api.delete_roster("不存在的班")
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"


class TestSaveRoster:
    """手动录入的落盘（PRD F-01 / F-04 / AC-01）。"""

    def test_save_and_persist(self, api, students_payload, make_api) -> None:
        """AC-01①⑤：添加学生后落盘，重启仍在。"""
        api.create_roster("初一(3)班")
        result = api.save_roster("初一(3)班", students_payload(3))
        assert result["ok"] is True
        assert result["count"] == 3

        reopened = make_api()
        assert reopened.load_roster("初一(3)班")["count"] == 3

    def test_blank_name_rows_are_skipped(self, api) -> None:
        """前端"新增一行空记录"是常见中间态，保存时应跳过而不是报错。"""
        api.create_roster("测试班")
        result = api.save_roster(
            "测试班",
            [{"num": "01", "name": "张三"}, {"num": "02", "name": "   "}],
        )
        assert result["ok"] is True
        assert result["count"] == 1

    def test_duplicate_flag(self, api) -> None:
        """完全相同的两条记录会被标记（仅提示，不拦截）。"""
        api.create_roster("测试班")
        result = api.save_roster(
            "测试班",
            [{"num": "01", "name": "张三"}, {"num": "01", "name": "张三"}],
        )
        assert result["duplicate"] is True

    def test_same_name_different_num_not_flagged(self, api) -> None:
        """同名不同学号不算重复（AC-04⑤ / US-13）。"""
        api.create_roster("测试班")
        result = api.save_roster(
            "测试班",
            [{"num": "01", "name": "张伟"}, {"num": "02", "name": "张伟"}],
        )
        assert result["duplicate"] is False

    @pytest.mark.parametrize("bad_students", ["张三", 123, None])
    def test_invalid_payload_rejected(self, api, bad_students) -> None:
        """载荷类型不对时返回错误而非抛异常。"""
        api.create_roster("测试班")
        result = api.save_roster("测试班", bad_students)
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"

    def test_save_blank_roster_name_rejected(self, api) -> None:
        """班级名为空时拒绝保存。"""
        assert api.save_roster("", [{"num": "01", "name": "张三"}])["ok"] is False

    def test_saving_current_roster_rebuilds_pool(self, api, students_payload) -> None:
        """编辑当前班级名单后候选池被重建，避免已删除的学生仍被抽出。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))
        api.draw(2)
        assert api.draw(1)["remaining"] == 2

        result = api.save_roster("测试班", students_payload(5))
        assert result["ok"] is True
        # 池已按新名单重建
        assert api.draw(1)["remaining"] == 4

    def test_saving_other_roster_keeps_progress(self, api, students_payload) -> None:
        """编辑非当前班级时，当前班级的抽取进度不受影响。"""
        api.create_roster("甲班")
        api.save_roster("甲班", students_payload(5, "甲"))
        api.create_roster("乙班")
        api.load_roster("甲班")
        api.draw(2)

        api.save_roster("乙班", students_payload(3, "乙"))
        assert api.draw(1)["remaining"] == 2


class TestBatchText:
    """批量粘贴解析接口（PRD F-01 / AC-01④）。"""

    def test_parse_mixed_styles(self, api) -> None:
        """AC-01④：混用两种写法能解析出正确条数。"""
        text = "\n".join(
            [f"{i:02d},学生{i}" for i in range(1, 6)] + [f"学生{i}" for i in range(6, 11)]
        )
        result = api.parse_batch_text(text)
        assert result["ok"] is True
        assert result["count"] == 10
        assert result["students"][0] == {"num": "01", "name": "学生1"}
        assert result["students"][-1] == {"num": "", "name": "学生10"}

    def test_parse_reports_duplicates(self, api) -> None:
        """与现有名单重复的条数会被统计出来，供界面提示。"""
        api.create_roster("测试班")
        api.save_roster("测试班", [{"num": "01", "name": "张三"}])
        result = api.parse_batch_text("01,张三\n02,李四")
        assert result["count"] == 2
        assert result["duplicate"] == 1

    def test_parse_empty_text_fails(self, api) -> None:
        """空内容给出明确错误。"""
        result = api.parse_batch_text("   \n\n")
        assert result["ok"] is False
        assert result["code"] == "invalid_csv"


class TestImportCsv:
    """CSV 导入（PRD F-02 / E-08 / AC-02 / TI-11）。"""

    def test_without_window_is_cancelled(self, api) -> None:
        """窗口未注入（或用户取消）时返回 cancelled，不报错。"""
        result = api.import_csv()
        assert result["ok"] is False
        assert result["code"] == "cancelled"

    def test_user_cancels_dialog(self, make_api, fake_window_factory) -> None:
        """用户点了取消 → cancelled。"""
        api = make_api(window=fake_window_factory(""))
        assert api.import_csv()["code"] == "cancelled"

    def test_import_into_empty_roster_creates_default_class(
        self, make_api, fake_window_factory, tmp_path: Path
    ) -> None:
        """首次使用直接导入 CSV：自动创建"默认班级"，零配置即可用。"""
        csv_path = write_csv(tmp_path / "名单.csv", "Num,Name\r\n01,张小雨\r\n02,李雷\r\n")
        window = fake_window_factory(str(csv_path))
        api = make_api(window=window)

        result = api.import_csv()
        assert result["ok"] is True
        assert result["imported"] == 2
        assert result["need_confirm"] is False
        assert api.list_rosters()["current"] == DEFAULT_ROSTER_NAME
        # 弹框时应当带上 CSV 过滤器
        assert "*.csv" in window.calls[0]["file_types"][0]

    def test_gbk_file_imported_without_mojibake(
        self, make_api, fake_window_factory, tmp_path: Path
    ) -> None:
        """AC-02②：Excel 另存的 GBK 文件导入后中文不乱码。"""
        csv_path = write_csv(tmp_path / "gbk.csv", "Num,Name\r\n01,张小雨\r\n", encoding="gbk")
        api = make_api(window=fake_window_factory(str(csv_path)))

        result = api.import_csv()
        assert result["ok"] is True
        assert api.load_roster(DEFAULT_ROSTER_NAME)["roster"]["students"][0]["name"] == "张小雨"

    def test_import_into_non_empty_roster_asks_first(
        self, make_api, fake_window_factory, tmp_path: Path, students_payload
    ) -> None:
        """TI-11：当前班级已有名单时必须先询问「覆盖」还是「追加」。"""
        api = make_api()
        api.create_roster("初一(3)班")
        api.save_roster("初一(3)班", students_payload(5, "原有"))

        csv_path = write_csv(tmp_path / "新增.csv", "Num,Name\r\n99,新同学\r\n")
        api.attach_window(fake_window_factory(str(csv_path)))

        result = api.import_csv()
        assert result["ok"] is True
        assert result["need_confirm"] is True
        assert result["pending"] == 1
        assert result["existing"] == 5
        assert result["modes"] == ["overwrite", "append"]
        # 询问阶段不允许写盘
        assert api.load_roster("初一(3)班")["count"] == 5

    def test_apply_import_append(
        self, make_api, fake_window_factory, tmp_path: Path, students_payload
    ) -> None:
        """选择「追加」：原名单保留，新记录接在后面。"""
        api = make_api()
        api.create_roster("初一(3)班")
        api.save_roster("初一(3)班", students_payload(5, "原有"))
        api.attach_window(
            fake_window_factory(str(write_csv(tmp_path / "n.csv", "Num,Name\r\n99,新同学\r\n")))
        )
        api.import_csv()

        result = api.apply_import("append")
        assert result["ok"] is True
        assert result["count"] == 6
        assert result["roster"]["students"][-1]["name"] == "新同学"

    def test_apply_import_overwrite(
        self, make_api, fake_window_factory, tmp_path: Path, students_payload
    ) -> None:
        """选择「覆盖」：原名单被整体替换。"""
        api = make_api()
        api.create_roster("初一(3)班")
        api.save_roster("初一(3)班", students_payload(5, "原有"))
        api.attach_window(
            fake_window_factory(str(write_csv(tmp_path / "n.csv", "Num,Name\r\n99,新同学\r\n")))
        )
        api.import_csv()

        result = api.apply_import("overwrite")
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["mode"] == "overwrite"

    def test_apply_import_without_pending_fails(self, api) -> None:
        """没有待导入数据时调用 apply_import 报错。"""
        result = api.apply_import("append")
        assert result["ok"] is False
        assert result["code"] == "invalid_csv"

    def test_apply_import_bad_mode_fails(
        self, make_api, fake_window_factory, tmp_path: Path, students_payload
    ) -> None:
        """导入方式只能是 overwrite / append。"""
        api = make_api()
        # 必须让当前班级先有名单，否则 import_csv 会直接导入、不留下待确认数据
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(2))

        csv_path = write_csv(tmp_path / "n.csv", "Num,Name\r\n01,张小雨\r\n")
        api.attach_window(fake_window_factory(str(csv_path)))
        assert api.import_csv()["need_confirm"] is True

        result = api.apply_import("删库")
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"

    def test_broken_csv_does_not_damage_existing_roster(
        self, make_api, fake_window_factory, tmp_path: Path, students_payload
    ) -> None:
        """AC-02⑤⑥/E-08：导入失败给出明确错误，且现有名单完好无损。"""
        api = make_api()
        api.create_roster("初一(3)班")
        api.save_roster("初一(3)班", students_payload(5, "原有"))
        api.attach_window(fake_window_factory(str(write_csv(tmp_path / "bad.csv", "Num,Name\r\n"))))

        result = api.import_csv()
        assert result["ok"] is False
        assert result["code"] == "invalid_csv"
        assert api.load_roster("初一(3)班")["count"] == 5

    def test_missing_file_after_selection(
        self, make_api, fake_window_factory, tmp_path: Path
    ) -> None:
        """选中的文件在导入前被删除 → 明确错误，不崩溃。"""
        api = make_api(window=fake_window_factory(str(tmp_path / "已删除.csv")))
        result = api.import_csv()
        assert result["ok"] is False
        assert result["code"] == "invalid_csv"

    def test_dialog_failure_is_cancelled(self, make_api, fake_window_factory) -> None:
        """文件选择框自身抛异常时按"取消"处理，不让界面崩溃。"""

        class BrokenWindow:
            def create_file_dialog(self, *args, **kwargs):
                raise RuntimeError("对话框炸了")

        api = make_api(window=BrokenWindow())
        assert api.import_csv()["code"] == "cancelled"


class TestCsvTemplate:
    """CSV 模板生成（PRD F-02b / AC-02b）。"""

    def test_creates_template_with_bom(self, api, base_dir: Path) -> None:
        """AC-02b①②③：模板落在 data/ 下，带 BOM，Excel 打开不乱码。"""
        result = api.create_csv_template()
        assert result["ok"] is True
        assert result["overwritten"] is False

        target = Path(result["path"])
        assert target == base_dir / "data" / TEMPLATE_FILENAME
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")
        assert target.read_bytes().decode("utf-8-sig") == "Num,Name\r\n"

    def test_reports_overwrite(self, api) -> None:
        """模板已存在时通过 overwritten 告知界面，便于弹覆盖确认。"""
        api.create_csv_template()
        assert api.create_csv_template()["overwritten"] is True

    def test_works_without_any_roster(self, api) -> None:
        """一个班级都没有时也能生成模板（新用户的第一步）。"""
        result = api.create_csv_template()
        assert result["ok"] is True
        assert Path(result["path"]).is_file()

    def test_io_error_is_reported(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        """磁盘写入失败时返回 io_error，而不是抛异常。"""
        from stellardraw.core import importer

        def boom(_target):
            raise OSError("磁盘已满")

        monkeypatch.setattr(importer, "create_template", boom)
        result = api.create_csv_template()
        assert result["ok"] is False
        assert result["code"] == "io_error"


class TestDraw:
    """抽取接口（PRD F-05 / F-06 / F-07 / E-01 / E-02 / E-03）。"""

    def test_empty_roster_guides_to_settings(self, api) -> None:
        """AC-10/E-01：空名单抽取不产生异常，提示直达设置面板。"""
        result = api.draw(1)
        assert result["ok"] is False
        assert result["code"] == "empty_roster"
        assert result["message"] == MSG_EMPTY_ROSTER
        assert result["open_settings"] is True

    def test_draw_once(self, api, students_payload) -> None:
        """AC-05：抽 1 次返回 1 名学生，字段与名单一致。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(10))

        result = api.draw(1)
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["requested"] == 1
        assert set(result["students"][0]) == {"num", "name"}
        assert result["remaining"] == 9

    def test_default_count_is_one(self, api, students_payload) -> None:
        """不传参数时默认抽 1 次。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))
        assert api.draw()["count"] == 1

    def test_draw_five_distinct(self, api, students_payload) -> None:
        """AC-06④：抽 5 次去重开启时学生互不相同。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(45))

        result = api.draw(5)
        assert result["count"] == 5
        assert len({s["num"] for s in result["students"]}) == 5
        assert result["message"] == ""

    def test_short_pool_message(self, api, students_payload) -> None:
        """AC-06/E-02：剩余不足 5 人时只发剩余数量的卡并提示。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(3))

        result = api.draw(5)
        assert result["count"] == 3
        assert "本轮仅剩 3 人，已全部抽出" in result["message"]

    def test_new_round_message(self, api, students_payload) -> None:
        """AC-04②/E-03：一轮抽完后自动重置并提示"新一轮开始"。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(2))
        api.draw(1)
        api.draw(1)

        result = api.draw(1)
        assert result["reset"] is True
        assert result["message"] == MSG_NEW_ROUND

    def test_full_round_has_no_duplicates(self, api, students_payload) -> None:
        """AC-04①：抽完全班期间无任何重复。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(10))

        drawn = [api.draw(1)["students"][0]["num"] for _ in range(10)]
        assert len(set(drawn)) == 10

    @pytest.mark.parametrize("bad_count", [0, -1, 6, 100])
    def test_invalid_count(self, api, students_payload, bad_count: int) -> None:
        """非法抽取人数被拒绝。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(10))
        result = api.draw(bad_count)
        assert result["ok"] is False
        assert result["code"] == "invalid_count"

    def test_non_numeric_count(self, api, students_payload) -> None:
        """前端传入字符串数字也能容忍。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(10))
        assert api.draw("5")["count"] == 5
        assert api.draw("不是数字")["code"] == "invalid_count"

    def test_dedupe_off_allows_duplicates(self, api, students_payload) -> None:
        """AC-04③：关闭去重后允许重复。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(2))
        api.set_dedupe(False)

        result = api.draw(5)
        assert result["count"] == 5

    def test_switching_roster_resets_pool(self, api, students_payload) -> None:
        """§6.3：切换班级后候选池按新班级重建。"""
        api.create_roster("甲班")
        api.save_roster("甲班", students_payload(5, "甲"))
        api.create_roster("乙班")
        api.save_roster("乙班", students_payload(8, "乙"))

        api.load_roster("甲班")
        api.draw(3)
        assert api.load_roster("乙班")["remaining"] == 8


class TestApplyDriftReveal:
    """F-17b 结果漂移在 reveal 阶段的应用（PRD 2026-09-13 第三轮）。

    语义变化：
        · 旧版本在 draw() 时按下标循环偏移，res.students 已是偏移后的；
        · 新版本 draw() 不再偏移；reveal 时由前端上报"raw 学生"，
          后端按 armed offset 在班级里找出替换学生，并清空 armed。

    本类覆盖：
        · 武装过漂移 → reveal 命中 → 返回偏移后学生、applied=True、清空 armed
        · 未武装 → reveal 命中 → 返回 raw、applied=False
        · 多卡多 reveal：armed 是单次性的，第二次起不再替换
        · raw 学生不在当前班级 → 兜底返回 raw（极端防御）
        · 非 dict raw_student → 安全返 ok=False
    """

    def test_returns_offset_student_when_armed(self, api, students_payload) -> None:
        """武装 +1 后 reveal 命中：raw = 学生 A，应展示 (A + 1) mod total。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5, "S"))
        api.arm_drift(1)

        # 学生 02 在班内下标 = 1（班级里 0=01, 1=02, 2=03, 3=04, 4=05）
        result = api.apply_drift_reveal({"num": "02", "name": "S2"})
        assert result["ok"] is True
        assert result["applied"] is True
        assert result["offset"] == 1
        assert result["student"] == {"num": "03", "name": "S3"}

    def test_no_arm_returns_raw(self, api, students_payload) -> None:
        """未武装时：reveal 命中应返回 raw，不替换。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))

        result = api.apply_drift_reveal({"num": "01", "name": "学生1"})
        assert result["ok"] is True
        assert result["applied"] is False
        assert result["student"] == {"num": "01", "name": "学生1"}

    def test_consume_one_shot(self, api, students_payload) -> None:
        """armed 是单次性的：第二次 reveal 不再替换。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))
        api.arm_drift(1)

        first = api.apply_drift_reveal({"num": "01", "name": "学生1"})
        assert first["applied"] is True

        # 第二次 reveal：armed 已消费，原值返回
        second = api.apply_drift_reveal({"num": "01", "name": "学生1"})
        assert second["applied"] is False
        assert second["student"] == {"num": "01", "name": "学生1"}

    def test_heavy_offset_three(self, api, students_payload) -> None:
        """武装 +3 后：raw = A → 展示 (A + 3) mod total。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(8, "Z"))
        api.arm_drift(3)

        # 学生 02 在班内下标 1，(1 + 3) mod 8 = 4 → 05/Z5
        result = api.apply_drift_reveal({"num": "02", "name": "Z2"})
        assert result["applied"] is True
        assert result["student"]["num"] == "05"
        assert result["student"]["name"] == "Z5"

    def test_wraps_around_at_end(self, api, students_payload) -> None:
        """超出列表末尾时取模回头：班级最后一名学生 +1 = 班级第一名。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(3))  # 01 / 02 / 03
        api.arm_drift(1)

        # 学生 03 = 下标 2，(2 + 1) mod 3 = 0 → 01
        result = api.apply_drift_reveal({"num": "03", "name": "学生3"})
        assert result["applied"] is True
        assert result["student"] == {"num": "01", "name": "学生1"}

    def test_raw_not_in_roster_falls_back(self, api, students_payload) -> None:
        """raw 学生不在当前班级里：applied=False，依然返回 raw（兜底）。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))
        api.arm_drift(1)

        # num=99 不在班内
        result = api.apply_drift_reveal({"num": "99", "name": "幽灵"})
        assert result["ok"] is True
        assert result["applied"] is False
        assert result["offset"] == 1
        assert result["student"] == {"num": "99", "name": "幽灵"}

    def test_invalid_input_rejected(self, api) -> None:
        """非 dict 入参：返回 invalid_roster 信封，不抛异常。"""
        result = api.apply_drift_reveal("not a dict")
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"

    def test_state_cleared_after_reveal(self, api, students_payload) -> None:
        """reveal 后 drift 的 armed 视觉在前端通过 FX.onDriftRevealed 清除；
        后端这边 armed_offset 在第二次 arm_drift 之前一直保持 None。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))
        api.arm_drift(1)
        api.apply_drift_reveal({"num": "01", "name": "学生1"})

        # effect_state 的 drift_armed 必须变回 False
        state = api.get_effect_state()["state"]
        assert state["drift_armed"] is False
        assert state["drift_offset"] is None


class TestSettings:
    """设置读写（PRD F-10 / AC-09④）。"""

    def test_default_settings(self, api) -> None:
        """默认配置满足 PRD：去重开、音效开、音量 0.7、看板娘开。"""
        settings = api.get_settings()["settings"]
        assert settings["dedupe"] is True
        assert settings["muted"] is False
        assert settings["volume"] == pytest.approx(0.7)
        assert settings["mascot_enabled"] is True
        assert settings["external_exe"] == ""
        assert set(settings) == {
            "dedupe",
            "muted",
            "volume",
            "mascot_enabled",
            "external_exe",
            "current_roster",
        }

    def test_partial_update_and_persist(self, api, make_api) -> None:
        """AC-09④：部分字段更新，重启后保留。"""
        result = api.save_settings({"muted": True, "volume": 0.25})
        assert result["ok"] is True
        assert result["settings"]["muted"] is True
        assert result["settings"]["volume"] == pytest.approx(0.25)

        reopened = make_api()
        assert reopened.get_settings()["settings"]["muted"] is True
        assert reopened.get_settings()["settings"]["volume"] == pytest.approx(0.25)

    def test_sound_toggle_reuses_muted_and_persists(self, api, make_api) -> None:
        """TI-15：音效开关复用 muted 字段，默认开启，可关闭并重启保留。"""
        # 默认开启 = 未静音
        assert api.get_settings()["settings"]["muted"] is False

        assert api.save_settings({"muted": True})["settings"]["muted"] is True
        assert make_api().get_settings()["settings"]["muted"] is True

        # 再打开（关闭静音）同样要落盘
        assert api.save_settings({"muted": False})["settings"]["muted"] is False
        assert make_api().get_settings()["settings"]["muted"] is False

    def test_mascot_toggle_default_on_and_persists(self, api, make_api) -> None:
        """TI-16：看板娘开关默认开启，可关闭并重启保留。"""
        assert api.get_settings()["settings"]["mascot_enabled"] is True

        result = api.save_settings({"mascot_enabled": False})
        assert result["ok"] is True
        assert result["settings"]["mascot_enabled"] is False
        assert make_api().get_settings()["settings"]["mascot_enabled"] is False

        assert api.save_settings({"mascot_enabled": True})["settings"]["mascot_enabled"] is True

    def test_mascot_toggle_does_not_affect_other_settings(self, api, make_api) -> None:
        """新开关不得污染既有配置项（做新功能不搞坏已有功能）。"""
        api.save_settings({"dedupe": False, "volume": 0.4, "muted": True, "mascot_enabled": False})

        settings = make_api().get_settings()["settings"]
        assert settings["dedupe"] is False
        assert settings["volume"] == pytest.approx(0.4)
        assert settings["muted"] is True
        assert settings["mascot_enabled"] is False

    def test_unknown_keys_ignored(self, api) -> None:
        """未知设置项被忽略，不会污染配置文件。"""
        api.save_settings({"不存在": 1, "muted": True})
        assert "不存在" not in api.get_settings()["settings"]

    def test_dedupe_synced_to_drawer(self, api, students_payload) -> None:
        """通过 save_settings 改去重开关时，抽取引擎必须同步。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(3))

        api.save_settings({"dedupe": False})
        assert api.draw(5)["count"] == 5  # 允许重复

    def test_set_dedupe_persists(self, api, make_api) -> None:
        """set_dedupe 也应落盘。"""
        api.set_dedupe(False)
        assert make_api().get_settings()["settings"]["dedupe"] is False

    def test_set_dedupe_rebuilds_pool(self, api, students_payload) -> None:
        """AC-04④：从"关"切到"开"时候选池被重建。"""
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(5))
        api.set_dedupe(False)
        api.draw(4)
        api.set_dedupe(True)
        assert api.draw(1)["remaining"] == 4

    def test_config_file_location(self, api, base_dir: Path) -> None:
        """配置写在 data/config.json（NFR-D5：绿色免安装）。"""
        api.save_settings({"muted": True})
        assert (base_dir / "data" / CONFIG_FILENAME).is_file()


class TestExternalProgram:
    """「简洁视图」外部程序（PRD F-09 / AC-08）。"""

    def test_choose_without_window(self, api) -> None:
        """没有窗口时按取消处理。"""
        assert api.choose_external_exe()["code"] == "cancelled"

    def test_choose_and_persist(self, make_api, fake_window_factory, tmp_path: Path) -> None:
        """AC-08①②：通过文件选择器指定 exe，路径重启后仍保留。"""
        exe = tmp_path / "抽号器.exe"
        exe.write_bytes(b"MZ")
        window = fake_window_factory(str(exe))
        api = make_api(window=window)

        result = api.choose_external_exe()
        assert result["ok"] is True
        assert result["path"] == str(exe)
        assert "*.exe" in window.calls[0]["file_types"][0]

        reopened = make_api()
        assert reopened.get_settings()["settings"]["external_exe"] == str(exe)

    def test_launch_not_set(self, api) -> None:
        """AC-08⑤/E-04：未设置路径时提示去设置，并标记 open_settings。"""
        result = api.launch_external()
        assert result["ok"] is False
        assert result["code"] == "not_set"
        assert result["open_settings"] is True

    def test_launch_invalid_path(self, api) -> None:
        """E-05：路径失效时提示重新设置。"""
        api.save_settings({"external_exe": "D:/不存在/工具.exe"})
        result = api.launch_external()
        assert result["ok"] is False
        assert result["code"] == "not_found"
        assert result["open_settings"] is True

    def test_launch_success(self, api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-08③④：成功启动外部程序，且不影响主程序状态。"""
        exe = tmp_path / "抽号器.exe"
        exe.write_bytes(b"MZ")
        api.save_settings({"external_exe": str(exe)})

        spawned: list[str] = []
        monkeypatch.setattr(launcher, "_spawn", spawned.append)

        result = api.launch_external()
        assert result["ok"] is True
        assert spawned == [str(exe)]
        # 主程序仍然可用（不退出、不崩）
        assert api.ping()["ok"] is True

    def test_launch_spawn_failure(
        self, api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """启动失败被转为友好提示。"""
        exe = tmp_path / "工具.exe"
        exe.write_bytes(b"MZ")
        api.save_settings({"external_exe": str(exe)})

        def boom(_path):
            raise OSError("拒绝访问")

        monkeypatch.setattr(launcher, "_spawn", boom)
        result = api.launch_external()
        assert result["ok"] is False
        assert result["code"] == "not_found"


class TestIoFailureHandling:
    """磁盘读写失败时的降级（接口层必须永不抛异常）。"""

    def test_save_roster_io_error(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        """保存名单时磁盘出错 → io_error。"""
        api.create_roster("测试班")

        def boom(_roster):
            raise OSError("磁盘只读")

        monkeypatch.setattr(api._storage, "save_roster", boom)
        result = api.save_roster("测试班", [{"num": "01", "name": "张三"}])
        assert result["ok"] is False
        assert result["code"] == "io_error"

    def test_create_roster_io_error(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        """新建班级时磁盘出错 → io_error。"""

        def boom(_roster):
            raise OSError("磁盘满了")

        monkeypatch.setattr(api._storage, "save_roster", boom)
        result = api.create_roster("测试班")
        assert result["ok"] is False
        assert result["code"] == "io_error"

    def test_rename_roster_io_error(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        """重命名时磁盘出错 → io_error。"""

        def boom(_old, _new):
            raise OSError("文件被占用")

        monkeypatch.setattr(api._storage, "rename_roster", boom)
        result = api.rename_roster("甲班", "乙班")
        assert result["ok"] is False
        assert result["code"] == "io_error"

    def test_import_csv_read_error(
        self, make_api, fake_window_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CSV 读取失败 → io_error，且不留待导入数据。"""
        from stellardraw.core import importer

        csv_path = write_csv(tmp_path / "n.csv", "Num,Name\r\n01,张小雨\r\n")
        api = make_api(window=fake_window_factory(str(csv_path)))

        def boom(_path):
            raise OSError("文件被 Excel 占用")

        monkeypatch.setattr(importer, "import_csv", boom)
        result = api.import_csv()
        assert result["ok"] is False
        assert result["code"] == "io_error"
        assert api.apply_import("append")["ok"] is False

    def test_apply_import_save_error(
        self,
        make_api,
        fake_window_factory,
        tmp_path: Path,
        students_payload,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """导入落盘失败 → io_error，且不破坏当前名单。"""
        api = make_api()
        api.create_roster("测试班")
        api.save_roster("测试班", students_payload(2))
        api.attach_window(
            fake_window_factory(str(write_csv(tmp_path / "n.csv", "Num,Name\r\n99,新同学\r\n")))
        )
        assert api.import_csv()["need_confirm"] is True

        def boom(_roster):
            raise OSError("磁盘只读")

        monkeypatch.setattr(api._storage, "save_roster", boom)
        result = api.apply_import("append")
        assert result["ok"] is False
        assert result["code"] == "io_error"

    def test_save_settings_io_error(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        """配置写入失败 → io_error。"""

        def boom(_patch):
            raise OSError("配置目录只读")

        monkeypatch.setattr(api._storage, "update_config", boom)
        result = api.save_settings({"muted": True})
        assert result["ok"] is False
        assert result["code"] == "io_error"

    def test_choose_external_exe_io_error(
        self, api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """保存外部程序路径失败 → io_error。"""
        exe = tmp_path / "工具.exe"
        exe.write_bytes(b"MZ")
        api.attach_window(_StubWindow(str(exe)))

        def boom(_patch):
            raise OSError("配置目录只读")

        monkeypatch.setattr(api._storage, "update_config", boom)
        result = api.choose_external_exe()
        assert result["ok"] is False
        assert result["code"] == "io_error"


class _StubWindow:
    """最小窗口替身：只返回一个固定路径。"""

    def __init__(self, path: str) -> None:
        self._path = path

    def create_file_dialog(self, *args, **kwargs):
        return (self._path,)


class TestDefaultClassName:
    """自动创建的默认班级名（避免与已有班级撞名）。"""

    def test_suffix_when_default_name_taken(self, api) -> None:
        """「默认班级」已被占用时应顺延为「默认班级2」。"""
        api.create_roster(DEFAULT_ROSTER_NAME)
        assert api._next_available_name(DEFAULT_ROSTER_NAME) == f"{DEFAULT_ROSTER_NAME}2"

    def test_suffix_increments(self, api) -> None:
        """连续占用时应持续顺延。"""
        api.create_roster(DEFAULT_ROSTER_NAME)
        api.create_roster(f"{DEFAULT_ROSTER_NAME}2")
        assert api._next_available_name(DEFAULT_ROSTER_NAME) == f"{DEFAULT_ROSTER_NAME}3"

    def test_free_name_returned_as_is(self, api) -> None:
        """名字没被占用时原样返回。"""
        assert api._next_available_name(DEFAULT_ROSTER_NAME) == DEFAULT_ROSTER_NAME

    def test_import_uses_suffixed_name(self, make_api, fake_window_factory, tmp_path: Path) -> None:
        """当前班级为空但「默认班级」已存在时，导入应落到顺延后的名字上。"""
        first = make_api()
        first.create_roster(DEFAULT_ROSTER_NAME)
        # 模拟配置被清空 / 损坏：班级还在，但"当前班级"指针丢了
        from stellardraw.core.storage import Storage

        Storage(base_dir=tmp_path).update_config({"current_roster": ""})

        csv_path = write_csv(tmp_path / "n.csv", "Num,Name\r\n01,张小雨\r\n")
        api = make_api(window=fake_window_factory(str(csv_path)))
        result = api.import_csv()

        assert result["ok"] is True
        assert api.list_rosters()["current"] == f"{DEFAULT_ROSTER_NAME}2"

    def test_delete_roster_blank_name(self, api) -> None:
        """删除时班级名为空 → 明确错误。"""
        result = api.delete_roster("   ")
        assert result["ok"] is False
        assert result["code"] == "invalid_roster"


class TestOpenDataDir:
    """打开 data 目录（PRD F-02c / AC-26）。"""

    def test_opens_and_creates(self, api, base_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-26②③：调用系统文件管理器打开 data/，目录不存在时先创建。"""
        opened: list[str] = []
        monkeypatch.setattr(launcher.os, "startfile", opened.append, raising=False)

        result = api.open_data_dir()
        assert result["ok"] is True
        assert Path(result["path"]) == base_dir / "data"
        assert Path(result["path"]).is_dir()
        assert opened == [str(base_dir / "data")]

    def test_failure_reported(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        """系统拒绝打开时返回 io_error。"""

        def boom(_path):
            raise OSError("没有默认程序")

        monkeypatch.setattr(launcher.os, "startfile", boom, raising=False)
        result = api.open_data_dir()
        assert result["ok"] is False
        assert result["code"] == "io_error"
