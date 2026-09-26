"""特殊效果状态机的单元测试（F-17a / F-17b / F-17c）。

需求：覆盖以下场景：
    · 加权名单的增删改查、姓名空白的过滤
    · 漂移偏移的合法值校验、武装/取消/一次性消费
    · 叫醒服务的三档音量、确认/取消、一次性的 bingo armed
    · 切换班级时所有一次性状态被清空
"""

from __future__ import annotations

import pytest

from stellardraw.core.special_effects import (
    BOOST_WEIGHT,
    DRIFT_OFFSET_HEAVY,
    DRIFT_OFFSET_LIGHT,
    DRIFT_OFFSETS,
    WAKE_UP_VOLUME_LABELS,
    SpecialEffectsState,
)


class TestSpecialEffectsState:
    """SpecialEffectsState 的内部行为。"""

    def test_initial_state_is_clean(self) -> None:
        s = SpecialEffectsState()
        assert s.boost == {}
        assert s.drift_offset is None
        assert s.wake_up_volume is None
        assert s.wake_up_armed is False
        assert s.to_dict() == {
            "boost": [],
            "drift_armed": False,
            "drift_offset": None,
            "wake_up_volume": None,
            "wake_up_armed": False,
            "wake_up_options": [
                {"label": "大", "level": 0.9},
                {"label": "中", "level": 0.6},
                {"label": "小", "level": 0.4},
            ],
            "drift_options": [
                {"label": "轻移", "offset": DRIFT_OFFSET_LIGHT},
                {"label": "重移", "offset": DRIFT_OFFSET_HEAVY},
            ],
        }

    def test_set_boost_replaces_existing(self) -> None:
        s = SpecialEffectsState()
        s.set_boost([{"num": "1", "name": "张三"}])
        s.set_boost([{"num": "2", "name": "李四"}, {"num": "3", "name": "王五"}])
        # 按姓名升序：李四 < 王五（Unicode 码点 674E < 738B）
        assert s.get_boost_list() == [
            {"num": "2", "name": "李四"},
            {"num": "3", "name": "王五"},
        ]

    def test_set_boost_filters_empty_name(self) -> None:
        s = SpecialEffectsState()
        s.set_boost(
            [
                {"num": "1", "name": ""},  # 姓名空
                {"num": "2", "name": "李四"},
                {"num": "3", "name": "  "},  # 全空白
            ],
        )
        assert s.get_boost_list() == [{"num": "2", "name": "李四"}]

    def test_set_boost_normalizes_whitespace(self) -> None:
        s = SpecialEffectsState()
        s.set_boost([{"num": " 1 ", "name": " 张三 "}])
        assert s.get_boost_list() == [{"num": "1", "name": "张三"}]

    def test_set_boost_tolerates_invalid_items(self) -> None:
        s = SpecialEffectsState()
        s.set_boost(
            [
                "not a dict",  # 类型错误
                None,
                {"num": "1", "name": "真名"},
            ],
        )
        assert s.get_boost_list() == [{"num": "1", "name": "真名"}]

    def test_clear_boost(self) -> None:
        s = SpecialEffectsState()
        s.set_boost([{"num": "1", "name": "张三"}])
        s.clear_boost()
        assert s.boost == {}

    def test_arm_drift_accepts_only_allowed_offsets(self) -> None:
        s = SpecialEffectsState()
        for offset in DRIFT_OFFSETS:
            assert s.arm_drift(offset) is True
            assert s.drift_offset == offset

        assert s.arm_drift(2) is False
        assert s.arm_drift(0) is False
        assert s.arm_drift(-1) is False
        assert s.arm_drift("3") is False  # 字符串也拒收

    def test_cancel_drift(self) -> None:
        s = SpecialEffectsState()
        s.arm_drift(DRIFT_OFFSET_LIGHT)
        s.cancel_drift()
        assert s.drift_offset is None

    def test_consume_drift_is_one_shot(self) -> None:
        s = SpecialEffectsState()
        s.arm_drift(DRIFT_OFFSET_HEAVY)
        assert s.consume_drift() == DRIFT_OFFSET_HEAVY
        assert s.consume_drift() is None  # 已消费
        assert s.drift_offset is None

    def test_set_wake_up_volume_accepts_three_levels(self) -> None:
        s = SpecialEffectsState()
        for _label, level in WAKE_UP_VOLUME_LABELS:
            assert s.set_wake_up_volume(level) is True
            assert s.wake_up_volume == pytest.approx(level)

        # 不允许的档位
        assert s.set_wake_up_volume(0.5) is False
        assert s.set_wake_up_volume(0.0) is False
        assert s.set_wake_up_volume(1.0) is False
        assert s.set_wake_up_volume("0.6") is False

    def test_set_wake_up_volume_clears_armed(self) -> None:
        s = SpecialEffectsState()
        s.set_wake_up_volume(0.6)
        s.confirm_wake_up()
        assert s.wake_up_armed is True
        # 换档时 armed 应当清掉（避免「选了一档没确认、又换一档」的状态串台）
        s.set_wake_up_volume(0.9)
        assert s.wake_up_armed is False
        assert s.wake_up_volume == pytest.approx(0.9)

    def test_confirm_wake_up_requires_volume(self) -> None:
        s = SpecialEffectsState()
        assert s.confirm_wake_up() is False  # 还没选档
        s.set_wake_up_volume(0.6)
        assert s.confirm_wake_up() is True
        assert s.wake_up_armed is True

    def test_consume_wake_up_is_one_shot(self) -> None:
        s = SpecialEffectsState()
        s.set_wake_up_volume(0.6)
        s.confirm_wake_up()
        assert s.consume_wake_up() is True
        assert s.consume_wake_up() is False  # 已消费

    def test_cancel_wake_up(self) -> None:
        s = SpecialEffectsState()
        s.set_wake_up_volume(0.6)
        s.confirm_wake_up()
        s.cancel_wake_up()
        assert s.wake_up_volume is None
        assert s.wake_up_armed is False

    def test_reset_for_roster_switch(self) -> None:
        s = SpecialEffectsState()
        s.set_boost([{"num": "1", "name": "张三"}])
        s.arm_drift(DRIFT_OFFSET_HEAVY)
        s.set_wake_up_volume(0.6)
        s.confirm_wake_up()
        s.reset_for_roster_switch()
        assert s.boost == {}
        assert s.drift_offset is None
        assert s.wake_up_volume is None
        assert s.wake_up_armed is False

    def test_to_dict_includes_options(self) -> None:
        s = SpecialEffectsState()
        d = s.to_dict()
        assert "drift_options" in d
        assert len(d["drift_options"]) == 2
        assert "wake_up_options" in d
        assert len(d["wake_up_options"]) == 3

    def test_boost_weight_constant_is_reasonable(self) -> None:
        # 防回归：用户明确说"不要求精确概率值"，所以 BOOST_WEIGHT 不必太小
        # 但也不应太大以至于破坏手感
        assert 1 < BOOST_WEIGHT <= 10
