"""Android app metadata: package listing, labels and icons.

Why aapt2: modern APKs ship an updated binary-XML manifest (header size 12)
that current pure-Python parsers (pyaxmlparser, androguard, apkutils2) fail
to read. Google's own ``aapt2`` handles them, so Duo downloads the official
build from Google Maven into its tools cache on first use.

Label resolution requires pulling the base APK from the device; the pull is
cached and invalidated automatically because Android rewrites the /data/app
install path on every app update (the random suffix in the path changes).
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from duo.core.engine import is_wsl
from duo.core.paths import apks_dir, icons_dir, tools_dir
from duo.core.winproc import creation_flags

if TYPE_CHECKING:
        import PIL.Image

#: Icon cache filename suffix. ".r3" bumps the icon cache generation: the
#: adaptive composite changed both its geometry (432/288 canvas instead of
#: 512/341) and its content layout (foreground alpha-bbox detection with
#: optical centring), so PNGs cached by the older composition must not be
#: served. APK and metadata caches are unaffected and keep their plain
#: names; abandoned ".r2" files are simply orphaned.
_ICON_CACHE_SUFFIX = ".r3.png"

#: Pinned aapt2 build from Google Maven (same artifact AGP uses).
AAPT2_VERSION = "9.4.0-15978811"
_AAPT2_URL = (
        "https://dl.google.com/android/maven2/com/android/tools/build/"
        f"aapt2/{AAPT2_VERSION}/aapt2-{AAPT2_VERSION}-windows.jar"
)

_RUN_TIMEOUT_S = 60.0
_PULL_TIMEOUT_S = 300.0
#: APKs larger than this are skipped for icon extraction (letter avatar stays).
_MAX_ICON_APK_BYTES = 200 * 1024 * 1024


class AdbError(RuntimeError):
        """Raised when an adb invocation fails."""


class Adb:
        """Thin wrapper around the adb binary bound to one device serial."""

        def __init__(self, binary: str, serial: str) -> None:
                self.binary = binary
                self.serial = serial

        def third_party_packages(self, timeout: float = _RUN_TIMEOUT_S) -> list[str]:
                """User-installed (third-party) packages, sorted.

                ``pm list packages -3`` skips the preinstalled system bulk -
                what a launcher should offer is what the user installed.
                """
                result = self.run("shell", "pm", "list", "packages", "-3", timeout=timeout)
                names = [
                        line.removeprefix("package:").strip()
                        for line in result.splitlines()
                        if line.startswith("package:")
                ]
                return sorted(names)

        def run(self, *args: str, timeout: float = _RUN_TIMEOUT_S) -> str:
                """Run adb for this device and return stdout."""
                result = subprocess.run(
                        [self.binary, "-s", self.serial, *args],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                        check=False,
                        creationflags=creation_flags(),
                )
                if result.returncode != 0:
                        detail = (result.stderr or "").strip()
                        raise AdbError(f"adb {' '.join(args)} failed: {detail}")
                return result.stdout

        def shell(self, command: str, timeout: float = _RUN_TIMEOUT_S) -> str:
                """Run ``adb shell <command>`` and return stdout."""
                return self.run("shell", command, timeout=timeout)

        def pull(self, remote: str, local: Path) -> None:
                """Pull a remote file to a local path."""
                self.run("pull", remote, str(local), timeout=_PULL_TIMEOUT_S)


# ----------------------------------------------------------------------------
# Pure parsers (unit-tested without a device)
# ----------------------------------------------------------------------------


def parse_device_density(wm_density_output: str) -> int | None:
        """Parse ``wm density`` output into the density apps actually see.

        Prefers the ``Override density`` line (the effective value, e.g. a
        display-zoom setting) and falls back to ``Physical density``:

        >>> parse_device_density("Physical density: 420\\nOverride density: 356")
        356
        >>> parse_device_density("Physical density: 420")
        420
        >>> parse_device_density("") is None
        True
        """
        override: int | None = None
        physical: int | None = None
        for line in wm_density_output.splitlines():
                text = line.strip()
                if text.startswith("Override density:"):
                        digits = "".join(ch for ch in text.split(":", 1)[1] if ch.isdigit())
                        if digits:
                                override = int(digits)
                elif text.startswith("Physical density:"):
                        digits = "".join(ch for ch in text.split(":", 1)[1] if ch.isdigit())
                        if digits:
                                physical = int(digits)
        return override or physical


def device_density(adb_path: str, serial: str | None) -> int | None:
        """Effective density of the device's main display (Override > Physical)."""
        target = serial or ""
        adb = Adb(adb_path, target)
        return parse_device_density(adb.shell("wm density"))


