"""pytest 共享夹具与测试替身。

集中放置跨文件复用的 fixture，避免各测试文件重复造轮子。

关键设计：
    1. **数据目录隔离**：Storage 支持注入 base_dir，所有测试都落在
       pytest 的 tmp_path 下，绝不触碰项目真实的 data/ 目录。
    2. **随机可复现**：Drawer 支持注入带固定种子的 Random，
       让"随机抽取"的结果在测试中稳定可断言。
    3. **假窗口**：Api 依赖 pywebview 的窗口对象来弹文件选择框，
       测试里用 FakeWindow 顶替，从而覆盖导入 / 选 exe 等分支。
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from stellardraw.core.roster import Student
from stellardraw.core.storage import Storage

# 固定随机种子：同一份名单在每次测试运行中抽出相同的顺序，
# 便于断言"抽完一轮不重复"这类性质（而不是依赖运气）
SEED = 20260913


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    """每个测试独立的"伪程序目录"，其下会自动出现 data/ 子目录。"""
    return tmp_path


@pytest.fixture
def storage(base_dir: Path) -> Storage:
    """指向隔离目录的 Storage 实例。"""
    return Storage(base_dir=base_dir)


@pytest.fixture
def rng() -> random.Random:
    """带固定种子的随机数发生器。"""
    return random.Random(SEED)


@pytest.fixture
def students_factory():
    """生成 n 名学生的工厂：学号补零、姓名带序号。"""

    def _make(count: int, name_prefix: str = "学生") -> list[Student]:
        return [Student(num=f"{i:02d}", name=f"{name_prefix}{i}") for i in range(1, count + 1)]

    return _make


@pytest.fixture
def students_payload():
    """生成可直接传给 Api.save_roster 的 JSON 载荷。"""

    def _make(count: int, name_prefix: str = "学生") -> list[dict]:
        return [{"num": f"{i:02d}", "name": f"{name_prefix}{i}"} for i in range(1, count + 1)]

    return _make


class FakeWindow:
    """pywebview 窗口对象的测试替身。

    只实现 Api 用到的一个方法 ``create_file_dialog``。

    参数：
        path: 模拟用户选中的文件路径。传空字符串表示"用户点了取消"。
    """

    def __init__(self, path: str = "") -> None:
        self.path = path
        # 记录每次调用的参数，便于断言"确实按预期弹了框、传对了过滤器"
        self.calls: list[dict] = []

    def create_file_dialog(
        self,
        dialog_type=None,
        directory: str = "",
        allow_multiple: bool = False,
        save_filename: str = "",
        file_types: tuple[str, ...] = (),
    ):
        """模拟系统文件选择框：记录调用并返回预设路径。"""
        self.calls.append(
            {
                "dialog_type": dialog_type,
                "allow_multiple": allow_multiple,
                "file_types": file_types,
            }
        )
        if not self.path:
            return None
        return (self.path,)


@pytest.fixture
def fake_window_factory():
    """创建 FakeWindow 的工厂夹具。"""
    return FakeWindow


@pytest.fixture
def make_api(base_dir: Path):
    """创建 Api 实例的工厂，数据目录与随机种子都已隔离。

    用法：
        api = make_api()                      # 不带窗口
        api = make_api(window=FakeWindow(p))  # 带假窗口
    """
    from stellardraw.api import Api
    from stellardraw.core.drawer import Drawer

    created: list = []

    def _make(window=None, dedupe: bool = True):
        api = Api(
            storage=Storage(base_dir=base_dir),
            drawer=Drawer(dedupe=dedupe, rng=random.Random(SEED)),
        )
        if window is not None:
            api.attach_window(window)
        created.append(api)
        return api

    return _make


@pytest.fixture
def api(make_api):
    """一个可用的、数据已隔离的 Api 实例（无窗口）。"""
    return make_api()
