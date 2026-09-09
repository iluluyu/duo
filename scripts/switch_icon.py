from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_DIR = ROOT / "docs" / "ui" / "mockups" / "icon_candidates"
TARGET = ROOT / "assets" / "duo.ico"

OPTIONS = {
        "pure_black": "do_equal_glass_pure_black.ico",
        "black": "do_equal_glass_pure_black.ico",
        "default": "do_equal_glass_pure_black.ico",
        "mono": "do_equal_glass_pure_black.ico",
        "glass_back": "do_equal_glass_back.ico",
        "back": "do_equal_glass_back.ico",
        "obsidian": "do_equal_glass_back.ico",
        "light": "do_equal_glass_light.ico",
        "glass_light": "do_equal_glass_light.ico",
        "white": "do_equal_glass_light.ico",
        "glass_front": "do_equal_glass_front_cutout.ico",
        "front": "do_equal_glass_front_cutout.ico",
        "glass_translucent": "do_equal_glass_front_translucent.ico",
        "translucent": "do_equal_glass_front_translucent.ico",
        "o_glass": "do_equal_o_glass_front.ico",
        "opt1": "new_opt1_equal_glass.ico",
        "opt1_mono": "new_opt1_equal_glass_mono.ico",
        "opt2": "new_opt2_equal_cutout.ico",
        "opt2_mono": "new_opt2_equal_cutout_mono.ico",
        "opt3": "do_equal_glass_pure_black.ico",
        "opt4": "new_opt4_do_cutout.ico",
        "opt4_mono": "new_opt4_do_cutout_mono.ico",
        "do_cutout": "new_opt4_do_cutout.ico",
        "opt5": "new_opt5_parallel_equal.ico",
        "opt5_mono": "new_opt5_parallel_equal_mono.ico",
        "parallel": "new_opt5_parallel_equal.ico",
        "baseline": "current_baseline.ico",
        "bak": "current_baseline.ico",
}


def main() -> int:
        if len(sys.argv) < 2 or sys.argv[1] not in OPTIONS:
                print("Usage: python scripts/switch_icon.py <opt1..opt5|baseline>")
                return 1
        choice = sys.argv[1]
        src_file = CANDIDATES_DIR / OPTIONS[choice]
        if not src_file.exists():
                print(f"Error: {src_file} not found.")
                return 1
        shutil.copyfile(src_file, TARGET)
        print(f"Updated {TARGET} -> {OPTIONS[choice]}")
        return 0


if __name__ == "__main__":
        raise SystemExit(main())
