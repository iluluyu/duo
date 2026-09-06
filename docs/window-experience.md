# 窗口体验：最小实施说明

配套清单：[TODO.md](../TODO.md)。目标平台为 Windows 10/11；Linux 自动测试不能替代 Windows 桌面验证。

## 1. 当前实现与问题入口

实际架构是 **PyQt6 启动面板 → Python 会话进程 → 独立 scrcpy SDL 窗口 + C# WinForms overlay**，不是把视频嵌入 Qt。修改 Qt 的 QSS 不会改变 scrcpy 窗口形状。

| 入口 | 已有能力 / 本轮关注点 |
|---|---|
| `duo/ui/main_window.py` | ✅ 已解锁宽度（最小 360×520，初始 420×660）；全部应用网格按宽度重算列数（`_columns_for`）、运行中芯片网格换行；`run_app()` 仍硬编码 `adb.exe`（设置页任务中处理）；只有 `gui_prefs.json` 横竖屏偏好 |
| `duo/__main__.py` | `_run_mirror()` 组装显示/编码参数并启动 overlay；已接入 `adb_pin_env` 与 overlay 新参数（模式/初始尺寸/会话日志）；GUI 子进程经 CLI 启动 |
| `duo/core/engine.py` | `probe()` 仅搜索 PATH；`adb_pin_env()` 钉 adb（ADB 环境变量 + WSLENV，不再用非法的 `--adb` 旗标）；`DisplaySpec` 已区分 mirror/fixed/flex |
| `duo/core/session.py` | `SessionSpec.env` 透传环境变量；scrcpy stdout/stderr 落会话日志；`INFO: Texture: WxH` 事件实测可用 |
| `duo/core/chrome.py` | 编译并缓存 C# overlay；`overlay_command()` 传 title/serial/adb/home + `--display-mode` / `--video-w/h` / `--session-log` |
| `duo/resources/chrome_overlay.cs` | 9 热区、比例锁定拖拽（`ConstrainToVideo`）、外部改窗收敛（`ConvergeToVideoAspect`）、会话日志尾读、按模式顶栏按钮、捕获丢失收尾；`Repair()` 仍是系统圆角（非 G2） |
| `duo/core/paths.py` | 实际数据目录为 `~/.local/share/duo`，本轮不迁移目录 |
| `tests/` | 74 项；新增 adb 钉死、Session env、overlay 新参数、列数计算；C# 行为仍需 Windows 验证 |

2026-09-05 实施后的现状（此前列的缺陷已修复，保留给验证者核对）：

- ✅ `SyncStrips()` 800 物理像素门槛已移除，改为尺寸自适应钳制；独立底边/底角热区已补齐（共 9 条）。
- ✅ `UpdateResize()` 现在知道视频比例（mirror/fixed）：边拖锚定对侧并垂直居中，角拖锁对角、主轴驱动；最小尺寸 DIP 化并联动。
- ✅ `FakeMaximize(fit)` 改用**视频比例**、工作区居中；外部改窗（含原生最大化）稳定后 350ms 一次性收敛，未覆盖区域露出桌面（浮动画面，无黑边）。
- ✅ 失效窗口分支补 `HideStrips`；三处拖拽面均加 `MouseCaptureChanged` 收尾。
- ✅ `--adb` 非法旗标 bug 已修（改走 ADB 环境变量 + WSLENV）。
- ⏳ 待 Windows 实测：拖拽手感、收敛观感、DPI 行为、`MaybeSample()` 仍存在但未被调用（烟色玻璃现状，见 §5）。

## 2. 无黑边与 resize

### 2.1 先固定产品规则

| 模式 | 拖拽行为 | 放大行为 |
|---|---|---|
| mirror（整机镜像） | 锁定当前视频宽高比 | 工作区内最大等比窗口 |
| fixed（固定虚拟屏） | 锁定实际视频宽高比 | 同上 |
| flex（自适应应用） | 自由宽高，Android 布局跟随 | 铺满工作区 |

