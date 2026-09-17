$ErrorActionPreference = 'SilentlyContinue'

$installDir = "$env:LOCALAPPDATA\Duo"

Get-Process Duo, duo-core -ErrorAction SilentlyContinue | Stop-Process -Force

Remove-Item ([Environment]::GetFolderPath('Desktop') + '\Duo.lnk') -Force
Remove-Item ([Environment]::GetFolderPath('Programs') + '\Duo.lnk') -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Duo' -Force

if (Test-Path $installDir) {
    Remove-Item $installDir -Recurse -Force
}

Write-Output 'Duo uninstalled.'
