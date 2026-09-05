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

## QML 面板上屏验证清单（原生运行）

WSL 侧没有显示，面板上屏只能在 Windows 侧验证。用 venv 的 Windows Python 直接跑：

```powershell
cd C:\duo
.venv\Scripts\python -m duo --gui
```

按清单逐项核对，全部通过才算上屏验证完成：

- [ ] **面板启动**：主窗口正常出现（液态玻璃风），不是空白窗；QML 加载失败会在 stderr 打 `error: Main.qml 加载失败`
- [ ] **设备列表**：插上平板 → 设备卡出现且状态点为绿；拔线 → 回落为未连接
- [ ] **投屏**：点应用图标 → scrcpy 无边框窗口打开，「运行中」出现对应芯片；点芯片 ✕ → 会话与窗口关闭
- [ ] **整机镜像**：整机镜像按钮 → 平板镜像窗口；overlay 下巴轻触=返回、长按=回桌面（仅此模式生效）
- [ ] **设置页（Ctrl+,）**：齿轮按钮或 `Ctrl+,` 进入；「检测」回显 scrcpy/adb 版本；保存后新会话生效（会话运行中引擎路径行锁定）；圆角模式/大小改动预览即时
- [ ] **竖屏长按**：图标触屏长按（或右键）切竖屏 → 重开窗为竖屏布局；「运行中」芯片点「横屏/竖屏」改下次启动方向

## 打包构建与产物校验

打包形态：PyInstaller **onedir**（win64）——产物为 `dist\Duo\Duo.exe` + `dist\Duo\_internal\`，**整个目录一起分发**。spec（`duo.spec`：QML 目录与 C# overlay 源入 datas、QtQml/QtQuick 隐藏导入、`assets\duo.ico` 占位图标说明）与一键脚本已入库，Windows 侧执行：

```powershell
cd C:\duo
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

脚本依次：复用/新建 `.venv` → 安装 `.[dev,gui,build]`（`build` = pyinstaller）→ 校验 64 位 Python → `pyinstaller duo.spec --noconfirm` → 检查产物文件齐全。构建与产物校验需 Windows 实测：

- [ ] `_internal\duo\ui\qml\` 内有 Main/SettingsPage/Style.qml + qmldir（QML 面板能上屏的前提）
- [ ] `_internal\duo\resources\chrome_overlay.cs` 在包内（`--chrome` 首窗要现场 csc 编译）
- [ ] 双击 `Duo.exe`（无参数）→ 面板正常打开：设备卡、应用网格显示真图标（Pillow 随依赖入包）、样式齐全
- [ ] 带参走 CLI 路由：`dist\Duo\Duo.exe --check; $LASTEXITCODE` → 0（工具齐全）/1（缺工具）；面板开窗实际 spawn 的是 `Duo.exe mirror ...`（冻结态 `sys.executable` 路由），确认能正常开会话
- [ ] 设置页在打包版可用：读改存 settings.json（仍是 `%USERPROFILE%\.local\share\duo`），保存后新会话生效
- [ ] 图标：exe/快捷方式用 `assets\duo.ico`（当前为占位图标，正式图标到位后同名替换重打即可）

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
