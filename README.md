# Duo

[![CI](https://github.com/iluluyu/duo/actions/workflows/ci.yml/badge.svg)](https://github.com/iluluyu/duo/actions/workflows/ci.yml)

> Turn your Android device into a headless application server for Windows.  
> **Keywords**: `scrcpy` · `Android apps on Windows` · `virtual display` · `headless Android` · `USB mirroring` · `Rust`

Duo 是一款基于 Rust 开发的 Windows 桌面应用。通过编排 scrcpy 与 adb，将安卓设备转化为电脑的无头应用服务器：手机熄屏插电、USB 直连电脑，即可在 Windows 大屏上以原生键鼠体验流畅运行各类安卓应用（背单词、阅读、视频等）。手机端屏幕全程熄灭，无发热与电量焦虑。

## 核心特性

- **应用会话（Virtual Display）**：在独立 2560×1440 虚拟显示屏中启动应用，完全不干扰物理屏。窗口具备原生 Windows 窗口行为，支持自由拖拽缩放、方向信 APP、异步下发尺寸。
- **整机镜像**：一键把整个设备画面投进 Windows 窗口，等比缩放，键鼠可直接操控整机。
- **窗口体验与沉浸交互**：无边框窗口设计，搭配 C# overlay 现场编译呈现的悬停胶囊与下巴控件；内置防旋转风暴机制，控制台调用全局静默（`CREATE_NO_WINDOW`），杜绝黑框闪烁。
- **高品质低延迟传输**：自动探测硬件编码器（H.264 优先），60fps 基准，支持 FLAC 音频传输与单会话独占仲裁，提供 1.0×~3.0× 渲染倍率线性换算。

> 初代 Python 验证栈（pyduo）已退役，完整生平与找回方式见 [docs/history/pyduo.md](docs/history/pyduo.md)。

## 快速上手（Windows）

### 第 1 步：安装 Scoop（命令行包管理器）

Scoop 以用户身份安装到 `~\scoop`，自动配置 PATH，**无需管理员权限**。在
PowerShell（Windows 10/11 自带）中执行：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
irm get.scoop.dev | iex
scoop install git    # scoop 自更新与 bucket 依赖 git，装完推荐执行
```

### 第 2 步：安装 adb 与 scrcpy

```powershell
scoop install adb scrcpy
```

两个包都在 scoop 默认 main bucket，与上游同步发版。Duo 需要
**scrcpy ≥ 4.1**（依赖 `--new-display` / `--flex-display` 等虚拟屏特性），
装完可用 `scrcpy --version` 确认。

不想用 Scoop 亦可：手动下载 [platform-tools](https://developer.android.com/tools/releases/platform-tools)
与 [scrcpy](https://github.com/Genymobile/scrcpy/releases) 解压后加入
PATH，或在 Duo 设置页「引擎」卡中填写 `adb.exe` / `scrcpy.exe` 绝对路径。

### 第 3 步：保持最新（最快的更新通道）

```powershell
scoop update              # 先更新 scoop 自身与 bucket 索引
scoop update adb scrcpy   # 升级到上游最新版
scoop status              # 查看待升级清单
```

上游发版后 scoop bucket 通常当天跟进——比 SDK Manager 或手动下 zip
快得多，一条命令即完成升级与 PATH 维护。

### 第 4 步：准备安卓设备

1. 手机开启「开发者选项」→「USB 调试」；
2. 数据线连接电脑，首次连接在手机端勾选「一律允许此计算机进行调试」；
3. `adb devices` 能看到设备即就绪。

### 第 5 步：获取 Duo

从源码构建并安装（当前发布方式）：

```powershell
git clone https://github.com/iluluyu/duo.git C:\duo
cd C:\duo
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

完整构建路径（WSL 交叉编译 / Windows 原生编译）与故障排查见
[docs/windows-setup.md](docs/windows-setup.md)。

## 架构

```
[UI 面板] Duo.exe (Rust / egui, src/rustduo/crates/duo-panel)
   │ (命令行 / JSON-lines)
[核心引擎] duo-core.exe (Rust / CLI, src/rustduo/crates/duo-core)
   ├── C# 悬停控件 (DuoChromeOverlay.exe, csc.exe 现场编译) ──> 无边框窗口与胶囊交互
   └── scrcpy 4.1+ 引擎 ──> adb ──> Android（无头应用服务器）
```

## 开发

```bash
cd src/rustduo
cargo test                          # 282+ passed（duo-core + duo-panel）
cargo clippy --all-targets -- -D warnings
cargo fmt --all --check
./scripts/build_wsl.sh --deploy     # WSL 交叉编译双 exe 并部署到 Windows
```

出图调试（无设备截图）：`duo-panel --shot out.png --page home|settings --theme light|dark`。

## 文档指引

| 文档 | 作用与内容 |
|---|---|
| [TODO.md](TODO.md) | **唯一活任务清单**：Windows 真机验收、挂起项、设计边界、出图对拍回路 |
| [docs/windows-setup.md](docs/windows-setup.md) | Windows 安装、构建部署与故障排查 |
| [docs/window-experience.md](docs/window-experience.md) | **窗口/会话活规范**：窗口行为合同、窗口栏三态、进程生命周期、玻璃总开关、平行视窗密度 |
| [docs/mirroring-quality.md](docs/mirroring-quality.md) | 编码器、帧率、音频仲裁、DPI 密度与渲染倍率设计依据 |
| [docs/ui/DESIGN.md](docs/ui/DESIGN.md) | UI 材质四层、视觉令牌与组件验收硬性标准（铁律） |
| [docs/ui/glass-recipe.md](docs/ui/glass-recipe.md) | 毛玻璃/液态玻璃配方唯一论述（菜单/岛/overlay 三端共用） |
| [docs/ui/chin-island-acrylic.md](docs/ui/chin-island-acrylic.md) | 下巴/沉浸岛机制链与定稿（Mica/acrylic/frost 通道实证） |
| [docs/ui/RESEARCH-ICONS.md](docs/ui/RESEARCH-ICONS.md) | 图标获取/统一化调研与设备端渲染方案 |
| [docs/history/](docs/history/) | **冻结存档**：pyduo 时代、迁移计划、窗口/overlay 实验、图标与玻璃调优轮次 |
| [src/rustduo/README.md](src/rustduo/README.md) | Rust 模块设计、WSL 交叉编译与二进制规格说明 |

## 开发纪律

- **注释纪律**：代码文件尽可能少写注释（单行、必要、解释 why），设计推导与决策写入 `docs/` 对应域文档；轮次日志与被取代方案一律进 `docs/history/`（冻结，不维护）。
- **质量门禁**：
  - Rust：`cargo test` 全绿 + `cargo clippy --all-targets` 0 警告 + `cargo fmt --check`。
  - C#：overlay（`src/rustduo/crates/duo-core/resources/chrome_overlay.cs`）保持 C# 5 兼容（Windows 自带 `csc.exe` 现场编译）。

## 许可证

- Duo 本体：[MIT](./LICENSE)
- 依赖第三方（scrcpy: Apache-2.0, adb 等）遵循其原有开源许可。