每种模式仅保留“最小化、放大/还原、关闭”三个顶栏动作。mirror/fixed 不提供会制造留边的强制铺满。放大窗口之外剩余的桌面空间不是黑边。

**全屏语义（2026-09-05 补充）**：等比放大铺不满屏幕时，窗口只占视频区域，未覆盖处直接露出桌面背景（浮动画面，无黑边、无空白窗口）——已由 FakeMaximize 视频比例居中 + 外部改窗收敛实现，观感待 Windows 实测。

不裁切、不拉伸视频；G2 仅裁掉窗口四角的外轮廓，不使用 cover/zoom 隐藏留边。Android 自己绘制的状态栏、信箱黑边无法通过 PC 窗口比例修复。若用户后续要求 flex 也锁比例，再单独增加选项，不在本轮扩展。

### 2.2 视频尺寸来源与最小通信

- `fixed` 可用请求的分辨率初始化；最终以 scrcpy 实际输出尺寸为准。
- `mirror` 必须取得实际视频宽高及旋转后的更新，不能用桌面窗口尺寸或 `adb wm size` 永久代替。
- ✅ **已验证（2026-09-05 实测）**：当前 scrcpy 4.1 会话日志在默认 verbosity 下输出 `INFO: Texture: WxH`，启动与每次尺寸变化（含旋转）都会重发；一次会话内 4 次翻转全部留痕。overlay 尾读线程经 `\\wsl.localhost` 路径 1.5 秒内读到首个尺寸。原始回退方案（日志不可用时降级为拖角近似）保留在案但无需启用。
- 复用现有 argv，增加 `--display-mode`、初始视频尺寸及 `--session-log` 即可；不建通用 IPC 框架。Windows overlay 接收日志路径时沿用 `wsl_to_windows_path()`。
- 当前标题查窗可能匹配同名会话；多窗口验收若复现误绑定，需用进程身份约束查找，并在 scrcpy 重启时重新绑定，不能让一个窗口控制另一个。

### 2.3 几何算法

把无副作用计算收敛为少量 C# 函数，供真实 resize 路径和测试共同调用，禁止另写一套仅供 Python 测试的算法。

- 设实际视频比例 `r = videoWidth / videoHeight`；约束对象是 **GetClientRect 对应的视频客户区**，不是含不可见边框的 `GetWindowRect`。
- 通过客户区屏幕坐标、外框、DWM 可见边界分别求出输入/输出需要的偏移；不能把 DWM 阴影内缩等同于非客户区厚度。
- 左右边拖动：宽驱动高，另一侧固定，竖直方向围绕原中心；上下边同理。拖角：固定对角，以相对变化较大的轴驱动另一轴。
- 最小尺寸用 DIP 定义并按比例联动约束，不能独立 clamp 宽高后破坏比例。极窄/极宽视频以当前工作区能容纳为先。
- 放大：扣除必要边框后 `scale = min(availableWidth / videoWidth, availableHeight / videoHeight)`，计算客户区尺寸再转换外框；在当前显示器工作区内居中或 clamp。取最近显示器应使用正确的 `MONITOR_DEFAULTTONEAREST` 常量。
- 允许整数舍入带来最多约 1 个物理像素的贴合误差；以 `abs(clientWidth - r * clientHeight)` 等尺寸误差检查，不用宽泛的百分比掩盖留边。
- 外部 `SetWindowPos`、系统最大化、Snap/FancyZones 改窗后，mirror/fixed 收敛为对应区域内的最大等比窗口；不能保证占满任意分区。这项策略必须实测，避免与外部窗口管理器持续抢尺寸。
- 保留当前定时器 + `GetCursorPos()` 的拖拽方式；位置未变化不调用 `SetWindowPos`；自发几何事件去重。外部改窗仅在稳定后纠正一次，并对纠正后的舍入容差放行。

补齐四边四角独立热区，最小窗口也要重新布局；非目标窗口遮挡时不能抢点击。`MouseCaptureChanged`、鼠标释放、目标最小化/销毁都应结束拖动并隐藏失效热区。不要靠全面 `TopMost` 掩盖层级问题。

