# Duo 项目计划

> 高效利用 Windows 电脑，让安卓设备成为服务器——熄屏无头运行、虚拟显示、键鼠操控、一键进入安卓应用生态。

- **仓库**: https://github.com/iluluyu/duo
- **状态**: 🚧 M0 仓库与骨架阶段
- **技术路线**: scrcpy 引擎 + Python (PyQt6) 自研壳
- **最后更新**: 2026-09-03

---

## 1. 愿景与定位

> **核心理念：高效利用 Windows 电脑，让安卓设备成为服务器。**

Duo 把安卓设备当作一台无头**应用服务器**：它躺在桌上熄着屏、插着电，通过 USB/WiFi 向外提供整个安卓生态（背单词、阅读、轻办公）；Windows 电脑是唯一的工作界面——大屏、键鼠、多窗口、所有注意力都在这里。Duo 就是这套 C/S 结构的「客户端 + 服务管理器」。

对应关系：

| 传统 C/S | Duo 世界 |
|---------|---------|
| 服务器主机 | 安卓手机/平板（熄屏、插电、无头运行） |
| 虚拟桌面会话 | scrcpy 虚拟显示（`--new-display`），不依赖物理屏幕 |
| 服务进程 | 各个安卓 App，按需拉起、随会话结束 |
| 瘦客户端 | Windows + Duo：负责展示、操控、多窗口 |
| 一键部署脚本 | Duo 预设（Profile）：如「背单词模式」 |

用户因此可以：

- 在电脑大屏上流畅使用安卓应用（背单词、阅读、轻办公）
- 平板/手机物理屏幕关闭（`--turn-screen-off`），设备放一边，电脑上继续用
- 用 PC 键盘鼠标完整操控安卓：返回、主页、多任务、中文输入
- 一键预设（Profile）：例如"背单词模式" = 虚拟显示 2K + 90fps + 黑屏 + 自动启动墨墨/不背单词

**Duo 不是**：不是 scrcpy 的 fork，不改引擎代码；不自研视频链路（v1 阶段）；不追求 scrcpy 全功能覆盖，只做"日常使用最顺手"的子集。

## 2. 需求清单（R 系列）

| # | 需求 | 引擎能力 | Duo 要做的 |
|---|------|----------|-----------|
| R1 | 安卓手机/平板投屏到 Windows | scrcpy 视频镜像 | 设备发现、一键投屏、会话管理 |
| R2 | 快捷键（返回键等） | scrcpy 自带 MOD 快捷键；`adb shell input keyevent` | 可视化快捷键方案 + 自定义映射 + 帮助面板 |
| R3 | 高帧率、高码率 | `--max-fps`、`--video-bit-rate`、`--video-codec` | 画质档位预设（流畅/均衡/极致），默认调优 |
| R4 | 适配电脑屏幕大小 | `--window-width/height`、自适应 | 窗口记忆、按显示器分辨率推荐尺寸、DPI 感知 |
| R5 | 单独打开应用 | `--start-app`、`--new-display`（虚拟显示器） | 应用启动器面板（枚举设备应用 + 图标） |
| R6 | 平板黑屏使用 | `--turn-screen-off --stay-awake` | 一键"沉浸模式"开关 |
| R7 | 键鼠/剪贴板输入 | scrcpy 键鼠注入、`--clipboard-autosync` | 输入状态指示、剪贴板同步开关 |
| R8 | 多设备管理 | scrcpy `--serial` | 多设备列表、并行会话、独立配置 |

### 2.1 核心场景："背单词模式"

用户在平板上装了背单词/阅读类 App，希望：

```
平板躺在桌上（屏幕熄灭） ──USB/WiFi──> 电脑上开一个 2560×1440 虚拟显示窗口
                                          │
                                          ├─ 自动启动指定 App
                                          ├─ PC 键盘打字（背单词输入）
                                          ├─ Esc/侧键 = 返回，快速切页面
                                          └─ 关窗口 = 会话结束，平板恢复正常
```

对应的引擎命令（Duo 负责拼装并隐藏复杂度）：

