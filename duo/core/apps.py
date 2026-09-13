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

import contextlib
import json
import re
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duo.core.engine import is_wsl
from duo.core.paths import apks_dir, icons_dir, tools_dir
from duo.core.winproc import creation_flags

if TYPE_CHECKING:
        import PIL.Image

#: Icon cache filename suffix. ".r7" bumps the icon cache generation:
#: the primary acquisition path is now the on-device renderer DEX
#: (duo/resources/duo_icons.dex): the system renders each icon with its
#: own PackageManager - no APK pull, no aapt2, no 200MB guard - and the
#: PC side only crops/recolors/masks (RESEARCH-ICONS.md §9). Cached
#: ".r6" files are simply orphaned.
_ICON_CACHE_SUFFIX = ".r20.png"

#: On-device renderer (app_process DEX, same deployment shape as the
#: scrcpy server). Renders every package's launcher icon the way the
#: system launcher sees it - adaptive layers, insets, gradients and
#: obfuscated resources all handled natively.
_RENDER_DEX = Path(__file__).resolve().parent.parent / "resources" / "duo_icons.dex"
_DEVICE_OUT = "/data/local/tmp/duo_icons_out"
_DEVICE_LIST = "/data/local/tmp/duo_icons_pkgs.txt"

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


        def all_packages(self, timeout: float = _RUN_TIMEOUT_S) -> list[str]:
                """Every installed package (system apps included)."""
                out = self.run("shell", "pm list packages", timeout=timeout)
                names = [line.removeprefix("package:").strip() for line in out.splitlines()]
                return sorted({name for name in names if name})

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

        def push(self, local: Path, remote: str) -> None:
                """Push a local file to a remote path."""
                self.run("push", str(local), remote, timeout=_PULL_TIMEOUT_S)


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

#: Two file-entry forms in aapt2 resources dumps: the classic
#: ``(xxhdpi) (file) res/x.png type=PNG`` and the obfuscated-path variant
#: (AndResGuard, Tencent APKs) ``(mdpi) "r/m-m/b.webp"``. WebP entries
#: may carry no ``type=`` suffix at all.
_FILE_ENTRY = re.compile(r"\(([\w-]*)\) \(file\) (\S+?)(?: type=\w+)?\s*$")
_FILE_ENTRY_QUOTED = re.compile(r'\(([\w-]*)\) "([^"]+)"\s*$')


def parse_resource_files(resources_dump: str) -> dict[str, list[tuple[int, str]]]:
        """Map resource id -> [(density rank, raster file path)] from aapt2 dump."""
        files: dict[str, list[tuple[int, str]]] = {}
        current: str | None = None
        for line in resources_dump.splitlines():
                header = re.match(r"\s*resource (0x[0-9a-f]+) \S+", line)
                if header:
                        current = header.group(1)
                        continue
                entry = _FILE_ENTRY.search(line) or _FILE_ENTRY_QUOTED.search(line)
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


def resource_xml_file(resources_dump: str, res_id: str) -> str | None:
        """The XML file of resource ``res_id`` (vector drawables live here)."""
        current: str | None = None
        for line in resources_dump.splitlines():
                header = re.match(r"\s*resource (0x[0-9a-f]+) \S+", line)
                if header:
                        current = header.group(1)
                        continue
                entry = re.search(r"\(file\) (\S+\.xml) type=", line)
                if entry and current == res_id:
                        return entry.group(1)
        return None


# ---------------------------------------------------------------------------
# VectorDrawable rasterization (xmltree -> SVG -> Qt SVG -> RGBA).
# Vector-only adaptive icons (ChatGPT, Office, Telegram, WPS...) have no
# raster layer to read; without this they composite to a background-only
# white plate. Qt's SVG renderer is already a gui-extra dependency of the
# panel, so the conversion rides it and degrades to None without Qt.
# ---------------------------------------------------------------------------

_VECTOR_ATTR = re.compile(r"A: \S+:(\w+)\(0x[0-9a-f]+\)=(.*)$")

#: One parsed vector-drawable node tree - a group holds a transform plus
#: children, a path holds d/fill/opacity (RESEARCH-ICONS.md §8.5).
_VectorTree = dict[str, Any]
_VECTOR_ELEMENT = re.compile(r"E: (\w+)")


def _vector_attr_value(raw: str) -> str:
        """First quoted segment for string attrs, else the bare token."""
        quoted = re.match(r'"([^"]*)"', raw)
        return quoted.group(1) if quoted else raw.strip()


def _vector_color(
        raw: str, colors: dict[str, str]
) -> tuple[str, float | None] | None:
        """Resolve one aapt2 color value to (SVG hex, alpha or None)."""
        raw = raw.strip()

        def from_hex(value: str) -> tuple[str, float | None] | None:
                if len(value) == 9:   # Android #AARRGGBB
                        alpha = int(value[1:3], 16) / 255
                        return f"#{value[3:]}", alpha if alpha < 1 else None
                if len(value) == 7:
                        return value, None
                return None

        if raw.startswith("@0x"):
                resolved = colors.get(raw[1:].lower()) or colors.get(raw[1:])
                return from_hex(resolved) if resolved else None
        if raw.startswith("#"):
                return from_hex(raw)
        try:
                packed = int(raw) & 0xFFFFFFFF
        except ValueError:
                return None
        return from_hex(f"#{packed:08X}")


def _inset_drawable_ref(xmltree_dump: str) -> str | None:
        """Inner drawable resource id when the root is an inset wrapper."""
        saw_inset = False
        for line in xmltree_dump.splitlines():
                element = _VECTOR_ELEMENT.search(line)
                if element:
                        if element.group(1) == "inset" and not saw_inset:
                                saw_inset = True
                                continue
                        if saw_inset:
                                return None   # nested content: not a plain wrapper
                        continue
                attr = _VECTOR_ATTR.match(line.strip())
                if attr and saw_inset and attr.group(1) == "drawable":
                        ref = _vector_attr_value(attr.group(2))
                        if ref.startswith("@0x"):
                                return ref[1:]
                        return None
        return None


def parse_vector_tree(xmltree_dump: str) -> _VectorTree | None:
        """Structured vector drawable from its aapt2 xmltree dump.

        Returns {"viewport": (w, h), "body": <group>} where a group is
        {"transform": {...}, "children": [groups | paths]} and a path is
        {"d": str, "fill": str | None, "opacity": float | None}. ``None``
        when the root element is not a vector (bitmap layers etc.).
        """
        root: _VectorTree | None = None
        stack: list[_VectorTree] = []
        current: _VectorTree | None = None   # attrs land on the last-opened element
        for line in xmltree_dump.splitlines():
                element = _VECTOR_ELEMENT.search(line)
                if element:
                        name = element.group(1)
                        if name == "vector":
                                if stack or root is not None:
                                        return None   # nested/suspicious: bail
                                root = {"transform": {}, "children": []}
                                stack = [root]
                                current = root
                                continue
                        if not stack:
                                continue
                        node: _VectorTree
                        if name == "group":
                                node = {"transform": {}, "children": []}
                                stack[-1]["children"].append(node)
                                stack.append(node)
                                current = node
                        elif name == "path":
                                node = {"d": "", "fill": None, "opacity": None}
                                stack[-1]["children"].append(node)
                                current = node
                        elif name == "clip-path":
                                current = None   # full-canvas rects in practice; skip
                        else:
                                current = None
                        continue
                attr = _VECTOR_ATTR.match(line.strip())
                if attr and current is not None:
                        key, raw = attr.group(1), _vector_attr_value(attr.group(2))
                        target = current
                        if key in {"viewportWidth", "viewportHeight"}:
                                target[key] = raw
                        elif "transform" in target and key in {
                                "translateX", "translateY", "scaleX", "scaleY",
                                "rotation", "pivotX", "pivotY",
                        }:
                                target["transform"][key] = raw
                        elif "d" in target:
                                if key == "pathData":
                                        target["d"] = raw
                                elif key == "fillColor":
                                        target["fill"] = raw
                                elif key == "fillAlpha":
                                        with contextlib.suppress(ValueError):
                                                target["opacity"] = float(raw)
        if root is None:
                return None
        try:
                vw = float(root.get("viewportWidth", 0))
                vh = float(root.get("viewportHeight", 0))
        except (TypeError, ValueError):
                vw = vh = 0.0
        viewport = (vw, vh)
        if viewport[0] <= 0 or viewport[1] <= 0:
                return None
        return {"viewport": viewport, "body": root}


