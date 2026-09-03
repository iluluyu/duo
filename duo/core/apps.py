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
                        timeout=timeout,
                        check=False,
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


def parse_device_serials(devices_output: str) -> list[str]:
        """Parse ``adb devices`` output into online device serials."""
        serials = []
        for line in devices_output.splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 2 and fields[1] == "device":
                        serials.append(fields[0])
        return serials


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


def extract_icon(apk_path: Path, icon_ref: str, out_png: Path) -> Path | None:
        """Extract an icon out of an APK into a PNG file.

        Adaptive icons (``.xml`` references) need foreground/background layer
        compositing which is deferred to the M3 launcher; for now they yield
        ``None`` and callers fall back to a generic icon.
        """
        if icon_ref.endswith(".xml"):
                return None
        try:
                with zipfile.ZipFile(apk_path) as apk:
                        data = apk.read(icon_ref)
        except (KeyError, zipfile.BadZipFile):
                return None
        try:
                from PIL import Image
        except ImportError:
                return None
        import io

        try:
                with Image.open(io.BytesIO(data)) as image:
                        image.save(out_png, format="PNG")
        except OSError:
                return None
        return out_png


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
                timeout=_RUN_TIMEOUT_S,
                check=False,
        )
        fields = parse_badging(result.stdout or "")

        label = fields.get("label") or package
        icon_ref = fields.get("icon") or ""
        icon_out = icon_cache / f"{package}.png"
        icon_path = extract_icon(apk_path, icon_ref, icon_out) if icon_ref else None

        return AppInfo(
                package=package,
                label=label,
                version_name=fields.get("version_name"),
                icon_path=icon_path,
        )


def list_device_serials(adb_binary: str) -> list[str]:
        """List serials of devices currently online (no serial binding)."""
        result = subprocess.run(
                [adb_binary, "devices"],
                capture_output=True,
                text=True,
                timeout=_RUN_TIMEOUT_S,
                check=False,
        )
        return parse_device_serials(result.stdout or "")