```bash
scrcpy \
  --serial=<device> \
  --new-display=2560x1440 \
  --start-app=<package> \
  --turn-screen-off --stay-awake \
  --max-fps=90 --video-bit-rate=30M --video-codec=h265 \
  --keyboard=uhid --clipboard-autosync \
  --window-title="Duo · 背单词"
```

## 3. 架构

```
┌──────────────────────────── Duo Shell (Python 3.11+ / PyQt6) ────────────────────────────┐
│                                                                                          │
│  ┌─────────── UI 层 (PyQt6) ───────────┐   ┌──────────── core 层（无 Qt 依赖，可单测）─────────┐ │
│  │ MainWindow   设备列表/会话切换/托盘    │   │ devices    设备发现与热插拔监听 (adb track)      │ │
│  │ LauncherPanel 应用启动器（图标网格）   │──>│ engine     scrcpy/adb 二进制定位、版本探测       │ │
│  │ ProfileEditor 预设编辑器             │   │ session    会话管理：spawn scrcpy、参数拼装、     │ │
│  │ ShortcutHelp 快捷键速查浮层           │   │            进程监控、崩溃自动重启                 │ │
│  │ Settings    引擎路径/开机自启/语言    │   │ profiles   预设持久化 (JSON, ~/.config/duo)     │ │
│  └────────────────────────────────────┘   │ apps       应用枚举/图标提取 (adb shell pm/cmd)  │ │
│                                           └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────┬───────────────────────────────────────────┘
                                               │ 子进程 spawn + stdout/stderr 结构化日志
                                    ┌──────────▼──────────┐        ┌──────────────┐
                                    │  scrcpy (引擎, 独占) │ <────> │ adb (平台工具) │
                                    └──────────┬──────────┘        └──────┬───────┘
                                               │ socket 视频流+控制协议       │
                                    ┌──────────▼───────────────────────▼───────┐
                                    │  Android 设备（需开启 USB 调试 / 无线调试） │
                                    └──────────────────────────────────────────┘
```

**分层原则**：

1. `core/` 纯 Python，不 import 任何 Qt 模块 → 可以脱离 GUI 跑测试、做 CLI 模式
2. `ui/` 只做展示与交互，一切副作用走 `core/` 的接口
3. 引擎细节（scrcpy 参数名、版本差异）收敛在 `engine.py` 一处 → 引擎升级只改一个文件
4. 预设/配置是纯数据（JSON），用户可手改、可分享

### 3.1 目录结构

```
duo/
├── plan.md                  # 本文件：唯一事实来源（single source of truth）
├── README.md
├── pyproject.toml
├── duo/
│   ├── __init__.py
│   ├── __main__.py          # python -m duo 入口
│   ├── core/
│   │   ├── engine.py        # scrcpy/adb 探测、版本、参数拼装 (EngineArgs)
│   │   ├── devices.py       # 设备枚举与监听
│   │   ├── session.py       # 会话生命周期
│   │   ├── profiles.py      # 预设模型与持久化
│   │   └── apps.py          # 应用枚举与启动
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── tray.py
│   │   └── ...
│   └── resources/           # 图标、默认预设
├── tests/
└── .github/workflows/ci.yml # lint (ruff) + test (pytest) + 类型检查 (mypy)
```

### 3.2 关键技术决策记录（ADR 摘要）

| 决策 | 选择 | 理由 | 放弃项 |
|------|------|------|--------|
| 视频链路 | 复用 scrcpy | 成熟稳定，黑屏/虚拟显示/键鼠注入全都有 | 自研 MediaCodec+FFmpeg（周期 1-3 个月） |
| GUI 框架 | PyQt6 | 开发速度快、成熟；本机 Linux 可开发，Windows 运行 | WPF（不能在 Linux 调试）、Qt/C++（周期长） |
| 引擎调用方式 | 子进程 spawn，解析 stderr | scrcpy 的库化接口（python-scrcpy-client 等）版本追随滞后，子进程最稳 | libscrcpy 嵌入、python-scrcpy-client |
| 配置存储 | `~/.config/duo/*.json` | 透明、可手编、可同步 | SQLite（过度设计） |
| Python 版本 | 3.11+ | asyncio 性能与语法特性 | — |

## 4. 里程碑

