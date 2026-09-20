# 窗口体验

> 活规范：当前窗口/会话语义与设计依据。代码是实现的事实来源；本文只记
> 录**当前语义**与**为什么**。实验记录与被取代方案（原 §2/§3/§11/
> §13 部分/§14）已存档 `docs/history/window-overlay-experiments.md`，
> 原编号保留便于引用；§11 尾部「胶囊右键固定」契约已并入 §10。

## 1. 当前窗口行为

| 模式 | 窗口 | 缩放 |
|---|---|---|
| 整机镜像 | 跟随设备画面，scrcpy 自管 | 等比锁定（`ConvergeToVideoAspect`：外部改窗 350ms 后收敛；**启动/换尺寸后 200ms 一次性贴比（绕过节流）**，见下） |
| 固定虚拟屏（竖屏等） | 同上 | 同上 |
| 应用会话（flex） | **纯 Windows 窗口**：拖哪是哪；缩放异步下发（SWP_ASYNCWINDOWPOS，不阻塞于目标窗口重排）；`window_aspect=locked`（设置项，2026-09-06 定稿）可改为约束在内容比例内（像视频播放器） | 自由缩放（默认）或比例锁定（设置）；虚拟屏恒定 2560×1440，方向由 APP 自主请求，APP 转屏时 scrcpy 原生把窗口贴合新内容（无黑边）；我们零干预 |

**启动贴比（2026-09-17）**：镜像/固定窗初开时曾永久 letterbox（黑边）——
旧路径只在「外部矩形变化 + 稳定 350ms」后收敛，而启动后矩形不再变化，
收敛永不触发，只能手动拖一下才贴合。现在 `HandleLogLine` 每收到新
`Texture:` 尺寸就武装 `_fitAt`（死线 +200ms，贴比走独立通道绕过
常规收敛的 500ms 节流），UI 泵 `FitWhenArmed` 到点做一次
`ConvergeToVideoAspect(wr, bypassThrottle: true)`：在视频首帧尚未
看清时完成变形，避免可见闪跳（2026-09-18 真机反馈优化）。
镜像首尺寸（无 argv 种子）与固定屏被设备改写尺寸都覆盖；比例已吻合时
容差内空操作不跳动；拖拽/移动/伪最大化期间武装作废（用户在驾驭）。
flex 不变：永不自改窗形。

**应用会话三层防御**（2026-09-06 定稿）：

1. 16:9 初始预设 `--new-display=1920x1080/240`（竖屏 1080x1920/270）+
   `--flex-display` 持续跟随：显示尺寸只由窗口决定；旋转请求被 overlay
   一次性 `wm set-ignore-orientation-request -d <id> 1` 忽略 → 风暴物理不可能，
   APP 自己适配或自挔黑边（原生平板语义）。
2. `--no-window-aspect-ratio-lock`：scrcpy 不再把窗口锁到视频比例；仅 `window_aspect=free`（默认）时传，locked 时不传（scrcpy 原生锁比例，永不黑边）。
3. overlay 钉扎（`EnforceFlexPin`）：仅【旋转级 Texture】（视频比例 ≠ 客户区比例，
   2026-09-09 起 HandleLogLine 判定武装）后 2.5s 内弹回用户矩形——那是 scrcpy
   转屏自动改窗；其余外部改窗（Win+左右/上下 snap、Win+Shift+方向键、
   PowerToys FancyZones、第三方窗口管理器）一律收编为新钉扎（snap 停得住，
   修复“窗口过去又闪回”）。豁免=拖拽中（`_moving/_resizing`/左键按住）；
   收编=松手 1.5s 内的新矩形。

其余语义：顶边 6px 缩放带 + 中央 1/2×24DIP 移动带（左右 1/4 穿透）；
灵动岛方向消歧（水平拖=移动 / 垂直滑=拉通知栏 / 点按=穿透）；下巴 ○ 单击=返回，
长按=镜像 keyevent 3 / 虚拟屏 `am start --display N -c HOME`；G2 圆角已回退为系统默认。


## §10 窗口栏三态与沉浸栏稳定性（2026-09-09）

> chrome_overlay.cs 相关决策的论述文档；代码只留单行指路注释。

### 三态窗口栏 immersive | native | none

