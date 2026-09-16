# duo-core Windows 构建（TODO 0.2.2，一次性前置：winget install Rustlang.Rustup）
# 在 Windows 侧执行：C:\duo\rust\scripts\build_windows.ps1
# 产物部署到 %USERPROFILE%\.local\share\duo\tools\duo-core.exe（duocore.py 的第③查找位）。
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

cargo build --release
if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }

$dest = Join-Path $env:USERPROFILE ".local\share\duo\tools"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item "target\release\duo-core.exe" (Join-Path $dest "duo-core.exe") -Force
Write-Host "deployed: $dest\duo-core.exe"
