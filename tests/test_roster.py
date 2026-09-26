"""core/roster.py 的单元测试。

对应需求：PRD F-01 / F-03 / F-04 / F-08 / E-07 / AC-01 / AC-03 / US-13

本文件覆盖"名单领域模型"的完整行为契约：
    · 学生记录的字段规则（学号可空、姓名必填）
    · 序列化 / 反序列化的容错能力（应对手工编辑过的 JSON）
    · 增删改查的全部边界（越界、空姓名、脏数据）
"""

from __future__ import annotations

import pytest

from stellardraw.core.roster import EMPTY_NAME_MESSAGE, Roster, Student


class TestStudent:
    """学生记录本身的字段行为。"""

    def test_full_record_to_dict(self) -> None:
        """学号 + 姓名能完整序列化（PRD F-08 要求结果卡拿到两个字段）。"""
        assert Student(num="07", name="张小雨").to_dict() == {"num": "07", "name": "张小雨"}

    def test_has_num_reflects_empty_num(self) -> None:
        """学号为空时 has_num 为 False —— 结果卡据此不渲染学号行（PRD E-07）。"""
        assert Student(num="07", name="张三").has_num is True
        assert Student(num="", name="张三").has_num is False
        assert Student(num="   ", name="张三").has_num is False

    def test_is_valid_requires_name(self) -> None:
        """姓名是必填项，学号不是。"""
        assert Student(num="", name="张三").is_valid is True
        assert Student(num="07", name="").is_valid is False
        assert Student(num="07", name="  ").is_valid is False

    def test_normalized_strips_whitespace(self) -> None:
        """首尾空白必须被清掉，否则"张三 "与"张三"会变成两条记录。"""
        assert Student(num=" 07 ", name=" 张小雨 ").normalized().to_dict() == {
            "num": "07",
            "name": "张小雨",
        }

    def test_from_dict_tolerates_missing_and_none(self) -> None:
        """缺字段 / None 都不应导致崩溃（应对手工编辑过的 JSON）。"""
        assert Student.from_dict({}).to_dict() == {"num": "", "name": ""}
        assert Student.from_dict({"num": None, "name": None}).to_dict() == {
            "num": "",
            "name": "",
        }

    def test_from_dict_coerces_non_string(self) -> None:
        """学号可能是数字类型（JSON 里 07 会被解析成 7），需统一转字符串。"""
        assert Student.from_dict({"num": 7, "name": "张三"}).to_dict() == {
            "num": "7",
            "name": "张三",
        }

    def test_from_dict_rejects_non_dict(self) -> None:
        """非 dict 的脏数据返回空学生，由上层过滤。"""
        assert Student.from_dict("张三").is_valid is False


class TestRosterSerialization:
    """名单整体的序列化与容错。"""

    def test_roundtrip(self) -> None:
        """序列化后再反序列化应完全还原。"""
        roster = Roster(name="初一(3)班", students=[Student("01", "张三"), Student("", "李四")])
        restored = Roster.from_dict(roster.to_dict())
        assert restored.name == "初一(3)班"
        assert [s.to_dict() for s in restored] == [s.to_dict() for s in roster]

    def test_from_dict_drops_invalid_records(self) -> None:
        """姓名为空的脏记录在反序列化阶段就被剔除，不会进入抽取池。"""
        restored = Roster.from_dict(
            {
                "name": "测试班",
                "students": [{"num": "01", "name": "张三"}, {"num": "02", "name": ""}, "脏数据"],
            }
        )
        assert len(restored) == 1
        assert restored.students[0].name == "张三"

    def test_from_dict_handles_missing_students(self) -> None:
        """没有 students 字段时得到空名单，而不是报错。"""
        assert len(Roster.from_dict({"name": "空班"})) == 0
        assert len(Roster.from_dict(None)) == 0  # type: ignore[arg-type]

    def test_from_payload_builds_from_bridge_json(self) -> None:
        """JS Bridge 传来的载荷能正确构造名单，并跳过姓名为空的行。"""
        roster = Roster.from_payload(
            " 测试班 ",
            [
                {"num": "01", "name": "张三"},
                {"num": "02", "name": "  "},
                {"num": "03", "name": "王五"},
            ],
        )
        assert roster.name == "测试班"
        assert [s.name for s in roster] == ["张三", "王五"]

    def test_from_payload_rejects_non_list(self) -> None:
        """载荷类型不对时抛出 TypeError，由 api 层转为友好提示。"""
        with pytest.raises(TypeError):
            Roster.from_payload("测试班", "张三")  # type: ignore[arg-type]

    def test_display_labels(self) -> None:
        """日志用标签：有学号拼学号，无学号只显示姓名。"""
        roster = Roster(students=[Student("01", "张三"), Student("", "李四")])
        assert roster.display_labels() == ["01 张三", "李四"]

    def test_has_duplicate_students(self) -> None:
        """完全相同的两条记录会被识别出来（仅作提示用途）。"""
        assert Roster(students=[Student("01", "张三")]).has_duplicate_students() is False
        assert (
            Roster(students=[Student("01", "张三"), Student("01", "张三")]).has_duplicate_students()
            is True
        )

    def test_same_name_different_num_is_not_duplicate(self) -> None:
        """同名不同学号是合法的两个个体（PRD AC-04⑤ / US-13）。"""
        roster = Roster(students=[Student("01", "张伟"), Student("02", "张伟")])
        assert roster.has_duplicate_students() is False

    def test_is_empty_and_len(self) -> None:
        """空名单判定与长度。"""
        assert Roster().is_empty() is True
        assert Roster(students=[Student("01", "张三")]).is_empty() is False
        assert len(Roster(students=[Student("01", "张三")])) == 1


