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
  --keyboard=uhid \
  --window-title="Duo · 背单词"
```

> 注：剪贴板同步在 scrcpy 3.0+ 已默认开启，无正向旗标（传 `--clipboard-autosync` 会报错退出）；关闭才需 `--no-clipboard-autosync`。

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
- [x] CI 绿灯（ruff + pytest + mypy）（2026-09-03 验证通过）

### M1：MVP 投屏（目标：可日常使用）
- [x] 设备列表与热插拔：`adb devices` 轮询差分（2s）+ 会话随设备断开自动停止（track-devices 二进制帧协议方案弃用，见实验记录）
- [x] 引擎探测：定位 PATH / 自带 bundle 目录中的 scrcpy 与 adb，读取 `--version`（M0 完成，WSL interop 就绪）
- [x] 一键投屏：默认参数（h265 / 30M / max-fps=90 / flex 显示 + dpi480）——已固化为 `duo mirror` CLI，全链路实测通过（2026-09-03）
- [x] 会话管理：进程存活监控、崩溃自动重启（最多 3 次）、退出码；拔线自动停止；`session.py` 完成
- [x] 参数拼装器 `EngineArgs`：覆盖 R3/R4 的所有旗标，带版本兼容检查（clipboard 正向旗标缺失、flex 依赖 new-display、flex 拒绝 window-size 旗标已固化在测试里）
- [x] 结构化日志：stdout/stderr 按行采集，写入 `~/.local/share/duo/logs/`
- [x] App 品牌化会话：窗口标题 = App 标签（aapt2 解析，APK 拉取缓存）——不背单词实测通过；⚠️ 窗口图标：scrcpy 无旗标，后续走 Windows AUMID+快捷方式或自绘窗口
- [x] GUI 雏形：`duo --gui`（PyQt6 面板：设备状态/应用列表/竖屏开关；Apple 美学：克制/留白/KISS；WSLg 不可用时 offscreen 验证，Windows 原生运行）
- [ ] GUI 在 Windows 侧实景运行验证（WSLg 本环境 DISPLAY 不可用）
- [x] 窗口 chrome：无边框 + 顶部悬停胶囊（min/max/close）+ 常驻下巴（‹返回 ○桌面）（2026-09-03 完成，实测悬停/常驻/心跳全通过）
- [ ] 拔线回落实景验收（用户拔线观察）

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
| 虚拟显示 | `--new-display=WxH[/dpi]` | 预设配置，默认 1920x1080/2560x1440 档位 | scrcpy ≥2.0；另有 `-x/--flex-display`（须与 `--new-display` 同用，不能单独用）：虚拟屏持续跟随窗口尺寸变化 |
| 启动应用 | `--start-app=<pkg>` | 启动器传入 | scrcpy ≥2.4 |
| 键盘 | `--keyboard=uhid` | uhid（支持中文输入法）| 旧版行为 `--prefer-text` 兼容 |
| 剪贴板 | 默认开启（无正向旗标） | 默认同步；关闭用 `--no-clipboard-autosync`（3.0 起默认开，⚠️ 4.1 实测正向旗标报错） | 同步安卓↔PC |
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

- **开发机**: WSL2 (Linux)；**目标平台**: Windows 10/11 x64
- **关键约定（WSL interop 工作流）**: 开发在 WSL，实验跑 Windows 侧 —— 通过 interop 调用 Windows 二进制（`scrcpy.exe` / `adb.exe`）。只有 Windows 侧 adb 能看到 USB 设备，也只有 Windows 侧 scrcpy 能在桌面开窗。`engine.probe()` 在 WSL 下自动优先探测 `.exe` 变体
- **引擎现状（已验证）**: scrcpy **4.1** + adb **37.0.1**（scoop 安装，均在 Windows PATH）
- Python ≥ 3.11；依赖：`PyQt6`（gui extra）；工具链：`ruff`、`pytest`、`mypy`
- 运行: `python -m duo --check`（环境自检，WSL 下显示 interop 模式） / `python -m duo`（GUI，M1 起可用）
- GUI 运行环境注意: PyQt 壳最终打包为 Windows exe（M5）；开发期可在 WSLg 预览 UI，但投屏会话（spawn `scrcpy.exe`）始终在 Windows 侧进行
- **手动测试设备（兼容矩阵第 1 行）**: OPPO 平板 `OPD2409`，serial `4444bd6b`，Android **16**，物理分辨率 2400×3392（刷新率 30/48/50/60/90/120/144Hz 七档），USB 连接——✅ 已验证：虚拟显示 1920×1080@201dpi 正常、D3D11 渲染正常（首次实验 2026-09-03）
- **实验记录（2026-09-03，黑屏与硬编串流验证）**:
    - ✅ 黑屏串流：`--turn-screen-off --stay-awake` → server 报 `Device display turned off`，串流继续；退出后屏幕自动恢复 ON（无需手动唤醒）
    - ✅ 静息跳帧：画面静止时 fps→0（scrcpy 只在内容变化时编码，黑屏挂机时功耗/带宽趋零）
    - ✅ 硬件编码：`--video-codec=h265 --video-encoder=c2.qti.hevc.encoder`（Qualcomm 硬编）+ `--video-bit-rate=30M` + `--max-fps=90`：滑动动画期间实测峰值 112fps、稳定段 80-100+；`max-fps` 为近似上限（短时可超）
    - ✅ 组合验证：黑屏 + 硬编 + 高帧率同开无冲突（即背单词命令形态，除 `--new-display`/`--start-app` 外全部就绪）
    - ✅ 比例解耦验证：`--new-display=2560x1440` + 黑屏 + 硬编 → 虚拟屏 16:9 横版、DPI 自动 268、平板 7:5 物理屏熄灭不参与，串流原生 2560×1440 零黑边；任意 WxH[/dpi] 可选（解决 PC 16:9 vs 平板 7:5 比例不匹配问题）
    - ⚠️ 副屏桌面不可用：虚拟屏回落到 ColorOS 启动器时渲染乱码（OEM launcher 副屏支持差）。产品决策：**虚拟屏 = 纯 App 容器**，预设必带 `start_app` 直达 App，Duo 不展示任何副屏桌面；App 退出由会话策略处理（重启 App 或结束会话）
    - ✅ 多虚拟屏并发：同设备同时 10 块 1280×720 虚拟屏（id 10–19）+ 10 路硬编 H.265 并行，零错误零残留。实测下限 ≥10，实际上限受硬编并发/USB 带宽/内存约束，产品合理甜点区 1–3 块高分辨率屏
    - ✅ flex-display 动态跟随（2026-09-03，不背单词实测）：`--new-display --flex-display` → 虚拟屏尺寸持续跟随 scrcpy 窗口（初始 1280×960 → 窗口最大化后自动变 3840×2054）。彻底解决任务栏导致的宽高比不匹配黑边（4K 屏减任务栏后 ≠16:9，固定尺寸虚拟屏必留边）。⚠️ 限制：`-x` 必须与 `--new-display` 同用。产品决策：Duo 默认显示模式 = flex，固定 WxH 作为预设可选
    - ⚠️ flex 默认 DPI=160 在 4K 窗口下过小（UI 元素按 1dp=1px 渲染）；用 `--new-display=/DPI` 单独指定 DPI 与 flex 兼容。4K 最大化窗口推荐 480（1dp=3px，≈1280dp 宽大平板布局；320 紧凑 / 560 特大）。EngineArgs 需提供 dpi 旋钮 + 按显示器宽度自动推荐逻辑
    - ✅ 竖屏 DPI 调优（2026-09-03）：固定 DPI 在竖屏下 dp 宽度只剩 ~500dp，手机布局拉伸到巨窗 = 元素巨大。解法：`monitor.py` 读物理工作区 → 横屏 flex+按目标 1280dp 反推 DPI（4K→480）；竖屏 `--portrait` = 固定 WxH 屏（宽=工作区高×0.6，≈640dp）+ 右侧预设窗口。实测 1252×2088/313（=640dp 手机布局）
    - ✅ PowerToys 调窗自动跟随（2026-09-03）：竖屏改回 flex（仅预设位置不锁尺寸，窗口几何完全交给用户的管理器）；用 Win32 SetWindowPos 模拟外部改窗实测 Texture 连续跟随（1250×1302→1886×2000）✅
    - ⚠️ 引擎约束（4.1 实测）：`--window-width/--height` 与 `--flex-display` 互斥（报错），竖屏固定模式可用；PowerShell 查工作区需先 `SetProcessDPIAware()` 否则拿到 150% 缩放的逻辑像素（2560×1392）而非物理像素（3840×2088），scrcpy 窗口用的是物理像素
    - ⚠️ `adb track-devices` 用二进制长度前缀帧协议（无行分隔，实测首帧 `\x00\x10` + payload），行式解析不可用 → 热插拔改用 2s 轮询 `adb devices` 差分（KISS，跨版本稳定）
    - ⚠️ ColorOS 限制：`screencap -d <虚拟屏id>` 报 "not valid"，无法直接截取非默认屏（排障时改用 PC 侧窗口截图）
- **M1 开工（2026-09-03）：代码落地与端到端验证**
- ✅ 窗口 chrome 交付（2026-09-03，子代理原型 + 主线重写视觉层）：`duo mirror --chrome`；架构 = scrcpy `--window-borderless` + Windows 侧 C# overlay（`chrome_overlay.cs`，csc.exe 现场编译缓存于 ~/.local/share/duo/overlay/，sha256 戳失效）。视觉 = UpdateLayeredWindow 逐像素 Alpha + PrintWindow(PW_RENDERFULLCONTENT) 采样自制亚克力（SetWindowCompositionAttribute 在 Win11 24H2 返回 E_FAIL，不可用）；窗口修复 = WS_THICKFRAME 重加（可 resize）+ DWMWCP_ROUND（Win11 圆角）；仿最大化防盖任务栏；WinEvent 钩子实时跟随
- ⚠️ 调试战史（全是实测换来的）：① CreateCompatibleDC/SelectObject 等住在 **gdi32.dll** 不是 user32（入口点缺失 = 静默崩溃）；② WinEvent 回调里 BeginInvoke 需先建句柄 + 防 teardown 竞态 try/catch；③ csc.exe 输出是本地化 GBK，解码要 errors=replace；④ powershell.exe 命令行会弄花 CJK（PS 走 UTF-8 BOM 脚本文件，C# exe 走 UTF-16 argv 无此问题）；⑤ WSL 里 pkill -f 的 pattern 会匹配含同样文本的自身命令行（清场与启动分两条命令）；⑥ 子代理后台 bash/孤儿进程会持续干扰实验（结束后必扫残留）
    - ✅ `duo mirror --app <pkg>` 全链路：自动选机 → APK 拉取缓存（设备路径变化自动失效）→ aapt2 标签解析 → EngineArgs 拼装 → 会话监管 → 窗口标题「不背单词」实测生效
    - ⚠️ APK 解析坑：新式 AXML（header 12 字节）击溃 pyaxmlparser/androguard/apkutils2 三个纯 Python 库；改用 Google Maven 官方 aapt2（`tools.py` 自动下载到 ~/.local/share/duo/tools/，版本 pin 9.4.0-15978811），M5 打包时随 bundle 分发
    - ✅ 测试 28 项全绿（fixtures 为设备真实输出）；ruff/mypy 干净
    - 待办：热插拔监听、GUI、窗口图标（AUMID 方案）、stdout flush（nohup 下缓冲）
    - ✅ 黑屏期间 `adb shell input` 注入正常（Duo 全局热键方案可行）
    - ⚠️ 引擎参数修正：scrcpy 4.1 无 `--clipboard-autosync` 正向旗标（已更新 §2.1 与 §5）；`EngineArgs` 必须做版本能力表

## 8. 验收清单（v1.0 Definition of Done）

1. ✅ 全新 Windows 机器：下载 zip → 解压 → 双击 exe → 首启引导 → 投屏成功
2. ✅ "背单词"预设一键：黑屏 + 2K 虚拟显示 + 90fps + 自动进 App + 键盘可输入中文
3. ✅ 快捷键面板可见可查；返回/主页/多任务全部可用
4. ✅ 断线重连 < 5s；崩溃不闪退，有日志可查
5. ✅ README 有完整截图与 FAQ；CI 全绿；LICENSE（MIT）与第三方组件声明齐全

---

*本文件随开发推进持续更新；每次里程碑完成在对应章节打勾并追加日期。*