def parse_package_list(packages_output: str, third_party: bool = True) -> list[str]:
        """Parse ``pm list packages [-3]`` output into package names (sorted)."""
        names = []
        for line in packages_output.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                        names.append(line[len("package:") :])
        return sorted(names)


def parse_base_apk_path(pm_path_output: str) -> str | None:
        """Extract the base APK device path from ``pm path <pkg>`` output."""
        for line in pm_path_output.splitlines():
                line = line.strip()
                if line.startswith("package:") and line.endswith("base.apk"):
                        return line[len("package:") :].strip()
        return None


def parse_resolve_activity(resolve_output: str) -> str | None:
        """Extract ``pkg/activity`` from ``cmd package resolve-activity --brief``.

        ``--brief`` prints just the launchable component (a blank line may
        precede it); the last non-empty line containing ``/`` wins. ``None``
        = nothing resolvable (package missing or exported=false), which the
        caller reports as a degradation instead of half an am start.
        """
        for line in reversed(resolve_output.splitlines()):
                line = line.strip()
                if line and "/" in line and " " not in line:
                        return line
        return None


def parse_badging(badging_output: str) -> dict[str, str]:
        """Parse the interesting fields from ``aapt2 dump badging`` output."""
        info: dict[str, str] = {}
        package = re.search(r"^package: name='(\S+)'", badging_output, re.MULTILINE)
        if package:
                info["package"] = package.group(1)
        version = re.search(r"^package: .*versionName='([^']*)'", badging_output, re.MULTILINE)
        if version:
                info["version_name"] = version.group(1)
        label = re.search(r"^application-label:'(.*)'$", badging_output, re.MULTILINE)
        if label:
                info["label"] = label.group(1)
        application = re.search(r"^application: .*icon='([^']*)'", badging_output, re.MULTILINE)
        if application:
                info["icon"] = application.group(1)
        return info


# ----------------------------------------------------------------------------
# Label sorting (pinyin initials)
# ----------------------------------------------------------------------------

#: GB2312 level-1 hanzi (0xB0A1..0xD7F9, 3755 chars) are laid out in pinyin
#: order, so each initial owns one contiguous run - the run's last code
#: point is enough to classify a character. Boundaries generated against
#: pypinyin over all level-1 chars (build-time tool only, not a dependency):
#: total run-edge imprecision is 21/3755 chars, all polyphones where GB2312's
#: chosen reading differs from pypinyin's (both defensible; harmless here).
_PINYIN_RUN_ENDS: tuple[tuple[int, str], ...] = (
        (0xB0C4, "a"),
        (0xB2C0, "b"),
        (0xB4EE, "c"),
        (0xB6E9, "d"),
        (0xB7A1, "e"),
        (0xB8C0, "f"),
        (0xB9FD, "g"),
        (0xBBF6, "h"),
        (0xBFA5, "j"),
        (0xC0AB, "k"),
        (0xC2E7, "l"),
        (0xC4C2, "m"),
        (0xC5B6, "n"),
        (0xC5BD, "o"),
        (0xC6D9, "p"),
        (0xC8BA, "q"),
        (0xC8F5, "r"),
        (0xCBFA, "s"),
        (0xCDD9, "t"),
        (0xCEF3, "w"),
        (0xD1B9, "x"),
        (0xD4D0, "y"),
        (0xD7F9, "z"),
)

