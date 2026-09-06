# Duo

> **让安卓设备成为 Windows 的应用服务器。**

设备熄屏插电，USB 连电脑；Windows 大屏 + 键鼠直接使用安卓应用（背单词、阅读、
视频……），设备屏幕全程熄灭。

## 架构

```
Windows (PyQt6-QML 面板 + C# overlay) ──> scrcpy 引擎 ──> Android（无头服务器）
```

- **整机镜像**：设备画面投窗，等比缩放。
- **应用会话**：应用进固定 2560×1440 虚拟屏独立运行，不碰物理屏；多应用可进同一屏
  （音频不重叠）。窗口纯 Windows 行为：自由拖改、永不自调。
- **窗口体验**：无边框 + 灵动岛（移动/缩放/通知栏）、下巴返回/HOME、系统圆角。
- **质量**：编码器探测自动钉硬件（h264 优先）、60fps（120Hz 面板整除）、FLAC 音频、
  多会话音频仲裁（latest）。

## 快速开始

```bash
git clone https://github.com/iluluyu/duo.git
cd duo && pip install -e .
python -m duo --check   # 环境自检
python -m duo           # GUI 面板
```

需要 Python 3.11+、scrcpy/adb 在 PATH、设备 USB 调试授权。
Windows 安装与打包（onefile → `C:\Tools\Duo.exe`）见 [docs/windows-setup.md](docs/windows-setup.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [TODO.md](TODO.md) | 待办（动态跟随虚拟屏、自研客户端）与基线 |
| [docs/window-experience.md](docs/window-experience.md) | 窗口语义 + 虚拟屏/横竖屏真机调研存档 |
| [docs/mirroring-quality.md](docs/mirroring-quality.md) | 编码/帧率/音频/旗标决策记录 |
| [docs/windows-setup.md](docs/windows-setup.md) | Windows 安装、打包、排障 |

## 开发规范

Python 3.11+，ruff + mypy + pytest（179 passed）全绿；8 空格缩进；C# 保持
C# 5 兼容（`ensure_built()` 现场编译）；测试跑 offscreen+software 后端。

## 许可证

- Duo 本体：[MIT](./LICENSE)
- 依赖第三方（scrcpy: Apache-2.0, adb 等）发布时附带声明
