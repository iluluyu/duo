#!/usr/bin/env bash
# WSL mingw 交叉构建（README §构建-WSL 的脚本化）：
#   src/rustduo/scripts/build_wsl.sh [--deploy]
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

resolve_win_home() {
    if command -v cmd.exe >/dev/null 2>&1; then
        local raw
        raw="$(cmd.exe /c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')"
        if [[ -n "$raw" ]] && command -v wslpath >/dev/null 2>&1; then
            wslpath -u "$raw" 2>/dev/null && return 0
        fi
    fi
    if [[ -d "/mnt/c/Users/$USER" ]]; then
        echo "/mnt/c/Users/$USER"
        return 0
    fi
    echo "/mnt/c/Users/Administrator"
}

MODE="${1:-}"
if [[ "$MODE" == "--deploy" ]]; then
    WIN_HOME="$(resolve_win_home)"
    DEST="$WIN_HOME/.local/share/duo/tools"
    mkdir -p "$DEST"
    cp "$OUT/duo-core.exe" "$DEST/duo-core.exe"
    cp "$OUT/duo-panel.exe" "$DEST/Duo.exe"
    echo "deployed: $DEST/duo-core.exe + $DEST/Duo.exe"
elif [[ "$MODE" == "--install" ]]; then
    WIN_HOME="$(resolve_win_home)"
    DEST="$WIN_HOME/AppData/Local/Duo"
    mkdir -p "$DEST"
    cp "$OUT/duo-core.exe" "$DEST/duo-core.exe"
    cp "$OUT/duo-panel.exe" "$DEST/Duo.exe"
    cp "../../scripts/uninstall-windows.ps1" "$DEST/uninstall.ps1"
    cp "assets/duo.ico" "$DEST/Duo.ico"
    echo "installed: $DEST/duo-core.exe + $DEST/Duo.exe"
fi