- `none` = 该边永不建可见栏：上巴 none = 胶囊不露出（隐形拖动面全保留
  ——侧带、caption 带、edge strips，窗口必须始终可拖可改）；下巴
  none = 下巴永不露出（底部 resize strips 隐形保留）。
- 默认：上巴沉浸、下巴 none。理由：scrcpy 右键在镜像窗上已是返回，
  下巴对多数会话冗余。设置页两字段 = 默认值；右键菜单「窗口栏 ▸」
  按应用覆盖（写 gui_prefs.json bars 节）。
- argv 链：`--chrome-top/--chrome-bottom`（CLI choices 与 settings 枚举
  同源）；C# 侧 `NormalizeBarMode` 三成员归一，未知值回退 immersive。

### 沉浸式上下巴不可见 = z 序插入方向反了（2026-09-09 真机定稿）

用户报告“沉浸式上下巴没有相关的内容、功能不生效”（上巴系统+下巴沉浸
同样“见不到内容”）。真机枚举 z 序：视频窗 rank=31、胶囊 rank=54——
**整层 chrome 被视频窗盖在下面**；直接调 API 复现语义：
`SetWindowPos(form, video, ...)` 使 form 从 rank 33 → 59（降到视频窗
下方）。

根因：`SetWindowPos` 的 `hWndInsertAfter` 语义是“**该窗口位于被定位
窗口之上**”——旧 `InsertAbove` 传视频窗句柄作插入点，实际把每个
overlay 面插到了视频窗**下方**。修复：插入点取视频窗上面那个窗
（`GetWindow(video, GW_HWNDPREV)`），被定位窗口就落在它与视频窗之间
= 紧贴视频之上；视频窗已在带顶时 GW_HWNDPREV 返回 NULL（= HWND_TOP）
仍正确。修复后实测：胶囊 rank 38 < video 62、下巴 rank 33 < video 57，
像素图可见三键图标与下巴按钮。

教训：几何/可见性断言（`vis=True`、矩形正确）证明不了“看得见”——
叠加层必须断言 z 序相对位置（rank 比较），真机像素/结构双证据。

### 上巴 native = scrcpy 带框窗（2026-09-09 真机三轮定稿）

**不再**给无边框窗事后补 `WS_CAPTION`：SDL 对 `--window-borderless`
建的窗自己接管 `WM_NCCALCSIZE` 并答“客户区=整窗”，补上的 caption 样
式永远占不到标题带（真机表现：上巴系统“见不到相关的内容”；WinForms
测试宿主走 DefWindowProc，复现不出）。跨进程子类化拦截 `WM_NCCALCSIZE`
已被 Windows 从 Vista 起禁用（真机实测 `SetWindowLongPtr(GWL_WNDPROC)`
= ERROR_ACCESS_DENIED）。

定稿方案：上巴 native 时 **duo 不传 `--window-borderless`**
（`duo.core.chrome.borderless_for`，CLI 接线在 `__main__`）——scrcpy
自己建带框窗口，系统标题栏从一开始就在：标题文字、─□✕、双击最大化、
标题拖动、Win 吸附、DWM 圆角全部原生（真机实测 frameH=57 /
SM_CYCAPTION=34）。overlay 仍跑（下巴 + 第 4 键 + mica）；`Repair()`
只在窗口带着不完整 caption 家族时防御性补齐，已完整则一律不动（避免
FRAMECHANGED 闪帧）。沉浸/无 上巴维持无边框 + 胶囊。

### 沉浸栏"闪现即隐"修复（真机日志锁定）

现象：不背单词（flex，虚拟屏 1920×1080 旋转 1248×1340）上下巴 hover
闪现 61–126ms 即隐。

根因：露出/保持带原是**半平面**（下巴触发无下界、上巴无上界）——箭头
热点骑在窗口边线外 1–2px 也触发；而窗外光标在 engaged 判定中无人搭救
（全部搭救矩形在窗内，纯 hover 不换前台）→ 下一拍 HideBars 连带全隐。

修复：两条可见性判定的带钳进窗口矩形（上巴 `[wr.Top, client.Top+band)`、
下巴 `(band, wr.Bottom]`；钳到 wr 而非 client：WS_THICKFRAME 幻影边也算
窗内）。窗外不触发；窗内触发后触发带 ⊂ 下巴自身矩形，overBars 必然
保住 engaged。


### 胶囊右键固定（按 APP 持久化）

`Controller._topPinned`（`ToggleTopPin`，胶囊任意处右键切换）：

