"""学生名单的数据模型与增删改查。

对应需求：PRD F-01 / F-03 / F-04 / E-07 / AC-01 / US-13

设计要点：
    1. 学生记录包含「学号」与「姓名」两个字段 —— PRD F-08 要求结果卡分两行展示。
    2. 学号允许为空：为空时结果卡只显示姓名行并垂直居中（PRD E-07）。
       但**姓名必填**（PRD §6.5 字段规则），因此本模块在写入时强制校验。
    3. **去重的判定依据是学生记录本身，而非姓名**，
       因此同名的两名学生（学号不同）会被视为不同个体（PRD §6.3 / AC-04⑤）。
    4. 本模块是纯数据结构，不读写文件、不依赖任何界面代码，便于 pytest 覆盖
       （NFR-M4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 姓名为空时的统一错误提示（界面层可直接显示）
EMPTY_NAME_MESSAGE = "姓名不能为空"


@dataclass
class Student:
    """一名学生。

    Attributes:
        num:  学号。允许为空字符串（此时界面上不显示学号行）。
        name: 姓名。必填。
    """

    num: str = ""
    name: str = ""

    # ------------------------------------------------------------------
    # 校验与派生属性
    # ------------------------------------------------------------------

    @property
    def has_num(self) -> bool:
        """是否有学号（决定结果卡要不要渲染学号行，PRD F-08 / E-07）。"""
        return bool(self.num.strip())

    @property
    def is_valid(self) -> bool:
        """是否满足最小完整性要求：姓名非空。"""
        return bool(self.name.strip())

    def normalized(self) -> Student:
        """返回去除首尾空白后的副本。

        界面上老师手工输入时很容易多打空格，统一在入口处清理，
        可以避免"张三 "与"张三"被当成两条记录。
        """
        return Student(num=self.num.strip(), name=self.name.strip())

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, str]:
        """转换为可 JSON 序列化的 dict，供 JS Bridge 与存储层使用。"""
        return {"num": self.num, "name": self.name}

    @classmethod
    def from_dict(cls, data: dict) -> Student:
        """从 dict 构造学生对象。

        对缺失字段与 None 做容错处理，并统一去除首尾空白。
        这样即便外部数据不规范（如手工编辑过的 JSON），也不会导致崩溃。
        """
        if not isinstance(data, dict):
            # 宽松处理：非 dict 的脏数据统一丢弃（返回空学生，由上层过滤）
            return cls()
        return cls(
            num=str(data.get("num") or "").strip(),
            name=str(data.get("name") or "").strip(),
        )


@dataclass
class Roster:
    """一个班级的名单。

    Attributes:
        name:     班级名称，例如 "初一(3)班"。
        students: 学生列表，列表顺序即名单原始顺序。
    """

    name: str = ""
    students: list[Student] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转换为 dict，用于写入 JSON 文件或返回给前端。"""
        return {
            "name": self.name,
            "students": [s.to_dict() for s in self.students],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Roster:
        """从 dict 构造名单对象，容错处理不规范数据。

        注意：这里会**过滤掉姓名为空的脏记录**，避免损坏的 JSON
        把无效学生带进抽取池（PRD E-08 的"不破坏可用数据"精神）。
        """
        if not isinstance(data, dict):
            return cls()
        raw_students = data.get("students") or []
        # 两步过滤：先剔除不是 dict 的脏数据，再剔除姓名为空的无效记录
        students = [Student.from_dict(item) for item in raw_students if isinstance(item, dict)]
        students = [s for s in students if s.is_valid]
        return cls(
            name=str(data.get("name") or "").strip(),
            students=students,
        )

    @classmethod
    def from_payload(cls, name: str, students: list[dict]) -> Roster:
        """从 JS Bridge 传来的 JSON 载荷构造名单。

        参数：
            name:     班级名称。
            students: 形如 [{"num": "07", "name": "张三"}, ...] 的列表。

        返回：
            Roster 对象。其中姓名为空的记录会被跳过（不报错，
            因为前端"新增一行空记录"是常见的中间状态）。

        异常：
            TypeError: students 不是列表时抛出。
        """
        if not isinstance(students, list):
            raise TypeError("students 必须是列表")
        result = cls(name=str(name).strip())
        for item in students:
            student = Student.from_dict(item)
            if student.is_valid:
                result.students.append(student.normalized())
        return result

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """返回学生人数，使 len(roster) 可直接使用。"""
        return len(self.students)

    def is_empty(self) -> bool:
        """名单是否为空（对应 PRD E-01 的空名单保护）。"""
        return not self.students

    def __iter__(self):
        """支持直接遍历名单中的学生。"""
        return iter(self.students)

    def display_labels(self) -> list[str]:
        """返回便于日志与调试的简短标签列表，如 ``["07 张三", "李雷"]``。

        仅用于日志与错误提示，不参与业务判定（学号为空的记录只显示姓名）。
        """
        return [f"{s.num} {s.name}" if s.has_num else s.name for s in self.students]

    def has_duplicate_students(self) -> bool:
        """是否存在「学号 + 姓名」完全相同的重复记录。

        仅作提示用途：PRD 未禁止重复记录，但完全相同的两条记录
        在课堂上是没有意义的（同名不同学号是允许的，见 AC-04⑤）。
        """
        seen: set[tuple[str, str]] = set()
        for student in self.students:
            key = (student.num, student.name)
            if key in seen:
                return True
            seen.add(key)
        return False

    # ------------------------------------------------------------------
    # 增删改（PRD F-01 / AC-01）
    # ------------------------------------------------------------------

    def add(self, student: Student) -> int:
        """追加一名学生，返回其下标。

        对应需求：PRD F-01 / AC-01①。

        参数：
            student: 待添加的学生。

        返回：
            新记录在列表中的下标。

        异常：
            ValueError: 姓名为空时抛出（PRD §6.5 规定姓名必填）。
        """
        normalized = student.normalized()
        if not normalized.is_valid:
            raise ValueError(EMPTY_NAME_MESSAGE)
        self.students.append(normalized)
        return len(self.students) - 1

    def extend(self, students: list[Student]) -> int:
        """批量追加学生，返回实际追加的条数。

        姓名无效的记录会被静默跳过 —— 批量粘贴时老师很容易多敲空行，
        为此报错会打断操作（对应 PRD F-01"自动过滤空行"）。
        """
        added = 0
        for student in students:
            normalized = student.normalized()
            if not normalized.is_valid:
                continue
            self.students.append(normalized)
            added += 1
        return added

    def remove_at(self, index: int) -> Student:
        """按下标删除一名学生，返回被删除的记录。

        对应需求：PRD F-01 / AC-01③。

        异常：
            IndexError: 下标越界时抛出（由上层转换为友好提示）。
        """
        if not 0 <= index < len(self.students):
            raise IndexError(f"记录下标越界：{index}（当前共 {len(self.students)} 条）")
        return self.students.pop(index)

    def update_at(self, index: int, student: Student) -> int:
        """按下标更新一名学生，返回其下标。

        对应需求：PRD F-01 / AC-01②。

        异常：
            IndexError: 下标越界时抛出。
            ValueError: 新姓名为空时抛出。
        """
        normalized = student.normalized()
        if not normalized.is_valid:
            raise ValueError(EMPTY_NAME_MESSAGE)
        if not 0 <= index < len(self.students):
            raise IndexError(f"记录下标越界：{index}（当前共 {len(self.students)} 条）")
        self.students[index] = normalized
        return index

    def clear(self) -> None:
        """清空全部学生（保留班级名称）。"""
        self.students.clear()

    def rename(self, new_name: str) -> None:
        """修改班级名称。

        异常：
            ValueError: 新名称为空时抛出。
        """
        cleaned = str(new_name).strip()
        if not cleaned:
            raise ValueError("班级名称不能为空")
        self.name = cleaned
