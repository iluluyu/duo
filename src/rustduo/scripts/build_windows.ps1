# duo Windows 构建（WSL mingw 交叉编译的 Windows 侧备选：本机 rustup msvc/gnu）。
# 在 Windows 侧执行：C:\duo\rust\scripts\build_windows.ps1 [-Deploy]
# 产物：target\release\duo-core.exe + duo-panel.exe；-Deploy 时
#   duo-core.exe → %USERPROFILE%\.local\share\duo\tools\（duocore.py 第③查找位）
#   duo-panel.exe → 同目录 Duo.exe（面板 exe 同目录探测 duo-core）。
param([switch]$Deploy)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

cargo build --release
if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }

foreach ($exe in @("duo-core.exe", "duo-panel.exe")) {
    if (-not (Test-Path "target\release\$exe")) { throw "missing $exe" }
}

if ($Deploy) {
    $dest = Join-Path $env:USERPROFILE ".local\share\duo\tools"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item "target\release\duo-core.exe" (Join-Path $dest "duo-core.exe") -Force
    Copy-Item "target\release\duo-panel.exe" (Join-Path $dest "Duo.exe") -Force
    Write-Host "deployed: $dest\duo-core.exe + $dest\Duo.exe"
} else {
    Write-Host "built: target\release\duo-core.exe + duo-panel.exe"
}
