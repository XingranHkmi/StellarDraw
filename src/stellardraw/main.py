"""StellarDraw 应用主入口。

职责（仅此四项，不含任何业务逻辑）：
    1. 初始化日志
    2. 计算窗口尺寸与位置（含系统 DPI 缩放适配）
    3. 创建 pywebview 主窗口 + 注册 JS Bridge（api.Api）
    4. 进入事件循环

业务逻辑一律写在 core/ 下，本文件不实现任何功能。
"""

from __future__ import annotations

import ctypes
import logging

import webview

from stellardraw import __version__
from stellardraw.api import Api
from stellardraw.paths import resource_path

# ---------------------------------------------------------------------------
# 窗口参数（集中定义，避免魔数散落；对应 PRD §4.3 布局规格）
# ---------------------------------------------------------------------------

# 主窗口的**目标物理像素**尺寸：4K 一体机上占屏约 44%，后排可读性优于 1080P。
#
# 【为什么这里是"物理像素"而不是直接给 pywebview】
# pywebview 的 ``width`` / ``height`` 是**逻辑像素**：WinForms 后端会再乘一次
# ``GetDpiForWindow() / 96`` 得到物理尺寸。若把 2560×1440 直接当逻辑像素传进去，
# 在 150% 缩放的 4K 机上就会变成 3840×2160 物理像素（满屏），与设计意图（占屏约
# 44%、1:1 像素映射）严重不符 —— 见 PRD TI-32。
# 因此这里显式声明设计值是物理像素，由 compute_window_geometry() 按系统缩放折算。
WINDOW_SIZE_PHYSICAL = (2560, 1440)

# 最小可用尺寸：逻辑像素（pywebview 的 min_size 同样是逻辑像素）
WINDOW_MIN_SIZE = (1280, 720)

WINDOW_TITLE = "StellarDraw 课堂抽号机"

# 窗口最多占**工作区**的比例，避免在小屏开发机上开出超出屏幕的窗口
_MAX_SCREEN_RATIO = 0.92

# Windows 系统调用常量
_SPI_GETWORKAREA = 0x0030  # 取主显示器工作区（已扣除任务栏）
_LOGPIXELSX = 88  # GetDeviceCaps 的横向 DPI 索引
_BASE_DPI = 96  # 100% 缩放时的 DPI
_MONITOR_DEFAULTTOPRIMARY = 1  # MonitorFromPoint：主显示器
_MDT_EFFECTIVE_DPI = 0  # GetDpiForMonitor：有效 DPI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 系统度量（全部为「内部函数」，失败一律静默降级，绝不阻断启动）
# ---------------------------------------------------------------------------


def _enable_dpi_awareness() -> None:
    """提前声明进程为「系统 DPI 感知」。

    pywebview 自己也会在 ``webview.start()`` 里调用同一个 API，但那时窗口已经要
    开出来了，我们来不及用它拿真实度量值。提前调用可以保证下面读到的屏幕尺寸、
    工作区、DPI 都是**未经系统虚拟化的真实值**（否则在 150% 缩放下
    ``GetSystemMetrics`` 返回的是除以 1.5 之后的值，单位混用会让折算彻底算错）。

    该 API 是幂等的：重复调用只会返回 False，不会报错；非 Windows 或调用失败时
    静默跳过，后续一律按「100% 缩放」处理。
    """
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover —— 非 Windows / 极老系统
        logger.debug("SetProcessDPIAware 不可用，按 100%% 缩放处理")


