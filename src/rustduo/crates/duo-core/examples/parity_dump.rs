//! Parity dump (Rust side): mirrors rust/parity/python_dump.py input-for-input.
//! Output must be byte-identical; compared by scripts/parity_check.sh.

use duo_core::aspects::{aspect_presets, body_aspect_from_wm_size, scaled_size, transposed};
use duo_core::catalog::APP_CATALOG;
use duo_core::engine::{DisplayMode, DisplaySpec, EngineArgs, VideoSpec};
use duo_core::icons::{lighten, render_preset_svg};

const WM_SAMPLES: &[&str] = &[
    "Physical size: 1080x2400",
    "Physical size: 1080x2400\nOverride size: 1440x3200\n",
    "Override size: 2223x1000\n",
    "Override size: 1000x2223\n",
    "Physical size: 0x0\n",
    "",
    "adb: device offline\n",
    "Override size: 1080x2340\nPhysical size: 1080x2400\n",
];

const SCALED: &[(i64, i64, f64, &str)] = &[
    (3840, 2160, 2.0, "2"),
    (3840, 2160, 4.0, "4"),
    (3840, 2160, 1.5, "1.5"),
    (3840, 2160, 1.25, "1.25"),
    (3841, 2055, 1.75, "1.75"),
    (1080, 1920, 2.0, "2"),
    (64, 32, 4.0, "4"),
    (255, 255, 1.1, "1.1"),
    (1001, 999, 1.75, "1.75"),
];

fn py_bool(b: bool) -> &'static str {
    if b {
        "True"
    } else {
        "False"
    }
}

fn py_repr(s: &str) -> String {
    // The Python dump prints {wm!r} - single quotes, \n escapes.
    format!("'{}'", s.replace('\\', "\\\\").replace('\n', "\\n"))
}

fn strip_text(svg: &str) -> String {
    let re = regex::Regex::new(r"(?s)\n<text.*?</text>").unwrap();
    re.replace_all(svg, "").into_owned()
}

fn main() {
    for preset in APP_CATALOG {
        // v7：rust SVG 无 <text>（面板叠字），python 保留（Qt 有字体）；
        // parity 比渐变+path 底形，字符等价性由面板层测试保证
        let svg = render_preset_svg(preset);
        let stripped = strip_text(&svg);
        println!("svg\t{}\t{}", preset.package, stripped);
    }
    for wm in WM_SAMPLES {
        match body_aspect_from_wm_size(wm) {
            None => println!("body\t{}\tNone", py_repr(wm)),
            Some(p) => println!(
                "body\t{}\t{}/{width}x{height}/{landscape}",
                py_repr(wm),
                p.id,
                width = p.width,
                height = p.height,
                landscape = py_bool(p.landscape)
            ),
        }
    }
    for src in &WM_SAMPLES[..4] {
        if let Some(p) = body_aspect_from_wm_size(src) {
            let t = transposed(&p);
            println!(
                "transpose\t{}x{}\t{}/{width}x{height}",
                p.width,
                p.height,
                t.id,
                width = t.width,
                height = t.height
            );
        }
    }
    for &(w, h, s, label) in SCALED {
        let out = scaled_size(w, h, s).unwrap();
        // Python prints tuples like "(1920, 1080)".
        println!("scaled\t{w}x{h}/{label}\t({}, {})", out.0, out.1);
    }
    for preset in aspect_presets() {
        println!(
            "preset\t{}\t{}x{}/{landscape}",
            preset.id,
            preset.width,
            preset.height,
            landscape = py_bool(preset.landscape)
        );
    }
    let mut variants: Vec<EngineArgs> = Vec::new();
    let mut a = EngineArgs::new("4444bd6b");
    a.app_package = Some("cn.com.langeasy.LangEasyLexis".into());
    variants.push(a);
    variants.push(EngineArgs::new("s"));
    let mut m = EngineArgs::new("s");
    m.display = DisplaySpec {
        mode: DisplayMode::Mirror,
        ..Default::default()
    };
    variants.push(m);
    let mut f = EngineArgs::new("s");
    f.display = DisplaySpec {
        mode: DisplayMode::Fixed,
        width: Some(2560),
        height: Some(1440),
        dpi: Some(268),
    };
    f.window_x = Some(10);
    f.window_y = Some(20);
    f.window_width = Some(1252);
    f.window_height = Some(2088);
    variants.push(f);
    let mut x = EngineArgs::new("s");
    x.display = DisplaySpec {
        mode: DisplayMode::Flex,
        width: Some(1120),
        height: Some(1872),
        dpi: Some(313),
    };
    variants.push(x);
    let mut t = EngineArgs::new("s");
    t.audio = false;
    t.window_title = Some("不背单词".into());
    t.borderless = true;
    variants.push(t);
    let mut k = EngineArgs::new("s");
    k.vd_keep_content = true;
    variants.push(k);
    let mut v = EngineArgs::new("s");
    v.video = VideoSpec {
        codec: "h264".into(),
        encoder: Some("c2.qti.hevc.encoder".into()),
        bitrate_mbps: 50,
        max_fps: 90,
    };
    variants.push(v);
    let mut p = EngineArgs::new("s");
    p.app_package = Some("+pre.fixed.pkg".into());
    p.print_fps = false;
    p.screen_off = false;
    variants.push(p);
    let mut w = EngineArgs::new("s");
    w.window_x = Some(-5);
    w.window_y = Some(7);
    variants.push(w);
    for (i, args) in variants.iter().enumerate() {
        let argv = args.to_argv("scrcpy").unwrap();
        println!("argv\t{i}\t{}", argv[1..].join(" "));
    }
    for (color, frac, label) in [
        ("#07C160", 0.08, "0.08"),
        ("#000000", 0.5, "0.5"),
        ("#FF6A00", 0.0, "0.0"),
        ("#404040", 1.0, "1.0"),
        ("#F7B500", 0.08, "0.08"),
        ("#1C1C1E", 0.25, "0.25"),
    ] {
        println!("lighten\t{color}/{label}\t{}", lighten(color, frac));
    }
}
