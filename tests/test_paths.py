"""paths.py 的单元测试。

对应需求：PRD NFR-M4（核心逻辑必须有 pytest 覆盖）
          PRD 附录 B 第 10-11 条（两类路径必须严格区分）

为什么这个模块值得优先写测试：
    paths.py 是全项目路径的唯一来源。它一旦出错，
    静态资源会加载失败（界面白屏），或运行时数据被写进临时目录
    （软件一关，老师的名单就丢了）。这两类故障都很难在现场排查，
    因此必须在开发阶段就锁死行为。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stellardraw import paths


class TestDevEnvironment:
    """开发环境（未打包）下的路径行为。"""

    def test_is_not_frozen(self) -> None:
        """未打包时应报告 is_frozen() 为 False。"""
        assert paths.is_frozen() is False

    def test_project_root_exists_and_contains_pyproject(self) -> None:
        """项目根目录应当能正确定位（判别标准：存在 pyproject.toml）。"""
        root = paths.project_root()
        assert root.is_dir()
        assert (root / "pyproject.toml").is_file()

    def test_resource_path_points_into_ui_dir(self) -> None:
        """资源路径必须落在 stellardraw/ui/ 目录内。"""
        css = paths.resource_path("css", "base.css")
        assert css.parent.name == "css"
        assert css.parent.parent.name == "ui"

    @pytest.mark.parametrize(
        "relative",
        [
            ("index.html",),
            ("css", "base.css"),
            ("js", "app.js"),
            # 注意层级：静态素材位于 ui/assets/ 下（见 PRD §9.4 目录结构）
            ("assets", "img", "card_back.svg"),
            ("assets", "img", "app_icon.svg"),
            # 看板娘三态：v1.9.2 起换成流萤 Q 版 PNG 位图（400×600, 2:3）。
            # 原 SVG 占位素材保留在同目录作为设计备份，不再被引用。
            ("assets", "mascot", "mascot_idle.png"),
            ("assets", "mascot", "mascot_loading.png"),
            ("assets", "mascot", "mascot_result.png"),
        ],
    )
    def test_required_assets_exist(self, relative: tuple[str, ...]) -> None:
        """前端入口与全部占位素材必须真实存在。

        这些文件缺失会导致打包后界面白屏或看板娘不显示，
        因此在骨架阶段就用测试把它们钉住。
        """
        assert paths.resource_path(*relative).is_file()

    def test_data_dir_is_outside_src(self) -> None:
        """data/ 必须位于项目根，而不是源码目录内。"""
        created = paths.data_dir()
        # 注意：本测试会真实创建 data/ 目录，这是预期行为
        assert created.is_dir()
        assert created.parent == paths.project_root()
        assert "src" not in created.parts

    def test_data_file_parent_is_created(self) -> None:
        """data_file() 应创建父目录但不创建文件本身。"""
        target = paths.data_file("rosters", "测试班级.json")
        assert target.parent.is_dir()
        assert not target.exists()


class TestFrozenEnvironment:
    """打包环境（PyInstaller）下的路径行为。

    通过临时注入 sys._MEIPASS 来模拟打包环境。
    """

    def test_is_frozen_when_meipass_present(self, tmp_path: Path) -> None:
        """存在 sys._MEIPASS 时应报告已打包。"""
        sys._MEIPASS = str(tmp_path)  # type: ignore[attr-defined]
        try:
            assert paths.is_frozen() is True
        finally:
            del sys._MEIPASS

    def test_resource_path_follows_meipass(self, tmp_path: Path) -> None:
        """打包后资源路径应指向 _MEIPASS 下的 stellardraw/ui。"""
        sys._MEIPASS = str(tmp_path)  # type: ignore[attr-defined]
        try:
            resolved = paths.resource_path("index.html")
        finally:
            del sys._MEIPASS

        assert resolved == tmp_path / "stellardraw" / "ui" / "index.html"

    def test_data_dir_follows_executable_not_meipass(self, tmp_path: Path) -> None:
        """打包后 data/ 必须跟随 exe，**绝不能**落在 _MEIPASS 里。

        这是本文件最重要的一条测试：
        若 data/ 指向临时解包目录，程序退出时目录被清理，
        老师录入的名单与配置会全部丢失。
        """
        fake_meipass = tmp_path / "meipass"
        fake_meipass.mkdir()

        sys._MEIPASS = str(fake_meipass)  # type: ignore[attr-defined]
        try:
            resolved = paths.data_dir()
        finally:
            del sys._MEIPASS

        # 不在临时解包目录内
        assert fake_meipass not in resolved.parents
        # 而是位于 sys.executable 的同级
        assert resolved.parent == Path(sys.executable).resolve().parent
        assert resolved.name == paths.DATA_DIR_NAME
