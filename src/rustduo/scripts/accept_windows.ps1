# Duo Windows 完整真机验收（Rust 原生栈双 exe：duo-core + duo-panel）
# 在 Windows 侧执行：powershell -File C:\duo\src\rustduo\scripts\accept_windows.ps1
# 验收链 = 双 exe 存在 → devices 识别 → 手动跑 chrome 会话与面板 → 退出拖树无孤儿。
$ErrorActionPreference = "Stop"

$candidates = @(
    "$env:LOCALAPPDATA\Duo\duo-core.exe",
    "$env:USERPROFILE\.local\share\duo\tools\duo-core.exe",
    "C:\Users\Administrator\.local\share\duo\tools\duo-core.exe",
    "$PSScriptRoot\..\target\x86_64-pc-windows-gnu\release\duo-core.exe",
    "$PSScriptRoot\..\target\release\duo-core.exe"
)
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

$results = [System.Collections.Generic.List[string]]::new()
$failed = 0

function Add-Result([string]$name, [bool]$ok, [string]$detail) {
    $script:results.Add(("{0}  {1}  {2}" -f ($(if ($ok) { "PASS" } else { "FAIL" }), $name, $detail)))
    if (-not $ok) { $script:failed++ }
}

# ① duo-core.exe 与 Duo.exe 已就位
$ok1 = ($null -ne $exe) -and (Test-Path $exe)
Add-Result "duo-core.exe 存在" $ok1 (if ($ok1) { $exe } else { "未找到 duo-core.exe" })
if (-not $ok1) {
    Write-Host "缺少 duo-core.exe —— 请先运行 build_wsl.sh --deploy 或 build_windows.ps1 -Deploy" -ForegroundColor Red
    $results | ForEach-Object { Write-Host $_ }
    exit 1
}

$panelExe = Join-Path (Split-Path $exe) "Duo.exe"
if (-not (Test-Path $panelExe)) {
    $panelExe = Join-Path (Split-Path $exe) "duo-panel.exe"
}
$okPanel = Test-Path $panelExe
Add-Result "Duo.exe (duo-panel) 存在" $okPanel (if ($okPanel) { $panelExe } else { "未找到 Duo.exe/duo-panel.exe" })

# ② devices 真机识别：stdout JSON 里至少一台设备状态为 "device"
$devOut = (& $exe devices --adb adb.exe 2>&1 | Out-String)
$ok2 = ($LASTEXITCODE -eq 0) -and ($devOut -match '"device"')
Add-Result "devices 识别到就绪设备" $ok2 ($devOut.Trim() -replace "`r?`n", " ")
if (-not $ok2) {
    Write-Host "设备未就绪：连上 USB 并 adb devices 确认状态为 device" -ForegroundColor Red
    $results | ForEach-Object { Write-Host $_ }
    exit 1
}

# ③ 手动验收 chrome 会话与面板启动
Write-Host ""
Write-Host "===== 手动验收（docs/window-experience.md §15 清单）=====" -ForegroundColor Cyan
Write-Host "1. 请测试核心会话："
Write-Host "   $exe mirror --app com.tencent.mobileqq --chrome" -ForegroundColor Yellow
Write-Host "   检查：无黑窗闪烁 / 视频渲染 / 顶部胶囊悬停露出 / 下巴 / 拖动缩放。"
if ($okPanel) {
    Write-Host "2. 请启动 Duo 面板并检查主界面与设置页："
    Write-Host "   $panelExe" -ForegroundColor Yellow
    Write-Host "   检查：设备状态灯 / 应用磁贴 / 菜单皮肤 / 设置页息屏开关排版。"
}
Write-Host "全部窗口关闭、会话彻底退出后回到这里继续。"
Read-Host "按 Enter 继续（先关掉上面所有窗口）"

# ④ 孤儿进程：宿主面板与会话关闭后，duo-core / scrcpy / Duo 进程树应随之消失（Job Object 契约）
$orphans = @(Get-Process duo-core, scrcpy, duo-panel, Duo -ErrorAction SilentlyContinue)
if ($orphans.Count -eq 0) {
    Add-Result "无孤儿进程 (duo-core/scrcpy/Duo)" $true "未发现残留"
} else {
    $desc = ($orphans | ForEach-Object { "{0}({1})" -f $_.ProcessName, $_.Id }) -join ", "
    Add-Result "无孤儿进程 (duo-core/scrcpy/Duo)" $false "残留: $desc"
    $reply = Read-Host "检测到上面残留进程，手动结束后输入 y 重查，其他输入跳过"
    if ($reply -eq "y") {
        $orphans = @(Get-Process duo-core, scrcpy, duo-panel, Duo -ErrorAction SilentlyContinue)
        if ($orphans.Count -eq 0) {
            $script:results[$script:results.Count - 1] = "PASS  无孤儿进程 (duo-core/scrcpy/Duo)  重查通过"
            $script:failed--
        }
    }
}

# ⑤ 汇总
Write-Host ""
Write-Host "===== 验收汇总 =====" -ForegroundColor Cyan
$results | ForEach-Object {
    $color = $(if ($_ -match "^PASS") { "Green" } else { "Red" })
    Write-Host $_ -ForegroundColor $color
}
if ($failed -gt 0) { Write-Host "结果: FAIL（$failed 项未过）" -ForegroundColor Red; exit 1 }
Write-Host "结果: PASS" -ForegroundColor Green
exit 0