#: Hanzi that appear in real Chinese app/game names but sit OUTSIDE the
#: pinyin-sorted level-1 range (GB2312 level-2 / GBK-only chars have no run
#: order), so the table above cannot classify them. Curated from a sweep of
#: popular Android store names - extend as labels surface (raw-char fallback
#: merely parks an app at the end of its letter group).
_PINYIN_EXTRA: dict[str, str] = {
        "吧": "b",
        "哔": "b",
        "魑": "c", "琛": "c",
        "哒": "d", "咚": "d", "嗲": "d",
        "斐": "f",
        "嗨": "h", "浣": "h", "獾": "h", "珩": "h", "晗": "h",
        "咔": "k", "氪": "k", "铠": "k",
        "浏": "l", "翎": "l", "岚": "l", "嘞": "l", "魉": "l", "啰": "l",
        "咪": "m", "喵": "m", "魅": "m", "旻": "m",
        "妞": "n", "嗯": "n",
        "噗": "p", "貔": "p",
        "穹": "q", "蜻": "q",
        "嗖": "s",
        "蜓": "t", "钛": "t",
        "魍": "w",
        "枭": "x", "貅": "x", "玺": "x", "晞": "x",
        "曜": "y", "嬴": "y", "樾": "y", "昱": "y", "玥": "y",
        "崽": "z",
}


def pinyin_initial(ch: str) -> str:
        """Pinyin initial (lowercase letter) of one hanzi character.

        Level-1 hanzi classify through the GB2312 run table, the curated
        dict covers common app-name hanzi outside it, and everything else
        (latin, digits, kana, unlisted hanzi) passes through lowercased -
        callers get one comparable key space either way.

        >>> pinyin_initial("微")
        'w'
        >>> pinyin_initial("哔")
        'b'
        >>> pinyin_initial("W")
        'w'
        """
        if len(ch) != 1:
                return ch.lower()
        extra = _PINYIN_EXTRA.get(ch)
        if extra is not None:
                return extra
        try:
                encoded = ch.encode("gb2312")
        except UnicodeEncodeError:
                return ch.lower()
        if len(encoded) != 2:
                return ch.lower()
        code = encoded[0] << 8 | encoded[1]
        if not 0xB0A1 <= code <= 0xD7F9:
                return ch.lower()   # level-2 hanzi / zone-1 symbols: no run order
        for end, initial in _PINYIN_RUN_ENDS:
                if code <= end:
                        return initial
        return ch.lower()   # defensive: the last run end covers 0xD7F9


def label_sort_key(label: str) -> str:
        """Comparable form of an app label for first-letter grid ordering.

        Every hanzi maps to its pinyin initial, everything else passes
        through lowercased: "不背单词" -> "bbdc", "哔哩哔哩" -> "blbl",
        "WPS Office" -> "wps office". Chinese and latin names then order
        together by first letter, the way launcher grids do.

        >>> sorted(["微信", "哔哩哔哩", "不背单词"], key=label_sort_key)
        ['不背单词', '哔哩哔哩', '微信']
        """
        return "".join(pinyin_initial(ch) for ch in label).lower()


# ----------------------------------------------------------------------------
# AppInfo assembly
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class AppInfo:
        """Resolved metadata for one installed app."""

        package: str
        label: str
        version_name: str | None = None
        icon_path: Path | None = None


def aapt2_ensure(tools_root: Path | None = None) -> Path:
        """Return the path to a working aapt2, downloading it if necessary."""
        target = (tools_root or tools_dir()) / "aapt2.exe"
        if target.exists():
                _ensure_executable(target)
                return target
        # Download the official jar and pull the binary out of it.
        with urllib.request.urlopen(_AAPT2_URL, timeout=120) as response:  # noqa: S310
                jar_bytes = response.read()
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as tmp:
                tmp.write(jar_bytes)
                jar_path = Path(tmp.name)
        try:
                with zipfile.ZipFile(jar_path) as jar:
                        target.write_bytes(jar.read("aapt2.exe"))
        finally:
                jar_path.unlink(missing_ok=True)
        _ensure_executable(target)
        return target


