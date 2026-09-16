"""Parity dump: the same inputs through Python core and Rust duo-core must
match byte-for-byte. Run by scripts/parity_check.sh; output is TSV compared
with diff. This is the hard guarantee that the 0.2 port is a translation,
not a reimplementation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from pyduo.core.aspects import (
        ASPECT_PRESETS,
        body_aspect_from_wm_size,
        scaled_size,
        transposed,
)
from pyduo.core.catalog import APP_CATALOG
from pyduo.core.engine import DisplaySpec, EngineArgs, VideoSpec
from pyduo.core.icon_presets import lighten, render_preset_svg

WM_SAMPLES = [
        "Physical size: 1080x2400",
        "Physical size: 1080x2400\nOverride size: 1440x3200\n",
        "Override size: 2223x1000\n",
        "Override size: 1000x2223\n",
        "Physical size: 0x0\n",
        "",
        "adb: device offline\n",
        "Override size: 1080x2340\nPhysical size: 1080x2400\n",
]

SCALED = [
        (3840, 2160, 2),
        (3840, 2160, 4),
        (3840, 2160, 1.5),
        (3840, 2160, 1.25),
        (3841, 2055, 1.75),
        (1080, 1920, 2),
        (64, 32, 4),
        (255, 255, 1.1),
        (1001, 999, 1.75),
]


def dump() -> None:
        for preset in APP_CATALOG:
                print(f"svg\t{preset.package}\t{render_preset_svg(preset)}")
        for wm in WM_SAMPLES:
                preset = body_aspect_from_wm_size(wm)
                if preset is None:
                        print(f"body\t{wm!r}\tNone")
                else:
                        print(
                                f"body\t{wm!r}\t{preset.id}/{preset.width}x{preset.height}"
                                f"/{preset.landscape}"
                        )
        for body_src in WM_SAMPLES[:4]:
                preset = body_aspect_from_wm_size(body_src)
                if preset is not None:
                        twin = transposed(preset)
                        print(f"transpose\t{preset.width}x{preset.height}\t{twin.id}/{twin.width}x{twin.height}")
        for w, h, s in SCALED:
                print(f"scaled\t{w}x{h}/{s}\t{scaled_size(w, h, s)}")
        for preset in ASPECT_PRESETS:
                print(f"preset\t{preset.id}\t{preset.width}x{preset.height}/{preset.landscape}")
        variants: list[EngineArgs] = [
                EngineArgs(serial="4444bd6b", app_package="cn.com.langeasy.LangEasyLexis"),
                EngineArgs(serial="s"),
                EngineArgs(serial="s", display=DisplaySpec(mode="mirror")),
                EngineArgs(
                        serial="s",
                        display=DisplaySpec(mode="fixed", width=2560, height=1440, dpi=268),
                        window_x=10,
                        window_y=20,
                        window_width=1252,
                        window_height=2088,
                ),
                EngineArgs(
                        serial="s",
                        display=DisplaySpec(mode="flex", width=1120, height=1872, dpi=313),
                ),
                EngineArgs(serial="s", audio=False, window_title="不背单词", borderless=True),
                EngineArgs(serial="s", vd_keep_content=True),
                EngineArgs(
                        serial="s",
                        video=VideoSpec(
                                encoder="c2.qti.hevc.encoder",
                                codec="h264",
                                bitrate_mbps=50,
                        ),
                ),
                EngineArgs(
                        serial="s",
                        app_package="+pre.fixed.pkg",
                        print_fps=False,
                        screen_off=False,
                ),
                EngineArgs(serial="s", window_x=-5, window_y=7),
        ]
        for i, args in enumerate(variants):
                argv = args.to_argv(binary="scrcpy")
                print(f"argv\t{i}\t{' '.join(argv[1:])}")
        for color, frac in [
                ("#07C160", 0.08),
                ("#000000", 0.5),
                ("#FF6A00", 0.0),
                ("#404040", 1.0),
                ("#F7B500", 0.08),
                ("#1C1C1E", 0.25),
        ]:
                print(f"lighten\t{color}/{frac}\t{lighten(color, frac)}")


if __name__ == "__main__":
        dump()