DPI：现状只有 `SetProcessDPIAware()`，不能据此宣称支持混合 DPI。Windows 原型需验证 Per-Monitor V2 初始化、`WM_DPICHANGED`、目标窗口 DPI 读取；所有 DIP→物理像素在明确边界转换一次，不把 Qt 逻辑坐标直接传 Win32。

## 3. G2：先证明裁切，后做曲线与设置

> **状态（2026-09-05）**：像素级验证已证明 `SetWindowRgn` 可裁切 scrcpy 视频窗口，但运行时体验未达标（缩放卡顿、画面只显示一半、描边模拟的边缘质量不足），用户决策**回退为系统默认圆角，G2 转长期目标**。代码保留（`corner_mode="g2"` + `--corner-radius` 可启用），默认 `system`。后续重启此项目时以本节为起点，先解决 §3.4 列出的阻塞项。

### 3.1 不重复旧失败

- ~~Windows 的圆角 preference 不是任意半径/G2 API~~（仍真）。
- ~~`plan.md` 记录过本机 SDL 上 `SetWindowRgn` 返回成功但没有视觉效果~~：**2026-09-05 像素级重验推翻了该结论**（块级对比：裁切后角落=桌面 1.7 vs 视频 48.8；生产路径 13.7 vs 104.2，见 [验证记录](validation/window-experience.md)）。GDI 区域裁切已作为 G2 实现落地：四次超椭圆多边形，16 采样/角，随矩形变化重施加。
- 透明 overlay 角部只会露出下面仍然方形的视频；桌面截图盖角会在动态背景、重叠窗口及拖动时穿帮，本轮仍排除。

### 3.2 限定范围的 Windows 原型

1. 先记录 HWND/style、Windows / scrcpy / 渲染器版本、DPI；验证源码与打包版确实使用当前 overlay 缓存哈希。
2. 隔离测试原生 region 对真实视频窗口是否生效及是否被 SDL 后续事件重置；**仅在发现与旧实验不同的明确条件时**继续这条路线。普通 HRGN 是硬边裁切，轮廓正确也不等于抗锯齿质量达标。
3. 必须在动态内容、其他窗口重叠、拖动/缩放、最小化/恢复后仍露出真实桌面，四角不黑、不漏方形、不错误截获点击；只有静态截图不能通过。
4. 若这条最小路线失败，输出阻塞。下一候选是“拥有视频合成表面的宿主 + alpha mask”，但嵌入 HWND、DWM 缩略图或捕获后重绘均不自动解决裁切、输入、延迟及生命周期；必须单独估算并验证，不直接迁移 Qt Quick / 重写播放器。

**降级不代表交付**：原型未过时保留 `system` 圆角，设置中 `g2` 禁用；验收项保持未完成。✅ 2026-09-05 已按此条执行：默认 `system`，`g2` 为选开实验项。

### 3.3 G2 的最小数学定义

G2 = 位置连续 + 切线方向连续 + 曲率连续。直线与普通圆弧相接时曲率从 0 跳到 `1/r`，只有切向连续，不能命名为 G2。

选一个固定形状即可，不增加“平滑度”滑块：局部四次超椭圆角 `|x/a|^4 + |y/a|^4 = 1` 的一个象限，拼接到矩形直边。轴端的切线与直边一致，曲率趋于 0；四角旋转复用。这里 `a` 是圆角占用范围，不是圆弧曲率半径，UI 简称“圆角大小”。

- 用户范围：0–64 DIP，建议默认 32；实际 `a = min(configuredSize, width/2, height/2)`，0 是直角。
- 解析曲线是验证基准；渲染可用误差受控的路径近似。不要用单段普通圆弧，也不要把粗糙多边形的折线称为解析 G2。
- 测试端点、切线、曲率极限、单调性、对称性和不越界；对采样/Bezier 近似记录最大物理像素偏差（目标 ≤0.5px），检查高 DPI 抗锯齿。
- Qt 控件与 C# 层分别实现同一公式/测试向量，不为此建设跨语言绘图库。
- 视频顶层、主面板顶层与普通控件是三类不同裁切对象；Qt 绘制路径也不能自动裁掉系统非客户区。设置需说明实际作用范围；最大化/系统管理状态可用系统直角策略，不宣称所有状态强制 G2。

