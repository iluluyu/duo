# Duo light installer: app folder + shortcuts + uninstall entry.
# Dev-stage layout: no installer engine, just a clean app presence.
$ErrorActionPreference = 'Stop'

$src = if ($args[0]) { $args[0] } else { 'C:\duo-build\dist\Duo.exe' }
$installDir = "$env:LOCALAPPDATA\Duo"
$app = Join-Path $installDir 'Duo.exe'

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item $src $app -Force
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
