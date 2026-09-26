"""core/drawer.py 的单元测试。

对应需求：PRD F-05 / F-06 / F-07 / E-01 / E-02 / E-03 / §6.3 / AC-04 / AC-05 / AC-06

抽取引擎是整个产品最核心、也最容易出现"隐性不公平"的地方，
因此这里逐条对照 PRD §6.3 的去重规则表与 AC-04 的五条验收标准编写用例。
"""

from __future__ import annotations

import random

import pytest

from stellardraw.core.drawer import DRAW_FIVE, DRAW_ONCE, MAX_DRAW_COUNT, Drawer
from stellardraw.core.roster import Student


def build(count: int, rng: random.Random | None = None, dedupe: bool = True) -> Drawer:
    """构造一个装了 count 名学生的抽取引擎（学号补零便于断言）。"""
    students = [Student(num=f"{i:02d}", name=f"学生{i}") for i in range(1, count + 1)]
    return Drawer(students=students, dedupe=dedupe, rng=rng or random.Random(20260913))


class TestEmptyRoster:
    """空名单保护（PRD E-01）。"""

    def test_is_empty(self) -> None:
        """没有学生时 is_empty 为 True，供界面提前禁用抽取按钮。"""
        assert build(0).is_empty is True
        assert build(1).is_empty is False

    def test_draw_raises_on_empty_roster(self) -> None:
        """空名单抽取必须抛错，交由 api 层转换为"请先添加学生名单"。"""
        with pytest.raises(ValueError, match="名单为空"):
            build(0).draw()

    def test_draw_five_raises_on_empty_roster(self) -> None:
        """抽 5 次同样受空名单保护。"""
        with pytest.raises(ValueError):
            build(0).draw(DRAW_FIVE)


class TestSingleDraw:
    """单次抽取（PRD F-06 / AC-05）。"""

    def test_draw_once_returns_one_student(self) -> None:
        """抽 1 次得到 1 名学生，且确实来自名单。"""
        drawer = build(10)
        outcome = drawer.draw(DRAW_ONCE)
        assert outcome.count == 1
        assert outcome.students[0].name.startswith("学生")

    def test_draw_once_decrements_pool(self) -> None:
        """抽中者被移出候选池，候选池余量减一。"""
        drawer = build(10)
        assert drawer.remaining == 10
        drawer.draw()
        assert drawer.remaining == 9

    def test_default_count_is_one(self) -> None:
        """不传参数时默认抽 1 人。"""
        assert build(5).draw().count == 1

    def test_total_and_remaining(self) -> None:
        """total 始终是全班人数；remaining 随抽取递减。"""
        drawer = build(4)
        drawer.draw(2)
        assert drawer.total == 4
        assert drawer.remaining == 2


class TestDedupeRound:
    """去重模式的一轮完整生命周期（PRD AC-04①②、E-03）。"""

    def test_no_repeat_until_round_finished(self) -> None:
        """AC-04①：连续抽取直至抽完全班，期间无任何重复学生。"""
        drawer = build(10)
        drawn: list[str] = []
        for _ in range(10):
            outcome = drawer.draw()
            drawn.append(outcome.students[0].name)
        assert len(set(drawn)) == 10
        assert drawer.remaining == 0

    def test_pool_empty_triggers_new_round(self) -> None:
        """AC-04②/E-03：抽完后再次抽取应自动重置，并标记 reset。"""
        drawer = build(3)
        for _ in range(3):
            assert drawer.draw().reset is False
        assert drawer.remaining == 0

        outcome = drawer.draw()
        assert outcome.reset is True
        assert drawer.remaining == 2

    def test_repeated_full_rounds_cover_everyone(self) -> None:
        """连抽多轮，每一轮都能覆盖全班每人恰好一次。"""
        drawer = build(6)
        for _ in range(3):
            names = [s.name for s in [drawer.draw().students[0] for _ in range(6)]]
            assert sorted(names) == sorted(f"学生{i}" for i in range(1, 7))

    def test_draw_five_returns_five_distinct(self) -> None:
        """AC-06④：去重开启时 5 张卡上的学生互不相同（PRD F-07）。"""
        outcome = build(45).draw(DRAW_FIVE)
        assert outcome.count == 5
        assert len({s.num for s in outcome.students}) == 5

    def test_draw_five_from_exactly_five(self) -> None:
        """全班刚好 5 人时应当全部抽出，且不算"人数不足"。"""
        outcome = build(5).draw(DRAW_FIVE)
        assert outcome.count == 5
        assert outcome.is_short is False
        assert outcome.reset is False


