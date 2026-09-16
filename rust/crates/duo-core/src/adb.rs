//! 设备媒体音量：mirror 卡需要的唯一一条 adb 命令。对译自 duo/core/adb.py
//! （纯逻辑部分）；合同镜像 tests/test_adb.py。
//!
//! 真机实测（serial 4444bd6b，ColorOS）：经典 ``media volume`` 工具在 OEM
//! ROM 上不存在（/system/bin/media: inaccessible），唯一可用写入路径是
//! ``cmd media_session volume``（Android 9+）。预读刻意放弃：
//! ``--get`` 输出 ``[V]`` 日志文本无可解析数字，源码不得出现 --get/dumpsys
//! 的读回尝试。进程层 spawn（5s 超时合同）住在 bin 的 volume 子命令。

/// ``cmd media_session volume`` 方言里的 STREAM_MUSIC。
pub const MEDIA_STREAM_MUSIC: &str = "3";

/// Android 经典媒体音量档位：index 0..15。
pub const MEDIA_VOLUME_MAX: i64 = 15;

/// 一次 ``cmd media_session`` 往返必须远快于 adb 默认 60s（200ms 防抖后的
/// 滑杆拖动才不拖手感）；bin 的 volume 子命令用同一预算。
pub const MEDIA_VOLUME_TIMEOUT_S: f64 = 5.0;

/// 把设备媒体流音量设为 ``index`` 的 adb 参数（不含 binary 与 ``-s serial``，
/// 后者由 Adb 包装层注入，见 media_volume_process_argv）。
///
/// 命令串是冻结合同：``cmd media_session volume --stream 3 --set N``。
pub fn media_volume_argv(index: i64) -> Vec<String> {
    vec![
        "shell".into(),
        "cmd".into(),
        "media_session".into(),
        "volume".into(),
        "--stream".into(),
        MEDIA_STREAM_MUSIC.into(),
        "--set".into(),
        index.to_string(),
    ]
}

/// 任意整数钳进设备的 0..15 档位（QML 侧滑杆 0..15，防御越界）。
pub fn clamp_media_volume(index: i64) -> i64 {
    index.clamp(0, MEDIA_VOLUME_MAX)
}

/// 完整进程 argv：``[adb, "-s", serial, *media_volume_argv(clamped)]``
/// （对译 Adb.run 的 ``-s`` 注入 + media_volume 的先钳后发语义）。
pub fn media_volume_process_argv(adb: &str, serial: &str, index: i64) -> Vec<String> {
    let mut argv = vec![adb.to_string(), "-s".into(), serial.to_string()];
    argv.extend(media_volume_argv(clamp_media_volume(index)));
    argv
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn media_volume_argv_is_cmd_media_session_stream3() {
        // 写入命令 = cmd media_session volume --stream 3 --set N（冻结）。
        assert_eq!(
            media_volume_argv(11),
            vec![
                "shell",
                "cmd",
                "media_session",
                "volume",
                "--stream",
                "3",
                "--set",
                "11",
            ]
        );
        // OEM 定论：经典 `media volume` 不存在，不得作为主路径。
        let argv = media_volume_argv(0).join(" ");
        assert!(!argv.contains("media volume"));
        assert!(argv.starts_with("shell cmd media_session volume"));
    }

    #[test]
    fn media_volume_clamps_into_0_15() {
        // 任意整数钳进设备档位 0..15。
        assert_eq!(MEDIA_VOLUME_MAX, 15);
        assert_eq!(clamp_media_volume(-3), 0);
        assert_eq!(clamp_media_volume(0), 0);
        assert_eq!(clamp_media_volume(9), 9);
        assert_eq!(clamp_media_volume(99), 15);
    }

    #[test]
    fn process_argv_binds_serial_and_clamps() {
        // media_volume 走绑好 serial 的 adb（-s 由包装层注入），先钳后发。
        assert_eq!(
            media_volume_process_argv("/fake/adb.exe", "S1", 40),
            [
                "/fake/adb.exe",
                "-s",
                "S1",
                "shell",
                "cmd",
                "media_session",
                "volume",
                "--stream",
                "3",
                "--set",
                "15",
            ]
        );
        assert_eq!(
            media_volume_process_argv("/fake/adb.exe", "S1", 7)[3..],
            media_volume_argv(7)[..]
        );
    }

    #[test]
    fn no_readback_command_anywhere() {
        // 预读已放弃（真机定论）：argv 不得出现 --get / dumpsys 读回。
        for index in 0..=MEDIA_VOLUME_MAX {
            let argv = media_volume_argv(index);
            assert!(!argv.iter().any(|a| a == "--get"));
            assert!(!argv.iter().any(|a| a.contains("dumpsys")));
        }
    }
}