def _ensure_executable(path: Path) -> None:
        """Under WSL the .exe needs the executable bit for interop."""
        if is_wsl():
                path.chmod(0o755)


_DENSITY_RANK = {"ldpi": 0, "mdpi": 1, "hdpi": 2, "xhdpi": 3, "xxhdpi": 4, "xxxhdpi": 5}
_RASTER_SUFFIXES = (".png", ".webp", ".jpg")


def parse_resource_files(resources_dump: str) -> dict[str, list[tuple[int, str]]]:
        """Map resource id -> [(density rank, raster file path)] from aapt2 dump."""
        files: dict[str, list[tuple[int, str]]] = {}
        current: str | None = None
        for line in resources_dump.splitlines():
                header = re.match(r"\s*resource (0x[0-9a-f]+) \S+", line)
                if header:
                        current = header.group(1)
                        continue
                entry = re.search(r"\(([\w-]*)\) \(file\) (\S+) type=", line)
                if entry and current:
                        path = entry.group(2)
                        if path.lower().endswith(_RASTER_SUFFIXES):
                                rank = _DENSITY_RANK.get(entry.group(1), 1)
                                files.setdefault(current, []).append((rank, path))
        return files


def parse_resource_colors(resources_dump: str) -> dict[str, str]:
        """Map resource id -> color hex (#RRGGBB[AA]) from aapt2 dump."""
        colors: dict[str, str] = {}
        current: str | None = None
        for line in resources_dump.splitlines():
                header = re.match(r"\s*resource (0x[0-9a-f]+) \S+", line)
                if header:
                        current = header.group(1)
                        continue
                hex_value = re.search(r"(#[0-9a-fA-F]{6,8})\b", line)
                if hex_value and current and current not in colors:
                        colors[current] = hex_value.group(1)
        return colors


def resource_id_for_file(resources_dump: str, file_path: str) -> str | None:
        """Find the resource id whose block lists ``file_path``."""
        current: str | None = None
        for line in resources_dump.splitlines():
                header = re.match(r"\s*resource (0x[0-9a-f]+) \S+", line)
                if header:
                        current = header.group(1)
                        continue
                if file_path in line and "(file)" in line and current:
                        return current
        return None


def parse_adaptive_refs(xmltree_dump: str) -> dict[str, str]:
        """Map layer name -> drawable resource id from adaptive-icon xmltree."""
        refs: dict[str, str] = {}
        layer: str | None = None
        for line in xmltree_dump.splitlines():
                element = re.search(r"E: (\w+)", line)
                if element:
                        layer = element.group(1)
                        continue
                attr = re.search(r"drawable\(0x[0-9a-f]+\)=@?(0x[0-9a-f]+)", line)
                if attr and layer in {"foreground", "background"} and layer not in refs:
                        refs[layer] = attr.group(1)
        return refs


def apply_rounded_mask(
        image: PIL.Image.Image, radius_ratio: float = 0.23
) -> PIL.Image.Image:
        """Return ``image`` with a rounded-rectangle alpha mask applied.

        Every extracted icon gets one uniform base shape - a rounded square
        with corner radius ``min(w, h) * radius_ratio`` (DESIGN.md §3.1: 23%,
        matching the preset/fallback templates). The image is neither
        resized nor cropped; opaque square sources (legacy rasters, the
        adaptive white-canvas composite) simply lose their corners. The
        mask is drawn at 4x resolution and scaled back down with LANCZOS so
        the arcs stay smooth at launcher sizes instead of stair-stepping.
        """
        from PIL import Image, ImageDraw

        base = image if image.mode == "RGBA" else image.convert("RGBA")
        w, h = base.size
        radius = round(min(w, h) * radius_ratio)
        scale = 4  # supersampling factor for the antialiased arc edges
        mask = Image.new("L", (w * scale, h * scale), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, w * scale - 1, h * scale - 1), radius=radius * scale, fill=255
        )
        base.putalpha(mask.resize((w, h), Image.Resampling.LANCZOS))
        return base


