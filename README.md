# Duo

[![CI](https://github.com/iluluyu/duo/actions/workflows/ci.yml/badge.svg)](https://github.com/iluluyu/duo/actions/workflows/ci.yml)

> **让安卓设备成为 Windows 的应用服务器。**

设备熄屏插电，USB 连电脑；Windows 大屏 + 键鼠直接使用安卓应用（背单词、阅读、视频……），设备屏幕全程熄灭。

## 架构与工程状态

项目正处于从 Python 快速验证栈迈向 Rust 原生性能栈的收敛阶段：

```
[UI 面板] Duo.exe (Rust / egui, src/rustduo/crates/duo-panel)
   │ (命令行 / JSON-lines)
[核心下沉] duo-core.exe (Rust / CLI, src/rustduo/crates/duo-core)
   ├── C# 悬停控件 (DuoChromeOverlay.exe, csc.exe 现场编译) ──> 无边框窗口与胶囊交互
   └── scrcpy 4.1+ 引擎 ──> adb ──> Android（无头应用服务器）

* 注：src/pyduo 为早期验证与行为基准参考栈，完整保留用于回归对拍。
```

- **整机镜像**：设备画面投窗，等比缩放。
- **应用会话（flex）**：独立 2560×1440 虚拟屏运行，不碰物理屏。窗口纯 Windows 行为：自由拖改、方向信 APP、异步下发尺寸。
- **窗口体验**：无边框窗口 + 沉浸式胶囊/下巴（C# overlay）+ 方向锁定（防旋转风暴）+ 全局无闪烁控制台（`CREATE_NO_WINDOW`）。
- **投屏质量**：自动探测硬件编码器（H.264 优先）、60fps 基准、FLAC 音频、单音频独占仲裁、渲染倍率（1.0×~3.0×）线性换算。

## 快速开始

### 1. 原生 Rust 栈（主力产物）

在 WSL 或 Linux 开发环境进行交叉编译（或直接在 Windows 编译）：

```bash
cd src/rustduo
./scripts/build_wsl.sh --deploy   # 编译出 Duo.exe 与 duo-core.exe 并部署
cargo test                         # 260 passed (duo-core 219 + duo-panel 41)
./scripts/parity_check.sh          # 逐字节一致性校验 (234/234)
```

Windows 真机部署与安装见 [docs/windows-setup.md](docs/windows-setup.md)。

### 2. Python 参考栈（基准对照）

```bash
pip install -e ".[gui,dev]"
python -m pyduo --check            # 环境自检
python -m pyduo                    # 启动 PyQt6-QML 面板
pytest                             # 488 passed
```

## 文档指引

| 文档 | 作用与内容 |
|---|---|
| [plan.md](plan.md) / [plan/todo.md](plan/todo.md) | **架构演进与当前任务唯一真相**：P0~P5 计划、待解决问题、决策记录 |
| [TODO.md](TODO.md) | 历史里程碑（0.1~0.4）存档与 Windows 待实测收尾清单 |
| [docs/windows-setup.md](docs/windows-setup.md) | Windows 安装、打包部署（Rust 主力 / Python 备用）与故障排查 |
| [docs/window-experience.md](docs/window-experience.md) | 窗口体系规范、C# overlay 机制、虚拟屏与 9 轮防旋转风暴实验存档 |
| [docs/mirroring-quality.md](docs/mirroring-quality.md) | 编码器、帧率、音频仲裁、DPI 密度与渲染倍率设计依据 |
| [docs/ui/DESIGN.md](docs/ui/DESIGN.md) | UI 材质四层、视觉令牌与组件验收硬性标准（铁律） |
| [src/rustduo/README.md](src/rustduo/README.md) | Rust 模块设计、WSL 交叉编译与二进制规格说明 |

## 开发纪律

- **注释纪律**：代码文件尽可能少写注释（单行、必要、解释 why），设计推导与决策写入 `docs/`。
- **质量门禁**：
  - Python：Python 3.11+，8 空格缩进，`ruff` + `mypy` + `pytest`（488 绿）全过。
  - Rust：`cargo test`（260 绿）+ `cargo clippy --all-targets` 0 警告 + `parity_check.sh` 逐字节一致。
  - C#：保持 C# 5 兼容（Windows 自带 `csc.exe` 现场编译）。

## 许可证

- Duo 本体：[MIT](./LICENSE)
- 依赖第三方（scrcpy: Apache-2.0, adb 等）遵循其原有开源许可。