def _group_transform_svg(transform: dict[str, str]) -> str:
        """SVG transform list for one VectorDrawable group."""

        def num(key: str, default: float) -> float:
                try:
                        return float(transform.get(key, default))
                except (TypeError, ValueError):
                        return default

        parts: list[str] = []
        tx, ty = num("translateX", 0.0), num("translateY", 0.0)
        px, py = num("pivotX", 0.0), num("pivotY", 0.0)
        rotation = num("rotation", 0.0)
        sx, sy = num("scaleX", 1.0), num("scaleY", 1.0)
        if tx or ty:
                parts.append(f"translate({tx} {ty})")
        if px or py:
                parts.append(f"translate({px} {py})")
        if rotation:
                parts.append(f"rotate({rotation})")
        if sx != 1.0 or sy != 1.0:
                parts.append(f"scale({sx} {sy})")
        if px or py:
                parts.append(f"translate({-px} {-py})")
        return " ".join(parts)


def parse_gradient_tree(xmltree_dump: str) -> dict[str, Any] | None:
        """Gradient drawable from its aapt2 xmltree (fills for vector paths).

        Handles both stop-item children and start/center/endColor attrs.
        ``type``: 0 linear, 1 radial, 2 sweep (rendered as radial).
        """
        root: dict[str, Any] | None = None
        for line in xmltree_dump.splitlines():
                element = _VECTOR_ELEMENT.search(line)
                if element:
                        if element.group(1) == "gradient" and root is None:
                                root = {"type": 1, "angle": 0.0, "cx": 0.5, "cy": 0.5,
                                        "radius": 0.5, "stops": []}
                        continue
                attr = _VECTOR_ATTR.match(line.strip())
                if not attr or root is None:
                        continue
                key, raw = attr.group(1), _vector_attr_value(attr.group(2))
                if key in {"type", "angle", "centerX", "centerY", "gradientRadius"}:
                        with contextlib.suppress(ValueError):
                                root[key] = float(raw)
                elif key == "color":
                        with contextlib.suppress(ValueError):
                                root["stops"].append((0.0, raw))
                elif key in {"startColor", "centerColor", "endColor"}:
                        offset = {"startColor": 0.0, "centerColor": 0.5, "endColor": 1.0}[key]
                        root["stops"].append((offset, raw))
                elif key == "offset" and root["stops"]:
                        with contextlib.suppress(ValueError):
                                root["stops"][-1] = (float(raw), root["stops"][-1][1])
        if root is None or not root["stops"]:
                return None
        stops = sorted(root["stops"], key=lambda s: s[0])
        return {**root, "stops": stops}


def _svg_stop(color: str) -> str:
        """One SVG stop attrs string from #RRGGBB[AA] / #AARRGGBB."""
        if len(color) == 9:   # #AARRGGBB
                alpha = int(color[1:3], 16) / 255
                return f'stop-color="#{color[3:]}" stop-opacity="{alpha:g}"'
        return f'stop-color="{color}"'


def _gradient_defs(ref: str, gradient: dict[str, Any], vw: float, vh: float) -> tuple[str, str]:
        """(defs fragment, fill value) for one gradient in viewport units."""
        gid = f"g{abs(hash(ref)) % 100000}"
        stops = "\n".join(
                f'    <stop offset="{offset:g}" {_svg_stop(color)}/>'
                for offset, color in gradient["stops"]
        )
        kind = int(gradient.get("type", 1))
        if kind == 0:
                import math

                angle = math.radians(gradient.get("angle", 0.0))
                dx, dy = math.cos(angle), -math.sin(angle)
                cx, cy = vw / 2, vh / 2
                span = (vw + vh) / 2
                body = (
                        f'x1="{cx - dx * span:g}" y1="{cy - dy * span:g}" '
                        f'x2="{cx + dx * span:g}" y2="{cy + dy * span:g}"'
                )
                tag = "linearGradient"
        else:
                body = (
                        f'cx="{gradient.get("cx", 0.5) * vw:g}" '
                        f'cy="{gradient.get("cy", 0.5) * vh:g}" '
                        f'r="{max(gradient.get("radius", 0.5) * max(vw, vh), 1.0):g}"'
                )
                tag = "radialGradient"
        defs = (
                f'  <{tag} id="{gid}" gradientUnits="userSpaceOnUse" {body}>\n'
                f"{stops}\n  </{tag}>"
        )
        return defs, f"url(#{gid})"


def vector_svg(
        tree: _VectorTree,
        colors: dict[str, str],
        gradients: dict[str, dict[str, Any]] | None = None,
) -> str | None:
        """SVG document for a parsed vector tree (None without any path)."""
        from xml.sax.saxutils import escape

        vw, vh = tree["viewport"]
        gradients = gradients or {}
        used_defs: list[str] = []

        def path_fill(raw: object) -> tuple[str, float | None]:
                resolved = _vector_color(str(raw or ""), colors)
                if resolved is not None:
                        return resolved
                ref = str(raw or "")
                if ref.startswith("@0x") and ref[1:] in gradients:
                        defs, value = _gradient_defs(ref, gradients[ref[1:]], vw, vh)
                        if defs not in used_defs:
                                used_defs.append(defs)
                        return value, None
                return "#000000", None

        def render_group(group: _VectorTree, depth: int) -> list[str]:
                out: list[str] = []
                indent = "  " * depth
                transform = _group_transform_svg(group.get("transform", {}))
                if transform:
                        out.append(f'{indent}<g transform="{escape(transform)}">')
                for child in group["children"]:
                        if "children" in child:
                                out.extend(render_group(child, depth + (1 if transform else 0)))
                                continue
                        d = child.get("d", "")
                        if not d:
                                continue
                        fill, fill_alpha = path_fill(child.get("fill"))
                        attrs = f'd="{escape(str(d))}" fill="{fill}"'
                        opacity = child.get("opacity")
                        if opacity is not None and fill_alpha is not None:
                                opacity = float(opacity) * fill_alpha
                        elif opacity is None:
                                opacity = fill_alpha
                        if opacity is not None:
                                attrs += f' fill-opacity="{opacity:g}"'
                        out.append(f'{indent}  <path {attrs}/>')
                if transform:
                        out.append(f"{indent}</g>")
                return out

        body = render_group(tree["body"], 1)
        if not body:
                return None
        head = (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="0 0 {vw:g} {vh:g}">'
        )
        defs = ["<defs>", *used_defs, "</defs>"] if used_defs else []
        return "\n".join([head, *defs, *body, "</svg>"])


def rasterize_svg(svg: str, size: int) -> PIL.Image.Image | None:
        """Qt-side SVG rasterization; ``None`` whenever Qt is unavailable."""
        try:
                from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
                from PyQt6.QtGui import QImage, QPainter
                from PyQt6.QtSvg import QSvgRenderer
        except ImportError:
                return None
        try:
                renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
                if not renderer.isValid():
                        return None
                image = QImage(QSize(size, size), QImage.Format.Format_RGBA8888)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                renderer.render(painter)
                painter.end()
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, "PNG"):
                        return None
                import io

                import PIL.Image

                return PIL.Image.open(io.BytesIO(bytes(buffer.data())))  # type: ignore[call-overload]
        except (RuntimeError, ValueError, OSError):
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