#: Adaptive-icon geometry, in canvas pixels. A 432px canvas maps the 108dp
#: layer model at 4px per unit, so the 72dp visible centre (288px) and the
#: 66dp safe-zone diameter (264px) both land on exact integers. The retired
#: 512px canvas made the visible crop 341.33px, and that fractional resample
#: accumulated aliasing whenever the cached PNG was scaled again for the
#: grid (RESEARCH-ICONS.md §5 / P2-5).
_ADAPTIVE_CANVAS = 432
_ADAPTIVE_VISIBLE = _ADAPTIVE_CANVAS * 72 // 108  # 288: launcher-visible crop
_ADAPTIVE_SAFE = _ADAPTIVE_CANVAS * 66 // 108  # 264: guaranteed-uncropped zone

#: A foreground whose content reaches within this fraction of every edge of
#: its layer is full-bleed artwork designed to be cropped by the mask
#: (RESEARCH-ICONS.md §5) - it keeps the legacy full-canvas stretch.
_ADAPTIVE_FULL_BLEED = 0.98


def _alpha_bbox(
        image: PIL.Image.Image, threshold: int = 8
) -> tuple[int, int, int, int] | None:
        """Bounding box of pixels whose alpha exceeds ``threshold``.

        A raw ``getbbox`` counts every non-zero alpha as ink, so an
        invisible fringe of alpha 1..8 pixels (lossy re-exports, padded
        rasters, antialiasing spill across a whole layer) would report a
        full-canvas box and defeat content detection. Ignoring alphas at
        or below ``threshold`` measures visible ink only. Returns ``None``
        when nothing crosses the threshold (a fully transparent layer).
        """
        alpha = image.getchannel("A")
        return alpha.point(lambda value: 255 if value > threshold else 0).getbbox()