def _dpi_from_primary_monitor() -> int:
    """主显示器**当前**的 DPI（Win8.1+，``GetDpiForMonitor``）。取不到返回 0。

    之所以优先用它而不是 ``GetDpiForSystem()``：后者返回的是"系统 DPI"，
    在用户**登录后**改过缩放比例的场景下可能仍是旧值；而这里读的是显示器
    当下的真实 DPI，与 pywebview 开窗时用的 ``GetDpiForWindow()`` 口径一致。
    """
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]

        class _Point(ctypes.Structure):
            """Win32 ``POINT``（MonitorFromPoint 按值传参）。"""

            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        # HMONITOR 是句柄（64 位下可能超出 int32），必须显式声明参数/返回类型，
        # 否则 ctypes 默认按 c_int 转换会把句柄截断
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [_Point, ctypes.c_uint]
        monitor = user32.MonitorFromPoint(_Point(0, 0), _MONITOR_DEFAULTTOPRIMARY)
        if not monitor:
            return 0

        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        status = shcore.GetDpiForMonitor(
            ctypes.c_void_p(monitor),
            _MDT_EFFECTIVE_DPI,
            ctypes.byref(dpi_x),
            ctypes.byref(dpi_y),
        )
        if status != 0:  # S_OK == 0
            return 0
        return int(dpi_x.value)
    except Exception:  # pragma: no cover —— Win8.1 及更早 / 非 Windows
        return 0


def _get_dpi_scale() -> float:
    """返回主显示器的 DPI 缩放因子（``1.0`` = 100%）。

    取值优先级：
        1. ``GetDpiForMonitor()``（主显示器当前 DPI，最准确）
        2. ``GetDpiForSystem()``（Win10 1600+）
        3. ``GetDeviceCaps(LOGPIXELSX)``（老系统兜底）

    全都拿不到时返回 ``1.0``；返回值被限制在 ``0.5 ~ 4.0`` 的合理区间内。
    """
    dpi = _dpi_from_primary_monitor()

    if dpi <= 0:
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover —— Win8.1 及更早
            dpi = 0

    if dpi <= 0:
        try:
            hdc = ctypes.windll.user32.GetDC(0)  # type: ignore[attr-defined]
            if hdc:
                dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, _LOGPIXELSX))  # type: ignore[attr-defined]
                ctypes.windll.user32.ReleaseDC(0, hdc)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            dpi = 0

    if dpi <= 0:
        return 1.0

    scale = dpi / _BASE_DPI
    if not 0.5 <= scale <= 4.0:  # 异常值（0 / 负数 / 离谱缩放）按 100% 处理
        return 1.0
    return scale


def _get_work_area() -> tuple[int, int, int, int] | None:
    """返回主显示器的工作区（**物理像素**，已扣除任务栏）：``(left, top, right, bottom)``。

    拿不到时返回 ``None``，调用方会退化为「不指定坐标」（交给 pywebview 自己居中）。
    """

    class _Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = _Rect()
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            _SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
    except Exception:  # pragma: no cover —— 非 Windows
        return None

    if not ok or rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


# ---------------------------------------------------------------------------
# 几何计算（纯函数，便于单元测试；不触碰任何系统 API）
# ---------------------------------------------------------------------------


def _to_logical(physical: float, dpi_scale: float) -> int:
    """物理像素 → 逻辑像素（pywebview 的 ``width/height/x/y`` 都用逻辑像素）。"""
    return int(round(physical / dpi_scale))


