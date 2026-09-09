"""Device-side media volume: the one adb command the mirror card needs.

真机实测（serial 4444bd6b，ColorOS）：经典 ``media volume`` 工具在 OEM
ROM 上不存在（``/system/bin/sh: media: inaccessible``），可用的写入路径是
``cmd media_session volume``（Android 9+）——已实测能把 STREAM_MUSIC
（stream 3）按 index 0..15 设下去。

预读刻意不做：``cmd media_session volume --stream 3 --get`` 输出的是
``[V]`` 日志文本，没有可解析数字；dump 类接口按机定制更脆。面板侧
的 mediaVolume 因此初始为 -1（未知），首次拖动滑杆才进入已知态。
"""

from __future__ import annotations

from duo.core.apps import Adb

#: STREAM_MUSIC in ``cmd media_session volume``'s dialect.
MEDIA_STREAM_MUSIC = "3"

#: Android 经典媒体音量档位：index 0..15。
MEDIA_VOLUME_MAX = 15

#: 一次 ``cmd media_session`` 往返在 200ms 防抖之后（滑杆拖动），必须
#: 远快于 adb 默认的 60s 才不拖手感。
_MEDIA_VOLUME_TIMEOUT_S = 5.0


def media_volume_argv(index: int) -> tuple[str, ...]:
        """The adb args that set the device media stream volume to ``index``.

        >>> media_volume_argv(11)
        ('shell', 'cmd', 'media_session', 'volume', '--stream', '3', '--set', '11')
        """
        return (
                "shell",
                "cmd", "media_session", "volume",
                "--stream", MEDIA_STREAM_MUSIC,
                "--set", str(index),
        )


def clamp_media_volume(index: int) -> int:
        """Pin an arbitrary int into the device's 0..15 index range."""
        return max(0, min(MEDIA_VOLUME_MAX, int(index)))


def media_volume(
        adb: Adb, index: int, timeout: float = _MEDIA_VOLUME_TIMEOUT_S
) -> int:
        """Set the device's media stream volume; returns the clamped index.

        投屏音频的采集源就是设备媒体流——调这里（提示音偏小的根因），
        Windows 端增益救不了。
        """
        clamped = clamp_media_volume(index)
        adb.run(*media_volume_argv(clamped), timeout=timeout)
        return clamped