class TestShortPool:
    """候选池不足（PRD E-02 / TBD-05）。"""

    def test_draw_five_with_three_left(self) -> None:
        """E-02：去重开启且只剩 3 人时，只发出 3 张卡并标记人数不足。"""
        drawer = build(5)
        drawer.draw(3)  # 先消耗掉 3 人，池中剩 2 人
        outcome = drawer.draw(DRAW_FIVE)
        assert outcome.count == 2
        assert outcome.requested == DRAW_FIVE
        assert outcome.is_short is True
        assert drawer.remaining == 0

    def test_short_draw_then_next_draw_resets(self) -> None:
        """不足人数抽完后，下一次抽取会开启新一轮。"""
        drawer = build(3)
        first = drawer.draw(DRAW_FIVE)
        assert first.count == 3
        assert first.is_short is True
        second = drawer.draw(DRAW_FIVE)
        assert second.reset is True

    def test_short_and_reset_can_happen_together(self) -> None:
        """全班不足 5 人时，第二次抽取既"开启新一轮"又"人数不足"。"""
        drawer = build(2)

        # 第一轮刚开始，池是满的 → 不算重置，只是人数不足
        first = drawer.draw(DRAW_FIVE)
        assert first.count == 2
        assert first.reset is False
        assert first.is_short is True

        # 池已空 → 重置开启新一轮，但全班只有 2 人，依然不足 5
        second = drawer.draw(DRAW_FIVE)
        assert second.count == 2
        assert second.reset is True
        assert second.is_short is True


class TestDedupeOff:
    """关闭去重（PRD AC-04③ / §6.3）。"""

    def test_duplicates_allowed(self) -> None:
        """关闭去重时允许出现重复学生。"""
        drawer = build(1, dedupe=False)
        outcome = drawer.draw(DRAW_FIVE)
        assert outcome.count == 5
        assert {s.name for s in outcome.students} == {"学生1"}

    def test_pool_not_consumed(self) -> None:
        """关闭去重时候选池不被消耗，remaining 恒等于全班人数。"""
        drawer = build(4, dedupe=False)
        for _ in range(6):
            drawer.draw()
        assert drawer.remaining == 4

    def test_never_reports_reset(self) -> None:
        """关闭去重不存在"新一轮"的概念，reset 恒为 False。"""
        drawer = build(2, dedupe=False)
        for _ in range(5):
            assert drawer.draw(DRAW_FIVE).reset is False


class TestDedupeToggle:
    """运行时切换去重开关（PRD AC-04④ / §6.3）。"""

    def test_switching_on_rebuilds_pool(self) -> None:
        """AC-04④：从"关"切到"开"时候选池被重建，避免残留脏状态。"""
        drawer = build(5, dedupe=False)
        drawer.draw(4)
        assert drawer.remaining == 5  # 关闭态下池不被消耗

        drawer.set_dedupe(True)
        assert drawer.dedupe is True
        assert drawer.remaining == 5

    def test_switching_on_rebuilds_after_partial_round(self) -> None:
        """开启状态下若先关闭再打开，进度被重置为整班。"""
        drawer = build(5)
        drawer.draw(3)
        assert drawer.remaining == 2

        drawer.set_dedupe(False)
        drawer.set_dedupe(True)
        assert drawer.remaining == 5

    def test_setting_same_value_keeps_progress(self) -> None:
        """把已经开启的开关再次设为开启，不应重置本轮进度。"""
        drawer = build(5)
        drawer.draw(2)
        drawer.set_dedupe(True)
        assert drawer.remaining == 3

    def test_switching_off_resets_pool_state(self) -> None:
        """关闭去重后 remaining 语义变成全班人数。"""
        drawer = build(5)
        drawer.draw(3)
        drawer.set_dedupe(False)
        assert drawer.remaining == 5


class TestClassSwitch:
    """切换班级（PRD §6.3）。"""

    def test_set_students_rebuilds_pool(self) -> None:
        """切换班级后候选池按新班级重建，且不带入旧班级的抽取进度。"""
        drawer = build(10)
        drawer.draw(4)
        assert drawer.remaining == 6

        drawer.set_students([Student("01", "新同学")])
        assert drawer.total == 1
        assert drawer.remaining == 1

    def test_reset_pool_explicitly(self) -> None:
        """手动重置候选池（供界面"重新开始一轮"使用）。"""
        drawer = build(3)
        drawer.draw(2)
        drawer.reset_pool()
        assert drawer.remaining == 3


class TestIdentitySemantics:
    """去重按"记录"而非"姓名"判定（PRD §6.3 / AC-04⑤ / US-13）。"""

    def test_same_name_different_num_are_two_individuals(self) -> None:
        """同名不同学号的两人都会被抽出，不会被误判成同一人。"""
        students = [Student("01", "张伟"), Student("02", "张伟")]
        drawer = Drawer(students=students, rng=random.Random(1), dedupe=True)
        outcome = drawer.draw(2)
        assert sorted(s.num for s in outcome.students) == ["01", "02"]
        assert drawer.remaining == 0

    def test_identical_records_are_removed_one_by_one(self) -> None:
        """完全相同的两条记录也按两条计算（按下标移出，不会一次删两条）。"""
        students = [Student("01", "张伟"), Student("01", "张伟")]
        drawer = Drawer(students=students, rng=random.Random(1), dedupe=True)
        assert drawer.draw(1).count == 1
        assert drawer.remaining == 1