参考 API 文档（供执行者核对平台限制）：[DWM 窗口圆角](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/apply-rounded-corners)。本轮未完成该页面在线核验，以上可行性必须以原型证据补齐。

### 3.4 重启 G2 前必须解决的阻塞（2026-09-05 实测记录）

1. **缩放与区域风暴**：拖拽缩放期间 `SetWindowRgn` 与 SDL 重布局相互作用，出现卡顿；已尝试“缩放期摘除区域 + 300ms settle 后恢复”，仍未根治。
2. **画面裁切异常**：区域套用后曾出现“只显示一半内容”，疑似区域尺寸与窗口/客户区在收敛期间不同步。
3. **1-bit 硬边的本质限制**：区域无法真 AA，描边/软阴影是视觉模拟，实测观感“走偏”；真 AA 需宿主自拥有合成表面（宿主重写）或 DWM 补丁（系统级注入，排除）。
4. **叠加窗口成本**：四角 layered 遮罩与 9 热区条的 z 序维护复杂（曾出现移动条被顶边条盖住导致拖不动）。

结论：在不重写视频链路的前提下，运行时 G2 圆角性价比不成立；系统圆角（`DWMWCP_ROUND`）零成本满足默认观感。重启此项目需先证明阻塞 1/2 可彻底解决，且边缘质量达到 §3.3 的验收标准。

## 4. 设置页与配置契约

### 4.1 最小数据模型

新增 `duo/core/settings.py`，无 Qt 依赖，存 `data_dir()/settings.json`。保留原 `gui_prefs.json`，不在本轮迁移应用偏好或搭预设系统。

```json
{
  "version": 1,
  "scrcpy_path": "",
  "adb_path": "",
  "fps": 90,
  "bitrate_mbps": 30,
  "dpi": null,
  "corner_mode": "g2",
  "corner_size_dip": 48,
  "glass_enabled": true
}
```

`corner_mode` 支持 `system / g2 / none`；G2 原型通过前默认 system，成功并通过回归后再将新安装默认改为 g2。圆角大小只在 g2 时可编辑。玻璃布尔值是风格开关，不承诺系统不支持时仍有真实背景模糊。

- 建议校验：FPS 1–240；码率 1–200 Mbps；DPI 自动/null 或 120–640；圆角 0–96（默认 48，参照 iPhone/iPad 连续曲率比例；测试可到 160，硬边锯齿接受度待实拍评审）。它们是产品输入范围，不代表每台设备都能达到对应性能。
- 路径为空走自动探测；显式路径无效则报错，不静默退回另一个 adb。检测使用 argv + 超时 + `--version` 返回码与输出检查，禁止 `shell=True`，禁止拼接路径命令串。
- 加载缺失/损坏/字段类型错误时安全回退并给出一次简短提示；不自动覆盖损坏文件。保存先校验，再同目录临时文件 + `os.replace` 原子替换。
- 优先级：显式 CLI 参数 > 保存设置 > 内置默认。argparse 可选覆盖项不能预先填 90/30 等值，否则会覆盖用户设置；布尔值也须区分未指定与显式设置。

### 4.2 贯通路径，不只做 UI

`settings → 统一工具解析 → GUI / --check / mirror → EngineArgs / DeviceMonitor / ChromeOverlay`。

去掉 `run_app()` 的 `adb.exe` 硬编码，并把 `_pick_serial()` 的重复探测改为使用本次已解析结果。保持所有调用使用同一个 adb，包括 scrcpy 的 `adb_binary`；自定义 scrcpy 要先核对版本/能力，不能假设任何版本都支持当前参数。

