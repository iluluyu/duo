# Duo Windows 安装指南

Duo 在 Windows 上原生运行（Python + PyQt6），通过 adb/scrcpy 控制平板。

## 一次性安装

### 1. 安装 Python（3.11+）

管理员 PowerShell：

```powershell
winget install -e --id Python.Python.3.12
```

安装后**重开终端**，确认 `py --version` 有输出。

### 2. 取代码到 C 盘

```powershell
robocopy \\wsl.localhost\archlinux\home\luyu\duo C:\duo /E /XD .venv __pycache__ .git
cd C:\duo
```

### 3. 建环境装依赖

```powershell
py -m venv .venv
.venv\Scripts\pip install -e ".[gui]"
```

### 4. 启动面板

```powershell
.venv\Scripts\duo --gui
```

或建桌面快捷方式，目标填 `C:\duo\.venv\Scripts\pythonw.exe -m duo --gui`（无黑窗）。

## 前置条件（已具备则跳过）

- `adb.exe`、`scrcpy.exe` 在 PATH（scoop 安装的已满足）
- 平板 USB 调试已授权

## 日常使用

1. 打开 Duo 面板 → 设备绿灯
2. 点应用图标开窗；**右键图标**切换竖屏/横屏（按应用记忆）
3. 首个窗口有声（FLAC 无损），后续窗口自动静音（系统级仲裁，防爆音）
4. 「运行中」芯片 ✕ 关闭对应窗口

## 故障排查

| 症状 | 处理 |
|---|---|
| 面板无设备 | 换线/换口，平板上重新授权调试 |
| 窗口无边框控件缺失 | 等待 2 秒（overlay 首次编译 csc）；仍无则看 `%USERPROFILE%\.local\share\duo\logs` |
| 图标显示为文字 | 首次会拉取 APK 解析，稍候 |