class TestRosterMutations:
    """增删改查（PRD F-01 / AC-01）。"""

    def test_add_appends_and_returns_index(self) -> None:
        """添加学生后立即出现在列表中，并返回下标（AC-01①）。"""
        roster = Roster()
        assert roster.add(Student("01", "张三")) == 0
        assert roster.add(Student("02", "李四")) == 1
        assert roster.display_labels() == ["01 张三", "02 李四"]

    def test_add_normalizes_whitespace(self) -> None:
        """录入时多打的空格被清理。"""
        roster = Roster()
        roster.add(Student(" 01 ", " 张三 "))
        assert roster.students[0].to_dict() == {"num": "01", "name": "张三"}

    def test_add_rejects_empty_name(self) -> None:
        """姓名为空时明确报错（姓名是必填字段）。"""
        with pytest.raises(ValueError, match=EMPTY_NAME_MESSAGE):
            Roster().add(Student("01", "   "))

    def test_extend_skips_invalid_rows(self) -> None:
        """批量追加时静默跳过无效行，返回实际条数（对应"自动过滤空行"）。"""
        roster = Roster()
        added = roster.extend([Student("01", "张三"), Student("02", ""), Student("03", "王五")])
        assert added == 2
        assert len(roster) == 2

    def test_remove_at(self) -> None:
        """按下标删除并能影响列表（AC-01③）。"""
        roster = Roster(students=[Student("01", "张三"), Student("02", "李四")])
        removed = roster.remove_at(0)
        assert removed.name == "张三"
        assert roster.display_labels() == ["02 李四"]

    def test_remove_at_out_of_range(self) -> None:
        """下标越界必须报 IndexError，由上层转成友好提示。"""
        roster = Roster(students=[Student("01", "张三")])
        with pytest.raises(IndexError):
            roster.remove_at(1)
        with pytest.raises(IndexError):
            roster.remove_at(-1)

    def test_update_at(self) -> None:
        """编辑已有记录并生效（AC-01②）。"""
        roster = Roster(students=[Student("01", "张三")])
        roster.update_at(0, Student("01", "张小雨"))
        assert roster.students[0].name == "张小雨"

    def test_update_at_out_of_range(self) -> None:
        """越界编辑同样报 IndexError。"""
        with pytest.raises(IndexError):
            Roster().update_at(0, Student("01", "张三"))

    def test_update_at_rejects_empty_name(self) -> None:
        """编辑成空姓名必须被拒绝。"""
        roster = Roster(students=[Student("01", "张三")])
        with pytest.raises(ValueError, match=EMPTY_NAME_MESSAGE):
            roster.update_at(0, Student("01", ""))

    def test_clear_keeps_name(self) -> None:
        """清空学生但保留班级名称。"""
        roster = Roster(name="初一(3)班", students=[Student("01", "张三")])
        roster.clear()
        assert roster.is_empty() is True
        assert roster.name == "初一(3)班"

    def test_rename(self) -> None:
        """重命名班级（AC-03③）。"""
        roster = Roster(name="旧班名")
        roster.rename(" 新班名 ")
        assert roster.name == "新班名"

    def test_rename_rejects_blank(self) -> None:
        """班级名不能为空。"""
        with pytest.raises(ValueError):
            Roster(name="旧班名").rename("   ")
