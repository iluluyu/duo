//! 视频编码器发现与选择（settings ``video_codec``）。对译自
//! duo/core/codec.py；合同镜像 tests/test_codec.py。
//!
//! 探测管线：对设备跑一次 ``<binary> --serial=<s> --list-encoders``，结果
//! 缓存进 ``data_dir/encoders.json``（serial + 时间戳 + TTL），选择优先级
//! h264 硬件 > h265 硬件 > av1 硬件 > h264 软件 > h264（scrcpy 默认）。
//! h264-hw 刻意居首：PC 侧软解 AVC 远轻于 HEVC（真机回归：h265 播放卡顿）。
//! 探测失败一律降级为无 pin 的 h264——镜像必须还能启动，绝不硬错误。

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Serialize;

use crate::paths;
use crate::quiet::quiet_command;

/// 缓存超龄即重探（对译 ENCODERS_TTL_S：编码器只随系统更新变化，一周自愈）。
pub const ENCODERS_TTL_S: f64 = 7.0 * 24.0 * 3600.0;

/// 一次 ``--list-encoders`` 的超时（先推 server 到设备，USB 实测 ~2s）。
pub const PROBE_TIMEOUT_S: f64 = 20.0;

/// auto 优先级（对译 _AUTO_PRIORITY）：最好在前，最后是 h264 软件兜底档。
const AUTO_PRIORITY: [(&str, bool); 4] = [
    ("h264", true),
    ("h265", true),
    ("av1", true),
    ("h264", false),
];

/// 一个设备视频编码器（``--list-encoders`` 报告的一项）。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct EncoderInfo {
    /// h264 / h265 / av1 / vp8 / vp9
    pub codec: String,
    /// MediaCodec 组件名，如 c2.qti.hevc.encoder
    pub name: String,
    pub hardware: bool,
}

/// 一次会话的编码器决策；encoder=None 表示不 pin（scrcpy 自选默认），
/// hardware=None 表示未知（无可用探测数据），note 为 CLI 打印的一行理由。
#[derive(Debug, Clone, PartialEq)]
pub struct CodecChoice {
    pub codec: String,
    pub encoder: Option<String>,
    pub hardware: Option<bool>,
    pub note: String,
}

impl CodecChoice {
    pub fn new(
        codec: &str,
        encoder: Option<&str>,
        hardware: Option<bool>,
        note: impl Into<String>,
    ) -> Self {
        Self {
            codec: codec.into(),
            encoder: encoder.map(Into::into),
            hardware,
            note: note.into(),
        }
    }
}

// ---------------------------------------------------------------- 解析（纯）

/// 解析 ``--list-encoders`` 文本。别名行（``(alias for ...)``）跳过：同一
/// MediaCodec 组件已列出，pin 别名冗余；audio 行用 --audio-codec 永不匹配。
pub fn parse_encoders(output: &str) -> Vec<EncoderInfo> {
    let mut encoders = Vec::new();
    for line in output.lines() {
        if line.contains("(alias") {
            continue;
        }
        if let Some((codec, name)) = parse_encoder_line(line) {
            encoders.push(EncoderInfo {
                codec,
                name,
                hardware: line.contains("(hw)"),
            });
        }
    }
    encoders
}

/// 行内匹配 ``--video-codec=<\S+> --video-encoder=<\S+>``（对译
/// _ENCODER_LINE_RE.search：从每个 --video-codec= 出现处尝试，失配换下一处）。
fn parse_encoder_line(line: &str) -> Option<(String, String)> {
    const CODEC_TAG: &str = "--video-codec=";
    const ENCODER_PREFIX: &str = " --video-encoder=";
    for (start, _) in line.match_indices(CODEC_TAG) {
        let rest = &line[start + CODEC_TAG.len()..];
        let Some(gap) = rest.find(char::is_whitespace) else {
            continue;
        };
        let codec = &rest[..gap];
        let Some(payload) = rest[gap..].strip_prefix(ENCODER_PREFIX) else {
            continue;
        };
        if codec.is_empty() {
            continue;
        }
        let end = payload.find(char::is_whitespace).unwrap_or(payload.len());
        if end == 0 {
            continue;
        }
        return Some((codec.to_string(), payload[..end].to_string()));
    }
    None
}

// -------------------------------------------------- 缓存 data_dir/encoders.json

/// 缓存落点：``data_dir/encoders.json``（base 显式给定时为 bin 的 --data-dir）。
pub fn encoders_cache_path(base: Option<&Path>) -> PathBuf {
    paths::data_dir(base).join("encoders.json")
}

