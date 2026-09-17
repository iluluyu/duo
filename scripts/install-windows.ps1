$ErrorActionPreference = 'Stop'

Get-Process Duo, duo-core -ErrorAction SilentlyContinue | Stop-Process -Force

$candidates = @(
    $args[0],
    "$PSScriptRoot\..\src\rustduo\target\x86_64-pc-windows-gnu\release\duo-panel.exe",
    "$PSScriptRoot\..\src\rustduo\target\release\duo-panel.exe",
    "C:\duo\src\rustduo\target\x86_64-pc-windows-gnu\release\duo-panel.exe",
    "C:\duo\src\rustduo\target\release\duo-panel.exe"
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path $_) }

$src = $candidates | Select-Object -First 1
if (-not $src) {
    throw "duo-panel.exe not found; please build or provide the executable path."
}

$core = Join-Path (Split-Path $src) 'duo-core.exe'
$installDir = "$env:LOCALAPPDATA\Duo"
$app = Join-Path $installDir 'Duo.exe'

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item $src $app -Force
if (Test-Path $core) {
    Copy-Item $core (Join-Path $installDir 'duo-core.exe') -Force
} elseif (-not (Test-Path (Join-Path $installDir 'duo-core.exe'))) {
    throw "duo-core.exe not found next to $src"
}
Copy-Item "$PSScriptRoot\uninstall-windows.ps1" (Join-Path $installDir 'uninstall.ps1') -Force
Copy-Item "$PSScriptRoot\..\assets\duo.ico" (Join-Path $installDir 'Duo.ico') -Force

$ws = New-Object -ComObject WScript.Shell
foreach ($shortcut in @(
        [tuple]::Create([Environment]::GetFolderPath('Desktop') + '\Duo.lnk', $app),
        [tuple]::Create([Environment]::GetFolderPath('Programs') + '\Duo.lnk', $app)
)) {
        $lnk = $ws.CreateShortcut($shortcut.Item1)
        $lnk.TargetPath = $shortcut.Item2
        $lnk.WorkingDirectory = $installDir
        $lnk.IconLocation = "$app,0"
        $lnk.Save()
}

# Add/Remove Programs entry so it uninstalls like normal software.
$reg = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Duo'
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty $reg 'DisplayName' 'Duo'
Set-ItemProperty $reg 'Publisher' 'Duo'
Set-ItemProperty $reg 'DisplayVersion' '0.1.0'
Set-ItemProperty $reg 'DisplayIcon' "$app,0"
Set-ItemProperty $reg 'InstallLocation' $installDir
Set-ItemProperty $reg 'UninstallString' "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$installDir\uninstall.ps1`""

Write-Output "installed: $app"
Write-Output "shortcuts: Desktop + Start Menu"
