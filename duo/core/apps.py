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

from duo.core.engine import is_wsl
from duo.core.paths import apks_dir, icons_dir, tools_dir
from duo.core.winproc import creation_flags

#: Pinned aapt2 build from Google Maven (same artifact AGP uses).
AAPT2_VERSION = "9.4.0-15978811"
_AAPT2_URL = (
        "https://dl.google.com/android/maven2/com/android/tools/build/"
        f"aapt2/{AAPT2_VERSION}/aapt2-{AAPT2_VERSION}-windows.jar"
)

_RUN_TIMEOUT_S = 60.0
_PULL_TIMEOUT_S = 300.0


class AdbError(RuntimeError):
        """Raised when an adb invocation fails."""


class Adb:
        """Thin wrapper around the adb binary bound to one device serial."""

        def __init__(self, binary: str, serial: str) -> None:
                self.binary = binary
                self.serial = serial

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
        resolved and composited (canvas 108 -> visible centre 72 model).
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
                        image.save(out_png, format="PNG")
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
        """Composite adaptive layers: 108-unit canvas, visible centre 72."""
        import io

        try:
                from PIL import Image
        except ImportError:
                return None
        canvas = 512
        visible = canvas * 72 // 108
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
                                fg: Image.Image = fg_file.convert("RGBA").resize((canvas, canvas))
                                base.paste(fg, (0, 0), fg)
                except OSError:
                        return None
        box = ((canvas - visible) // 2,) * 2 + ((canvas + visible) // 2,) * 2
        cropped = base.crop(box)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
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
        icon_out = icon_cache / f"{package}.png"
        icon_path = extract_icon(apk_path, icon_ref, icon_out, aapt2) if icon_ref else None

        return AppInfo(
                package=package,
                label=label,
                version_name=fields.get("version_name"),
                icon_path=icon_path,
        )