### M0：仓库与骨架（当前）
- [x] GitHub 仓库创建（public）
- [x] 包结构 + pyproject + CI 骨架
- [x] `python -m duo` 可运行：打印版本、探测 scrcpy/adb
- [ ] CI 绿灯（ruff + pytest + mypy）

### M1：MVP 投屏（目标：可日常使用）
- [ ] 设备列表：`adb devices` 解析 + 热插拔刷新（`adb track-devices` 或轮询）
- [ ] 引擎探测：定位 PATH / 自带 bundle 目录中的 scrcpy 与 adb，读取 `--version`
- [ ] 一键投屏：默认参数（h265 / 20Mbps / max-fps 与设备刷新率对齐 / 窗口自适应）
- [ ] 会话管理：进程存活监控、退出码展示、崩溃重启（最多 3 次）
- [ ] 参数拼装器 `EngineArgs`：覆盖 R3/R4 的所有旗标，带版本兼容检查
- [ ] 结构化日志：stdout/stderr 按行采集，写入 `~/.local/share/duo/logs/`

**验收**：插上平板 → 点一下按钮 → 电脑出现镜像，帧率 ≥ 60，窗口尺寸贴合屏幕；拔线后 Duo 状态正确回落。

### M2：快捷键与预设
- [ ] 快捷键中心：可视化展示 scrcpy MOD 快捷键 + Duo 自身快捷键（可禁用冲突）
- [ ] Duo 全局热键（如 `Ctrl+Alt+B` = BACK），走 `adb shell input keyevent 4`，不抢 scrcpy 窗口焦点也能用
- [ ] 预设系统：增删改查/克隆/导出导入；字段覆盖分辨率、fps、码率、编码器、黑屏、虚拟显示、启动 App
- [ ] 内置预设：`流畅`、`均衡`、`极致`、`背单词`
- [ ] 每设备记住上次使用的预设

**验收**：新建"背单词"预设 → 连接后一键应用 → 黑屏 + 虚拟显示 + 指定 App 启动，全程无需碰平板。

### M3：应用启动器
- [ ] 枚举第三方应用：`adb shell pm list packages -3` + `dumpsys package` 取 launcher Activity
- [ ] 图标提取：`adb shell cmd package ...` / APK 拉取后本地解包（`pm path` + unzip），缓存到本地
- [ ] 启动器 UI：搜索框 + 网格 + 最近使用；点击 = 在虚拟显示中启动（`--start-app` 或 `am start`）
- [ ] "固定到任务栏/桌面快捷方式"：生成一键启动某 App 的会话参数

### M4：体验打磨
- [ ] 系统托盘：常驻、快速重连、设备状态图标
- [ ] 无线连接向导：`adb pair` / `adb connect` 的分步引导（Android 11+ 无线调试）
- [ ] 剪贴板：双向同步开关与状态提示
- [ ] 中文输入优化：`--keyboard=uhid` 验证 + 输入法注意事项写进 UI 提示
- [ ] 多显示器：记住窗口所在显示器与几何信息
- [ ] 自动更新检查（GitHub Releases API）

### M5：打包发布 v1.0
- [ ] PyInstaller 打包单 exe（内嵌 scrcpy-win64 + adb，遵守各自许可证并注明）
- [ ] GitHub Actions 自动构建 + Release 产物（win64 zip + sha256）
- [ ] 安装体验：首次运行引导开启 USB 调试的图文指引
- [ ] README 完善：截图、GIF、FAQ

## 5. 引擎参数映射表（实现 R1–R7 的速查）