def g2_outline(
        width: float, height: float, radius: float, exponent: float = 5.0
) -> list[tuple[float, float]]:
        """Outline of a rectangle whose corners are superellipse curves.

        The G2-style "smooth corner": inside each r×r corner box the
        boundary follows |u/r|^n + |v/r|^n = 1 (n = exponent), meeting the
        straight edges with zero curvature - no visible tangent break like
        a circular-arc rounded rectangle has at 60px. Points run clockwise
        starting at the left edge below the top-left corner.
        """
        import math

        def quarter(
                cx: float, cy: float, sx: float, sy: float, swap: bool
        ) -> list[tuple[float, float]]:
                points = []
                steps = 96
                for i in range(steps + 1):
                        theta = math.pi / 2 * i / steps
                        c = math.cos(theta) ** (2.0 / exponent)
                        s = math.sin(theta) ** (2.0 / exponent)
                        if swap:
                                c, s = s, c
                        points.append((cx + sx * r * c, cy + sy * r * s))
                return points

        r = min(radius, width / 2, height / 2)
        outline: list[tuple[float, float]] = []
        outline += quarter(r, r, -1, -1, False)        # TL: (0, r) -> (r, 0)
        outline.append((width - r, 0))                 # top edge
        outline += quarter(width - r, r, 1, -1, True)   # TR: (width-r, 0) -> (width, r)
        outline.append((width, height - r))             # right edge
        outline += quarter(width - r, height - r, 1, 1, False)  # BR
        outline.append((r, height))                     # bottom edge
        outline += quarter(r, height - r, -1, 1, True)  # BL: (r, height) -> (0, height-r)
        outline.append((0, r))                          # left edge
        return outline


def apply_rounded_mask(
        image: PIL.Image.Image, radius_ratio: float = 0.50
) -> PIL.Image.Image:
        """Return ``image`` with a G2 smooth-corner alpha mask applied.

        Every extracted icon gets one uniform base shape - a smooth-corner
        square with corner extent ``min(w, h) * radius_ratio`` (DESIGN.md
        §3.1: 23%, superellipse n=5, matching the preset/fallback
        templates). The image is neither resized nor cropped; opaque
        square sources (legacy rasters, the adaptive composite) simply
        lose their corners. The mask multiplies the existing alpha and is
        drawn at 4x resolution, scaled back with LANCZOS so the curves
        stay smooth at launcher sizes.
        """
        from PIL import Image, ImageChops, ImageDraw

        base = image.convert("RGBA")   # convert always copies: no caller aliasing
        w, h = base.size
        radius = round(min(w, h) * radius_ratio)
        scale = 6  # supersampling factor for the antialiased curve edges
        outline = [
                (x * scale, y * scale)
                for x, y in g2_outline(w, h, radius)
        ]
        mask = Image.new("L", (w * scale, h * scale), 0)
        ImageDraw.Draw(mask).polygon(outline, fill=255)
        rounded = mask.resize((w, h), Image.Resampling.LANCZOS)
        rounded = rounded.point(lambda value: 0 if value < 4 else value)
        base.putalpha(ImageChops.multiply(base.getchannel("A"), rounded))
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


# ---------------------------------------------------------------------------
# Generative unification of legacy raster icons (RESEARCH-ICONS.md §6 P0-2).
# Two failure shapes get fixed, everything else passes through untouched:
#   * solid near-white background -> flood the bg from the borders and
#     recolour it to a pastel tint of the content's dominant colour, so
#     the tile stops evaporating on the light panel;
#   * floating colour blob (circle/squircle logo on transparency, e.g.
#     Coolapk) -> paste it scaled to _PLATE_CONTENT_RATIO onto a
#     same-colour plate: shape and visual size join the preset system.
# ---------------------------------------------------------------------------

#: Pasted content occupies this fraction of the plate's larger dimension
#: (75% sits inside the 62-78% band well-designed adaptive foregrounds
#: use, so plated blobs read the same size as adaptive content).
_PLATE_CONTENT_RATIO = 0.75
#: Summed RGB distance under which a pixel counts as background ink.
_BG_TOLERANCE = 60
#: Luma/chroma bounds for "near white" (recolor) plate decisions.
_NEAR_WHITE_LUMA = 224
_NEAR_NEUTRAL_CHROMA = 18
#: Rasters below this size skip normalization (too small to trust).
_MIN_NORMALIZE_SIZE = 48
#: Content spans >= this fraction of both axes AND fills >= 90% of its
#: bbox -> authored full-bleed artwork; the mask alone is the treatment.
_FULL_BLEED_SPAN = 0.96
_FULL_BLEED_COVERAGE = 0.90

#: Plate for white-bg icons whose content is entirely neutral ink - HSL
#: (215°, 0.16, 0.87): the shared pastel-tint formula evaluated at a
#: blue-less hue so it reads as cool silver, not a specific colour.
_NEUTRAL_PLATE = (217, 221, 227)
#: Blob pasting needs the content to be a filled shape, not line art.
_BLOB_COVERAGE = 0.50


def _luma(rgb: tuple[int, int, int]) -> int:
        return (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) // 1000


def _is_near_white(rgb: tuple[int, int, int]) -> bool:
        return _luma(rgb) >= _NEAR_WHITE_LUMA and max(rgb) - min(rgb) <= _NEAR_NEUTRAL_CHROMA


def _is_neutral(rgb: tuple[int, int, int]) -> bool:
        """Near-white or near-black: useless as a plate colour."""
        chroma = max(rgb) - min(rgb)
        luma = _luma(rgb)
        return chroma <= _NEAR_NEUTRAL_CHROMA and (luma >= _NEAR_WHITE_LUMA or luma <= 40)


