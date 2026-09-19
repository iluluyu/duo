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

## 架构

```
[UI 面板] Duo.exe (Rust / egui, src/rustduo/crates/duo-panel)
   │ (命令行 / JSON-lines)
[核心引擎] duo-core.exe (Rust / CLI, src/rustduo/crates/duo-core)
   ├── C# 悬停控件 (DuoChromeOverlay.exe, csc.exe 现场编译) ──> 无边框窗口与胶囊交互
   └── scrcpy 4.1+ 引擎 ──> adb ──> Android（无头应用服务器）
```

## 快速开始

在 WSL 或 Linux 开发环境交叉编译（或直接在 Windows 编译）：

```bash
cd src/rustduo
./scripts/build_wsl.sh --deploy   # 编译出 Duo.exe 与 duo-core.exe 并部署
cargo test                         # 282 passed (duo-core 227 + duo-panel 55)
```

Windows 真机部署与安装见 [docs/windows-setup.md](docs/windows-setup.md)。

## 文档指引

| 文档 | 作用与内容 |
|---|---|
| [TODO.md](TODO.md) | **唯一活任务清单**：Windows 真机验收、挂起项、设计边界、出图对拍回路 |
| [docs/windows-setup.md](docs/windows-setup.md) | Windows 安装、构建部署与故障排查 |
| [docs/window-experience.md](docs/window-experience.md) | 窗口体系规范、C# overlay 机制、虚拟屏与 9 轮防旋转风暴实验存档 |
| [docs/mirroring-quality.md](docs/mirroring-quality.md) | 编码器、帧率、音频仲裁、DPI 密度与渲染倍率设计依据 |
| [docs/ui/DESIGN.md](docs/ui/DESIGN.md) | UI 材质四层、视觉令牌与组件验收硬性标准（铁律） |
| [docs/history/](docs/history/) | 项目史：pyduo 时代、Python→Rust 迁移计划、历史任务存档（冻结不维护） |
| [src/rustduo/README.md](src/rustduo/README.md) | Rust 模块设计、WSL 交叉编译与二进制规格说明 |

## 开发纪律

- **注释纪律**：代码文件尽可能少写注释（单行、必要、解释 why），设计推导与决策写入 `docs/`；历史材料进 `docs/history/`。
- **质量门禁**：
  - Rust：`cargo test` 全绿 + `cargo clippy --all-targets` 0 警告 + `cargo fmt --check`。
  - C#：overlay（`src/rustduo/crates/duo-core/resources/chrome_overlay.cs`）保持 C# 5 兼容（Windows 自带 `csc.exe` 现场编译）。

## 许可证

- Duo 本体：[MIT](./LICENSE)
- 依赖第三方（scrcpy: Apache-2.0, adb 等）遵循其原有开源许可。
