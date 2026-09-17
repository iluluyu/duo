# 在 Windows 侧执行：C:\duo\src\rustduo\scripts\build_windows.ps1 [-Deploy] [-Install]
param([switch]$Deploy, [switch]$Install)
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
} elseif ($Install) {
    $installer = Join-Path $PSScriptRoot "..\..\..\scripts\install-windows.ps1"
    & powershell.exe -ExecutionPolicy Bypass -File $installer (Join-Path (Get-Location) "target\release\duo-panel.exe")
} else {
    Write-Host "built: target\release\duo-core.exe + duo-panel.exe"
}
