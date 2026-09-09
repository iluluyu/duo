"""duo.core.adb: the device-side media volume command.

命令串是冻结合同（真机实测定论，serial 4444bd6b/ColorOS）：
- ``media volume`` 在 OEM ROM 上不存在（/system/bin/media: inaccessible），
  主路径必须是 ``cmd media_session volume``；
- ``--get`` 输出 [V] 日志文本无可解析数字——预读彻底放弃，源码里
  不得再出现 --get/dumpsys 的读回尝试。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from duo.core import adb as adb_mod  # noqa: E402
from duo.core.adb import (  # noqa: E402
        MEDIA_VOLUME_MAX,
        clamp_media_volume,
        media_volume,
        media_volume_argv,
)
from duo.core.apps import Adb, AdbError  # noqa: E402


class _RecordingAdb(Adb):
        """Adb stand-in that records run() argv instead of shelling out."""

        def __init__(self) -> None:
                super().__init__("/fake/adb.exe", "S1")
                self.calls: list[tuple[str, ...]] = []

        def run(self, *args: str, timeout: float = 5.0) -> str:
                self.calls.append(args)
                return ""


def test_media_volume_argv_is_cmd_media_session_stream3():
        """写入命令 = cmd media_session volume --stream 3 --set N（冻结）。"""
        assert media_volume_argv(11) == (
                "shell",
                "cmd", "media_session", "volume",
                "--stream", "3",
                "--set", "11",
        )
        # OEM 定论：经典 `media volume` 不存在，不得作为主路径
        argv = " ".join(media_volume_argv(0))
        assert "media volume" not in argv
        assert argv.startswith("shell cmd media_session volume")


def test_media_volume_clamps_into_0_15():
        """任意整数钳进设备档位 0..15（QML 侧滑杆 0..15，防御越界）。"""
        assert MEDIA_VOLUME_MAX == 15
        assert clamp_media_volume(-3) == 0
        assert clamp_media_volume(0) == 0
        assert clamp_media_volume(9) == 9
        assert clamp_media_volume(99) == 15


def test_media_volume_runs_clamped_command_on_bound_serial():
        """media_volume 走绑好 serial 的 Adb.run（-s 由 Adb 注入），返回钳后值。"""
        recorder = _RecordingAdb()
        assert media_volume(recorder, 40) == 15
        assert media_volume(recorder, 7) == 7
        assert recorder.calls == [media_volume_argv(15), media_volume_argv(7)]


def test_media_volume_propagates_adb_error():
        """设备侧失败沿 AdbError 上抛（controller 的状态消息路径靠它）。"""

        class _FailingAdb(Adb):
                def run(self, *args: str, timeout: float = 5.0) -> str:
                        raise AdbError("device offline")

        with pytest.raises(AdbError):
                media_volume(_FailingAdb("/fake/adb.exe", "S1"), 7)


def test_no_readback_command_anywhere():
        """预读已放弃（真机定论）：源码不得出现 --get / dumpsys 读回。"""
        source = (adb_mod.__file__,)
        import pathlib

        text = pathlib.Path(source[0]).read_text(encoding="utf-8")
        assert '"--get"' not in text
        assert "dumpsys" not in text
