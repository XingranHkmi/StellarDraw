"""抽取引擎。

对应需求：PRD F-05 / F-06 / F-07 / F-17a / F-17b / E-01 / E-02 / E-03 / §6.3 / AC-04 / AC-05 / AC-06

核心概念 —— 候选池（candidate pool）：
    去重模式开启时，候选池 = 全班名单中「本轮尚未被抽中」的学生。
    每次抽取从池中随机取出并移出；池为空时自动重置为全班。

去重规则（PRD §6.3）：

    ┌───────────────────────┬────────────────────────────────────────────┐
    │ 开关 = 开，池非空      │ 从池中随机抽取，抽中者移出池                 │
    │ 开关 = 开，池为空      │ 自动重置池 = 全班，提示"新一轮开始"，再抽     │
    │ 开关 = 开，抽 5 人     │ 一次性抽 5 个不重复；池不足 5 人则抽出剩余全部 │
    │ 开关 = 关             │ 每次从全班独立随机，允许重复                  │
    │ 运行时切换开关         │ 切到"开"时重建候选池，避免残留脏状态          │
    │ 切换班级              │ 清空候选池并按新班级重建                     │
    └───────────────────────┴────────────────────────────────────────────┘

特殊效果叠加（P2）：
    · F-17a 增大概率：被加权的学生在候选池中多出现 BOOST_WEIGHT 次。
      实现：直接复制条目到池中，采样后只删除命中那一条而非全部，
            剩余条目继续抬高后续抽中的概率。
    · F-17b 结果漂移：抽取完毕后，把抽中的学生按下标整体平移（取模循环），
      偏移量由调用方在调用 draw() 时一次性传入；draw() 内部不读这个状态，
      因此漂移是无状态 API —— 由 api 层在每次抽取前从状态机里 consume。

注意：
    1. 本模块是纯逻辑，不持有窗口或界面状态，便于 pytest 覆盖（NFR-M4）。
    2. 去重的判定依据是**学生记录本身**：同名不同学号的两人分别参与抽取
       （PRD §6.3 / AC-04⑤）。因此内部用**下标**而不是值来移出候选池，
       避免同名记录被误判成同一个。
    3. 随机数发生器可注入，便于测试复现（也便于将来做"可复现的随机"）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from stellardraw.core.roster import Student

# 界面上的两个抽取档位（PRD 第三轮确认：不做自定义 N 次）
DRAW_ONCE = 1
DRAW_FIVE = 5

# 允许的抽取人数上限，用于防御性校验（防止前端传入异常值拖垮界面）
MAX_DRAW_COUNT = DRAW_FIVE


@dataclass(frozen=True)
class DrawOutcome:
    """一次抽取的结果及其附带状态。

    Attributes:
        students:  抽中的学生列表。长度可能小于 requested（PRD E-02）。
        requested: 本次请求的人数。
        reset:     本次抽取前是否发生过"候选池自动重置"
                   （决定界面要不要提示"全班已抽完，新一轮开始"，PRD E-03）。

    注意：
        旧版本还包含 `drifted` / `drift_offset` 字段，由 draw() 阶段就应用漂移
        并把偏移后的学生填进 students。2026-09-13 第三轮反馈该实现"无实际意义"
        —— 前端拿到的已是偏移后的，看不到翻牌时的"漂"感。
        新版：draw() 只产出真实随机结果；漂移偏移单独由 api.apply_drift_reveal()
        在翻牌那一瞬间消费 + 替换，所以这两个字段从 DrawOutcome 中移除。
    """

    students: list[Student] = field(default_factory=list)
    requested: int = DRAW_ONCE
    reset: bool = False

    @property
    def count(self) -> int:
        """实际抽中的人数。"""
        return len(self.students)

    @property
    def is_short(self) -> bool:
        """是否"人数不足"（实际抽中数少于请求数，对应 PRD E-02 的提示）。"""
        return self.count < self.requested


class Drawer:
    """抽取引擎。

    Attributes:
        dedupe: 是否开启去重模式（一轮内不重复）。
        boost:  加权学生集合（按 (num, name) 标识），决定池中的额外条目数。
    """

    def __init__(
        self,
        students: list[Student] | None = None,
        dedupe: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        """初始化抽取引擎。

        参数：
            students: 全班名单。可为空，后续用 set_students() 设置。
            dedupe:   是否开启去重模式，默认开启。
            rng:      可选的随机数发生器（测试时注入固定种子即可复现结果）。
        """
        self._students: list[Student] = []
        self._dedupe: bool = bool(dedupe)
        self._pool: list[Student] = []
        self._boost: set[tuple[str, str]] = set()
        self._rng = rng if rng is not None else random.Random()
        self.set_students(students or [])

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------

    def set_students(self, students: list[Student]) -> None:
        """设置全班名单，并重建候选池。

        对应需求：PRD F-03（切换班级时调用）/ §6.3。

        这里保存的是学生对象的**浅拷贝列表**：学生是不可变值对象，
        共享实例安全；但列表本身必须拷贝，否则外部增删会直接影响抽取。
        """
        self._students = list(students)
        self.reset_pool()

    @property
    def dedupe(self) -> bool:
        """当前是否开启去重模式。"""
        return self._dedupe

    def set_dedupe(self, enabled: bool) -> None:
        """切换去重模式。

        对应需求：PRD F-05 / §6.3。

        规则：**只在「关 → 开」的那一次切换时重建候选池**。
        原因是切到"开"意味着开启新一轮去重，此时遗留的池是脏状态；
        而把它反复设为 True 不应该重置已经进行到一半的进度。
        """
        enabled = bool(enabled)
        if enabled and not self._dedupe:
            self.reset_pool()
        elif not enabled:
            # 关闭去重时让候选池回到"全班"状态，
            # 这样 remaining 的语义（去重关闭时等于全班人数）始终成立
            self.reset_pool()
        self._dedupe = enabled

    @property
    def remaining(self) -> int:
        """候选池中还剩多少人（去重关闭时等于全班人数）。

        注：加权学生的池条目数多于 1，因此本字段**不反映加权后的剩余抽取次数**。
        调用方若需要"再抽多少次能抽完"，应另算 unique_count(pool)。
        """
        return len(self._pool)

    @property
    def total(self) -> int:
        """全班人数。"""
        return len(self._students)

    @property
    def is_empty(self) -> bool:
        """全班名单是否为空（对应 PRD E-01 的空名单保护）。"""
        return not self._students

    def set_boost(self, boosted: list[Student] | set[tuple[str, str]]) -> None:
        """设置 F-17a 加权名单。

        参数：
            boosted: 可以是 Student 列表（取 (num, name)）或直接传 (num, name) 元组的集合。
                     姓名为空或不在现有名单里的记录会被静默忽略。
        """
        fresh: set[tuple[str, str]] = set()
        known = {(s.num, s.name) for s in self._students}
        for item in boosted or []:
            if isinstance(item, Student):
                key = (item.num, item.name)
            else:
                key = (str(item[0] or "").strip(), str(item[1] or "").strip())
            if not key[1] or key not in known:
                continue
            fresh.add(key)
        if fresh != self._boost:
            self._boost = fresh
            # 加权集合变了就要重建池（否则新加权的不会生效）。
            # 这里 BOOST_WEIGHT 表示"额外多出的条目数" —— 加权学生在池中出现
            # (1 + BOOST_WEIGHT) 次，未加权学生出现 1 次。比值 BOOST_WEIGHT 倍。
            self.reset_pool()

    def clear_boost(self) -> None:
        """清空加权集合并重建候选池。"""
        if self._boost:
            self._boost.clear()
            self.reset_pool()

    def reset_pool(self) -> None:
        """把候选池重置为全班名单（按加权集合插入额外条目）。

        对应需求：PRD F-05 / E-03（本轮抽完自动重置）/ F-17a（加权生效）。
        """
        # 避免 import 循环 —— 在文件顶部之外再引一次
        from stellardraw.core.special_effects import BOOST_WEIGHT

        pool: list[Student] = []
        for s in self._students:
            entries = 1 + BOOST_WEIGHT if (s.num, s.name) in self._boost else 1
            pool.extend([s] * entries)
        self._pool = pool

    # ------------------------------------------------------------------
    # 抽取
    # ------------------------------------------------------------------

    def draw(self, count: int = DRAW_ONCE) -> DrawOutcome:
        """抽取指定数量的学生。

        对应需求：PRD F-06（count=1）/ F-07（count=5）/ E-02 / E-03。

        参数：
            count: 抽取人数，界面上只有 1 和 5 两个取值。

        返回：
            DrawOutcome。其中 students 的长度可能小于 count
            （见 PRD E-02：候选池不足时只抽出剩余全部）。

        异常：
            ValueError: 抽取人数非法（< 1 或 > MAX_DRAW_COUNT）。
            ValueError: 全班名单为空时抛出（对应 PRD E-01 空名单保护，
                        由上层捕获后提示"请先添加学生名单"）。

        注意（旧版语义对比）：
            旧版接受 drift_offset，由后端在抽签时按下标循环偏移。
            发现该实现"无实际意义"——前端拿到的 res.students 已是偏移后的，
            翻牌那一瞬间看不到任何"漂"的感觉（PRD 2026-09-13 第三轮）。
            新版 draw() 不再负责漂移；真正的偏移改在 reveal 阶段由
            Api.apply_drift_reveal() 独立处理。
        """
        if count < 1:
            raise ValueError(f"抽取人数必须大于 0，收到 {count}")
        if count > MAX_DRAW_COUNT:
            raise ValueError(f"单次最多抽取 {MAX_DRAW_COUNT} 人，收到 {count}")

        if not self._students:
            # PRD E-01：空名单保护。此处只负责报错，提示文案由 api 层组装。
            raise ValueError("名单为空，无法抽取")

        if not self._dedupe:
            # 关闭去重：每次从加权独立池随机抽，且**不动候选池**。
            # 加权池：每个加权学生多出现 BOOST_WEIGHT 次（与 reset_pool 一致）。
            from stellardraw.core.special_effects import BOOST_WEIGHT

            weighted: list[Student] = []
            for s in self._students:
                entries = BOOST_WEIGHT if (s.num, s.name) in self._boost else 1
                weighted.extend([s] * entries)
            picks = [self._rng.choice(weighted) for _ in range(count)]
            return DrawOutcome(
                students=picks,
                requested=count,
                reset=False,
            )

        reset = False
        if not self._pool:
            # PRD E-03：本轮已抽完 → 自动开启新一轮
            self.reset_pool()
            reset = True

        # 按下标抽样，保证"同名不同学号"被当作两个独立个体（AC-04⑤）
        take = min(count, len(self._pool))
        indexes = self._rng.sample(range(len(self._pool)), take)
        picks = [self._pool[i] for i in indexes]
        # 从后往前删，避免删除时下标偏移
        for index in sorted(indexes, reverse=True):
            self._pool.pop(index)

        return DrawOutcome(
            students=picks,
            requested=count,
            reset=reset,
        )