- 固定时 `showTop` 恒真（engaged 期间常驻露出；采样心跳照跑，
  SampleTop ~300ms 零额外成本）；
- 固定态视觉：胶囊白亮边 90→160 加深，其余不动（无新增动效）；
- 常驻语义（2026-09-10 用户拍板）：engaged 判定含 `_topPinned`——
  失活（切窗口）不再隐藏；最小化/关闭仍隐藏；被其他窗口盖住时三明治
  z 序天然遮挡，不是 always-on-top；
- native 顶（真系统标题栏恒在）与 none 顶不适用；右键落在胶囊上
  不会穿透到 scrcpy（不触发安卓返回）。

**按应用记忆**（用户拍板：固定按 app 生效）：argv 链
`--pin-top 0|1`（初值）+ `--pin-file <win 路径>`；overlay 每次切换回写
该文件（`"1"`/`"0"`，写失败仅记日志、会话内固定不受影响）；文件位于
数据目录 `overlay-pin/<pkg>.flag`（duo.core.chrome.top_pin_path /
read_top_pin，包名天然文件名安全，异常字符防御性替换）。CLI 在
`--app` 存在时接线（__main__），整机镜像无包名不传文件——固定随
会话生灭。

日志：`top pin on` / `top pin off`；启动横幅带 `pin=`。

### 顺带修复：右键曾与左键同效触发胶囊字形键（2026-09-09 真机反馈）

用户真机：右键胶囊未固定，反而“像左键一样执行了确认动作”。根因：
WinForms `MouseClick` 对右键同样触发，而共享 `WireInput` 的触键路径没有
左键守卫——字形圆占胶囊宽度约八成，右键几乎必然命中 ─/⤢/✕ 并与左键
同效触发（最小化/比例最大化/直接关窗）；固定切换实际也发生了，但
hairline 加深太细微被掩盖，命中 ✕ 则直接关窗全不可见。修复：

- 触键路径一律左键专属（共享 `OverlayWindow.WireInput` 与下巴
  `ChinWindow.WireInput` 的 `MouseClick` 加 `Left` 守卫；下巴右键不再
  发 BACK 键）；
- 胶囊右键只做固定切换，切换后立即 `Render()`（hairline 即时生效，
  不等下一个 ~300ms 采样 tick）；
- native 顶第四键不接线固定语义：右键无操作，也不静默翻转并回写
  pin 文件（那会污染同应用下次 immersive 启动的初值）。


## §12 进程生命周期与单实例（2026-09-10）

面板是唯一主进程，其余全是它的进程树（会话 CLI `Duo.exe mirror` 是孩子，
scrcpy 与 C# overlay 是孙子）；面板前台窗口一关，整棵树必须消失。

**为什么需要显式设计**（2026-09-10 真机事故）：Windows 上 `Popen.terminate`
是裸 TerminateProcess——会话 CLI 的 SIGTERM 清理器从不执行；且 `shutdown()`
旧实现只停轮询器不碰会话。结果面板关了一夜，任务管理器里残留两组
scrcpy.exe + 会话 Duo.exe，还握着 panel 日志句柄（次日 WinError 32 崩溃的
诱因之一）。

**契约**（实现：`duo/core/winproc.py`，面板侧接线：`PanelController`）：

- 停单个会话（`stopSession`、音频静音重启）：`terminate_tree`——Windows 走
  `taskkill /T /F`（scrcpy/overlay 随树而死；设备端 scrcpy server 感知
  socket 断开自毁虚拟屏，`--turn-screen-off` 也由 server 侧恢复），POSIX
  走优雅 `terminate`。
- 面板退出（`shutdown`）：树杀全部存活会话后关闭 Job Object。
- 崩溃兜底：每个会话进程挂进 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的
  Job Object；面板无论怎么死（正常退出/崩溃/任务管理器强杀），内核关掉
  job 句柄时把树里剩余进程一并拖走。adb server 不入 job（它是共享资源，
  由面板自己的首个 adb 客户端在 job 外拉起）。
- 单实例：QLockFile 锁在 `duo.ui.app.run_app`（所有 GUI 入口共用：冻结
  exe 双击、`duo --gui`、`python -m duo --gui`）；第二实例弹原生
  MessageBox 后以 85 退出。会话 CLI 不经过 `run_app`，不受锁约束——它们
  是面板的孩子，不是竞争面板。多面板互抢虚拟显示的历史事故（2026-09-06
  「全部失效」）见 gui_entry 旧注释存档。