/// 读缓存：缺失/损坏/过期/serial 不符 → None。serial 不符绝不读别机缓存：
/// 编码器组件是机型专属（c2.qti.* vs c2.mtk.*），串号命中会 pin 不存在的
/// 编码器导致 scrcpy 启不来。
pub fn load_cached_encoders(
    path: &Path,
    serial: &str,
    now: Option<f64>,
) -> Option<Vec<EncoderInfo>> {
    let raw = fs::read_to_string(path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let probed_at = data.get("probed_at")?.as_f64()?;
    if data.get("serial")?.as_str()? != serial {
        return None;
    }
    let timestamp = now.unwrap_or_else(unix_now);
    if timestamp - probed_at > ENCODERS_TTL_S {
        return None;
    }
    let mut encoders = Vec::new();
    for entry in data.get("encoders")?.as_array()? {
        let obj = entry.as_object()?;
        encoders.push(EncoderInfo {
            codec: obj.get("codec")?.as_str()?.to_string(),
            name: obj.get("name")?.as_str()?.to_string(),
            hardware: py_bool(obj.get("hardware")?)?,
        });
    }
    (!encoders.is_empty()).then_some(encoders)
}

/// 落盘一次探测结果（时间戳 + serial；schema 与 Python 版逐字段相同）。
pub fn save_encoders_cache(
    path: &Path,
    serial: &str,
    encoders: &[EncoderInfo],
    now: Option<f64>,
) -> std::io::Result<()> {
    #[derive(Serialize)]
    struct Payload<'a> {
        serial: &'a str,
        probed_at: f64,
        encoders: &'a [EncoderInfo],
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let payload = Payload {
        serial,
        probed_at: now.unwrap_or_else(unix_now),
        encoders,
    };
    fs::write(
        path,
        serde_json::to_string_pretty(&payload).expect("payload serializes"),
    )
}

/// Python ``bool()`` 真值语义（缓存条目 hardware 字段的容错读取）。
fn py_bool(value: &serde_json::Value) -> Option<bool> {
    match value {
        serde_json::Value::Null => Some(false),
        serde_json::Value::Bool(b) => Some(*b),
        serde_json::Value::Number(n) => Some(n.as_f64() != Some(0.0)),
        serde_json::Value::String(s) => Some(!s.is_empty()),
        serde_json::Value::Array(a) => Some(!a.is_empty()),
        serde_json::Value::Object(o) => Some(!o.is_empty()),
    }
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// ------------------------------------------------------ 探测（唯一副作用步）

/// 跑一次 ``<scrcpy_path> --serial=<serial> --list-encoders``。stdout 与
/// stderr 拼接后解析；None = 探测失败（二进制缺失/设备不在/超时）或无可用
/// 条目——调用方降级为不 pin 的 h264。
pub fn probe_encoders(scrcpy_path: &str, serial: &str, timeout_s: f64) -> Option<Vec<EncoderInfo>> {
    // spawn 重试：高负载下偶发 EAGAIN/ENOMEM，重试 3 次（间隔 100ms）。
    let mut child = None;
    for attempt in 0..3 {
        match quiet_command(scrcpy_path)
            .arg(format!("--serial={serial}"))
            .arg("--list-encoders")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(spawned) => {
                child = Some(spawned);
                break;
            }
            Err(err)
                if err.kind() == std::io::ErrorKind::WouldBlock
                    || err.raw_os_error() == Some(11)
                    || err.raw_os_error() == Some(12) =>
            {
                if attempt == 2 {
                    return None;
                }
                thread::sleep(Duration::from_millis(100));
            }
            Err(_) => return None,
        }
    }
    let mut child = child?;
    let deadline = Instant::now() + Duration::from_secs_f64(timeout_s.max(0.0));
    loop {
        match child.try_wait() {
            // check=False 合同：非零退出码照样解析输出。
            Ok(Some(_)) => break,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(_) => return None,
        }
    }
    let mut stdout_buf = Vec::new();
    let mut stderr_buf = Vec::new();
    if let Some(mut pipe) = child.stdout.take() {
        let _ = pipe.read_to_end(&mut stdout_buf);
    }
    if let Some(mut pipe) = child.stderr.take() {
        let _ = pipe.read_to_end(&mut stderr_buf);
    }
    let output = format!(
        "{}\n{}",
        String::from_utf8_lossy(&stdout_buf),
        String::from_utf8_lossy(&stderr_buf)
    );
    let encoders = parse_encoders(&output);
    (!encoders.is_empty()).then_some(encoders)
}

// ------------------------------------------------------- 选择（纯：优先级测试即此）

/// 请求编码器 + 硬件档位下命中的第一条探测条目。
fn best_entry<'a>(
    encoders: &'a [EncoderInfo],
    codec: &str,
    hardware: bool,
) -> Option<&'a EncoderInfo> {
    encoders
        .iter()
        .find(|e| e.codec == codec && e.hardware == hardware)
}

