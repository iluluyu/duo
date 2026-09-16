//! 文件系统落点：所有运行时产物（日志/APK/图标缓存/工具）统一住在单个
//! 数据目录下，便于检查与整体清除。对译自 duo/core/paths.py。

use std::path::PathBuf;

const BASE_NAME: &str = "duo";

/// Python duo.core.paths.data_dir 的对译：Windows 用 %USERPROFILE%，否则
/// $HOME；两者都取不到时退回当前目录。恒创建目录。
pub fn home_data_dir() -> PathBuf {
    let key = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    let base = std::env::var_os(key)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let dir = base.join(".local").join("share").join(BASE_NAME);
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// base 显式给定时用 base（bin 的 --data-dir 语义），否则 home_data_dir()。
pub fn data_dir(base: Option<&std::path::Path>) -> PathBuf {
    match base {
        Some(b) => {
            let _ = std::fs::create_dir_all(b);
            b.to_path_buf()
        }
        None => home_data_dir(),
    }
}

/// data_dir()/logs，恒创建。
pub fn logs_dir(base: Option<&std::path::Path>) -> PathBuf {
    let dir = data_dir(base).join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// data_dir()/icons，恒创建（设备渲染图标缓存 + device_meta.json）。
pub fn icons_dir(base: Option<&std::path::Path>) -> PathBuf {
    let dir = data_dir(base).join("icons");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn default_dirs_live_under_local_share_duo() {
        assert!(home_data_dir().ends_with(Path::new(".local/share/duo")));
        let d = data_dir(None);
        assert!(d.ends_with(Path::new(".local/share/duo")));
        assert!(d.is_dir());
    }

    #[test]
    fn explicit_base_is_respected_and_created() {
        let base = std::env::temp_dir().join(format!("duo-core-paths-base-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(data_dir(Some(&base)), base);
        assert!(base.is_dir());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn logs_dir_lives_under_data_dir() {
        let base = std::env::temp_dir().join(format!("duo-core-paths-logs-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let logs = logs_dir(Some(&base));
        assert_eq!(logs, base.join("logs"));
        assert!(logs.is_dir());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn icons_dir_lives_under_data_dir() {
        let base =
            std::env::temp_dir().join(format!("duo-core-paths-icons-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let icons = icons_dir(Some(&base));
        assert_eq!(icons, base.join("icons"));
        assert!(icons.is_dir());
        let _ = std::fs::remove_dir_all(&base);
    }
}
