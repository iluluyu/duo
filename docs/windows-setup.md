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
# Windows 侧（管理员）：C:\duo 为最新 main，一键构建 + 部署（spec 为唯一规格，
# 内含 datas/hiddenimports；脚本负责 venv、pyinstaller、taskkill、部署）
C:\duo\scripts\build_windows.ps1

# 手动等价：cd C:\duo; pyinstaller duo.spec --noconfirm; 部署 dist\Duo.exe
# （或 scoop python：python3 -m pip install -e ".[gui,build]" 后同上）
```

### 产物校验清单

- [ ] 双击 `Duo.exe` → 面板正常（设备卡、应用真图标、玻璃样式）
- [ ] 点应用 → 会话窗口 + overlay 控件（首窗 csc 编译约 2s）
- [ ] `Duo.exe --check` 退出码 0
- [ ] 设置页读写 settings.json（`%USERPROFILE%\.local\share\duo`）
- [ ] 图标 `assets/duo.ico`（占位，正式后同名替换重打）

**打包版 adb 提示**：exe 继承的 PATH 与终端不同，scoop 的 adb 可能探测不到——
设置页固定 adb/scrcpy 路径（`adb_path`/`scrcpy_path` 优先于 PATH）。

## 日常使用

1. 面板 → 设备绿灯
2. 点图标开窗；**右键/长按图标**切竖横屏（按应用记忆）
3. 首窗有声（FLAC），后续自动静音（latest 仲裁）
4. 「运行中」芯片 ✕ 关窗；点芯片 = 应用拉回该虚拟屏

## 故障排查

| 症状 | 处理 |
|---|---|
| 面板无设备 | 换线/口；重新授权调试 |
| 找不到 adb（打包版） | 设置页固定 adb 路径 |
| 设备状态抖动 | ~6s 容错内正常；持续离线看 `adb devices` |
| 窗口控件缺失 | 等 2s（csc 首编）；看 `%USERPROFILE%\.local\share\duo\logs` |
| 图标显示为文字 | 首次拉 APK 解析，稍候 |