设置页即使引擎缺失也能打开。路径探测在后台进行，结果回到 UI 线程；失败显示字段附近短错误。WSL 路径需区分 Python 可执行路径与传入 Windows 程序的路径，覆盖空格、中文及原生 Windows 场景。

KISS 生效策略：设置页内预览即时；保存后新会话读取。已有镜像会话时禁止应用引擎路径变更，避免旧 adb / 新 adb 并存争抢 server；无会话时停止旧轮询/后台查询后重建，或明确要求重启面板再生效。不要后台强制停止用户会话。

### 4.3 页面只分两组

- **引擎**：scrcpy、adb 路径行（文本框 / 文件选择 / 检测）；FPS、码率、DPI。
- **外观**：圆角模式、大小滑块 + 数值、玻璃开关、小预览。

底部“取消 / 保存”；顶部返回。齿轮入口加 `Ctrl+,`。不加侧边导航、选项搜索、插件系统或高级参数 JSON 编辑器。页面窄时单列滚动。

## 5. 精简液态玻璃设计约束

保留现有技术栈：QPainter/QSS + C# GDI+，不引入 WebView/React。小型颜色、间距和轮廓规范即可，不建大型主题系统。

- 中性背景 + 一种强调色，Windows 优先 Segoe UI 系列与统一线性图标。
- 控制层用轻透明底、约 1 DIP 细亮边、顶部高光、轻阴影；默认没有持续动画，hover/press 约 120–180ms。
- 玻璃在控制胶囊、设置容器等有限区域使用，视频不模糊、不加大面积装饰边框。
- 当前是烟色半透明，不是真实模糊/折射。优先验证受支持的 Windows backdrop；若 layered/自定义形状不兼容，回落高对比半透明或不透明底，不宣称实现了系统级 Liquid Glass。
- 首轮不做实时折射 shader，不恢复全窗高频 `PrintWindow` 采样；隐藏时停止视觉刷新。高对比、关闭透明效果或系统 API 失败时使用不透明底。
- 删重复“应用 / 全部应用 / 运行中”容器标题和常驻成功状态；保留必要应用辨识文本、空态及错误。图标按钮有 tooltip、accessible name、键盘焦点和至少约 32 DIP 点击区域。
- 应用网格由可用宽度计算列数，窗口 resize 时复用按钮重排，不重新拉 APK/解析图标。运行中区域可换行；隐藏名称不能让字母占位应用变得无法辨认。

## 6. 验证交付格式

下一位验证 AI 创建 `docs/validation/window-experience.md`，记录环境、commit/构建哈希、步骤、结果与截图/录像位置。没有 Windows 桌面时明确写“未验证”，不要用模拟测试替代。

| 重点 | 必测 |
|---|---|
| resize | 四边四角；小于 800px；达到最小尺寸；失去捕获；横竖屏；旋转发生在拖拽中 |
| 比例 | mirror/fixed/flex；反复放大还原；任务栏；外部改窗；视频无拉伸且无 PC 新增黑边 |
| DPI/层级 | 100/150/200%；混合 DPI 移屏；两个重叠会话；其他应用覆盖热区；负坐标显示器 |
| G2 | 0/16/32/64；动态背景；实际透明角；近看边缘质量；视频输入；最小化恢复 |
| 设置 | 文件缺失/损坏/非法类型；中文空格路径；检测超时；保存取消；新会话生效；已有会话路径修改受限 |
| 生命周期 | 断线/退出/崩溃后无残留热区；overlay 跟随正确窗口；打包后配置与路径可用 |
| 性能 | 同机同场景比较改前改后 CPU/GPU、视频流畅度及拖拽延迟；空闲无持续截图，资源无持续增长 |

自动测试建议：`python -m pytest -q`、`ruff check duo tests`；Windows 编译并测试真实 C# 几何函数，补充交互脚本。现有 CI 安装 `.[dev]`，但 GUI 测试导入 PyQt6，需检查干净环境并补 `.[dev,gui]` 安装或明确分拆 GUI 测试。历史 lint 问题应单列，不混入本轮全仓格式化。

