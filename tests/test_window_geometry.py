"""主窗口几何计算（PRD TI-32 / TBD-03 / AC-27）。

【为什么要专门测这一层】
    窗口尺寸与位置是本项目里唯一"直接跟系统打交道"的地方，而且有一个非常容易
    踩、又不会报错的单位陷阱：

        · 设计值 2560×1440 是**物理像素**（4K 一体机上占屏约 44%、1:1 像素映射）；
        · pywebview 的 ``width/height/x/y`` 却全是**逻辑像素**
          —— WinForms 后端内部会再乘一次 ``GetDpiForWindow()/96``。

    两者混用不会抛异常，只会让窗口在高缩放比的教室一体机上悄悄变成满屏、并且
    偏离正中 —— 而开发机通常是 100% 缩放，这类 bug 在开发机上根本看不出来。
    所以这里用纯函数把折算与居中的算法规格锁死（不依赖任何系统 API）。
"""

from __future__ import annotations

from stellardraw.main import (
    WINDOW_MIN_SIZE,
    WINDOW_SIZE_PHYSICAL,
    compute_window_geometry,
)


class TestWindowGeometry:
    """尺寸折算与居中（纯函数，不触碰系统 API）。"""

    def test_design_values(self) -> None:
        """设计尺寸是物理像素 2560×1440；最小尺寸是逻辑像素 1280×720。"""
        assert WINDOW_SIZE_PHYSICAL == (2560, 1440)
        assert WINDOW_MIN_SIZE == (1280, 720)

    def test_100_percent_scale_keeps_clamping_behaviour(self) -> None:
        """100% 缩放：不做折算，仅按工作区上限钳制（与旧版行为一致）。"""
        width, height, x, y = compute_window_geometry(1.0, (0, 0, 1920, 1040))

        # 1920×1040 的 92%
        assert (width, height) == (1766, 956)
        # 居中：左右 / 上下留白尽量相等
        assert x == (1920 - width) // 2
        assert y == (1040 - height) // 2

    def test_150_percent_on_4k_keeps_the_physical_design_size(self) -> None:
        """4K@150%：折算成逻辑 1707×960，最终物理尺寸仍是 2560×1440。"""
        work_area = (0, 0, 3840, 2120)  # 4K 扣掉约 40px 高的任务栏
        width, height, x, y = compute_window_geometry(1.5, work_area)

        assert (width, height) == (1707, 960)
        # 关键回归点：物理尺寸必须回到设计值，而不是 3840×2160（满屏）
        assert (round(width * 1.5), round(height * 1.5)) == (2560, 1440)

        # 居中基准是**逻辑**工作区
        logical_work_w = round(3840 / 1.5)
        logical_work_h = round(2120 / 1.5)
        assert x == (logical_work_w - width) // 2
        assert y == (logical_work_h - height) // 2
        assert abs(x - (logical_work_w - width - x)) <= 1
        assert abs(y - (logical_work_h - height - y)) <= 1

    def test_200_percent_on_4k_is_exactly_the_min_size(self) -> None:
        """4K@200%：折算后 1280×720，恰好等于最小尺寸，物理尺寸仍是设计值。"""
        width, height, _x, _y = compute_window_geometry(2.0, (0, 0, 3840, 2080))

        assert (width, height) == (1280, 720)
        assert (width * 2, height * 2) == WINDOW_SIZE_PHYSICAL

    def test_small_screen_never_overflows_the_work_area(self) -> None:
        """小屏（1366×768@100%）：窗口必须完整落在工作区内。"""
        work_area = (0, 0, 1366, 728)
        width, height, x, y = compute_window_geometry(1.0, work_area)

        assert (width, height) == (1256, 669)  # 1366×728 的 92%
        assert x >= 0 and y >= 0
        assert x + width <= 1366
        assert y + height <= 728

    def test_work_area_smaller_than_min_size(self) -> None:
        """工作区比最小尺寸还小时，以工作区为准（不能小于它，否则必然越界）。"""
        width, height, x, y = compute_window_geometry(1.0, (0, 0, 800, 600))

        assert (width, height) == (736, 552)
        assert x >= 0 and y >= 0
        assert x + width <= 800
        assert y + height <= 600

    def test_work_area_origin_offset_is_respected(self) -> None:
        """工作区不在 (0, 0)（副屏 / 多显示器）时，坐标要带上工作区自身的偏移。"""
        width, height, x, y = compute_window_geometry(1.0, (1920, 0, 3840, 1040))

        assert x >= 1920, "居中坐标必须叠加工作区偏移，否则会跑到主屏上"
        assert x + width <= 3840
        assert y >= 0

    def test_missing_work_area_falls_back_to_size_only(self) -> None:
        """拿不到工作区时：只返回折算后的尺寸，坐标交给 pywebview 自己居中。"""
        width, height, x, y = compute_window_geometry(1.5, None)

        assert (width, height) == (1707, 960)
        assert x is None and y is None

    def test_invalid_scale_falls_back_to_100_percent(self) -> None:
        """非法缩放值（0 / 负数 / None）一律按 100% 处理，绝不抛异常。"""
        for bad in (0, 0.0, -1.5, None):
            width, height, _x, _y = compute_window_geometry(bad, None)  # type: ignore[arg-type]
            assert (width, height) == WINDOW_SIZE_PHYSICAL

    def test_extreme_scale_never_produces_zero_size(self) -> None:
        """极端缩放值下也必须给出 ≥1×1 的合法尺寸（窗口尺寸不能变成 0）。"""
        width, height, _x, _y = compute_window_geometry(12.0, None)

        assert (width, height) == (213, 120)
        assert width >= 1 and height >= 1


class TestSystemMetricsSmoke:
    """系统度量的烟雾测试：只断言合理区间，不绑定具体机器。"""

    def test_dpi_scale_is_within_reasonable_range(self) -> None:
        """异常时会回退 1.0，正常机器上应落在 0.5 ~ 4.0。"""
        from stellardraw.main import _get_dpi_scale

        scale = _get_dpi_scale()
        assert 0.5 <= scale <= 4.0

    def test_measure_window_returns_usable_geometry(self) -> None:
        """真实度量链路：尺寸为正、不超过设计值，坐标非负或为 None。"""
        from stellardraw.main import measure_window

        width, height, x, y = measure_window()

        assert 1 <= width <= WINDOW_SIZE_PHYSICAL[0]
        assert 1 <= height <= WINDOW_SIZE_PHYSICAL[1]
        for value in (x, y):
            assert value is None or value >= 0