| 需求 | scrcpy 参数 | 默认值策略 | 备注 |
|------|-------------|-----------|------|
| 高帧率 | `--max-fps=<n>` | 读取 `adb shell dumpsys display` 实际刷新率，min(设备刷新率, 90) | scrcpy ≥1.18 |
| 高码率 | `--video-bit-rate=30M` | 预设决定：流畅 8M / 均衡 20M / 极致 50M | — |
| 编码器 | `--video-codec=h265` | 设备支持则 h265（省 30% 带宽），否则回落 h264 | `--video-codec=h265?` 问号语法需 v2.0+ |
| 硬解 | `--video-encoder=xxx`（可选） | 默认不动 | 高级选项 |
| 屏幕适配 | `--window-width/--window-height` | 按主显示器工作区 85% 与设备宽高比计算 | 与 `--max-size` 二选一，倾向窗口侧 |
| 黑屏 | `--turn-screen-off --stay-awake` | "沉浸模式"开关 | 唤醒用 `MOD+Shift+o` |
| 虚拟显示 | `--new-display=WxH[/dpi]` | 预设配置，默认 1920x1080/2560x1440 档位 | scrcpy ≥2.0 |
| 启动应用 | `--start-app=<pkg>` | 启动器传入 | scrcpy ≥2.4 |
| 键盘 | `--keyboard=uhid` | uhid（支持中文输入法）| 旧版行为 `--prefer-text` 兼容 |
| 剪贴板 | `--clipboard-autosync` | 开 | 同步安卓↔PC |
| 多设备 | `--serial=<id>` | 会话绑定设备 | — |
| 音频 | `--audio-codec=opus`（可选开关） | 默认关闭（背单词场景手机外放即可） | scrcpy ≥2.0，Android ≥11 |
| 旋转 | `--rotation`/`MOD+r` | — | 平板横竖屏切换 |

**快捷键速查**（scrcpy 默认 MOD = `LAlt` 或 `LSuper`，Duo 帮助面板直接展示）：

| 动作 | 快捷键 |
|------|--------|
| 返回 | `MOD+b` / `MOD+Backspace` / 右键 |
| 主页 | `MOD+h` / 中键 |
| 多任务 | `MOD+s` |
| 菜单/解锁 | `MOD+m` |
| 音量± | `MOD+↑/↓` |
| 电源 | `MOD+p` |
| 熄灭物理屏 | `MOD+o` |
| 点亮物理屏 | `MOD+Shift+o` |
| 旋转 | `MOD+r` |
| 通知面板 | `MOD+n`（再按 `n` 展开设置） |
| 复制/剪切/粘贴 | `MOD+c` / `MOD+x` / `MOD+v` |
| 全屏 | `MOD+f` |
| 适应窗口 | `MOD+g` |

## 6. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| scrcpy 版本升级改参数名 | 中 | 中 | 参数拼装集中在 `engine.py`；启动时做版本探测 + 能力表 |
| `--turn-screen-off` 部分机型异常（亮一下/触控失灵） | 中 | 低 | 记录设备兼容矩阵；提供"仅锁屏"替代路径 |
| 虚拟显示下部分 App 检测/拒绝 | 低 | 中 | 回落普通镜像模式；FAQ 说明 |
| PyQt6 + PyInstaller 体积大（~60MB） | 高 | 低 | 接受；可选 Nuitka 压缩；资源外置 |
| Windows Defender 误报未签名 exe | 中 | 中 | GH Actions 构建加证书说明、提供 zip 版 |
| 中文输入 uhid 在部分输入法失效 | 中 | 中 | 设置里提供 aoa/hid/uhid 切换 + 场景提示 |
| 用户环境无 scrcpy/adb | 高 | 高 | 首启引导下载或使用内置 bundle（M5） |

## 7. 开发与调试环境

- **开发机**: Linux（本仓库维护处）；**目标平台**: Windows 10/11 x64
- Python ≥ 3.11；`uv` 或 `pip` 管理；依赖：`PyQt6`、`pyqt6-tools`(dev)
- 工具链：`ruff`（lint+format）、`pytest`、`mypy`
- 运行: `python -m duo`（GUI） / `python -m duo --check`（环境自检）
- 手动测试设备：用户的安卓平板（开启 USB 调试）

## 8. 验收清单（v1.0 Definition of Done）

1. ✅ 全新 Windows 机器：下载 zip → 解压 → 双击 exe → 首启引导 → 投屏成功
2. ✅ "背单词"预设一键：黑屏 + 2K 虚拟显示 + 90fps + 自动进 App + 键盘可输入中文
3. ✅ 快捷键面板可见可查；返回/主页/多任务全部可用
4. ✅ 断线重连 < 5s；崩溃不闪退，有日志可查
5. ✅ README 有完整截图与 FAQ；CI 全绿；LICENSE（MIT）与第三方组件声明齐全

---

*本文件随开发推进持续更新；每次里程碑完成在对应章节打勾并追加日期。*