def _tint_plate(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
        """Pastel plate colour for white-bg icons: hue-true, presence-calibrated.

        HSL keeps the source hue where RGB lightening drifts; the clamped
        saturation lands every hue at ΔE 13-16 from the light canvas - the
        "visible but calm" band (RESEARCH-ICONS.md §8.2, agy Opus + glm
        cross-review 2026-09-13).
        """
        import colorsys

        hue, _, sat = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
        sat = min(0.38, max(0.16, sat * 0.55))
        return tuple(round(c * 255) for c in colorsys.hls_to_rgb(hue, 0.87, sat))  # type: ignore[return-value]


def _solid_border_color(image: PIL.Image.Image) -> tuple[int, int, int] | None:
        """Modal border colour when the border ring is one flat colour."""
        from collections import Counter

        w, h = image.size
        data = image.tobytes()
        border: Counter[tuple[int, int, int]] = Counter()
        step = max(1, min(w, h) // 36)

        def sample(x: int, y: int) -> None:
                o = (y * w + x) * 4
                border[(data[o], data[o + 1], data[o + 2])] += 1

        for x in range(0, w, step):
                sample(x, 0)
                sample(x, h - 1)
        for y in range(0, h, step):
                sample(0, y)
                sample(w - 1, y)
        if not border:
                return None
        majority = sum(border.values())
        # A gradient border (Amap's green->white diagonal wash) must not be
        # treated as a "white base": flooding a mean colour repaints part
        # of it and leaves a half-tinted mess. Any channel spread across
        # the border ring disqualifies the uniform-base assumption.
        channels = list(zip(*border.keys(), strict=True))
        if max(max(c) - min(c) for c in channels) > 40:
                return None
        color, count = border.most_common(1)[0]
        # White-base icons with a brand band along one edge (OPPO store:
        # white top + green banner) still have white as a large border
        # MINORITY: any near-white family holding >= 40% of the border
        # floods from its mean and keeps the band as content. Other
        # colours keep the strict 85% uniform demand.
        whites = [c for c, n in border.items() if _is_near_white(c)]
        white_count = sum(n for c, n in border.items() if _is_near_white(c))
        if white_count >= majority * 0.4:
                total = sum(border[c] for c in whites)
                return (
                        sum(c[0] * border[c] for c in whites) // total,
                        sum(c[1] * border[c] for c in whites) // total,
                        sum(c[2] * border[c] for c in whites) // total,
                )
        return color if count >= majority * 0.85 else None


def _flood_bg_mask(image: PIL.Image.Image, bg: tuple[int, int, int]) -> bytearray:
        """Indices of background pixels connected to the border (flood fill).

        Only border-reached pixels count, so interior ink matching the bg
        colour (a white ring inside a logo) survives the recolour.
        """
        w, h = image.size
        data = image.tobytes()
        stride = 4
        mask = bytearray(w * h)
        br, bgc, bb = bg
        tol = _BG_TOLERANCE

        def close(i: int) -> bool:
                o = i * stride
                return (
                        abs(data[o] - br) + abs(data[o + 1] - bgc) + abs(data[o + 2] - bb) <= tol
                )

        stack: list[int] = []
        for x in range(w):
                stack.extend((x, (h - 1) * w + x))
        for y in range(h):
                stack.extend((y * w, y * w + w - 1))
        while stack:
                i = stack.pop()
                if mask[i] or not close(i):
                        continue
                mask[i] = 1
                x, y = i % w, i // w
                if x:
                        stack.append(i - 1)
                if x < w - 1:
                        stack.append(i + 1)
                if y:
                        stack.append(i - w)
                if y < h - 1:
                        stack.append(i + w)
        return mask


def _mask_content_stats(
        mask: bytearray, w: int, h: int
) -> tuple[tuple[int, int, int, int] | None, float]:
        """Content bbox over a 1-per-pixel selection mask, plus bbox coverage."""
        xs: list[int] = []
        ys: list[int] = []
        count = 0
        for i, selected in enumerate(mask):
                if not selected:
                        xs.append(i % w)
                        ys.append(i // w)
                        count += 1
        if not count:
                return None, 0.0
        bbox = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        coverage = count / ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        return bbox, coverage


def _dominant_color(
        image: PIL.Image.Image, mask: bytearray | None = None
) -> tuple[int, int, int] | None:
        """Modal chromatic colour of the selected pixels, neutral inks skipped."""
        from collections import defaultdict

        w, h = image.size
        data = image.tobytes()
        buckets: dict[tuple[int, int, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for i in range(w * h):
                if mask is not None and mask[i]:
                        continue
                o = i * 4
                rgb = (data[o], data[o + 1], data[o + 2])
                if data[o + 3] < 128 or _is_neutral(rgb):
                        continue
                key = (rgb[0] >> 4, rgb[1] >> 4, rgb[2] >> 4)
                bucket = buckets[key]
                bucket[0] += 1
                bucket[1] += rgb[0]
                bucket[2] += rgb[1]
                bucket[3] += rgb[2]
        if not buckets:
                return None
        count, rsum, gsum, bsum = max(buckets.values(), key=lambda b: b[0])
        if not count:
                return None
        return (rsum // count, gsum // count, bsum // count)


def _resize_over(
        content: PIL.Image.Image,
        size: tuple[int, int],
        backdrop: PIL.Image.Image,
) -> PIL.Image.Image:
        """LANCZOS-resize ``content`` to ``size`` composited over ``backdrop``.

        Resizing straight-alpha RGBA directly lets transparent pixels' RGB
        bleed into the filter (a black fringe wherever the art's hidden RGB
        is black), because LANCZOS runs per channel without premultiplying.
        This composites in premultiplied space instead - mathematically the
        standard ``over`` operator - using only ImageChops primitives.
        ``backdrop`` must already be ``size`` and is treated as opaque.
        """
        from PIL import Image, ImageChops

        red, green, blue = content.split()[:3]
        alpha = content.getchannel("A")
        premult = Image.merge(
                "RGB",
                (
                        ImageChops.multiply(red, alpha),
                        ImageChops.multiply(green, alpha),
                        ImageChops.multiply(blue, alpha),
                ),
        ).resize(size, Image.Resampling.LANCZOS)
        alpha_resized = alpha.resize(size, Image.Resampling.LANCZOS)
        inverse = alpha_resized.point(lambda value: 255 - value)
        back = backdrop.convert("RGB")

        def over(channel: PIL.Image.Image, base_channel: PIL.Image.Image) -> PIL.Image.Image:
                return ImageChops.add(
                        channel, ImageChops.multiply(base_channel, inverse)
                )

        merged = Image.merge(
                "RGB",
                (
                        over(premult.getchannel("R"), back.getchannel("R")),
                        over(premult.getchannel("G"), back.getchannel("G")),
                        over(premult.getchannel("B"), back.getchannel("B")),
                ),
        )
        return merged.convert("RGBA")


def _is_monochrome_art(
        blob: PIL.Image.Image, dominant: tuple[int, int, int], tolerance: float = 48.0
) -> bool:
        """True when nearly all opaque pixels sit within one colour bucket.

        EasyTier is a bright-blue mesh on transparency; ChatGPT-style line
        art behaves the same. The dominant colour is the ART, not a plate
        candidate.
        """
        data = blob.tobytes()
        near = total = 0
        t2 = tolerance * tolerance
        for i in range(0, len(data), 12):   # sample every 3rd pixel
                if data[i + 3] < 200:
                        continue
                total += 1
                d2 = (
                        (data[i] - dominant[0]) ** 2
                        + (data[i + 1] - dominant[1]) ** 2
                        + (data[i + 2] - dominant[2]) ** 2
                )
                if d2 < t2:
                        near += 1
        if total == 0 or near / total < 0.85:
                return False
        # A SOLID monochrome blob (Coolapk's green disc with a small
        # white core) is a plate in its own right - it keeps the dominant
        # plate. Only sparse, hollowed line art needs the white base.
        sampled = len(range(0, len(data), 12))
        return total / sampled < 0.55


def _plate_compose(
        content: PIL.Image.Image, plate: tuple[int, int, int], canvas: tuple[int, int]
) -> PIL.Image.Image:
        """Centred content at _PLATE_CONTENT_RATIO on a solid-colour plate."""
        from PIL import Image

        base = Image.new("RGBA", canvas, (*plate, 255))
        limit = round(min(canvas) * _PLATE_CONTENT_RATIO)
        scale = min(limit / max(content.size), 1.0)
        size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
        backdrop = Image.new("RGB", size, plate)
        base.paste(_resize_over(content, size, backdrop), (
                (canvas[0] - size[0]) // 2,
                (canvas[1] - size[1]) // 2,
        ))
        return base


def _is_full_bleed(
        bbox: tuple[int, int, int, int], coverage: float, w: int, h: int
) -> bool:
        wide = (bbox[2] - bbox[0]) >= w * _FULL_BLEED_SPAN
        tall = (bbox[3] - bbox[1]) >= h * _FULL_BLEED_SPAN
        return wide and tall and coverage >= _FULL_BLEED_COVERAGE


def _is_disc_content(
        bbox: tuple[int, int, int, int], coverage: float, w: int, h: int
) -> bool:
        """True when the content is one big disc (Telegram/Quark style)."""
        content_w = bbox[2] - bbox[0]
        content_h = bbox[3] - bbox[1]
        aspect = content_w / max(1, content_h)
        return (
                0.9 <= aspect <= 1.1
                and 0.60 <= coverage <= 0.90
                and min(content_w, content_h) >= min(w, h) * 0.65
        )


def _dilate_mask(mask: bytearray, w: int, h: int) -> bytearray:
        """One-pixel 8-neighbour dilation of a flood mask."""
        from PIL import Image, ImageFilter

        base = Image.frombytes("L", (w, h), bytes(mask))
        return bytearray(base.filter(ImageFilter.MaxFilter(3)).tobytes())


def _recolor_white_bg(image: PIL.Image.Image) -> PIL.Image.Image:
        """Normalise a near-white solid background, content kept.

        Policy (2026-09-13, user review): a white base is BRAND - Quark,
        the ColorOS calendar, Youku all read best on their official white.
        The pass therefore only fills transparent corners with the border
        white so the G2 corners cut clean; a sheet with no ink at all
        becomes the neutral plate so it never reads as an empty hole.
        """
        w, h = image.size
        bg = _solid_border_color(image)
        if bg is None or not _is_near_white(bg):
                return image
        mask = _flood_bg_mask(image, bg)
        bbox, coverage = _mask_content_stats(mask, w, h)
        if bbox is None:
                from PIL import Image as _Image

                return _Image.new("RGBA", image.size, _NEUTRAL_PLATE + (255,))
        # White base with real ink: keep it as-is (brand-faithful), only
        # flattening any transparent corners onto the white.
        alpha = image.getchannel("A")
        if alpha.point(lambda v: 255 if v < 250 else 0).getbbox() is None:
                return image
        return _flatten_white(image)


def _edge_ring_color(image: PIL.Image.Image) -> tuple[int, int, int] | None:
        """Mean colour of opaque pixels within 3px of the alpha edge.

        A gradient disc (Email's blue sweep) must fuse into a plate of its
        OWN EDGE colour - a mean-colour plate shows a tonal seam where the
        sweep meets the fill.
        """
        from PIL import ImageChops, ImageFilter

        alpha = image.getchannel("A").point(lambda v: 255 if v > 200 else 0)
        ring = ImageChops.subtract(
                alpha, alpha.filter(ImageFilter.MinFilter(7))
        )
        if ring.getbbox() is None:
                return None
        rgb = image.convert("RGB")
        data = rgb.tobytes()
        ring_data = ring.tobytes()
        rs = gs = bs = count = 0
        w, h = image.size
        for i in range(w * h):
                if ring_data[i]:
                        o = i * 3
                        # Skip near-white pixels: the disc's solid-white
                        # outline (Weather) would wash the mean towards a
                        # pale plate colour.
                        if _is_near_white((data[o], data[o + 1], data[o + 2])):
                                continue
                        rs += data[o]
                        gs += data[o + 1]
                        bs += data[o + 2]
                        count += 1
        if not count:
                return None
        return (rs // count, gs // count, bs // count)


def _defringe_to(image: PIL.Image.Image, colour: tuple[int, int, int]) -> PIL.Image.Image:
        """Repaint semitransparent pixels' RGB to ``colour``.

        A device render's antialiased edge carries whatever RGB the source
        art had (black corners on Quark's transparent triangle, white on
        the Weather disc): compositing that over a plate or a white
        flatten mixes a grey/pale fringe into the tile. Repainting the RGB
        (alpha untouched) keeps the smooth alpha ramp but makes the blend
        run content-colour -> plate-colour with no foreign hue.
        """
        from PIL import Image

        out = image.convert("RGBA")
        alpha = out.getchannel("A")
        solid = Image.new("RGB", out.size, colour)
        soft = alpha.point(lambda v: 255 if v < 250 else 0)
        if soft.getbbox() is None:
                return out
        out.paste(solid, (0, 0), soft)
        return out


def _repaint_ring_whites(
        image: PIL.Image.Image, colour: tuple[int, int, int]
) -> PIL.Image.Image:
        """Repaint near-white pixels hugging the transparency boundary.

        Device renders of full-width discs carry a 1-2px solid-white
        apron just inside their antialiased edge (Weather): alpha is 255
        there so the defringe pass skips them, and the median field in
        _crisp_edges reads white around them too. Only their position -
        within 3px of transparency - identifies them.
        """
        from PIL import Image, ImageChops, ImageFilter

        solid = image.getchannel("A").point(lambda v: 255 if v > 200 else 0)
        ring = ImageChops.subtract(
                solid, solid.filter(ImageFilter.MinFilter(7))
        )
        data = bytearray(image.tobytes())
        w, h = image.size
        ring_data = ring.tobytes()
        for i, on in enumerate(ring_data):
                if not on:
                        continue
                o = i * 4
                if _is_near_white((data[o], data[o + 1], data[o + 2])):
                        data[o] = colour[0]
                        data[o + 1] = colour[1]
                        data[o + 2] = colour[2]
        return Image.frombytes("RGBA", (w, h), bytes(data))


def _mean_opaque_color(image: PIL.Image.Image) -> tuple[int, int, int] | None:
        """Mean colour of opaque pixels, neutrals included (plate bases)."""
        data = image.tobytes()
        rs = gs = bs = count = 0
        for i in range(image.width * image.height):
                o = i * 4
                if data[o + 3] > 200:
                        rs += data[o]
                        gs += data[o + 1]
                        bs += data[o + 2]
                        count += 1
        if not count:
                return None
        return (rs // count, gs // count, bs // count)


def _flatten_white(image: PIL.Image.Image) -> PIL.Image.Image:
        """Fill transparent corners with the border's modal colour.

        A rounded-SQUARE icon (Weather: blue plate, only tiny transparent
        corners) must regrow its corners in the plate colour - white
        corners would show as a pale arc after the G2 mask cuts. White
        bases (Quark) keep white corners: the modal border colour IS
        white. Semitransparent edge RGB is repainted to the same colour
        first (black corner RGB would mix into grey dots otherwise).
        """
        from PIL import Image

        # Plate rule for transparent-cornerned art: an OPAQUE border's
        # modal colour may regrow the corners directly (Quark's white).
        # A floating blob's border reads transparent-black, so the mean
        # fallback is only trustworthy when the art is mostly opaque -
        # i.e. the mean IS a plate (Weather's blue sheet, 95% opaque).
        # Sparse art (EasyTier's mesh, 35%) would plate on its own line
        # colour and swallow it - white is the only safe corner fill.
        colour = _solid_border_color(image)
        if colour is None or max(colour) < 40:
                alpha = image.getchannel("A").point(lambda v: 255 if v > 200 else 0)
                cov = sum(alpha.tobytes()) / 255 / (image.width * image.height)
                if cov >= 0.70:
                        colour = _mean_opaque_color(image) or (255, 255, 255)
                else:
                        colour = (255, 255, 255)
        treated = _repaint_ring_whites(_defringe_to(image, colour), colour)
        plate = Image.new("RGBA", image.size, (*colour, 255))
        return Image.alpha_composite(plate, treated)


def normalize_raster(image: PIL.Image.Image) -> PIL.Image.Image:
        """Unify one legacy raster icon into the squircle system (or pass it through).

        Full design rationale: docs/ui/RESEARCH-ICONS.md §6 P0-2.
        """
        w, h = image.size
        if min(w, h) < _MIN_NORMALIZE_SIZE:
                return image
        has_transparency = (
                image.getchannel("A").point(lambda v: 255 if v < 250 else 0).getbbox()
                is not None
        )
        if has_transparency:
                bbox = _alpha_bbox(image)
                if bbox is None:
                        return image
                blob = image.crop(bbox)
                blob_alpha = blob.getchannel("A").point(lambda v: 255 if v > 8 else 0)
                coverage = sum(blob_alpha.tobytes()) / 255 / (blob.width * blob.height)
                # A silhouette that already spans the canvas (ColorOS
                # calculator: a full-width dark disc) keeps its size and
                # gets the canvas flooded with its own colour - pastel for
                # light art, the raw colour for dark art so the tile keeps
                # its dark identity. The G2 mask then cuts clean corners.
                # ...but only for DARK silhouettes. A plate fill only shows
                # through the transparent corners; a light silhouette is a
                # white-base raster (Quark) where the opaque white covers
                # any plate - those must fall through to the flood recolour.
                span = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / max(w, h)
                if span >= 0.95 and coverage >= 0.40:
                        colour = _edge_ring_color(image)
                        if colour is None:
                                colour = _mean_opaque_color(image)
                        if colour is not None:
                                luma = (
                                        0.299 * colour[0]
                                        + 0.587 * colour[1]
                                        + 0.114 * colour[2]
                                ) / 255
                                if luma < 0.45:
                                        from PIL import Image as _Image

                                        plate_img = _Image.new("RGBA", (w, h), (*colour, 255))
                                        treated = _defringe_to(image, colour)
                                        treated = _repaint_ring_whites(treated, colour)
                                        return _Image.alpha_composite(plate_img, treated)
                # Every pass-through exit flattens onto white first and
                # retries the recolour: full-bleed WHITE bases (Quark),
                # sparse neutral ink (ColorOS calendar) and thin content
                # (OTA assistant) all look like holes otherwise. Colourful
                # full-bleed bases stay untouched: flattening only adds
                # white corners and the flood leaves them irrelevant.
                if _is_full_bleed(bbox, coverage, w, h):
                        return _recolor_white_bg(_flatten_white(image))
                if coverage < _BLOB_COVERAGE:
                        return _recolor_white_bg(_flatten_white(image))
                dominant = _dominant_color(blob)
                if dominant is None or _is_neutral(dominant):
                        return _recolor_white_bg(_flatten_white(image))
                # Plate-vs-content contrast is the one inviolable rule of
                # plate selection. A monochrome line logo (EasyTier's
                # bright-blue mesh) IS its dominant colour: plating on it
                # swallows the art into a solid tile. Monochrome art gets
                # the faithful white base instead.
                if _is_monochrome_art(blob, dominant):
                        return _plate_compose(
                                _repaint_ring_whites(blob, (255, 255, 255)),
                                (255, 255, 255),
                                (w, h),
                        )
                return _plate_compose(
                        _repaint_ring_whites(blob, dominant), dominant, (w, h)
                )
        bg = _solid_border_color(image)
        if bg is None:
                return image
        return _recolor_white_bg(image)


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
                layer = _resize_over(fg, (base.width, base.height), base.convert("RGB"))
                base.paste(layer, (0, 0))
                return
        content = fg.crop(bbox)
        scale = base.width / max(fg.size)
        if max(content.size) * scale > _ADAPTIVE_SAFE:
                scale = _ADAPTIVE_SAFE / max(content.size)
        size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
        x0 = (base.width - size[0]) // 2
        y0 = (base.height - size[1]) // 2
        backdrop = base.convert("RGB").crop((x0, y0, x0 + size[0], y0 + size[1]))
        base.paste(_resize_over(content, size, backdrop), (x0, y0))


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
                argv = ["dump", "xmltree", "--file", icon_ref, str(apk_path)]
                tree = _aapt2_output(aapt2, argv)
                refs = parse_adaptive_refs(tree) if tree else {}

                def layer(
                        ref_id: str | None,
                ) -> tuple[bytes | PIL.Image.Image | None, PIL.Image.Image | None]:
                        """(raster bytes, vector-rendered image) for one layer ref."""
                        if not ref_id:
                                return None, None
                        raster = best_resource_file(dump, ref_id)
                        data = read(raster) if raster else None
                        if data:
                                return data, None
                        xml = resource_xml_file(dump, ref_id)
                        if not xml:
                                return None, None
                        vtree = _aapt2_output(
                                aapt2, ["dump", "xmltree", "--file", xml, str(apk_path)]
                        )
                        parsed = parse_vector_tree(vtree) if vtree else None
                        if parsed is None:
                                # Inset wrappers (Office suite) just pad their
                                # inner drawable - unwrap and recurse; the
                                # alpha-bbox centring absorbs the padding.
                                inner = _inset_drawable_ref(vtree) if vtree else None
                                if inner:
                                        return layer(inner)
                                return None, None
                        colors = parse_resource_colors(dump)

                        def collect_gradient_refs(node: _VectorTree, acc: set[str]) -> None:
                                for child in node.get("children", []):
                                        fill = str(child.get("fill") or "")
                                        if fill.startswith("@0x") and fill[1:] not in colors:
                                                acc.add(fill[1:])
                                        if "children" in child:
                                                collect_gradient_refs(child, acc)

                        refs_needed: set[str] = set()
                        collect_gradient_refs(parsed["body"], refs_needed)
                        gradients: dict[str, dict[str, Any]] = {}
                        for ref in refs_needed:
                                gxml = resource_xml_file(dump, ref)
                                if not gxml:
                                        continue
                                gtree = _aapt2_output(
                                        aapt2, ["dump", "xmltree", "--file", gxml, str(apk_path)]
                                )
                                gradient = parse_gradient_tree(gtree) if gtree else None
                                if gradient is not None:
                                        gradients[ref] = gradient
                        svg = vector_svg(parsed, colors, gradients)
                        rendered = rasterize_svg(svg, _ADAPTIVE_CANVAS) if svg else None
                        return None, rendered

                fg_data, fg_vector = layer(refs.get("foreground"))
                bg_data, bg_vector = layer(refs.get("background"))
                bg_color = None
                if refs.get("background") and bg_data is None and bg_vector is None:
                        bg_color = parse_resource_colors(dump).get(refs["background"])
                # Adaptive layers composite FIRST - the legacy raster variant
                # of the same resource is circle-era art that must not shadow
                # the authored modern artwork (RESEARCH-ICONS.md §6). But a
                # composite needs BOTH sides: a foreground floating on the
                # white default canvas loses the icon's background identity,
                # so an unresolvable bg (layer-list etc.) defers to a legacy
                # raster when one exists; with nothing better the composite
                # still runs (white plate beats no icon).
                composed = None
                has_fg = fg_data is not None or fg_vector is not None
                has_bg = bg_data is not None or bg_vector is not None or bg_color is not None
                if tree is not None and ((has_fg and has_bg) or legacy is None):
                        composed = _compose_adaptive(
                                fg_vector if fg_vector is not None else fg_data,
                                bg_vector if bg_vector is not None else bg_data,
                                bg_color,
                        )
                if composed is not None:
                        out_png.write_bytes(composed)
                        return out_png
                if legacy:
                        ref = legacy
                else:
                        return None

        data = read(ref)
        if data is None:
                return None
        try:
                with Image.open(io.BytesIO(data)) as image:
                        base = image if image.mode == "RGBA" else image.convert("RGBA")
                        apply_rounded_mask(normalize_raster(base)).save(out_png, format="PNG")
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
        fg_data: bytes | PIL.Image.Image | None,
        bg_data: bytes | PIL.Image.Image | None,
        bg_color: str | None,
) -> bytes | None:
        """Composite adaptive layers: 108-unit canvas, visible centre 72.

        Layers arrive as raster bytes or as an already-rendered RGBA image
        (vector drawables rasterize before this point). The canvas is 432px
        (4px per 108-unit cell) so the 72-unit visible centre is an exact
        288px integer crop - the retired 512/341 pair forced a 341.33
        fractional scale that accumulated aliasing whenever the cached PNG
        was resized again for display (RESEARCH-ICONS.md P2-5). The
        background layer still stretches full-canvas (it is backdrop by
        design); the foreground goes through :func:`_paste_foreground`,
        which keys on its actual content, so layers with lopsided
        transparent padding no longer render off-centre. A near-white
        background layer gets the pastel recolour (RESEARCH-ICONS.md §8.4)
        before the crop, and the flattened crop then goes through
        :func:`apply_rounded_mask`.
        """
        import io

        try:
                from PIL import Image
        except ImportError:
                return None

        def as_image(data: bytes | PIL.Image.Image | None) -> PIL.Image.Image | None:
                if data is None:
                        return None
                if isinstance(data, bytes):
                        try:
                                with Image.open(io.BytesIO(data)) as file:
                                        return file.convert("RGBA")
                        except OSError:
                                return None
                return data.convert("RGBA")

        canvas = _ADAPTIVE_CANVAS
        base = Image.new("RGBA", (canvas, canvas), bg_color or "#FFFFFF")
        bg_img = as_image(bg_data)
        if bg_img is not None:
                backdrop = Image.new("RGB", (canvas, canvas), bg_color or "#FFFFFF")
                base.paste(_resize_over(bg_img, (canvas, canvas), backdrop), (0, 0))
        fg_img = as_image(fg_data)
        if fg_img is not None:
                _paste_foreground(base, fg_img)
        base = _recolor_white_bg(base)
        offset = (_ADAPTIVE_CANVAS - _ADAPTIVE_VISIBLE) // 2
        box = (offset, offset, _ADAPTIVE_CANVAS - offset, _ADAPTIVE_CANVAS - offset)
        rounded = apply_rounded_mask(base.crop(box))
        buffer = io.BytesIO()
        rounded.save(buffer, format="PNG")
        return buffer.getvalue()


def parse_renderer_meta(labels_text: str) -> dict[str, dict[str, object]]:
        """Parse the renderer's labels.txt into per-package metadata."""
        meta: dict[str, dict[str, object]] = {}
        for line in labels_text.splitlines():
                parts = line.split("\t")
                if len(parts) < 5:
                        continue
                pkg, kind, version, version_name, label = parts[:5]
                meta[pkg] = {
                        "kind": kind,
                        "version": version,
                        "version_name": version_name,
                        "label": label,
                }
        return meta


#: Adaptive fg silhouette band, as a share of the visible 288px square.
#: Apps place their fg anywhere in ~0.55-0.78; beyond _ADAPTIVE_FG_SPAN the
#: art crowds its tile (bilibili/Google/Flexcil "obviously enlarged"),
#: below _ADAPTIVE_FG_FLOOR it starves it. The compose step gathers every
#: silhouette into one band so launcher tiles read at one visual size.
_ADAPTIVE_FG_SPAN = 0.72
_ADAPTIVE_FG_TARGET = 0.66


def _strip_ground_tone(
        layer: PIL.Image.Image, plate_pixel: tuple[int, ...]
) -> PIL.Image.Image:
        """Drop fg pixels that merely repeat the plate colour.

        Some apps paint their fg layer on a patch of the SAME colour as
        the bg layer (bilibili: the pink TV face on a pink bg): the patch
        edges then read as a colour shift. When the matching share is
        large the patch is transparentised so the plate shows through and
        the tone becomes continuous.
        """
        from PIL import Image, ImageChops

        solid = Image.new("RGB", layer.size, plate_pixel[:3])
        diff = ImageChops.difference(layer.convert("RGB"), solid).convert("L")
        close = diff.point(lambda v: 255 if v < 28 else 0)
        alpha = layer.getchannel("A")
        opaque = alpha.point(lambda v: 255 if v > 200 else 0)
        matching = ImageChops.multiply(close, opaque)
        hist = matching.histogram()
        matched = hist[255]
        total = alpha.point(lambda v: 255 if v > 200 else 0).histogram()[255]
        if total == 0 or matched / total < 0.40:
                return layer
        stripped = layer.copy()
        stripped.putalpha(ImageChops.subtract(alpha, matching))
        return stripped


def _compose_layers(
        fg: PIL.Image.Image, bg: PIL.Image.Image
) -> PIL.Image.Image:
        """Compose adaptive fg/bg layer renders into one 288px plate.

        The DEX ships the two adaptive layers as separate renders; the fg
        alpha bbox is the artwork's true silhouette, which the flattened
        composite loses. ``bg`` becomes the plate (near-white layers take
        the pastel recolour), and the fg is scaled so its silhouette spans
        the uniform band, then centred.
        """
        from PIL import Image

        offset = (_ADAPTIVE_CANVAS - _ADAPTIVE_VISIBLE) // 2
        box = (offset, offset, _ADAPTIVE_CANVAS - offset, _ADAPTIVE_CANVAS - offset)
        plate = _recolor_white_bg(bg.convert("RGBA").crop(box))
        layer = fg.convert("RGBA").crop(box)
        bbox = layer.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if bbox is None:
                return plate
        plate_empty = plate.getchannel("A").getextrema()[1] == 0
        # A bg layer with zero ink (plain white or fully transparent) means
        # the white IS the official base (BuBeiDanCi's stamp, ChatGPT's
        # knot, Sam Helper's art carries its own pale-blue block in the
        # fg). Restore white rather than the neutral plate - and never
        # borrow the fg's mean colour as a plate: an orange stamp would
        # swallow the whole tile.
        sample = plate.getpixel((2, 2))
        plate_blank = isinstance(sample, tuple) and sample[:3] == _NEUTRAL_PLATE
        if plate_empty or plate_blank:
                from PIL import Image as _Image

                plate = _Image.new("RGBA", plate.size, (255, 255, 255, 255))
        # Drop fg patches that just repeat the plate colour (bilibili's
        # pink face on a pink bg) so the tone stays continuous, then
        # re-measure the silhouette the strip may have shrunk.
        centre = plate.getpixel((plate.width // 2, plate.height // 2))
        assert isinstance(centre, tuple)
        layer = _strip_ground_tone(layer, centre)
        bbox = layer.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if bbox is None:
                return plate
        span = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / _ADAPTIVE_VISIBLE
        # An inkless bg handed the WHOLE design to the fg: the stamp IS
        # the official tile at its native size (BuBeiDanCi 0.78) - the
        # 0.66 band would shrink it and read as a rendering bug.
        if span <= _ADAPTIVE_FG_SPAN or span >= 0.95 or plate_empty or plate_blank:
                # Within the band - or spanning the full artwork, which is
                # the design itself (Youku's full-bleed disc): never
                # upscale either, a small fg layer is often a decoration
                # artefact (Telegram's stray dark patch).
                return Image.alpha_composite(plate, layer)
        scale = _ADAPTIVE_FG_TARGET / span
        art = layer.crop(bbox)
        size = (
                max(1, round(art.width * scale)),
                max(1, round(art.height * scale)),
        )
        # The premultiply backdrop must match the plate, not black: the
        # art's transparent fringe would otherwise bake black into the tile.
        pixel = plate.getpixel((4, 4))
        assert isinstance(pixel, tuple)
        plate_rgb = pixel[:3]
        positioned = _resize_over(art, size, Image.new("RGB", size, plate_rgb))
        canvas = plate.copy()
        canvas.alpha_composite(
                positioned, ((_ADAPTIVE_VISIBLE - size[0]) // 2, (_ADAPTIVE_VISIBLE - size[1]) // 2)
        )
        return canvas


def _crisp_edges(image: PIL.Image.Image) -> PIL.Image.Image:
        """Repaint the bright AA fringe around content onto the plate field.

        Small legacy rasters upscaled to 288 drag their antialiasing band
        along (2-3px wide after 2.7x) - a pale ring around logos (Quark)
        that survives every flood. The plate field is approximated with a
        wide median (thin content vanishes in it); pixels on the content
        edge band that sit brighter than the field get repainted with it.
        """
        from PIL import Image, ImageChops, ImageFilter

        rgb = image.convert("RGB")
        # Field = blurred art with the content area REPLACED by its blur:
        # gradients survive untouched (a median field would posterise
        # them into visible bands - the Flexcil regression) while thin
        # content still vanishes from the field.
        blurred = rgb.filter(ImageFilter.GaussianBlur(7))
        rough = rgb.filter(ImageFilter.MedianFilter(5))
        content = ImageChops.difference(rgb, rough).convert("L").point(
                lambda v: 255 if v > 16 else 0
        )
        field = rgb.copy()
        field.paste(blurred, (0, 0), content.filter(ImageFilter.MaxFilter(9)))
        edge = ImageChops.subtract(
                content, content.filter(ImageFilter.MinFilter(5))
        )
        brighter = ImageChops.subtract(
                rgb.convert("L"), field.convert("L")
        ).point(lambda v: 255 if v > 8 else 0)
        fix = ImageChops.multiply(edge, brighter)
        if fix.getbbox() is None:
                return image
        # Thin rims only: a fix area beyond 0.4% of the canvas is broad
        # content edge (a big glyph on a gradient plate - Flexcil), where
        # repainting the blurred field leaves visible tonal steps.
        fix_data = fix.tobytes()
        fixed = sum(1 for v in fix_data if v)
        total = image.width * image.height
        if fixed > total * 0.004:
                # Broad content edge (a big glyph on a gradient plate -
                # Flexcil) must not be repainted. But a fix concentrated
                # near the canvas border is a pale rim around a disc
                # (Weather) and always gets repainted.
                rim = max(4, min(image.size) // 8)
                near_rim = 0
                w_, h_ = image.size
                for i, v in enumerate(fix_data):
                        if v:
                                x, y = i % w_, i // w_
                                if x < rim or y < rim or x >= w_ - rim or y >= h_ - rim:
                                        near_rim += 1
                if fixed and near_rim / fixed < 0.5:
                        return image
        out = Image.composite(field, rgb, fix).convert("RGBA")
        out.putalpha(image.getchannel("A"))
        return out


def finish_device_icon(
        image: PIL.Image.Image,
        kind: str,
        layers: tuple[PIL.Image.Image, PIL.Image.Image] | None = None,
) -> PIL.Image.Image:
        """Post-process one system-rendered icon into the squircle system.

        Adaptive renders arrive as the full 108-unit artwork on a 432px
        canvas. With layer renders (fg, bg) the compose step rebuilds the
        tile at a uniform content scale; without them the flattened render
        is cropped to the 72-unit visible centre. Legacy renders resample
        to the canonical 288px square and go through the raster
        normalization pass.
        """
        from PIL import Image

        base = image.convert("RGBA")
        if kind == "adaptive" and base.size == (_ADAPTIVE_CANVAS, _ADAPTIVE_CANVAS):
                if layers is not None:
                        composed = _compose_layers(layers[0], layers[1])
                        alpha = composed.getchannel("A")
                        if alpha.point(lambda v: 255 if v < 250 else 0).getbbox() is not None:
                                return apply_rounded_mask(
                                        _crisp_edges(normalize_raster(composed))
                                )
                        return apply_rounded_mask(_crisp_edges(composed))
                offset = (_ADAPTIVE_CANVAS - _ADAPTIVE_VISIBLE) // 2
                box = (offset, offset, _ADAPTIVE_CANVAS - offset, _ADAPTIVE_CANVAS - offset)
                base = _recolor_white_bg(base.crop(box))
                # Some ROM/app combos render the adaptive fg on a TRANSPARENT
                # bg layer (Telegram on ColorOS relies on the OEM mask): the
                # visible square then floats with see-through corners. Any
                # adaptive result that is not effectively full-bleed takes
                # the same blob-plating pass as a legacy raster.
                alpha = base.getchannel("A")
                if alpha.point(lambda v: 255 if v < 250 else 0).getbbox() is not None:
                        return apply_rounded_mask(_crisp_edges(normalize_raster(base)))
                return apply_rounded_mask(_crisp_edges(base))
        if base.size != (_ADAPTIVE_VISIBLE, _ADAPTIVE_VISIBLE):
                # Legacy renders arrive at their intrinsic size (density-
                # picked, square letterboxed, capped 576 on-device); one
                # LANCZOS resample to the canonical 288 makes every tile
                # in the grid go through identical display filtering.
                base = base.resize(
                        (_ADAPTIVE_VISIBLE, _ADAPTIVE_VISIBLE), Image.Resampling.LANCZOS
                )
        return apply_rounded_mask(_crisp_edges(normalize_raster(base)))


def render_device_icons(
        adb: Adb, packages: list[str], cache_root: Path | None = None
) -> bool:
        """Batch-render launcher icons on-device; False when unavailable.

        The DEX renders every package in one app_process run and the whole
        output folder comes back with one pull; results land in the icon
        cache (``.r7``) plus a ``device_meta.json`` that ``app_info`` reads
        before ever considering the slow APK+aapt2 fallback.
        """
        import json
        import tempfile

        try:
                from PIL import Image
        except ImportError:
                return False
        if not packages or not _RENDER_DEX.exists():
                return False
        icon_cache = (cache_root / "icons") if cache_root else icons_dir()
        icon_cache.mkdir(parents=True, exist_ok=True)
        existing = read_device_meta(cache_root)
        with tempfile.TemporaryDirectory() as tmp:
                local = Path(tmp)
                try:
                        adb.push(_RENDER_DEX, "/data/local/tmp/duo_icons.dex")
                        listing = local / "pkgs.txt"
                        listing.write_text("\n".join(packages) + "\n", encoding="utf-8")
                        adb.push(listing, _DEVICE_LIST)
                        adb.shell(
                                f"rm -rf {_DEVICE_OUT}; CLASSPATH=/data/local/tmp/duo_icons.dex "
                                f"app_process / DuoIconRenderer {_DEVICE_LIST} {_DEVICE_OUT}"
                        )
                        labels = local / "labels.txt"
                        adb.pull(f"{_DEVICE_OUT}/labels.txt", labels)
                except (AdbError, OSError):
                        return False
                if not labels.exists():
                        return False
                meta = parse_renderer_meta(labels.read_text(encoding="utf-8", errors="replace"))

                # Incremental: only NEW or UPGRADED packages (or ones with
                # a missing cache PNG) cost a file pull and the ~55ms
                # finish pass. A warm cache renders nothing on the PC side.
                def _stale(package: str) -> bool:
                        entry = meta.get(package)
                        if not isinstance(entry, dict) or entry.get("kind") == "error":
                                return False
                        old = existing.get(package)
                        png_cache = icon_cache / f"{package}{_ICON_CACHE_SUFFIX}"
                        return (
                                not isinstance(old, dict)
                                or str(old.get("version")) != str(entry.get("version"))
                                or not png_cache.exists()
                        )

                pending = [p for p in packages if _stale(p)]
                if not pending:
                        return True
                out_dir = local / "out"
                try:
                        if len(pending) > 30:
                                adb.pull(_DEVICE_OUT, out_dir)   # bulk: one dir pull
                        else:
                                out_dir.mkdir(parents=True, exist_ok=True)
                                for package in pending:
                                        for suffix in (".png", ".fg.png", ".bg.png"):
                                                try:
                                                        adb.pull(
                                                                f"{_DEVICE_OUT}/{package}{suffix}",
                                                                out_dir / f"{package}{suffix}",
                                                        )
                                                except (AdbError, OSError):
                                                        continue
                except (AdbError, OSError):
                        return False

                def _finish_one(package: str) -> None:
                        entry = meta.get(package)
                        if not isinstance(entry, dict) or entry.get("kind") == "error":
                                return
                        png = out_dir / f"{package}.png"
                        if not png.exists():
                                return
                        try:
                                layers = None
                                if entry.get("kind") == "adaptive":
                                        fg_png = out_dir / f"{package}.fg.png"
                                        bg_png = out_dir / f"{package}.bg.png"
                                        if fg_png.exists() and bg_png.exists():
                                                with Image.open(fg_png) as fg_raw, Image.open(
                                                        bg_png
                                                ) as bg_raw:
                                                        layers = (fg_raw.copy(), bg_raw.copy())
                                with Image.open(png) as raw:
                                        finished = finish_device_icon(
                                                raw.copy(), str(entry.get("kind")), layers
                                        )
                                finished.save(
                                        icon_cache / f"{package}{_ICON_CACHE_SUFFIX}", format="PNG"
                                )
                        except OSError:
                                return

                # PIL's C kernels release the GIL, so threads scale.
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=8) as pool:
                        list(pool.map(_finish_one, pending))
                merged = dict(existing)
                merged.update({p: meta[p] for p in pending})
                (icon_cache / "device_meta.json").write_text(
                        json.dumps(merged), encoding="utf-8"
                )
        return True


def read_device_meta(cache_root: Path | None = None) -> dict[str, dict[str, object]]:
        """The renderer's merged per-package metadata ({} when absent)."""
        meta_path = (cache_root / "icons" if cache_root else icons_dir()) / "device_meta.json"
        try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
                return {}
        return loaded if isinstance(loaded, dict) else {}


def app_info(
        adb: Adb,
        package: str,
        cache_root: Path | None = None,
        device_meta: dict[str, dict[str, object]] | None = None,
        cache_only: bool = False,
) -> AppInfo:
        """Resolve label/version/icon for an installed app (with caching).

        The on-device renderer's cache (``.r7`` PNG + ``device_meta.json``)
        short-circuits everything below; the APK pull + aapt2 walk is the
        fallback for devices where the renderer DEX cannot run.
        """
        apk_cache = (cache_root / "apks") if cache_root else apks_dir()
        icon_cache = (cache_root / "icons") if cache_root else icons_dir()
        cached_png = icon_cache / f"{package}{_ICON_CACHE_SUFFIX}"
        if device_meta is None:
                device_meta = read_device_meta(cache_root)
        if cached_png.exists() and device_meta:
                entry = device_meta.get(package)
                if isinstance(entry, dict) and entry.get("kind") != "error":
                        version_name = entry.get("version_name")
                        return AppInfo(
                                package,
                                str(entry.get("label") or package),
                                str(version_name) if version_name not in (None, "?") else None,
                                cached_png,
                        )
        if cache_only:
                # Caller wants the instant cache-only sweep: surface what
                # is cached, skip the minutes-long APK fallback entirely.
                raise AdbError(f"cache miss for {package}")
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
