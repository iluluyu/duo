//! 设置页视图模型（UI 逻辑与渲染分离：本模块零 egui 依赖）。
//!
//! 草稿模式：load 进来的 Settings 是工作副本，编辑置 dirty，保存走
//! duo-core `settings::save_settings`（先校验再原子写）。分组与 PyQt
//! 设置页对齐：视频编码 / 帧率 / 音频 / 上下巴模式 / 玻璃 / 主题。

use std::path::{Path, PathBuf};

use duo_core::settings::{
    load_settings, save_settings, Settings, VALID_AUDIO_POLICIES, VALID_BAR_MODES,
    VALID_THEMES, VALID_VIDEO_CODECS,
};

/// 三态控件（上下巴）与枚举下拉共用的选项值。
pub const AUDIO_CHOICES: [&str; 3] = VALID_AUDIO_POLICIES;
pub const BAR_CHOICES: [&str; 3] = VALID_BAR_MODES;
pub const THEME_CHOICES: [&str; 3] = VALID_THEMES;
pub const CODEC_CHOICES: [&str; 4] = VALID_VIDEO_CODECS;

#[derive(Debug)]
pub struct SettingsPageModel {
    /// 工作草稿（编辑只动这里）。
    pub draft: Settings,
    /// 是否有未保存修改。
    pub dirty: bool,
    /// 保存结果的一次性提示（渲染层 Toast）。
    pub flash: Option<String>,
    data_dir: Option<PathBuf>,
}

impl SettingsPageModel {
    /// 读取磁盘（永不失败；问题清单进 flash）。
    pub fn load(data_dir: Option<&Path>) -> Self {
        let (settings, problems) = load_settings(data_dir);
        Self {
            draft: settings,
            dirty: false,
            flash: if problems.is_empty() {
                None
            } else {
                Some(problems.join("; "))
            },
            data_dir: data_dir.map(Path::to_path_buf),
        }
    }

    pub fn data_dir(&self) -> Option<&Path> {
        self.data_dir.as_deref()
    }

    fn touch(&mut self) {
        self.dirty = true;
    }

    /// 保存：草稿 → duo-core save（先校验再原子替换）。
    pub fn save(&mut self) {
        match save_settings(&self.draft, self.data_dir()) {
            Ok(()) => {
                self.dirty = false;
                self.flash = Some("已保存".into());
            }
            Err(problems) => self.flash = Some(format!("未保存：{problems}")),
        }
    }

    pub fn dismiss_flash(&mut self) {
        self.flash = None;
    }

    // ------------------------------------------------ 编辑入口（渲染层调）

    pub fn set_fps(&mut self, fps: i64) {
        self.draft.fps = Some(fps.clamp(duo_core::settings::FPS_RANGE.0, duo_core::settings::FPS_RANGE.1));
        self.touch();
    }

    pub fn set_bitrate(&mut self, mbps: i64) {
        self.draft.bitrate_mbps = Some(
            mbps.clamp(
                duo_core::settings::BITRATE_RANGE.0,
                duo_core::settings::BITRATE_RANGE.1,
            ),
        );
        self.touch();
    }

    pub fn set_video_codec(&mut self, codec: &str) {
        if CODEC_CHOICES.contains(&codec) {
            self.draft.video_codec = codec.into();
            self.touch();
        }
    }

    pub fn set_audio_policy(&mut self, policy: &str) {
        if AUDIO_CHOICES.contains(&policy) {
            self.draft.audio_policy = policy.into();
            self.touch();
        }
    }

    pub fn set_bar_mode(&mut self, top: bool, mode: &str) {
        if !BAR_CHOICES.contains(&mode) {
            return;
        }
        if top {
            self.draft.top_bar_mode = mode.into();
        } else {
            self.draft.bottom_bar_mode = mode.into();
        }
        self.touch();
    }

    pub fn set_glass(&mut self, on: bool) {
        self.draft.glass_enabled = on;
        self.touch();
    }

    pub fn set_theme(&mut self, theme: &str) {
        if THEME_CHOICES.contains(&theme) {
            self.draft.theme = theme.into();
            self.touch();
        }
    }

    /// 非法枚举静默拒绝（编辑入口只收合法值，非法值进不了草稿）。
    pub fn reject_invalid_enum(&mut self, _s: &str) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "duo-panel-settings-{tag}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn edit_marks_dirty_and_save_roundtrips() {
        let dir = base("roundtrip");
        let mut model = SettingsPageModel::load(Some(&dir));
        assert!(!model.dirty);
        model.set_fps(120);
        model.set_bitrate(8);
        model.set_video_codec("h264");
        model.set_audio_policy("all");
        model.set_bar_mode(true, "none");
        model.set_glass(false);
        model.set_theme("dark");
        assert!(model.dirty);
        model.save();
        assert!(!model.dirty);
        assert_eq!(model.flash.as_deref(), Some("已保存"));

        let reloaded = SettingsPageModel::load(Some(&dir));
        assert_eq!(reloaded.draft.fps, Some(120));
        assert_eq!(reloaded.draft.bitrate_mbps, Some(8));
        assert_eq!(reloaded.draft.video_codec, "h264");
        assert_eq!(reloaded.draft.audio_policy, "all");
        assert_eq!(reloaded.draft.top_bar_mode, "none");
        assert!(!reloaded.draft.glass_enabled);
        assert_eq!(reloaded.draft.theme, "dark");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn invalid_enum_edits_are_rejected() {
        let mut model = SettingsPageModel::load(None);
        let before = model.draft.clone();
        model.set_video_codec("mpeg2");
        model.set_theme("solarized");
        model.set_audio_policy("loud");
        model.set_bar_mode(true, "floating");
        assert!(!model.dirty);
        assert_eq!(model.draft, before);
    }

    #[test]
    fn numeric_edits_clamp_into_range() {
        let mut model = SettingsPageModel::load(None);
        model.set_fps(9999);
        assert_eq!(model.draft.fps, Some(240));
        model.set_fps(0);
        assert_eq!(model.draft.fps, Some(1));
        model.set_bitrate(-5);
        assert_eq!(model.draft.bitrate_mbps, Some(1));
    }

    #[test]
    fn corrupt_file_reports_problem_in_flash() {
        let dir = base("corrupt");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("settings.json"), "{oops").unwrap();
        let model = SettingsPageModel::load(Some(&dir));
        assert!(model.flash.is_some());
        assert_eq!(model.draft, Settings::default());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