def _paste_foreground(base: PIL.Image.Image, fg: PIL.Image.Image) -> None:
        """Paste an adaptive-icon foreground layer onto the canvas, content first.

        Foregrounds are authored on the full 108-unit layer with the logo
        floating somewhere inside the 66-unit safe zone and transparent
        padding around it. Stretching the layer across the whole canvas
        (the old behaviour) reproduces that padding verbatim, which looks
        wrong whenever the padding is lopsided or the logo spills past the
        safe zone - the "circle logo clipped by the squircle" complaint
        (RESEARCH-ICONS.md P0-2 / P2-5). Instead:

        * crop the layer to its alpha bounding box (:func:`_alpha_bbox`);
        * keep the authored scale - the layer's larger dimension maps onto
          the canvas, so content keeps the on-canvas size its designer drew
          and small logos never balloon to fill the safe zone (no upscaling
          beyond 1x);
        * shrink, aspect preserved, only when the content would spill past
          the 66-unit safe zone;
        * centre the result on the canvas so the 72-unit visible crop always
          shows the logo optically centred.

        Two shapes take the degenerate exits instead: a fully transparent
        layer is skipped (the background shows through), and a layer whose
        content already spans ~the whole canvas is full-bleed artwork whose
        design relies on being cropped by the mask - recentring or
        shrinking it would change icons that already render correctly.
        """
        from PIL import Image

        bbox = _alpha_bbox(fg)
        if bbox is None:
                return
        bleed_x = fg.width * (1.0 - _ADAPTIVE_FULL_BLEED)
        bleed_y = fg.height * (1.0 - _ADAPTIVE_FULL_BLEED)
        if (
                bbox[0] <= bleed_x
                and bbox[1] <= bleed_y
                and bbox[2] >= fg.width - bleed_x
                and bbox[3] >= fg.height - bleed_y
        ):
                layer = fg.resize((base.width, base.height))
                base.paste(layer, (0, 0), layer)
                return
        content = fg.crop(bbox)
        scale = base.width / max(fg.size)
        if max(content.size) * scale > _ADAPTIVE_SAFE:
                scale = _ADAPTIVE_SAFE / max(content.size)
        size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
        layer = content.resize(size, Image.Resampling.LANCZOS)
        base.paste(layer, ((base.width - size[0]) // 2, (base.height - size[1]) // 2), layer)


def extract_icon(
        apk_path: Path,
        icon_ref: str,
        out_png: Path,
        aapt2: Path | None = None,
) -> Path | None:
        """Extract an app icon out of an APK into a PNG file.

        Strategy: a raster icon ref is read directly. An adaptive icon (.xml)
        first falls back to the same resource's legacy raster variant (most
        apps still ship one); when that is missing, the adaptive layers are
        resolved and composited (108-unit canvas model, foreground content
        optically centred). Both output paths finish through
        :func:`apply_rounded_mask` so every cached icon shares the
        launcher's rounded-square base shape.
        """
        import io

        try:
                from PIL import Image
        except ImportError:
                return None

        def read(path: str) -> bytes | None:
                try:
                        with zipfile.ZipFile(apk_path) as apk:
                                return apk.read(path)
                except (KeyError, zipfile.BadZipFile):
                        return None

        def best_resource_file(dump: str, res_id: str) -> str | None:
                candidates = sorted(
                        parse_resource_files(dump).get(res_id, []), key=lambda item: -item[0]
                )
                return candidates[0][1] if candidates else None

        ref = icon_ref
        if icon_ref.endswith(".xml"):
                if aapt2 is None:
                        return None
                dump = _aapt2_output(aapt2, ["dump", "resources", str(apk_path)])
                if dump is None:
                        return None
                res_id = resource_id_for_file(dump, icon_ref)
                legacy = best_resource_file(dump, res_id) if res_id else None
                if legacy:
                        ref = legacy
                else:
                        argv = ["dump", "xmltree", "--file", icon_ref, str(apk_path)]
                        tree = _aapt2_output(aapt2, argv)
                        if tree is None:
                                return None
                        refs = parse_adaptive_refs(tree)
                        fg_data = None
                        if refs.get("foreground"):
                                path = best_resource_file(dump, refs["foreground"])
                                fg_data = read(path) if path else None
                        bg_data = None
                        bg_color = None
                        if refs.get("background"):
                                path = best_resource_file(dump, refs["background"])
                                if path:
                                        bg_data = read(path)
                                if bg_data is None:
                                        colors = parse_resource_colors(dump)
                                        bg_color = colors.get(refs["background"])
                        composed = _compose_adaptive(fg_data, bg_data, bg_color)
                        if composed is None:
                                return None
                        out_png.write_bytes(composed)
                        return out_png

        data = read(ref)
        if data is None:
                return None
        try:
                with Image.open(io.BytesIO(data)) as image:
                        apply_rounded_mask(image).save(out_png, format="PNG")
        except OSError:
                return None
        return out_png


def _aapt2_output(aapt2: Path, argv: list[str]) -> str | None:
        """Run aapt2 and return stdout (None on failure)."""
        try:
                result = subprocess.run(
                        [str(aapt2), *argv],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_RUN_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
        except (OSError, subprocess.TimeoutExpired):
                return None
        return result.stdout or None


def _compose_adaptive(
        fg_data: bytes | None, bg_data: bytes | None, bg_color: str | None
) -> bytes | None:
        """Composite adaptive layers: 108-unit canvas, visible centre 72.

        The canvas is 432px (4px per 108-unit cell) so the 72-unit visible
        centre is an exact 288px integer crop - the retired 512/341 pair
        forced a 341.33 fractional scale that accumulated aliasing whenever
        the cached PNG was resized again for display (RESEARCH-ICONS.md
        P2-5). The background layer still stretches full-canvas (it is
        backdrop by design); the foreground goes through
        :func:`_paste_foreground`, which keys on its actual content, so
        layers with lopsided transparent padding no longer render
        off-centre. The flattened crop then goes through
        :func:`apply_rounded_mask` - without it the white-square canvas
        would show up in the grid as a hard-cornered square next to the
        rounded presets.
        """
        import io

        try:
                from PIL import Image
        except ImportError:
                return None
        canvas = _ADAPTIVE_CANVAS
        base = Image.new("RGBA", (canvas, canvas), bg_color or "#FFFFFF")
        if bg_data:
                try:
                        with Image.open(io.BytesIO(bg_data)) as bg_file:
                                bg: Image.Image = bg_file.convert("RGBA").resize((canvas, canvas))
                                base.paste(bg, (0, 0), bg)
                except OSError:
                        pass
        if fg_data:
                try:
                        with Image.open(io.BytesIO(fg_data)) as fg_file:
                                fg: Image.Image = fg_file.convert("RGBA")
                        _paste_foreground(base, fg)
                except OSError:
                        return None
        offset = (_ADAPTIVE_CANVAS - _ADAPTIVE_VISIBLE) // 2
        box = (offset, offset, _ADAPTIVE_CANVAS - offset, _ADAPTIVE_CANVAS - offset)
        rounded = apply_rounded_mask(base.crop(box))
        buffer = io.BytesIO()
        rounded.save(buffer, format="PNG")
        return buffer.getvalue()


def app_info(adb: Adb, package: str, cache_root: Path | None = None) -> AppInfo:
        """Resolve label/version/icon for an installed app (with caching)."""
        apk_cache = (cache_root / "apks") if cache_root else apks_dir()
        icon_cache = (cache_root / "icons") if cache_root else icons_dir()
        apk_cache.mkdir(parents=True, exist_ok=True)
        icon_cache.mkdir(parents=True, exist_ok=True)

        device_path = parse_base_apk_path(adb.shell(f"pm path {package}"))
        if device_path is None:
                raise AdbError(f"package {package} is not installed on {adb.serial}")

        # Size guard: huge APKs (games) are not worth pulling just for an
        # icon; the panel keeps its letter avatar for them.
        size_stat = adb.shell(f"stat -c %s {device_path}").strip()
        try:
                if int(size_stat) > _MAX_ICON_APK_BYTES:
                        raise AdbError(
                                f"{package} apk too large for icon extraction "
                                f"({int(size_stat) // (1 << 20)} MB)"
                        )
        except ValueError:
                pass  # unexpected stat output - let the pull try

        apk_path = apk_cache / f"{package}.apk"
        meta_path = apk_cache / f"{package}.json"
        cached_path: str | None = None
        if meta_path.exists():
                try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        cached_path = meta.get("device_path")
                except (json.JSONDecodeError, OSError):
                        cached_path = None
        if not apk_path.exists() or cached_path != device_path:
                adb.pull(device_path, apk_path)
                meta_path.write_text(
                        json.dumps({"device_path": device_path}), encoding="utf-8"
                )

        aapt2 = aapt2_ensure((cache_root / "tools") if cache_root else None)
        result = subprocess.run(
                [str(aapt2), "dump", "badging", str(apk_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_RUN_TIMEOUT_S,
                check=False,
                creationflags=creation_flags(),
        )
        fields = parse_badging(result.stdout or "")

        label = fields.get("label") or package
        icon_ref = fields.get("icon") or ""
        icon_out = icon_cache / f"{package}{_ICON_CACHE_SUFFIX}"
        icon_path = extract_icon(apk_path, icon_ref, icon_out, aapt2) if icon_ref else None

        return AppInfo(
                package=package,
                label=label,
                version_name=fields.get("version_name"),
                icon_path=icon_path,
        )
