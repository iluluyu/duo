#!/usr/bin/env bash
# 一键产出 Duo setup.exe（WSL 侧）：交叉编译 → 暂存 → iscc.exe 打包。
#   scripts/installer/build_setup.sh [--no-build]
# 产物 dist/Duo-<version>-setup.exe。版本号取自 crates/duo-panel/Cargo.toml。
set -euo pipefail
cd "$(dirname "$0")/../.."

ROOT="$PWD"
RUST="$ROOT/src/rustduo"
STAGE="$ROOT/dist/installer"
OUT="$ROOT/dist"
VERSION="$(awk -F'"' '/^version/{print $2; exit}' "$RUST/crates/duo-panel/Cargo.toml")"
ISCC="${ISCC:-/mnt/c/Users/Administrator/scoop/shims/iscc.exe}"
command -v iscc.exe >/dev/null 2>&1 && ISCC="$(command -v iscc.exe)"

if [[ "${1:-}" != "--no-build" ]]; then
    "$RUST/scripts/build_wsl.sh"
fi

mkdir -p "$STAGE"
cp "$RUST/target/x86_64-pc-windows-gnu/release/duo-panel.exe" "$STAGE/Duo.exe"
cp "$RUST/target/x86_64-pc-windows-gnu/release/duo-core.exe" "$STAGE/duo-core.exe"
cp "$RUST/assets/duo.ico" "$STAGE/Duo.ico"
cp "$ROOT/LICENSE" "$STAGE/LICENSE"

command -v wslpath >/dev/null 2>&1 || { echo "需要 wslpath" >&2; exit 1; }
WIN_STAGE="$(wslpath -w "$STAGE")"
WIN_ISS="$(wslpath -w "$ROOT/scripts/installer/duo.iss")"
WIN_CWD="$(wslpath -w "$ROOT/scripts/installer")"

cd "$ROOT/scripts/installer"
"$ISCC" /DAppVersion="$VERSION" /DSourceDir="$WIN_STAGE" "$WIN_ISS"

SETUP="$STAGE/Duo-$VERSION-setup.exe"
mv -f "$SETUP" "$OUT/Duo-$VERSION-setup.exe"
echo
echo "setup.exe: $OUT/Duo-$VERSION-setup.exe ($(du -h "$OUT/Duo-$VERSION-setup.exe" | cut -f1))"
