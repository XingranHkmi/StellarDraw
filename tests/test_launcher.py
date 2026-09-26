"""core/launcher.py 的单元测试。

对应需求：PRD F-09 / F-02c / E-04 / E-05 / §6.4 / AC-08 / AC-26

这里的测试重点不是"能不能启动进程"，而是**绝不能因为路径问题把界面搞崩**：
    · 未设置路径 → 提示去设置（E-04）
    · 路径失效   → 提示重新设置（E-05）
    · 启动异常   → 被捕获成返回值，不向上抛

因此所有涉及真实进程的调用都被替换为测试替身。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from stellardraw.core import launcher
from stellardraw.core.launcher import (
    CREATE_FLAGS,
    MSG_NOT_FOUND,
    MSG_NOT_SET,
    is_valid_exe,
    launch,
    normalize_path,
    open_directory,
)


class TestNormalizePath:
    """路径输入的规整（老师从资源管理器复制路径常带引号/空格）。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("D:/tools/a.exe", "D:/tools/a.exe"),
            ("  D:/tools/a.exe  ", "D:/tools/a.exe"),
            ('"D:/tools/a.exe"', "D:/tools/a.exe"),
            ('  "D:/Program Files/a.exe"  ', "D:/Program Files/a.exe"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize(self, raw: str | None, expected: str) -> None:
        """引号与首尾空白被剥离，None 变成空串。"""
        assert normalize_path(raw) == expected


class TestIsValidExe:
    """路径有效性判定（E-04 / E-05 的判定依据）。"""

    def test_existing_file(self, tmp_path: Path) -> None:
        """存在的文件返回 True。"""
        target = tmp_path / "tool.exe"
        target.write_bytes(b"MZ")
        assert is_valid_exe(str(target)) is True

    def test_missing_file(self, tmp_path: Path) -> None:
        """不存在的文件返回 False。"""
        assert is_valid_exe(str(tmp_path / "不存在.exe")) is False

    def test_directory_is_not_valid(self, tmp_path: Path) -> None:
        """目录不算有效的可执行文件（老师可能误选文件夹）。"""
        assert is_valid_exe(str(tmp_path)) is False

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_blank_path(self, raw: str | None) -> None:
        """空路径返回 False。"""
        assert is_valid_exe(raw) is False  # type: ignore[arg-type]


class TestLaunch:
    """启动外部程序（PRD F-09 / E-04 / E-05）。"""

    def test_blank_path_gives_setup_hint(self) -> None:
        """E-04：未设置路径时返回明确的引导提示。"""
        ok, message = launch("")
        assert ok is False
        assert message == MSG_NOT_SET

    def test_missing_file_gives_re_setup_hint(self, tmp_path: Path) -> None:
        """E-05：路径失效时提示重新设置。"""
        ok, message = launch(str(tmp_path / "已删除的工具.exe"))
        assert ok is False
        assert message == MSG_NOT_FOUND

    def test_successful_launch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """路径有效时调用 _spawn，并返回成功。"""
        target = tmp_path / "tool.exe"
        target.write_bytes(b"MZ")
        spawned: list[str] = []

        monkeypatch.setattr(launcher, "_spawn", spawned.append)
        ok, message = launch(str(target))

        assert ok is True
        assert message == ""
        assert spawned == [str(target)]

    def test_spawn_failure_is_captured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """启动异常被捕获成返回值，绝不向上抛导致界面崩溃。"""
        target = tmp_path / "tool.exe"
        target.write_bytes(b"MZ")

        def boom(_path: str) -> None:
            raise OSError("拒绝访问")

        monkeypatch.setattr(launcher, "_spawn", boom)
        ok, message = launch(str(target))

        assert ok is False
        assert MSG_NOT_FOUND in message
        assert "拒绝访问" in message

    def test_quoted_path_is_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """带引号的路径（从资源管理器复制）也能正常启动。"""
        target = tmp_path / "tool.exe"
        target.write_bytes(b"MZ")
        monkeypatch.setattr(launcher, "_spawn", lambda _path: None)
        assert launch(f'"{target}"')[0] is True

    def test_spawn_uses_detached_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_spawn 必须带上"独立进程"标志，避免子进程随主程序退出而被关掉。"""
        target = tmp_path / "tool.exe"
        target.write_bytes(b"MZ")
        captured: dict = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        launcher._spawn(str(target))

        assert captured["args"] == [str(target)]
        # 工作目录设为程序自身所在目录：绿色小工具常依赖相对路径读自己的配置
        assert captured["cwd"] == str(target.parent)
        assert captured["creationflags"] == launcher._creation_flags()

    def test_creation_flags_are_defined(self) -> None:
        """独立进程标志必须包含 DETACHED_PROCESS 与 CREATE_NEW_PROCESS_GROUP。"""
        assert CREATE_FLAGS & 0x00000008
        assert CREATE_FLAGS & 0x00000200


class TestOpenDirectory:
    """打开 data 目录（PRD F-02c / AC-26）。"""

    def test_creates_missing_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-26③：目录不存在时先自动创建再打开，不报错。"""
        opened: list[str] = []
        monkeypatch.setattr(launcher.os, "startfile", opened.append, raising=False)

        target = tmp_path / "data"
        assert target.exists() is False

        result = open_directory(target)

        assert result == target
        assert target.is_dir()
        assert opened == [str(target)]

    def test_opens_existing_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-26②：已有目录直接打开。"""
        opened: list[str] = []
        monkeypatch.setattr(launcher.os, "startfile", opened.append, raising=False)

        result = open_directory(tmp_path)

        assert result == tmp_path
        assert opened == [str(tmp_path)]

    def test_failure_is_raised_for_caller(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """系统拒绝打开时向上抛 OSError，由 api 层转成友好提示。"""

        def boom(_path: str) -> None:
            raise OSError("没有默认程序")

        monkeypatch.setattr(launcher.os, "startfile", boom, raising=False)
        with pytest.raises(OSError):
            open_directory(tmp_path)
