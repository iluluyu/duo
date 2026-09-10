# Duo Windows 指南

> Windows 原生运行（Python + PyQt6-QML）+ adb/scrcpy 控制设备。

## 安装（一次性）

```powershell
# 1. Python 3.11+（或 scoop install python）
winget install -e --id Python.Python.3.12

# 2. 取代码
robocopy \\wsl.localhost\archlinux\home\luyu\duo C:\duo /E /XD .venv __pycache__ .git
cd C:\duo

# 3. 环境与依赖
py -m venv .venv
.venv\Scripts\pip install -e ".[gui]"

# 4. 启动
.venv\Scripts\duo --gui
```

前置：`adb.exe`/`scrcpy.exe` 在 PATH（scoop 已满足）；设备 USB 调试已授权。

## 打包（onefile 单文件，固定产物 `C:\Tools\Duo.exe`）

```powershell
# Windows 侧（管理员）：先 robocopy 同步 WSL 工作区（脚本预检会比对
# duo.spec/app.py/duo.ico 哈希，旧拷贝直接拒建），后一键构建 + 部署 +
# 图标嵌入验证（spec 为唯一规格，内含 datas/hiddenimports）
robocopy \\wsl.localhost\archlinux\home\luyu\duo C:\duo /E /XD .venv __pycache__ .git
C:\duo\scripts\build_windows.ps1

# 手动等价：cd C:\duo; pyinstaller duo.spec --noconfirm; 部署 dist\Duo.exe
# （或 scoop python：python3 -m pip install -e ".[gui,build]" 后同上）
```

### 产物校验清单

- [ ] 双击 `Duo.exe` → 面板正常（设备卡、应用真图标、玻璃样式）
- [ ] 点应用 → 会话窗口 + overlay 控件（首窗 csc 编译约 2s）
- [ ] `Duo.exe --check` 退出码 0
- [ ] 设置页读写 settings.json（`%USERPROFILE%\.local\share\duo`）
- [ ] 图标两条链路均取 `assets/duo.ico`：exe 资源（资源管理器）+ 运行时任务栏/
  标题栏（spec datas 携带、app.py setWindowIcon）；换图标同名覆盖后重打，
  或 `python scripts/switch_icon.py <候选名>`

**打包版 adb 提示**：exe 继承的 PATH 与终端不同，scoop 的 adb 可能探测不到——
设置页固定 adb/scrcpy 路径（`adb_path`/`scrcpy_path` 优先于 PATH）。

## 日常使用

1. 面板 → 设备绿灯
2. 点图标开窗；**右键/长按图标**切竖横屏（按应用记忆）
3. 首窗有声（FLAC），后续自动静音（latest 仲裁）
4. 「运行中」芯片 ✕ 关窗；点芯片 = 应用拉回该虚拟屏
5. 应用网格按标签拼音首字母排序；点图标右上角 ☆/★ **置顶**（常驻最上，
   记忆在 `gui_prefs.json`）

## 故障排查

| 症状 | 处理 |
|---|---|
| 面板无设备 | 换线/口；重新授权调试 |
| 找不到 adb（打包版） | 设置页固定 adb 路径 |
| 日志见 `protocol fault` / `Could not start adb server`、设备应用全消失 | 第三方软件自带的**旧版 adb** 与 PATH 上的 adb 互杀 5037（实例：SuperDisplay 的 `MirrorService` 服务自带 adb 28，与 scoop adb 37 每 2s 轮询互杀对方 server）。定位：`Get-CimInstance Win32_Process -Filter "name='adb.exe'"` 看命令行与父进程。处理：`Stop-Service` + `Set-Service -StartupType Manual` 禁用对方服务，`taskkill /F /IM adb.exe` 清残留，面板自动恢复 |
| 设备状态抖动 | ~6s 容错内正常；持续离线看 `adb devices` |
| 窗口控件缺失 | 等 2s（csc 首编）；看 `%USERPROFILE%\.local\share\duo\logs` |
| 面板关了但 Duo.exe/scrcpy 还在后台 | 旧版 bug（2026-09-10 起已修：面板退出整树终止 + Job Object 崩溃兕底，见 docs/window-experience.md §12）。重打包后不再出现；临时清理：`taskkill /T /F /IM Duo.exe`（注意会连面板一起杀） |
| 图标显示为文字 | 首次拉 APK 解析，稍候 |
| 重打后仍显示旧图标 | Windows 图标缓存：`ie4uinit.exe -show` 后重启 explorer；
  任务栏钉住项需取消后重新钉 |
