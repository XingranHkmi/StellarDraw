"""特殊效果状态机（PRD §5 P2 — 替换 F-18 后新增的三个效果）。

需求：
    F-17a「增大概率」—— 选中若干学生后，他们在后续抽取中的权重被临时提升；
                  直到老师主动关闭（不在 config.json 落盘，进程退出即清空）；
                  可同时加权多名学生。
    F-17b「结果漂移」—— 下一次抽卡时，把抽到的学生按「抽取索引 + 偏移值」循环
                  替换；偏移值取自 2 档（轻移 +1 / 重移 +3），老师选择；仅生效一次。
    F-17c「课堂叫醒服务」—— 老师从 3 档音量（90%/60%/40%）中选一档并"确认使用"，
                  该档仅影响 **bingo 五连音** 的相对响度（不触碰 Windows 系统音量）；
                  选大 → bingo 播得响；选小 → bingo 播得轻；下次抽卡完成后播 bingo；
                  也是单次生效。

设计原则：
    1. 纯内存状态机 —— 不写盘，不进 config.json。重启即清空，符合「一节课内一次性
       工具」的语义。
    2. 接口层（api.py）只暴露 OK 信封与必要的纯数据；本模块不依赖任何界面代码。
    3. 所有副作用（播放 bingo）由调用方注入，本模块不直接做事。
    4. 所有方法对非法输入都做收敛，不会抛异常（接口纪律：永不抛异常）。

变更记录：
    2026-09-13 第六轮：F-17c 不再调系统音量（移除 system_volume.py / comtypes 依赖），
    仅作为 bingo 音量乘数传给前端。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# F-17a：每个加权学生在候选池中多出现的次数。
# 该值是「体感」量级，不追求严格的概率论精确 —— 用户原话"不要求精确概率值"。
# 加权学生在候选池中出现 (1 + BOOST_WEIGHT) 次，未加权学生出现 1 次。
# 当前设定 2 → 加权学生综合被抽中的概率 ≈ 3 倍（2026-09-13 第二轮反馈：4 倍太强）。
BOOST_WEIGHT = 2

# F-17b：两档偏移
DRIFT_OFFSET_LIGHT = 1
DRIFT_OFFSET_HEAVY = 3
DRIFT_OFFSETS: tuple[int, ...] = (DRIFT_OFFSET_LIGHT, DRIFT_OFFSET_HEAVY)

# F-17c：bingo 音量的三档（直接作为音量乘数使用，0.4/0.6/0.9）
# 选「大」(0.9) 时 bingo 用 90% 响度播；选「小」(0.4) 用 40% 响度。
WAKE_UP_VOLUME_LABELS: tuple[tuple[str, float], ...] = (
    ("大", 0.9),
    ("中", 0.6),
    ("小", 0.4),
)


def _student_key(num: str, name: str) -> tuple[str, str]:
    """学生记录的统一标识（去前后空格）。"""
    return (str(num or "").strip(), str(name or "").strip())


@dataclass
class SpecialEffectsState:
    """三个特殊效果的进程内状态。

    注意：本类**不负责校验音量值**是否在允许档位内 —— 那是接口层的工作。
    这里只保证状态机本身不出错。

    Attributes:
        boost: 加权学生集合，键为 (num, name)，值固定为 BOOST_WEIGHT。
               用 dict 而不是 set 是为了方便序列化与将来扩展「每个学生不同权重」。
        drift_offset: 当前生效的漂移偏移量；None 表示未武装。
        wake_up_volume: 当前待确认的音量档；None 表示尚未选档。
        wake_up_armed: 是否已"确认使用"，下一次抽卡完成后将播 bingo。
    """

    boost: dict[tuple[str, str], int] = field(default_factory=dict)
    drift_offset: int | None = None
    wake_up_volume: float | None = None
    wake_up_armed: bool = False

    # ------------------------------------------------------------------
    # F-17a 增大概率
    # ------------------------------------------------------------------

    def set_boost(self, students: list[dict]) -> None:
        """整体替换加权名单（多选用法）。

        参数：
            students: [{"num": "07", "name": "张三"}, ...]，
                      姓名空白的记录会被自动忽略。
        """
        cleaned: dict[tuple[str, str], int] = {}
        for item in students or []:
            if not isinstance(item, dict):
                continue
            num = str(item.get("num") or "").strip()
            name = str(item.get("name") or "").strip()
            if not name:
                continue  # 姓名为空跳过（避免把"无名"学生加进加权集合）
            cleaned[(num, name)] = BOOST_WEIGHT
        self.boost = cleaned

    def clear_boost(self) -> None:
        """清空全部加权。"""
        self.boost = {}

    def get_boost_list(self) -> list[dict]:
        """返回加权名单的可序列化形式（按姓名升序，方便 UI 稳定展示）。"""
        return [
            {"num": num, "name": name}
            for (num, name) in sorted(self.boost.keys(), key=lambda k: (k[1], k[0]))
        ]

    # ------------------------------------------------------------------
    # F-17b 结果漂移
    # ------------------------------------------------------------------

    def arm_drift(self, offset: int) -> bool:
        """武装漂移偏移。返回 True 表示成功；非法偏移返回 False。"""
        if offset not in DRIFT_OFFSETS:
            return False
        self.drift_offset = int(offset)
        return True

    def cancel_drift(self) -> None:
        """取消漂移（老师主动放弃）。"""
        self.drift_offset = None

    def peek_drift(self) -> int | None:
        """读取当前漂移偏移，但**不消费**。

        用途：draw 时让前端拿到本轮按 offset 的提示文案，**不消费 armed**；
        真正的"按偏移替换展示的学生"由 reveal 阶段单独申请
        （apply_drift_reveal），那里会一次性消费。
        """
        return self.drift_offset

    def consume_drift(self) -> int | None:
        """读取并清空漂移偏移（一次性的关键方法）。返回旧值。

        调用时机：旧版本由 api.draw() 在抽签时消费，导致"按池子偏移"完全
        在前端看不到效果（视觉上跟没漂一样）。新版由 reveal 时调用，
        让"翻到的是另一个人"在视觉上与翻牌动作绑定（PRD 2026-09-13 第三轮）。
        """
        old = self.drift_offset
        self.drift_offset = None
        return old

    # ------------------------------------------------------------------
    # F-17c 课堂叫醒服务
    # ------------------------------------------------------------------

    def set_wake_up_volume(self, volume: float) -> bool:
        """设置待确认的音量档。合法的三档之一才会被接受。

        类型必须是数字（不接受字符串 / 列表等）；档位必须在 0.4 / 0.6 / 0.9 之一。
        这里**不调用**系统音量接口 —— 那是 api 层的工作（side effect）。
        本方法只更新内部状态。
        """
        # 严格的类型校验：拒绝 bool、str、列表等
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            return False
        # 用「在允许档位 0.01 内」判断以容忍浮点误差
        if not any(abs(float(volume) - v) < 1e-2 for v in (0.4, 0.6, 0.9)):
            return False
        self.wake_up_volume = float(volume)
        # 选档时把 armed 清掉，避免「选了一档没确认、又换一档确认」的状态串台
        self.wake_up_armed = False
        return True

    def cancel_wake_up(self) -> None:
        """取消待确认或已确认的叫醒服务。"""
        self.wake_up_volume = None
        self.wake_up_armed = False

    def confirm_wake_up(self) -> bool:
        """把当前音量档标记为"已确认"。

        返回 True 表示确实进入了 armed 状态；返回 False 表示尚未选档。
        """
        if self.wake_up_volume is None:
            return False
        self.wake_up_armed = True
        return True

    def consume_wake_up(self) -> bool:
        """读取并清空叫醒 armed 标志（一次性）。返回旧值。"""
        old = self.wake_up_armed
        self.wake_up_armed = False
        # 注意：这里不清 wake_up_volume —— 老师可能想再用一次同样音量，
        # 状态机的视角看，「armed」是一次性的，volume 档位是用户偏好。
        return old

    # ------------------------------------------------------------------
    # 跨效果管理
    # ------------------------------------------------------------------

    def reset_for_roster_switch(self) -> None:
        """切换班级时清理所有一次性效果；加权名单因为按 (num, name) 标识，新班级里
        同名同号的学生可能根本不存在，索性一起清掉，避免加权指向错误对象。"""
        self.boost.clear()
        self.drift_offset = None
        self.wake_up_volume = None
        self.wake_up_armed = False

    def to_dict(self) -> dict:
        """把状态机序列化成前端需要的字典。"""
        return {
            "boost": self.get_boost_list(),
            "drift_armed": self.drift_offset is not None,
            "drift_offset": self.drift_offset,
            "wake_up_volume": self.wake_up_volume,
            "wake_up_armed": self.wake_up_armed,
            "wake_up_options": [
                {"label": label, "level": level} for (label, level) in WAKE_UP_VOLUME_LABELS
            ],
            "drift_options": [
                {"label": "轻移", "offset": DRIFT_OFFSET_LIGHT},
                {"label": "重移", "offset": DRIFT_OFFSET_HEAVY},
            ],
        }
