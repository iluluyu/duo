#!/usr/bin/env bash
# WSL mingw 交叉构建（README §构建-WSL 的脚本化）：
#   rust/scripts/build_wsl.sh [--deploy]
# 产物 target/x86_64-pc-windows-gnu/release/*.exe；--deploy 时复制到
# Windows 侧 tools 目录（duocore.py / duo-panel 的 exe 同目录探测位）。
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET=x86_64-pc-windows-gnu
export PATH="/usr/x86_64-w64-mingw32/bin:$PATH"

cargo build --target "$TARGET" --release

OUT="target/$TARGET/release"
echo "built:"
ls -la "$OUT/duo-core.exe" "$OUT/duo-panel.exe"

if [[ "${1:-}" == "--deploy" ]]; then
    DEST="/mnt/c/Users/Administrator/.local/share/duo/tools"
    mkdir -p "$DEST"
    cp "$OUT/duo-core.exe" "$DEST/duo-core.exe"
    cp "$OUT/duo-panel.exe" "$DEST/Duo.exe"
    echo "deployed: $DEST/duo-core.exe + $DEST/Duo.exe"
fi