/// 探测条目里最好的 h264（硬件优先）；无硬件 → 不 pin（scrcpy 默认软编）。
fn h264_fallback(encoders: &[EncoderInfo]) -> CodecChoice {
    if let Some(hw) = best_entry(encoders, "h264", true) {
        return CodecChoice::new("h264", Some(&hw.name), Some(true), "使用 h264 硬件编码");
    }
    match best_entry(encoders, "h264", false) {
        Some(sw) => CodecChoice::new(
            "h264",
            Some(&sw.name),
            Some(false),
            "设备无硬件编码器，回退 h264 软编",
        ),
        None => CodecChoice::new("h264", None, None, "探测结果无可用编码器，回退 h264"),
    }
}

/// ``video_codec`` 设置 + 探测数据 → 一个决策。encoders=None（探测失败）
/// 降级为不 pin 的 h264；显式 h264/h265 无硬件条目保持不 pin；显式 av1
/// 无硬件降级 h264（软编 AV1 会打满设备 CPU）。
pub fn resolve_codec(video_codec: &str, encoders: Option<&[EncoderInfo]>) -> CodecChoice {
    let Some(encoders) = encoders else {
        return CodecChoice::new(
            "h264",
            None,
            None,
            "编码器探测不可用，回退 h264（scrcpy 默认选择）",
        );
    };
    match video_codec {
        "auto" => {
            for (codec, hardware) in AUTO_PRIORITY {
                if let Some(entry) = best_entry(encoders, codec, hardware) {
                    let label = if hardware { "硬件" } else { "软件" };
                    return CodecChoice::new(
                        &entry.codec,
                        Some(&entry.name),
                        Some(hardware),
                        format!("自动选择：{codec} {label}编码（{}）", entry.name),
                    );
                }
            }
            h264_fallback(encoders)
        }
        "h264" => match best_entry(encoders, "h264", true) {
            Some(hw) => CodecChoice::new(
                "h264",
                Some(&hw.name),
                Some(true),
                format!("h264 硬件编码（{}）", hw.name),
            ),
            None => CodecChoice::new(
                "h264",
                None,
                Some(false),
                "无 h264 硬件编码器，使用 scrcpy 默认",
            ),
        },
        "h265" | "av1" => match best_entry(encoders, video_codec, true) {
            Some(hw) => CodecChoice::new(
                video_codec,
                Some(&hw.name),
                Some(true),
                format!("{video_codec} 硬件编码（{}）", hw.name),
            ),
            None if video_codec == "h265" => h264_fallback(encoders),
            // av1 未确认硬件：降级，绝不信软件编码。
            None => {
                let fallback = h264_fallback(encoders);
                CodecChoice::new(
                    &fallback.codec,
                    fallback.encoder.as_deref(),
                    fallback.hardware,
                    "设备无 av1 硬件编码器，回退 h264",
                )
            }
        },
        _ => h264_fallback(encoders),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// TESTPAD（骁龙）真机 ``--list-encoders`` 抓包（型号/序列号已假名化）：
    /// 别名行、.cq/.hdr
    /// 变体、sw 档、audio 段（逐字对齐 tests/test_codec.py 的 OPPO_OUTPUT）。
    const OPPO_OUTPUT: &str = concat!(
        "scrcpy 4.1 <https://github.com/Genymobile/scrcpy>\n",
        "[server] INFO: Device: [OPPO] OPPO TESTPAD (Android 16)\n",
        "[server] INFO: List of video encoders:\n",
        "    --video-codec=h264 --video-encoder=c2.qti.avc.encoder             (hw) [vendor]\n",
        "    --video-codec=h264 --video-encoder=OMX.qcom.video.encoder.avc (hw) (alias for c2.qti.avc.enc)\n",
        "    --video-codec=h264 --video-encoder=c2.android.avc.encoder         (sw)\n",
        "    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder            (hw) [vendor]\n",
        "    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder.cq         (hw) [vendor]\n",
        "    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder.hdr        (hw) [vendor]\n",
        "    --video-codec=h265 --video-encoder=c2.android.hevc.encoder        (sw)\n",
        "    --video-codec=av1 --video-encoder=c2.android.av1.encoder          (sw)\n",
        "[server] INFO: List of audio encoders:\n",
        "    --audio-codec=flac --audio-encoder=c2.android.flac.encoder        (sw)\n",
    );

    fn fixture_encoders() -> Vec<EncoderInfo> {
        parse_encoders(OPPO_OUTPUT)
    }

    fn info(codec: &str, name: &str, hardware: bool) -> EncoderInfo {
        EncoderInfo {
            codec: codec.into(),
            name: name.into(),
            hardware,
        }
    }

    fn scratch_dir(tag: &str) -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "duo-core-codec-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// 写一个可执行假探测脚本（参照 session.rs 的假 shell 风格）。
    fn write_script(dir: &Path, body: &str) -> PathBuf {
        let path = dir.join("fake-probe.sh");
        fs::write(&path, body).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        }
        path
    }

    fn script_path(path: &Path) -> String {
        path.display().to_string()
    }

    // ------------------------------------------------------------------ 解析

    #[test]
    fn parse_encoders_extracts_hw_and_sw() {
        // (hw) 标硬件；audio-encoder 行永不匹配。
        let encoders = parse_encoders(OPPO_OUTPUT);
        let triples: Vec<_> = encoders
            .iter()
            .map(|e| (e.codec.as_str(), e.name.as_str(), e.hardware))
            .collect();
        assert!(triples.contains(&("h264", "c2.qti.avc.encoder", true)));
        assert!(triples.contains(&("h264", "c2.android.avc.encoder", false)));
        assert!(triples.contains(&("h265", "c2.qti.hevc.encoder", true)));
        assert!(triples.contains(&("av1", "c2.android.av1.encoder", false)));
        assert!(encoders
            .iter()
            .all(|e| ["h264", "h265", "av1"].contains(&e.codec.as_str())));
    }

    #[test]
    fn parse_encoders_skips_aliases() {
        // 别名行重复已列组件：丢弃。
        let encoders = parse_encoders(OPPO_OUTPUT);
        assert!(!encoders.iter().any(|e| e.name.contains("OMX.")));
        assert_eq!(encoders.len(), 7);
    }

    #[test]
    fn parse_encoders_empty_on_garbage() {
        assert!(parse_encoders("no encoders here").is_empty());
        assert!(parse_encoders("").is_empty());
        // 有 --video-codec= 但缺 --video-encoder= 段：失配。
        assert!(parse_encoders("--video-codec=h264 only").is_empty());
    }

    // -------------------------------------------------------- 缓存读写

    #[test]
    fn encoders_cache_path_lives_under_data_dir() {
        let base = scratch_dir("cachepath");
        assert_eq!(encoders_cache_path(Some(&base)), base.join("encoders.json"));
    }

    #[test]
    fn cache_roundtrip() {
        let dir = scratch_dir("roundtrip");
        let path = dir.join("encoders.json");
        let encoders = fixture_encoders();
        save_encoders_cache(&path, "TESTSERIAL", &encoders, Some(1000.0)).unwrap();
        let raw: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(raw["serial"], "TESTSERIAL");
        assert_eq!(raw["probed_at"].as_f64(), Some(1000.0));
        assert_eq!(raw["encoders"][0]["codec"], "h264");
        assert_eq!(
            load_cached_encoders(&path, "TESTSERIAL", Some(1000.0 + 3600.0)),
            Some(encoders)
        );
    }

    #[test]
    fn cache_expires_past_ttl() {
        let dir = scratch_dir("ttl");
        let path = dir.join("encoders.json");
        let encoders = fixture_encoders();
        save_encoders_cache(&path, "TESTSERIAL", &encoders, Some(1000.0)).unwrap();
        assert_eq!(
            load_cached_encoders(&path, "TESTSERIAL", Some(1000.0 + ENCODERS_TTL_S + 1.0)),
            None
        );
        // TTL 边界仍新鲜。
        assert_eq!(
            load_cached_encoders(&path, "TESTSERIAL", Some(1000.0 + ENCODERS_TTL_S)),
            Some(encoders)
        );
    }

    #[test]
    fn cache_rejects_foreign_serial() {
        // 编码器组件机型专属：别机 serial 必须重探。
        let dir = scratch_dir("foreign");
        let path = dir.join("encoders.json");
        save_encoders_cache(&path, "TESTSERIAL", &fixture_encoders(), Some(1000.0)).unwrap();
        assert_eq!(load_cached_encoders(&path, "OTHER", Some(1001.0)), None);
    }

    #[test]
    fn cache_survives_corrupt_file() {
        let dir = scratch_dir("corrupt");
        let path = dir.join("encoders.json");
        fs::write(&path, "{not json").unwrap();
        assert_eq!(load_cached_encoders(&path, "TESTSERIAL", None), None);
        assert_eq!(
            load_cached_encoders(&dir.join("missing.json"), "TESTSERIAL", None),
            None
        );
    }

    #[test]
    fn cache_rejects_bad_entries() {
        let dir = scratch_dir("badentries");
        let path = dir.join("encoders.json");
        fs::write(
            &path,
            r#"{"serial": "S", "probed_at": 1.0, "encoders": [{"codec": "h264"}]}"#,
        )
        .unwrap();
        assert_eq!(load_cached_encoders(&path, "S", None), None);
    }

    #[test]
    fn cache_empty_entries_load_as_none() {
        // 对译 `encoders or None`：空列表与缺失同义。
        let dir = scratch_dir("empty");
        let path = dir.join("encoders.json");
        save_encoders_cache(&path, "S", &[], Some(1.0)).unwrap();
        assert_eq!(load_cached_encoders(&path, "S", Some(2.0)), None);
    }

    // -------------------------------------------------------- 选择优先级

    #[test]
    fn auto_prefers_h264_hardware() {
        // 档 1：h264 硬件压过一切，含 h265 硬件（PC 侧软解 AVC 远轻于 HEVC）。
        let choice = resolve_codec("auto", Some(&fixture_encoders()));
        assert_eq!(choice.codec, "h264");
        assert_eq!(choice.hardware, Some(true));
        assert!(choice.note.contains("h264"));
    }

    #[test]
    fn auto_h265_only_when_no_h264_hardware() {
        // h265 硬件是档 2：仅当没有任何 h264 硬件时才中选。
        let encoders = fixture_encoders();
        let stripped: Vec<_> = encoders
            .iter()
            .filter(|e| e.codec != "h264" || !e.hardware)
            .cloned()
            .collect();
        let choice = resolve_codec("auto", Some(&stripped));
        assert_eq!(choice.codec, "h265");
        assert_eq!(choice.hardware, Some(true));
    }

    #[test]
    fn auto_falls_to_h264_hardware() {
        // 无 h265 硬件 → h264 硬件并 pin 编码器。
        let only_h264_hw = vec![
            info("h264", "c2.qti.avc.encoder", true),
            info("h264", "c2.android.avc.encoder", false),
            info("h265", "c2.android.hevc.encoder", false),
        ];
        let choice = resolve_codec("auto", Some(&only_h264_hw));
        assert_eq!(choice.codec, "h264");
        assert_eq!(choice.encoder.as_deref(), Some("c2.qti.avc.encoder"));
        assert_eq!(choice.hardware, Some(true));
    }

    #[test]
    fn auto_falls_to_h264_software() {
        // 完全无硬件 → 软件 h264（note 逐字合同）。
        let sw_only = vec![info("h264", "c2.android.avc.encoder", false)];
        let choice = resolve_codec("auto", Some(&sw_only));
        assert_eq!(
            choice,
            CodecChoice::new(
                "h264",
                Some("c2.android.avc.encoder"),
                Some(false),
                "自动选择：h264 软件编码（c2.android.avc.encoder）"
            )
        );
    }

    #[test]
    fn auto_with_no_h264_at_all_uses_fallback() {
        // 探测里无可用项 → 不 pin 的 h264。
        let alien = vec![info("vp9", "c2.android.vp9.encoder", false)];
        let choice = resolve_codec("auto", Some(&alien));
        assert_eq!(choice.codec, "h264");
        assert_eq!(choice.encoder, None);
    }

    #[test]
    fn av1_hardware_only_reached_when_confirmed() {
        // av1 硬件是真实 auto 档——但须探测确认。
        let with_av1_hw = vec![
            info("h264", "c2.qti.avc.encoder", true),
            info("av1", "c2.qti.av1.encoder", true),
        ];
        assert_eq!(
            resolve_codec("auto", Some(&with_av1_hw)).codec,
            "h264" // h264 硬件压过 av1 硬件
        );
        let hw_av1_only = vec![info("av1", "c2.qti.av1.encoder", true)];
        let choice = resolve_codec("auto", Some(&hw_av1_only));
        assert_eq!(choice.codec, "av1");
        assert_eq!(choice.hardware, Some(true));
    }

    #[test]
    fn probe_failure_degrades_to_plain_h264() {
        // 无探测数据 → 不 pin 的 h264（scrcpy 自选默认）。
        let choice = resolve_codec("auto", None);
        assert_eq!(choice.codec, "h264");
        assert_eq!(choice.encoder, None);
        assert_eq!(choice.hardware, None);
    }

    #[test]
    fn explicit_h265_pins_hardware_encoder() {
        let choice = resolve_codec("h265", Some(&fixture_encoders()));
        assert_eq!(choice.codec, "h265");
        assert_eq!(choice.encoder.as_deref(), Some("c2.qti.hevc.encoder"));
    }

    #[test]
    fn explicit_h264_pins_hardware_encoder() {
        let choice = resolve_codec("h264", Some(&fixture_encoders()));
        assert_eq!(choice.encoder.as_deref(), Some("c2.qti.avc.encoder"));
    }

    #[test]
    fn explicit_av1_without_hardware_degrades_to_h264() {
        // TESTPAD 实况：仅 sw av1 → h264 硬件，绝不软编 AV1。
        let choice = resolve_codec("av1", Some(&fixture_encoders()));
        assert_eq!(choice.codec, "h264");
        assert_eq!(choice.encoder.as_deref(), Some("c2.qti.avc.encoder"));
        assert!(choice.note.contains("av1"));
    }

    #[test]
    fn explicit_h265_without_hardware_degrades_to_h264() {
        let only = vec![info("h264", "c2.qti.avc.encoder", true)];
        let choice = resolve_codec("h265", Some(&only));
        assert_eq!(choice.codec, "h264");
        assert_eq!(choice.encoder.as_deref(), Some("c2.qti.avc.encoder"));
    }

    // ------------------------------------------------------------------ 探测

    #[test]
    fn probe_encoders_returns_none_on_binary_missing() {
        let dir = scratch_dir("missing");
        assert_eq!(
            probe_encoders(
                &script_path(&dir.join("no-such-scrcpy")),
                "S",
                PROBE_TIMEOUT_S
            ),
            None
        );
    }

    #[test]
    fn probe_encoders_passes_serial_and_parses_stdout() {
        // 假探测脚本：记录收到的 argv，吐出 OPPO 列表。
        let dir = scratch_dir("argv");
        let body = format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{args_file}'\ncat <<'LISTING'\n{listing}LISTING\n",
            args_file = dir.join("args.txt").display(),
            listing = OPPO_OUTPUT
        );
        let script = write_script(&dir, &body);
        let encoders = probe_encoders(&script_path(&script), "SER42", PROBE_TIMEOUT_S)
            .expect("listing parses");
        assert_eq!(encoders.len(), 7);
        assert!(encoders
            .iter()
            .any(|e| e.codec == "h264" && e.name == "c2.qti.avc.encoder" && e.hardware));
        assert_eq!(
            fs::read_to_string(dir.join("args.txt")).unwrap(),
            "--serial=SER42\n--list-encoders\n"
        );
    }

    #[test]
    fn probe_encoders_parses_stderr_too() {
        // scrcpy 把 INFO 行打到 stderr：stdout+stderr 拼接后再解析。
        let dir = scratch_dir("stderr");
        let body = format!(
            "#!/bin/sh\ncat <<'LISTING' 1>&2\n{listing}LISTING\n",
            listing = OPPO_OUTPUT
        );
        let script = write_script(&dir, &body);
        let encoders = probe_encoders(&script_path(&script), "S", PROBE_TIMEOUT_S);
        assert_eq!(encoders.map(|e| e.len()), Some(7));
    }

    #[test]
    fn probe_encoders_none_on_unparseable_output() {
        // check=False：非零退出码也照样解析（这里解析不出条目 → None）。
        let dir = scratch_dir("garbage");
        let script = write_script(&dir, "#!/bin/sh\necho 'no encoders here'\nexit 1\n");
        assert_eq!(
            probe_encoders(&script_path(&script), "S", PROBE_TIMEOUT_S),
            None
        );
    }

    #[test]
    fn probe_encoders_times_out() {
        let dir = scratch_dir("timeout");
        let script = write_script(&dir, "#!/bin/sh\nsleep 5\necho hi\n");
        assert_eq!(probe_encoders(&script_path(&script), "S", 0.2), None);
    }
}