class TestCountValidation:
    """抽取人数校验。"""

    @pytest.mark.parametrize("bad_count", [0, -1, DRAW_FIVE + 1, 100])
    def test_invalid_count(self, bad_count: int) -> None:
        """非法人数必须抛 ValueError，防止前端传入异常值。"""
        with pytest.raises(ValueError):
            build(10).draw(bad_count)

    def test_max_count_constant_matches_five(self) -> None:
        """单次上限与 PRD "仅 1 次和 5 次" 的约定一致。"""
        assert MAX_DRAW_COUNT == DRAW_FIVE == 5

    def test_draw_not_raising_for_allowed_counts(self) -> None:
        """1 与 5 是唯一两个合法档位，都必须能正常工作。"""
        for count in (DRAW_ONCE, DRAW_FIVE):
            assert build(10).draw(count).count == count


class TestRandomness:
    """随机性基本性质（不测分布，只测"结果确实随种子变化"）。"""

    def test_different_seeds_give_different_order(self) -> None:
        """不同随机种子下抽出的顺序应当不同，避免"顺序固定"的不公平。"""
        students = [Student(f"{i:02d}", f"学生{i}") for i in range(1, 21)]
        first = [Drawer(students, rng=random.Random(1)).draw().students[0].num for _ in range(1)]
        second = [Drawer(students, rng=random.Random(2)).draw().students[0].num for _ in range(1)]
        # 若两棵种子在一份 20 人名单上抽出的首人完全相同，说明实现没有真正随机
        assert first != second

    def test_same_seed_is_reproducible(self) -> None:
        """同一种子结果可复现（测试可依赖的性质）。"""
        names_a = [build(10).draw().students[0].name for _ in range(1)]
        names_b = [build(10).draw().students[0].name for _ in range(1)]
        assert names_a == names_b


class TestBoostWeight:
    """F-17a 加权效果：被加权学生在候选池中出现多次 → 抽中概率提升。"""

    def test_set_boost_requires_existing_students(self) -> None:
        """只接受当前班级里存在的学生；不存在的（哪怕 (num, name) 长得像）会被忽略。"""
        drawer = build(3)
        # num=99 不在班级里
        drawer.set_boost([Student("99", "外星人")])
        assert drawer._boost == set()

    def test_set_boost_rebuilds_pool(self) -> None:
        """加权集合变化后，池应被重建以让新加权的条目生效。"""
        drawer = build(5)
        original_pool = list(drawer._pool)
        drawer.set_boost([Student("01", "学生1")])
        # 池长度变化（学生1 出现 1 + BOOST_WEIGHT = 3 次）
        # 其他人仍各 1 次：3 + 4 = 7
        assert len(drawer._pool) == 3 + 4
        assert drawer._pool != original_pool

    def test_clear_boost_rebuilds_pool(self) -> None:
        """清空加权后池回到「每名学生 1 次」。"""
        drawer = build(3)
        drawer.set_boost([Student("01", "学生1")])
        drawer.clear_boost()
        assert drawer._boost == set()
        assert len(drawer._pool) == 3  # 每名学生 1 次

    def test_boosted_student_is_drawn_more_often(self) -> None:
        """加权学生被抽中的频率显著高于其他人（概率上的"更易被抽中"）。"""
        # 用大量抽取次数 + 固定种子，证明概率上确实偏向加权学生
        drawer = build(
            5,
            rng=random.Random(20260913),
        )
        drawer.set_boost([Student("01", "学生1")])
        counts = {f"学生{i}": 0 for i in range(1, 6)}
        for _ in range(200):
            drawer.reset_pool()  # 每轮都从满池重新开始，让加权始终生效
            out = drawer.draw()
            counts[out.students[0].name] += 1
        # 学生1 加权后应该出现 3/7 ≈ 42.86% 的概率；其他学生各 14.29%
        # 200 次中期望：学生1 ~ 86 次；其他 ~ 28 次
        assert counts["学生1"] > counts["学生2"] * 2

    def test_boost_does_not_break_dedupe(self) -> None:
        """加权不应破坏去重语义：同一学生在池中有多份，但抽中只移出一份。"""
        drawer = build(3)
        drawer.set_boost([Student("01", "学生1")])
        # 池应当有：学生1×3, 学生2, 学生3 → 池大小 5
        assert len(drawer._pool) == 5
        first = drawer.draw()
        assert first.count == 1
        # 只移走了一份，仍剩学生1×2
        # 池大小：4
        assert len(drawer._pool) == 4


class TestDriftRemoved:
    """F-17b 漂移的语义在 2026-09-13 第三轮被迁移到 reveal 阶段。

    旧版本（draw 阶段就偏移）在 TestDrift 里覆盖。
    新版本（reveal 阶段才替换显示的学生）在 tests/test_api.py 的
    TestApplyDriftReveal 里覆盖 —— 这里只留一个占位说明，避免再次引入旧测试。
    """

    def test_legacy_drift_removed(self) -> None:
        """占位：旧版 TestDrift 已删除，所有漂移断言都搬到 test_api.py。"""
        assert True