本轮已运行：`.venv/bin/python -m pytest -q`，**74 passed**；ruff / mypy 全绿；C# overlay 经 csc.exe 现场编译通过；真实 mirror 会话冒烟验证（尺寸通道 1.5s 生效、ADB 钉死无重启循环）。未运行 Windows 交互验证（拖拽手感/收敛观感/DPI）、G2 原型或视觉性能验证。

## 7. 虚拟屏 HOME 语义（2026-09-05 真机调研）

> 用户反馈：flex 虚拟屏里出现混乱的"应用选择器"、HOME 行为诡异。本节为**纯调研**（不改产品行为），在 OPD2409 / ColorOS Android 16 (SDK 36) / scrcpy 4.1 真机上实测，全部结论来自实机 dumpsys/截图/日志，非文档转述。复现命令见 §7.5，实验后已清理设备现场。

### 7.1 根因链（全部实测）

1. **scrcpy `--new-display` 创建的虚拟屏带系统装饰标志**。`dumpsys display displays` 实测该屏 flags：`FLAG_PRESENTATION, FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS, FLAG_TRUSTED, FLAG_OWN_DISPLAY_GROUP, FLAG_ALWAYS_UNLOCKED, FLAG_DESTROY_CONTENT_ON_REMOVAL`。
2. **带 `FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS` 的副屏会自动拉起 per-display home**，解析走 `android.intent.category.SECONDARY_HOME`（非普通 CATEGORY_HOME）。本机**唯一** handler 是 `com.android.launcher/com.android.launcher3.secondarydisplay.SecondaryDisplayLauncher`（`cmd package query-activities -c android.intent.category.SECONDARY_HOME` 仅 1 条）。这是 **AOSP Launcher3 内置的副屏选择器**：实测画面 = 整屏壁纸 + 右下角一个孤零零九宫格按钮，与 ColorOS 桌面完全两个世界——这就是用户看到的"应用选择器"。它**不是**可配置的系统桌面，ColorOS 主 launcher 不参与副屏。
3. **HOME 键是全局拦截，与来源 display 无关**。在虚拟屏上注入 `input -d <id> keyevent KEYCODE_HOME`（等效 scrcpy MOD+h / 中键 / 任何"发 HOME"按钮）：焦点与全局 resumed **立即跳到 display 0 的物理桌面**（`topDisplayFocusedRootTask=Task{#1 type=home}`），而虚拟屏画面不变（前后截图仅时钟级像素差）——应用失去焦点进入 paused，用户视角即"HOME 没反应 / 应用假死"。因此**任何 display 定向的 HOME 注入都无解**（定向注入已实测同样漂移；HOME 由 PhoneWindowManager 全局截获，落到物理屏）。overlay 现行"虚拟屏上 chin 长按 = 关窗"是当前唯一正确的 HOME 替代语义，调研反而补强了它的依据（见 `chrome_overlay.cs` `ChinHold()` 注释）。
4. **`--start-app`（无 `+`）在应用已有存活 task 时不落虚拟屏**。实测：不背单词已在物理屏运行时，二次会话 `--start-app=pkg` 日志声称 `Starting app ... on display 157...`，但 task 留在原处（"delivered to running instance"），157 上 resumed 的仍是选择器。加 `+` 前缀（force-stop 后启动）后 task 稳定落在新屏并 resumed。这正是"背单词模式开第二次只见选择器"的产品级 bug。
5. **会话退出 = 虚拟屏内容销毁**（默认 `FLAG_DESTROY_CONTENT_ON_REMOVAL`；实测退出后 task 记录消失、应用进程转缓存）。`--no-vd-destroy-content` 可改为搬回物理屏（设备端"服务器"语义），本轮不动默认值。
6. 附带实测：从虚拟屏会话中 `am start --display <id>` 可**跨屏搬移已存在的 task**（QQ 从 display 0 搬到 158 并 resumed）；虚拟屏销毁后 task 回落到 display 0。