- **二次点击 = 激活田实例（2026-09-20，rustduo）**：开始菜单/桌面再点
  Duo 时不再弹「已在运行」死胡同框（旧行为产生两个同名任务栏项，用户
  视角即「开始菜单的 Duo 无法使用」）；改为枚举窗口按进程名（duo.exe，
  大小写不敏感）找到首个实例主窗口，最小化则 SW_RESTORE、再
  SetForegroundWindow，随后静默退出。找不到可激活窗口（僵尸进程）才
  回退提示框并附任务管理器指引。实现：`duo-panel/src/winproc.rs`
  `activate_existing`。


## §13 玻璃材质总开关（--glass 0|1 + --bar-theme）

> 下巴 native 材质已迁移「窗外系统条 + Mica，沉浸下巴玻璃胶囊」，几何/
> 窗口形态/玻璃通道见 `docs/ui/chin-island-acrylic.md` §6；历史 frost
> 参数与迭代存 `docs/history/window-overlay-experiments.md` §13。

### 玻璃材质总开关（--glass 0|1 + --bar-theme）

设置页「外观 ▸ 玻璃材质」总开关（settings `glass_enabled`，2026-09-12
实装），上巴胶囊/下巴 native/面板右键菜单三表面统一；argv 链：
controller `_pin_glass`（fresh-read）→ 会话 CLI → `overlay_command` →
C#。关玻璃的普通材质（Opus 配方）：

| 表面 | 亮态 | 暗态 |
|---|---|---|
| 上巴胶囊/下巴填充 | **#F3F3F3** 不透明 | **#202020** 不透明（对齐 Win11 SolidBackgroundFillColorBase） |
| 常驻 hairline | 1px rgba(0,0,0,0.08) | 1px rgba(255,255,255,0.10)（不透明面无 frost 对比度，描边补分离） |
| 字形/药丸 | 墨 #1D1D1F / 药丸 rgba(29,29,31,0.60) | 白 #F5F5F7 / 药丸 rgba(255,255,255,0.75)（不透明度略提补偿） |
| 自适应 | **无**（不透明色不随底层内容变；顶光渐变同废） | 态随面板主题 |

- 主题来源：`--bar-theme light|dark|system`（面板 settings `theme`
  fresh-read 注入）；`system` 经注册表 `AppsUseLightTheme` 解析（缓存一次）。
- 采样门控：`!Glass` 时 `SampleTop`/`SampleNativeChin` 直接返回（不采样
  不模糊，零开销）。
- 固定态 rim 与关闭键红 hover 在普通材质下保留（色源换
  `Ctrl.BarThemeDark`）。
- QML 菜单侧开关见 glass-recipe.md §6（`Style.menuGlass` 双闸门）。


## §15 方案收敛与生产基线（2026-09-17）

### 1. 架构收敛：回归并固化 C# Overlay 三明治方案

经过工程实测与权衡：
- **SetParent 宿主嵌入归档**：跨进程 `SetParent` 将 SDL 渲染窗转为子窗口虽能省去跟随同步，但带来了多 DPI 混合拉伸、SDL 输入队列抢占、WinForms/Win32 消息循环偶发死锁等复杂边界条件。该方案正式归档为实验性探索。
- **C# Overlay 生产基线**：继续全面采用成熟稳定的三明治架构（`chrome_overlay.cs` + `chrome.rs`）。通过现场 C# 编译运行无边框透明交互顶栏与毛玻璃下巴，兼备高帧率渲染自由与沉浸式窗口质感。
- **CLI 参数统一**：`duo-core mirror` 与 Python CLI 统一使用 `--chrome`，移除未成熟的 `--embed` 实验参数，保证双端逻辑与测试合同 100% 对齐。

### 2. 全局静默无黑窗（CREATE_NO_WINDOW）

针对 Windows 平台下后台调用进程时偶发的 cmd/conhost 控制台黑窗闪烁问题，Duo 原生层统一引入 `quiet_command` 规范：
- Windows 侧创建全部子进程（`adb.exe`、`scrcpy.exe`、`csc.exe`、`DuoChromeOverlay.exe`）时显式附加 `CREATE_NO_WINDOW = 0x08000000` 标志。
- 后台轮询与进程托管全面静默化，彻底消除用户界面弹窗抖动。

