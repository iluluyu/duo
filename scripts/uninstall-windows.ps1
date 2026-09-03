# Duo uninstaller: removes app folder, shortcuts, and the registry entry.
$ErrorActionPreference = 'SilentlyContinue'

$installDir = "$env:LOCALAPPDATA\Duo"

# Stop running instances first.
Get-Process Duo -ErrorAction SilentlyContinue | Stop-Process -Force

Remove-Item (Join-Path $installDir 'Duo.exe') -Force
Remove-Item (Join-Path $installDir 'uninstall.ps1') -Force
Remove-Item (Join-Path $installDir 'Duo.ico') -Force
Remove-Item $installDir -Force

Remove-Item ([Environment]::GetFolderPath('Desktop') + '\Duo.lnk') -Force
Remove-Item ([Environment]::GetFolderPath('Programs') + '\Duo.lnk') -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Duo' -Force

Write-Output 'Duo uninstalled.'
