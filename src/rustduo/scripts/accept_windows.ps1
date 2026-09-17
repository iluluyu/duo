# duo-core Windows 最终验收（TODO 0.2.2/0.2.3 收尾；标准见 TODO.md §0）
# 在 Windows 侧真机执行：powershell -File C:\duo\src\rustduo\scripts\accept_windows.ps1
# 验收链 = exe 存在 → devices 真机识别 → 手动跑 chrome 会话 → 无孤儿进程。
$ErrorActionPreference = "Stop"

$exe = "C:\Users\Administrator\.local\share\duo\tools\duo-core.exe"
$results = [System.Collections.Generic.List[string]]::new()
$failed = 0

function Add-Result([string]$name, [bool]$ok, [string]$detail) {
    $script:results.Add(("{0}  {1}  {2}" -f ($(if ($ok) { "PASS" } else { "FAIL" }), $name, $detail)))
    if (-not $ok) { $script:failed++ }
}

# ① duo-core.exe 已部署（build_windows.ps1 产物落点）
$ok1 = (Test-Path $exe) -and -not (Test-Path $exe -PathType Container)
Add-Result "duo-core.exe 存在" $ok1 $exe
if (-not $ok1) {
    Write-Host "缺少 $exe —— 先跑 src\rustduo\scripts\build_windows.ps1" -ForegroundColor Red
    $results | ForEach-Object { Write-Host $_ }
    exit 1
}

# ② devices 真机识别：stdout JSON 里至少一台设备状态为 "device"
#    （offline/unauthorized/recovery 均不含该子串，只有就绪态命中）
$devOut = (& $exe devices --adb adb.exe 2>&1 | Out-String)
$ok2 = ($LASTEXITCODE -eq 0) -and ($devOut -match '"device"')
Add-Result "devices 识别到就绪设备" $ok2 ($devOut.Trim() -replace "`r?`n", " ")
if (-not $ok2) {
    Write-Host "设备未就绪：连上 USB 并 adb devices 确认状态为 device" -ForegroundColor Red
    $results | ForEach-Object { Write-Host $_ }
    exit 1
}

# ③ 手动验收 chrome 会话（真机验证过的路径：scrcpy 无边框 + C# overlay 贴窗）
Write-Host ""
Write-Host "===== 手动验收（docs/window-experience.md §14 清单）=====" -ForegroundColor Cyan
Write-Host "请另开终端执行："
Write-Host "  C:\Users\Administrator\.local\share\duo\tools\duo-core.exe mirror --app com.tencent.mobileqq --chrome" -ForegroundColor Yellow
Write-Host "检查：视频渲染 / 鼠标键盘 / 顶部胶囊悬停露出 / 下巴 / 拖动缩放。"
Write-Host "全部窗口关闭、会话彻底退出后回到这里继续。"
Read-Host "按 Enter 继续（先关掉上面所有窗口）"

# ④ 孤儿进程：宿主面板一关，duo-core / scrcpy 进程树应随之消失（§12 契约）
$orphans = @(Get-Process duo-core, scrcpy -ErrorAction SilentlyContinue)
if ($orphans.Count -eq 0) {
    Add-Result "无孤儿进程 (duo-core/scrcpy)" $true "未发现残留"
} else {
    $desc = ($orphans | ForEach-Object { "{0}({1})" -f $_.ProcessName, $_.Id }) -join ", "
    Add-Result "无孤儿进程 (duo-core/scrcpy)" $false "残留: $desc"
    $reply = Read-Host "检测到上面残留进程，手动结束后输入 y 重查，其他输入跳过"
    if ($reply -eq "y") {
        $orphans = @(Get-Process duo-core, scrcpy -ErrorAction SilentlyContinue)
        if ($orphans.Count -eq 0) {
            $script:results[$script:results.Count - 1] = "PASS  无孤儿进程 (duo-core/scrcpy)  重查通过"
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