### 3. Flex 虚拟显示屏方向锁定

在自由窗口模式（flex）下，安卓系统响应传感器或应用请求可能会自动旋转虚拟屏，导致桌面端窗口与视频比例冲突。
- 一旦解析到虚拟显示屏 ID，立即执行 `wm set-ignore-orientation-request -d <id> 1`，将虚拟屏方向与旋转响应严格锁定在桌面端窗口决定的横/竖规格下。



## §16 固定比例的平行视窗与密度保障（2026-09-19）

> 用户报告：酷安在「自适应窗口」能打开平行视窗，「固定比例」不能。
> 本节记录真机标定过程与最终规则；实现在 duo-core `mirror.rs`
> `plan_display` 的 `parallel_view_dpi_cap`。

### 1. 机理（真机 TESTPAD / ColorOS 16 逐条 dumpsys 标定）

- scrcpy flex 与固定建屏参数完全一致（同一 `createNewVirtualDisplay`
  调用与 flags）；唯一差异是 flex 会随窗口 `resize()`。**resize 事件
  不是平行视窗开关**（3392×2294 固定屏、零 resize 同样触发）。
- 竖屏锁定应用（酷安）在横屏虚拟屏上被系统**信箱化**为「短边 × 9:16」
  的居中竖条；应用看到的是该竖条的 dp 配置。
- 酷安的平行视窗 = 应用自建 TaskFragment adjacent 嵌入
  （`organizerProc=com.coolapk.market`，sz=2），在首次导航时激活，
  触发阈值是字框 `screenWidthDp > 900`：

| 显示 | 密度 | 字框 sw | 形态 |
|---|---|---|---|
| 1920×1080（flex 小窗） | 160 | 599dp | 手机 |
| 2560×1440（固定 16:9） | 160 | 801dp | 手机 |
| 2560×1440（固定） | 150 | ~864dp | 手机 |
| 2560×1440（固定） | 144 | ~900dp | 手机（900 不满足 >900） |
| **2560×1440（固定）** | **140** | **917dp** | **平行视窗 ON** |
| 3392×2294（固定/flex 皆然） | 160 | 1541dp | 平行视窗 ON |

- 「跟随设备」密度（本机 356）下字框仅 ~364dp，固定与 flex（4K 全屏
  上限 ~580dp）都永远到不了阈值——用户印象里「flex 可以」来自全局
  密度 160 时期的大窗口。

### 2. 规则（mirror.rs）

固定横屏（`--display fixed` 且 w≥h）且无显式 `--dpi`、全局密度为出厂
默认 160 或「跟随设备」时，密度自动取
`round(短边×9/16×160/925)`（1440 短边 → **140**，字框 ~917dp，阈值上
留 17dp 余量）；上限不超过出厂默认、下限 80。自定义全局密度与按应用
DPI 钉扎（面板右键 → DPI）不被改写。竖屏固定屏不介入（竖屏应用原生
填满，无信箱化）。

**窗口补偿（同日追加，用户反馈「字太小」）**：降密度使同屏 dp 数变大、
屏上文字物理尺寸缩水（140 档 = ×0.875）。补偿 = 固定窗按
`出厂 160 / 实际密度` 放大（140 档 ×8/7 → 2926×1646），屏上 px/dp
回到 160 桌面密度时代的 1.0；钳进工作区（放不下退回工作区适配）。
补偿基准恒为 160——「跟随设备」的探测值（如 356）不作基准，否则
补偿被推到 fit 上限变全屏窗（真机撞出后修正）。display 几何与
Android 侧配置不受窗口放大影响（平行视窗不回退，已复验）。

### 3. 顺带修复

`build_engine_args` 曾直接取 `args.serial.unwrap_or_default()`：CLI
省略 `--serial` 时传出空 `--serial=`，scrcpy 报「Could not find ADB
device :」拒连（面板路径因恒传 serial 未暴露）。现改用驱动层已解析的
在线 serial。

### 4. 验收（2026-09-19 生产路径）

`duo-core.exe mirror --app com.coolapk.market --display fixed --width
2560 --height 1440`（设置 dpi=null 跟随设备）→ diag `display: fixed
dpi=140`，酷安收 `sw917dp`，首导航即出现 coolapk 自组织 AdjacentSet
（平行视窗激活）。