def compute_window_geometry(
    dpi_scale: float,
    work_area: tuple[int, int, int, int] | None,
    *,
    design_physical: tuple[int, int] = WINDOW_SIZE_PHYSICAL,
    min_logical: tuple[int, int] = WINDOW_MIN_SIZE,
    max_ratio: float = _MAX_SCREEN_RATIO,
) -> tuple[int, int, int | None, int | None]:
    """把「物理像素设计尺寸」折算成 pywebview 需要的逻辑尺寸与居中坐标。

    参数：
        dpi_scale:       系统 DPI 缩放因子（1.0 = 100%）。
        work_area:       主显示器工作区（物理像素，(left, top, right, bottom)）；
                         传 ``None`` 时不做居中与钳制，x/y 返回 ``None``。
        design_physical: 设计尺寸（物理像素）。
        min_logical:     最小尺寸（逻辑像素）。
        max_ratio:       窗口最多占工作区的比例。

    返回：
        ``(width, height, x, y)``，前两项为逻辑像素尺寸，后两项为逻辑像素坐标
        （``None`` 表示交给 pywebview 自行居中）。

    行为：
        1. 设计尺寸是**物理**像素，按缩放折算成逻辑像素 —— 4K@150% 下
           2560×1440 折算为 1707×960 逻辑，最终呈现的物理尺寸仍是 2560×1440。
        2. 尺寸被钳制在 ``[min_logical, 工作区 × max_ratio]`` 之间，
           保证窗口既不小于最小可用尺寸，也不会超出屏幕。
        3. 以**工作区**（已扣任务栏）为基准居中，窗口完整可见。
    """
    scale = dpi_scale if dpi_scale and dpi_scale > 0 else 1.0

    width = max(1, _to_logical(design_physical[0], scale))
    height = max(1, _to_logical(design_physical[1], scale))

    if work_area is None:
        return width, height, None, None

    left, top, right, bottom = work_area
    logical_w = max(1, _to_logical(right - left, scale))
    logical_h = max(1, _to_logical(bottom - top, scale))

    # 上限：不超过工作区的一定比例（屏幕装不下时不越界）
    cap_w = max(1, int(logical_w * max_ratio))
    cap_h = max(1, int(logical_h * max_ratio))

    # 下限：不低于最小可用尺寸；但工作区本身比最小尺寸还小时，以工作区为准
    floor_w = min(min_logical[0], cap_w)
    floor_h = min(min_logical[1], cap_h)

    width = min(max(width, floor_w), cap_w)
    height = min(max(height, floor_h), cap_h)

    # 居中：工作区可能不在 (0, 0)（多显示器时），因此要加上工作区自身的偏移
    x = _to_logical(left, scale) + max(0, (logical_w - width) // 2)
    y = _to_logical(top, scale) + max(0, (logical_h - height) // 2)
    return width, height, x, y


def measure_window() -> tuple[int, int, int | None, int | None]:
    """读取系统度量并算出窗口几何（``(width, height, x, y)``，单位均为逻辑像素）。"""
    _enable_dpi_awareness()
    scale = _get_dpi_scale()
    work_area = _get_work_area()
    width, height, x, y = compute_window_geometry(scale, work_area)

    if work_area is None:
        logger.warning("无法获取工作区，窗口位置交由系统居中")
    logger.info(
        "主窗口 %sx%s 逻辑像素 @ (%s, %s)；设计值 %sx%s 物理像素，系统缩放 %.2f×",
        width,
        height,
        x,
        y,
        WINDOW_SIZE_PHYSICAL[0],
        WINDOW_SIZE_PHYSICAL[1],
        scale,
    )
    return width, height, x, y


# ---------------------------------------------------------------------------
# 窗口创建
# ---------------------------------------------------------------------------


def create_window() -> webview.Window:
    """创建应用主窗口并完成 JS Bridge 注册。

    返回：
        创建好的 pywebview 窗口对象。
    """
    # 实例化 API 集合：pywebview 会把它的公开方法暴露为前端的 window.pywebview.api
    api = Api()

    # 通过 resource_path 获取前端入口，保证开发 / 打包两种环境下都能正确定位
    index_html = resource_path("index.html")

    width, height, x, y = measure_window()

    window = webview.create_window(
        title=WINDOW_TITLE,
        url=index_html.as_uri(),  # 转成 file:// URI，避免路径含中文或空格时解析异常
        js_api=api,
        width=width,
        height=height,
        # x/y 为 None 时 pywebview 退回 FormStartPosition.CenterScreen；
        # 正常路径下则用我们按工作区算好的逻辑坐标精确定位（PRD TI-32）
        x=x,
        y=y,
        min_size=WINDOW_MIN_SIZE,
        resizable=True,
        # 触摸屏优化（PRD NFR-C3）：禁用文本选择，避免触屏长按选中文字
        text_select=False,
    )

    # 把窗口引用交给 API，供后续实现"最大化 / 关闭"等窗口级操作
    api.attach_window(window)
    return window


def main() -> None:
    """程序入口：初始化日志 → 创建窗口 → 进入事件循环。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("StellarDraw v%s 启动中……", __version__)

    create_window()

    # 阻塞在此，直到用户关闭窗口
    webview.start()
    logger.info("StellarDraw 已退出")


if __name__ == "__main__":
    main()