### 7.2 逐项结论（对照调研提纲）

| 方向 | 结论 |
|---|---|
| ① Android 虚拟屏 launcher 行为（Android 16） | 副屏 home = `CATEGORY_SECONDARY_HOME` 唯一 handler（AOSP SecondaryDisplayLauncher），非 OEM 桌面；HOME 全局落 display 0；副屏 task 随屏销毁。 |
| ② scrcpy 4.1 旗标 | `--new-display[=WxH/DPI]`、`--flex-display`（无尺寸时默认 1280x960/160）；`--start-app=+pkg/?名`（`+`=先 force-stop）；`--no-vd-system-decorations`（关掉系统装饰 → **选择器永不出现**，无应用时整屏无帧）；`--no-vd-destroy-content`（退出改搬回物理屏）；`--display-ime-policy=local`（输入法落在虚拟屏，默认 fallback=物理屏）；无"运行时切应用"控制消息。 |
| ③ adb 控制面 | `am start --display <id>` 可启动/搬移应用到虚拟屏 ✅；`input keyevent -d <id>` 对 HOME 无效（全局拦截，见 §7.1.3）❌；`cmd package set-home-activity` 改的是**全局默认 launcher**（会动用户物理桌面，禁用）❌；`settings global` 无 per-display home 键 ❌。→ **adb 侧无法给虚拟屏配独立 home**。 |
| ④ Duo KISS 方案 | 见 §7.3。 |

### 7.3 推荐方案（含成本/风险）

| # | 方案 | 成本 | 风险 |
|---|---|---|---|
| R1 | **引擎两行**：flex/fixed 会话默认 `--no-vd-system-decorations`；`--start-app` 一律带 `+` 前缀。选择器从根上消失（无装饰 → 系统不建 home task，实测 159 号屏无 type=home task、应用直达） | `engine.py` 两处小改 + argv 断言测试更新 | 无应用会话**整屏无帧**（官方文档明示），Texture 尺寸通道静默、窗口黑屏——空 flex 体验需产品确认（见 TODO 任务 7）；`+` 会 force-stop 应用（丢运行态，对背单词类无害）；OEM 差异需一台非 ColorOS 设备抽查 |
| R2 | **HOME 永不注入虚拟屏**：chin ○ 长按 = 关窗（现状保持）；GUI/快捷键上的 HOME 按钮在 flex/fixed 下映射为"回 Duo 面板"而非发键 | 面板路由小改；tooltip 说明 | scrcpy 内置 MOD+h / 中键无法逐键禁用（仅 `--shortcut-mod` 换修饰键），窗口聚焦时按下仍会触发"焦点漂移到物理屏"——画面不变但应用 paused；影响限于 scrcpy 原生快捷键，记录为已知限制 |
| R3 | **会话内切应用走 adb**：会话日志已输出 `[server] INFO: New display: 1200x1600/280 (id=N)`（stderr → SessionSpec 日志，尾读通道现成），解析出 display id 后 `am start --display N -n <pkg>/<activity>`（`cmd package resolve-activity` 预解析；可搬已运行应用，无需重建会话） | Python 侧正则一行 + spawn adb 一处 | scrcpy 4.1 无运行时切应用消息，adb 通道是唯一正道；需处理 display id 未知（日志缺失）时降级为"提示重建会话" |
| R4 | **中文输入（待实测）**：uhid 键盘下设备输入法候选窗默认落在物理屏（`--display-ime-policy` 默认 fallback）；若 Windows 实测复现乱象，加 `--display-ime-policy=local` | 一个旗标 | 需真机验证；OEM 输入法对 `local` 的支持未证 |

### 7.4 明确不做

- `cmd package set-home-activity`：改全局默认 launcher，会接管用户物理桌面。
- 给虚拟屏装第三方 `SECONDARY_HOME` launcher（Fossify Home 等）：多装一个包、风格割裂，且 R1 已让 home task 不再创建；未来若要"虚拟屏完整 Android 桌面"再评估。
- 任何"display 定向 HOME"：已证伪（§7.1.3），不是实现问题而是系统语义。

