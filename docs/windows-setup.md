# Duo Windows 指南

> Duo 原生运行环境部署、双架构构建与使用指引。
>
> - **生产推荐（Rust 原生栈）**：`duo-panel`（`Duo.exe` 面板）+ `duo-core`（`duo-core.exe` 核心引擎），零 Python 运行时依赖，秒启、低内存占用，静默无控制台黑窗。
> - **参考验证（Python 栈）**：`src/pyduo`（PyQt6-QML 面板 + CLI），作为功能对译基准与快速原型验证。

---

## 1. 运行前置准备

1. **环境工具**：
   - `adb.exe` 与 `scrcpy.exe` 须在系统 `PATH`（推荐通过 `scoop install adb scrcpy` 安装，或在 Duo 设置页固定路径）。
2. **安卓设备准备**：
   - 开启系统「开发者选项」并允许「USB 调试」。
   - 首次连接电脑时在手机/平板端勾选「一律允许此计算机进行调试」。

---

## 2. 安装与构建（Rust 原生栈，推荐）

### 方案 A：WSL 交叉构建并自动部署（日常开发最快）

在 WSL2 Linux 终端下一键交叉编译 Windows 双 exe 并直装到 Windows 本地目录：

```sh
# 一次性前置工具（Arch Linux 为例）
sudo pacman -S --needed mingw-w64-gcc
rustup target add x86_64-pc-windows-gnu

# 交叉构建并部署到 ~/.local/share/duo/tools/
cd src/rustduo
./scripts/build_wsl.sh --deploy
```

### 方案 B：Windows 原生编译

在 Windows PowerShell（管理员或普通终端）中：

```powershell
cd C:\duo\src\rustduo
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Deploy
```

### 方案 C：安装到系统应用目录

将编译产物安装为标准桌面应用（创建桌面/开始菜单快捷方式与控制面板卸载项，自动搜索编译产物）：

```powershell
cd C:\duo
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

- **安装目录**：`%LOCALAPPDATA%\Duo`
- **主程序**：`Duo.exe`（自动探测同目录下的 `duo-core.exe`）
- **数据与日志**：`%USERPROFILE%\.local\share\duo`

---

## 3. Python 验证栈（可选，开发参考）

供对比行为与运行现有 pytest 套件：

```powershell
# 1. 环境准备（Python 3.11+）
winget install -e --id Python.Python.3.12

# 2. 拉取依赖并以可编辑模式安装
cd C:\duo
py -m venv .venv
.venv\Scripts\pip install -e ".[gui,build]"

# 3. 启动 Python GUI 面板
.venv\Scripts\duo --gui

# 4. （可选）打包 Python 版单文件 exe 产物
powershell -ExecutionPolicy Bypass -File scripts\build_pyduo.ps1
```

---

## 4. 产物功能校验清单

- [ ] **面板启动**：双击 `Duo.exe` → 瞬时启动，正常显示设备状态卡与已安装应用磁贴。
- [ ] **投屏开窗**：点击应用图标或主界面「投屏」→ 启动对应虚拟屏会话与 C# overlay 交互条（首窗编译 C# 约 2s，后续秒开）。
- [ ] **静默无闪屏**：启动会话与后台轮询无任何 cmd/conhost 控制台黑窗闪烁。
- [ ] **横竖屏记忆**：右键磁贴切换固定比例（16:9 / 9:16 等）或自适应窗口，再次启动保留设定。
- [ ] **音频策略**：首窗声音输出正常，多窗启动按设置策略（默认仅最新会话发声）自动仲裁。
- [ ] **生命周期**：面板退出时，后台附属会话进程（`scrcpy.exe`、`DuoChromeOverlay.exe` 等）通过 Job Object 干净退出，无孤儿进程残留。

---

## 5. 常见问题与排查

| 现象 | 原因分析 | 解决方案 |
|---|---|---|
| 面板显示「无设备在线」 | 数据线仅供电、驱动未装、未授权调试 | 换高速数据线；在设备弹窗确认授权；终端运行 `adb devices` 确认识别 |
| 找不到 adb 或 scrcpy | 安装路径未加入系统 PATH | 在设置页「引擎」卡中手动填写或浏览选择 `adb.exe` / `scrcpy.exe` 的绝对路径 |
| 终端见 `protocol fault` / 端口被占 | 第三方软件自带的陈旧 adb 服务抢占 5037 端口 | 检查并禁用竞争服务（如投屏软件后台服务），终端执行 `taskkill /F /IM adb.exe` 清理残留后重启 Duo |
| 窗口交互条/下巴未出现 | C# 现场编译器报错或环境缺失 .NET Framework 4.5+ | 检查 `%USERPROFILE%\.local\share\duo\logs` 中的 overlay 日志；确认系统具备 `csc.exe`（Win10/11 均自带） |
| 面板退出后后台仍有 scrcpy | 历史版本残留或强杀主进程 | 生产版本已由 Job Object 绑定生命周期；如遇异常残留执行 `taskkill /F /IM scrcpy.exe` |