### 7.5 复现命令（供验证者）

```bash
# 无窗口保活地起一个虚拟屏（--no-window 会连视频一起关，必须用 --record 保住管线）
scrcpy -s <serial> --new-display=1200x1600/280 --no-window --no-audio \
  --record=exp.mp4 --video-bit-rate=1M --max-fps=5
adb shell dumpsys display displays | grep -E 'Display id|FLAG_'   # flags / id
adb shell cmd package query-activities -a android.intent.action.MAIN \
  -c android.intent.category.SECONDARY_HOME --brief               # 副屏 home 候选
adb shell input -d <id> keyevent KEYCODE_HOME                     # HOME 漂移复现
adb shell "dumpsys activity activities | grep topDisplayFocusedRootTask"
adb shell am start --display <id> -n <pkg>/<activity>             # 应用落屏/搬屏
adb shell screencap -d $(dumpsys SurfaceFlinger --display-id | awk '/Virtual/{print $2}') /sdcard/x.png
```

注意：`screencap -d <逻辑id>` 在该 ColorOS 上报 "not valid"，需用 SurfaceFlinger 输出的虚拟屏 id；scrcpy 会话日志的 `New display: ... (id=N)` 行是 R3 的 display id 来源。

### 7.6 实现状态（2026-09-06，对齐 TODO 任务 7）

已落地（未做 Windows 实测的项目见下，不宣称完成）：

- **R1 引擎两行**：`DisplaySpec.to_flags()` 对 flex/fixed 一律输出 `--no-vd-system-decorations`（mirror 不加）；`EngineArgs.to_argv()` 的 `--start-app` 值一律带 `+` 前缀（幂等，已带 `+` 不双加）。测试断言拼装后的 argv 列表，非源码关键字。已知代价（R1 风险列）：空 flex 会话整屏无帧，待 Windows 实测决定是否保留默认关闭（TODO 任务 7 实测项）。
- **R3 直达应用**：CLI `mirror` 新增 `--session-log`（面板托管的会话传固定路径 `logs/panel-<pkg>.log`，CLI 自启仍用时间戳文件名）；`duo.core.session` 提供 `parse_display_id()` / `display_id_from_log()`（点击时尾读日志，无后台线程）；面板 `PanelController.startAppOnDisplay(package)` 走 `cmd package resolve-activity --brief` 预解析组件 → `am start --display N -n <cmp>`（adb 工作线程 + hop 信号回状态栏）。会话已存活时再点应用磁贴不再提示"已在运行"而是直达虚拟屏（不重建会话）。降级路径（全部只是状态栏提示，不重建）：会话未运行 / 无日志或无 `New display:` 行（虚拟屏未就绪）/ resolve 失败 / `am start` 报 Error。每次启动会话先截断 panel 日志，避免把上一次运行的旧 display id 发给已销毁的屏。
- **R2 HOME 语义（零 C# 改动，`chrome_overlay.cs` 未动）**：虚拟屏上永不发 keyevent 3——chin 长按 = 关窗的现状保持（overlay 按 display-mode 门控，已实现）；"HOME = 回 Duo 面板"由面板侧实现：运行中芯片的标签可点（tooltip 注明"在虚拟屏中打开应用（HOME = 回 Duo 面板）"），语义是把应用拉回虚拟屏/回面板，而非发 HOME 键。**已知限制**：scrcpy 内置 MOD+h / 中键仍会触发 HOME（全局拦截，焦点漂移到物理屏、画面不变），无法逐键禁用，记录为限制而非缺陷。QML 面板无独立 HOME 按钮，无需映射。

待 Windows 实测（TODO 任务 7 保留项）：空 flex 会话无帧时 Texture 通道静默的窗口/overlay 降级体验；uhid 键盘下中文输入候选窗是否落物理屏（决定是否加 `--display-ime-policy=local`）。
