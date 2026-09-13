# UI 地图与修改指引（交接文档）

> 读者：接手 Duo UI 改动的 agent。本文是**地图 + 入口 + 合同**：每个 UI
> 部件在哪个文件、改哪一步要动哪里、什么不许破。验收标准在
> [DESIGN.md](DESIGN.md)（十条铁律，任何视觉决定先对照它）；本文不重复
> 规范，只补齐"代码事实"。冲突时以 DESIGN.md 为准。

## 0. 先消歧：两个"顶栏"

| 名字 | 是什么 | 代码 |
|---|---|---|
| **面板顶栏胶囊** | Duo 主面板窗口内部的「首页 / 设置」分段导航（通栏亚克力胶囊） | `duo/ui/qml/Main.qml` `topCapsule` |
| **会话窗右上角上巴** | scrcpy 会话/镜像窗口右上角悬浮的窗口控制胶囊（最小化 / 比例适配 / 铺满 / 关闭），hover 顶部边缘露出 | `duo/resources/chrome_overlay.cs` `TopWindow` |

改 UI 前先确认目标是哪一个——两者技术栈完全不同（QML vs WinForms 分层窗）。

## 1. 代码地图

### 面板（PyQt6 + QML）

| 文件 | 职责 |
|---|---|
| `duo/ui/app.py` | `run_app()` 引导：建 QGuiApplication、解析 adb、把 `ctrl` / `settingsApi` / `shadersUsable` 三个上下文属性注给 QML 引擎、加载 `Main.qml`。`SettingsApi` 是设置页后端（load/save/probe/loadProblems） |
| `duo/ui/controller.py` | `PanelController`：QML 的唯一数据源（`ctrl`）。设备/应用/会话/偏好全部经它；图标后台拉取、批量通知契约也在这 |
| `duo/ui/qml/Main.qml` | 主面板全部 QML：布局、顶栏胶囊、四枚菜单浮层、可复用组件库（`component` 定义在文件尾部） |
| `duo/ui/qml/SettingsPage.qml` | 设置页（StackView push），经 `settingsApi` 读写 |
| `duo/ui/qml/Style.qml` | 视觉令牌单例（亮/暗双值，`dark` 绑定 `ctrl.effectiveDark`；改色只改这里 + DESIGN.md §2 同步） |
| `duo/ui/qml/qmldir` | 注册 `Style` singleton（删文件必炸 import，勿动） |

### 会话窗 chrome（C# overlay，Windows 专用）

| 文件 | 职责 |
|---|---|
| `duo/resources/chrome_overlay.cs` | 单文件 C# 程序：`TopWindow`（上巴）、`ChinWindow`（下巴）、`SideBandWindow`（侧移动带）、`EdgeStrip`（缩放带/移动带）、`CornerMask`（圆角）、`Controller`（总调度）。**必须保持 C# 5 兼容**（legacy `csc.exe` 编译） |
| `duo/core/chrome.py` | Python 侧：首次使用时用 .NET Framework `csc.exe` 编译 .cs（约 0.2s），产物缓存于数据目录并带 **sha256 内容戳**——改 .cs 后下次会话自动重编，无需手动清理。`borderless_for(top_mode)` 决定是否给 scrcpy 传 `--window-borderless`；`overlay_command()` 组装 overlay argv |

### 图标

| 文件 | 职责 |
|---|---|
| `duo/core/catalog.py` | `APP_CATALOG`：27 个常用应用预设表 `AppPreset(label, package, color, glyph, glyph_ink)`。目录同时解决 ROM 预装应用（QQ 等）不在 `pm list packages -3` 的问题 |
| `duo/core/icon_presets.py` | 预设图标 SVG 模板：品牌色微渐变 squircle + 单字。缓存在 `data_dir()/presets/<pkg>.v2.svg` |
| `duo/core/apps.py` | 真实图标提取：`pm path` 拉 APK → aapt2 badging → adaptive fg/bg 合成 → squircle 蒙版（23%）→ 缓存 `.r2.png`；`apply_rounded_mask` / `_compose_adaptive` / `_paste_foreground` |
| `assets/duo.ico` | **应用自身图标（占位，蓝底圆环）**。正式图标到位后同名替换 + 重打包即可，`duo.spec` 无需改 |

### 工具与测试

| 文件 | 职责 |
|---|---|
| `scripts/qml_shots.py` | offscreen + software 渲染出图：`qml-main.png` / `qml-settings.png` / `qml-main-dark.png` / `qml-settings-dark.png`（暗色走 applyTheme 生产路径）到 `docs/validation/assets/`。DESIGN.md 铁律 10 的出图验收 |
| `tests/test_qml_app.py` | 引擎加载、设置往返、controller 绑定 |
| `tests/test_settings_qml.py` | SettingsPage.qml 结构断言（按 objectName） |
| `tests/test_icon_presets.py` | 预设 SVG 内容/缓存断言 |
| `tests/test_icons.py` | 提取/蒙版/合成 |
| `tests/test_chrome.py` | overlay 源码指纹与加固断言（`test_overlay_source_shipped_and_hardened`） |
| `tests/test_controller.py` / `test_gui_launch.py` | controller 状态机 / GUI 启动 |

### 相关文档

- [DESIGN.md](DESIGN.md)——验收标准（铁律、令牌、组件规范、性能预算）
- [glass-recipe.md](glass-recipe.md)——菜单毛玻璃算法与配方（MenuGlassPlate 唯一论述）
- [../window-experience.md](../window-experience.md)——会话窗行为规范；§10 窗口栏三态与 z 序定稿（**改上巴前必读**）
- [RESEARCH-ICONS.md](RESEARCH-ICONS.md)——图标获取/统一化调研与法律边界（**改图标前必读**）
- [RESEARCH.md](RESEARCH.md)——UI 优化起点的事实底账
- `mockups/`——HTML 方案稿（用户预览用，非实现）

## 2. 主面板（Main.qml）结构地图

### 2.1 层次（自外向内）

```
ApplicationWindow (root)
└─ zoomLayer                 // 舒适缩放层：DPR=1.0 屏整层 1.25×，见 §6 DPI
   ├─ canvasRoot (objectName) // layer.enabled 毛玻璃快照源；子树 = 画布 + 页面
   │  ├─ bgLayer             // Style.bg 底 + 6 个装饰色斑 Rectangle（同心三层×2）
   │  └─ stack (StackView)   // panelComp ⇄ SettingsPage
   ├─ topCapsule             // 面板顶栏胶囊（两页常驻，悬于 StackView 之上）
   ├─ ctxScrim               // 菜单打开时的点击拦截层（z 90）
   ├─ ctxMenu (appContextMenu, z 100)   // 磁贴/固定卡右键菜单
   ├─ aspectSub (z 110)      // 「固定比例 ▸」二级菜单
   ├─ barSub (z 110)         // 「窗口栏 ▸」二级菜单（上巴/下巴按应用设置）
   └─ mirrorMenu (z 100)     // 镜像卡右键菜单
```

菜单浮层挂 `zoomLayer`（在 `canvasRoot` 子树**外**）：毛玻璃快照源是
canvasRoot，菜单在其子树内会产生自采样环。此结构不许改，配方见
glass-recipe.md。

### 2.2 页面内容顺序（panelComp 内，DESIGN.md §3）

1. `deviceCard` 设备卡（y 64，高 76）：状态点 + 状态文字 + serial，纯展示
2. `pinnedCard` 固定卡（高 68）：44px 小图标 Flow，无置顶时不占位
3. `mirrorCard` 镜像卡（高 64）：标题 + `mediaVolumeSlider` + `mirrorButton`
   （唯一强调色按钮）；右键卡本体弹 `mirrorMenu`
4. `searchCapsule` 搜索（高 36）：放大镜 SVG + `searchField` + `searchClearButton`
5. `appsGrid` 应用网格（92×102 格，列数随宽自适应，AppTile 委托）
6. `runningCard` 运行卡（bottom 56）：`sessionChip` Flow
7. `statusToast` Toast（bottom 16，2.5s 淡出）

### 2.3 可复用组件（Main.qml 尾部 `component` 定义）

| 组件 | 用途 | 关键合同 |
|---|---|---|
| `Dot` | ≤8px 圆点 | 双 Rectangle 同心叠（小尺寸禁 border，GL 真机会错） |
| `CapsuleSegment` | 顶栏分段 | 选中 = 不透明纯白 `segmentFill` + DemiBold |
| `AppGlyph` | 应用图标 60/44px | 见 §3 |
| `AppTile` | 92×102 磁贴 | hover 只洗图标区；★ 置顶角标（`pinButton`，常显/hover 露出） |
| `PinnedIcon` | 固定卡 44px 小图标 | 语义与磁贴一致 |
| `MenuGlassPlate` | 四枚菜单共用毛玻璃底板 | 三明治结构，硬规则见 glass-recipe.md |
| `MenuRow` / `MenuCheckRow` / `MenuSubmenuRow` | 菜单条目 | 高 32、圆角 10、凹槽栅格（圆点 x8 / 文字 x20） |
| `MenuSectionLabel` | 菜单小节头 | 高 20、11px 次色；objectName 可换名 |
| `AspectMenuRow` + `AspectPickEntry` | 比例条目 | 高 28、圆角 8、右侧 SVG 示意矩形（2× 生成抗锯齿） |
| `BarPickRow` + `BarPickEntry` | 窗口栏条目 | 同度量、无示意；空 mode = 「跟随默认」 |
| `SessionChip` | 运行芯片 | 8px 绿点白环 + 标签 + hover 露出 ✕（危险色洗底） |

### 2.4 objectName 是冻结接口

`scripts/qml_shots.py` 与 `tests/test_*_qml.py` 按 objectName 驱动/断言。
**改名 = 改脚本 = 破坏验收链**。特别注意：顶栏「设置」段的 objectName 沿用
`gearButton`（历史名，语义 = 打开设置，qml_shots 冻结依赖）；设置页根为
`settingsPageQml`。新增可测元素时沿用此惯例。

### 2.5 ctrl 数据合同（摘要）

完整清单见 Main.qml 文件头注释块（以它为准）。要点：

- 属性 `devices` / `statusText` / `runningSessions` / `engineLocked` /
  `apps`（未置顶，拼音序）/ `pinnedApps` / `mediaVolume` / `turnScreenOff`
- 槽：`startSession` / `startSessionWithAspect` / `startMirror` /
  `setDisplayFlex` / `setDisplayFixed` / `setAppBar` / `setAudioExclusive` /
  `setKeepVd` / `setMediaVolume` / `togglePin` / `stopSession` …
- 即时刷新信号：`displayModeChanged` / `barPrefsChanged` /
  `audioPrefsChanged` / `behaviorPrefsChanged`（都带 package，菜单开着时
  刷选中圆点）
- 外观态（2026-09-12）：属性 `effectiveDark`（system 已折叠的亮/暗）与
  `glassMaterial`（玻璃总开关），notify 都是 `themeChanged`；保存后调
  `applyTheme()` 重读 settings 即时生效（Style 单例绑定这两个属性）
- 按应用记忆全部落 `gui_prefs.json`：`display`（显示模式）/ `bars`（窗口栏）/
  `audio`（音频独占）/ `behavior`（断开保留画面）

## 3. 图标体系

### 3.1 三来源优先级（AppGlyph 内）

1. **真实提取图**：controller 后台线程拉 APK 提取 → `.r2.png` 缓存（已套
   23% squircle 蒙版）→ `iconReady` 信号 → 批量 flush → QML `Image` 渲染
   （file URL）。渲染前条目先显示预设图，不空窗。
2. **预设 SVG**：`APP_CATALOG` 命中 → `icon_presets.render_preset_svg()`：
   60×60 viewBox、rx 14（23%）、品牌色纵向微渐变（顶部提亮 8%）+ 28px
   600 字重单字。字形基线按字符类烘焙（小写 37 / Q 38 / 其余 40）——Qt SVG
   渲染器不支持 `dominant-baseline`。**改模板必须 bump
   `_TEMPLATE_VERSION`**，否则旧缓存继续生效。
3. **未知应用 fallback**（纯 QML，无文件）：包名 charCode 和 % 12 取
   `Style.fallbackPalette` 柔和色板 + 白色首字 squircle。同包名恒同色。
   **禁止灰底圆**（DESIGN.md §3.1）。

### 3.2 修改入口速查

| 想改什么 | 动哪里 | 注意 |
|---|---|---|
| 新增/修改预设应用 | `catalog.py` `APP_CATALOG`（顺序即清单序，勿乱动） | color/glyph/glyph_ink 三件套 |
| 预设图标观感（渐变/字号/圆角） | `icon_presets.py` 模板 | **bump `_TEMPLATE_VERSION`**；跑 `test_icon_presets.py` |
| fallback 色板 | `Style.qml` `fallbackPalette` | 12 色、相对亮度 ≤0.42 托得住白字 |
| 提取管线（合成/蒙版/裁切） | `apps.py`（`_compose_adaptive` / `_paste_foreground` / `apply_rounded_mask`） | 圆角 23% 是 DESIGN.md 基准，勿动 |
| 大 APK / 不规则 logo 根治方案 | 见 RESEARCH-ICONS.md §6（P0 端侧 DEX、生成式统一） | 调研已做完，按优先级实施 |
| 应用自身 exe 图标 | 同名替换 `assets/duo.ico` + 重打包 | spec 无需改 |

### 3.3 法律红线（RESEARCH-ICONS.md §4.2）

**不内置任何真实品牌 logo**（Simple Icons 被 MS/Adobe 法务下架的先例）。
预设体系是"品牌色 + 原创单字"；设备提取的真实图标仅存用户本地缓存，
不随安装包分发。任何"把官方 logo 塞进仓库"的方案直接否决。

### 3.4 模型契约（改图标逻辑必守）

QML 网格是 QVariantList 整表替换模型：每次 emit `appsChanged` 都会整格
重建（异步图片闪白）。图标批量回填必须收敛为**一次** emit（controller 的
`_dirty_icons` + 单发 QTimer 机制，勿破坏）；信息扫描期间顺序冻结、原位
patch，扫描结束一次重排（`_apply_info_sweep_done`）。

## 4. 会话窗上巴 / 下巴（chrome_overlay.cs）

### 4.1 三态与链路

每条会话窗的上巴、下巴各有三态 `immersive | native | none`：

- **链路**：面板右键菜单「窗口栏 ▸」（`setAppBar`，按应用写
  `gui_prefs.json` bars 节）→ 启动会话时 controller 取 effective
  （per-app override 否则设置页默认）→ 注入 `--chrome-top` /
  `--chrome-bottom`（`chrome.py overlay_command`）→ settings 校验枚举 →
  C# `NormalizeBarMode` 归一（未知值回退 immersive）。
- **玻璃材质总开关**（2026-09-12）：同链路追加 `--glass 0|1` +
  `--bar-theme light|dark|system`（controller `_pin_glass` fresh-read
  settings 的 `glass_enabled`/`theme`）；关 = 上巴/下巴普通不透明材质
  （配方见 window-experience.md §13）。
- **默认值**（`settings.py`）：上巴 immersive、下巴 none（scrcpy 右键已是
  返回，下巴对多数会话冗余）。设置页两字段 = 默认值，仅未单独设置的应用
  生效。
- **none** = 该边永不建可见栏，但拖动/缩放热区（侧带、caption 带、edge
  strips）隐形保留。

### 4.2 上巴 immersive（默认形态）——右上角胶囊

`TopWindow`，GhostBackdrop 分层窗（UpdateLayeredWindow，alpha=0 处点击
穿透）。锚定：`client.Right − width − 10DIP`、`client.Top + 10DIP`
（`TopMargin=10`），hover 顶部 6DIP 触发带露出、48DIP 保持带（已钳进窗口
矩形，窗外不触发）。拖胶囊顶部 = 从上缘缩放（HTTOP/HTTOPLEFT/HTTOPRIGHT，
左右 20% 分界）。

- **几何**（逻辑 px × Dpi）：按钮 30、内边距 5、间距 6；flex 会话 4 键
  （最小化/比例适配/铺满/关闭），mirror/fixed 3 键（无铺满——比例窗不可
  拉伸）。
- **底板**（`DrawCapsuleAcrylic`）：PrintWindow 采样视频内容（reveal 时 +
  约 300ms 节奏；胶囊 + 3σ overscan）→ Kovesi 3×box 真高斯
  （σ = **6.0 DIP ×DPI，亮暗一致**）→ core 区 1:1 直贴 → 双态
  vibrancy（暗 0.90/+0.02，亮 0.98/+0.10）。frost 直接进入透明层，
  不再叠于暗干板：暗态 α0.90 / 10% 活底，亮态 α0.86 / 14% 活底；
  无样本时才用 #1C1C1E@92% 干板。顶光渐变暗态 2.4%→0.4%、亮态
  8%→1.5%。亮度阈值 0.50±0.04 迟滞；亮态深墨、暗态白字。
  静止态零描边；固定态 rim 为 35% 黑 / 50% 白。轮廓、rim 与字形
  走 3× 超采样，预乘后用 N×N 盒平均降采样（无振铃），再送
  UpdateLayeredWindow。完整论述见 `docs/window-experience.md` §11。
- **按钮字形**：Segoe Fluent Icons（回退 Segoe MDL2 Assets）12px×scale，
  自适应墨/白（亮态 #1D1D1F α0.85 / 暗态 #FFFFFF α0.90，hover 均 1.0）：
  `─ E921` 最小化、`⤢ E740` 比例适配（FakeMaximize fit）、`⤒ E922` 铺满
  工作区（FakeMaximize fill，仅 flex）、`✕ E8BB` 关闭；激活态切换
  `⤡ E923` restore 字形（`SetMaximized`）。
- **动作 id**（`TopAction`）：0 = SW_MINIMIZE、1 = FakeMaximize(fit)、
  2 = FakeMaximize(fill)、3 = WM_CLOSE。

### 4.3 上巴 native（系统标题栏形态）

- **duo 不传 `--window-borderless`**（`borderless_for`）——scrcpy 自己建
  带框窗，系统真 caption 自带 ─□✕/拖动/吸附/DWM 圆角。事后给 SDL 无边框
  窗补 WS_CAPTION 是死路（SDL 接管 WM_NCCALCSIZE；跨进程子类化被系统禁用，
  实测 ERROR_ACCESS_DENIED），勿再尝试（window-experience.md §10）。
- overlay 缩为**第 4 键**：46×32 逻辑 px，锚 `visible.Right − 4×capBtnW`
  （系统三键簇左侧一格），垂直中心 = 标题带下 16 逻辑 px，钳进工作区。
  字形 `⤢ E740`，激活切 E923；hover 洗底 rgba(0,0,0,0.06) + 墨色
  #1D1D1F（静止 alpha 140 / hover 235）。
- `Repair()` 只在 caption 样式家族（CAPTION|SYSMENU|MIN|MAXBOX）不完整时
  防御性补齐（SDL 会重assert样式，主 tick 常备检查）；已完整则不动
  （FRAMECHANGED 闪帧）。

### 4.4 z 序铁律（2026-09-09 真机定稿）

所有 overlay 面**不悬浮**，紧贴视频窗之上：`SetWindowPos` 插入点取
`GetWindow(video, GW_HWNDPREV)`（视频窗上面那扇），让 overlay 落在它与
视频窗之间。`hWndInsertAfter` 语义是"该窗口位于被定位窗口之上"——直接传
视频窗句柄会把整层 chrome 插到视频**下方**（"沉浸栏没内容"事故根因）。
几何/可见性断言证明不了"看得见"，必须断言 z 序 rank + 真机像素双证据。

### 4.5 其余 chrome 面

- **下巴** `ChinWindow`：底部通栏 32px 毛玻璃（2026-09-12 Opus 统一：真高斯
  σ10 + 1:2 预降采样、双态矩阵/活底/顶光/干底与胶囊同值、静止零描边、原始
  采样亮度判据；配方与性能路径见 window-experience.md §13），○ 单击 = 返回，
  长按 = 镜像窗 keyevent 3 / 虚拟屏 `am start --display N -c HOME`。
  **虚拟屏永不发 keyevent 3**（HOME 被系统全局拦截落物理屏，面板"回主页"
  芯片同语义）。
- **侧带** `SideBandWindow`：仅 immersive 上巴存在，左右各 8DIP 隐形移动带
  （三带皆可拖窗）。native 上巴不建（系统 caption 拖动）。
- **EdgeStrip**：顶边 6px 缩放带 + caption 中央 1/2×24DIP 移动带（左右 1/4
  穿透）；灵动岛方向消歧（水平拖 = 移动 / 垂直滑 = 拉通知栏 / 点按 = 穿透）。
  native 上巴时顶部 strip 让开系统三键簇（`SyncStrips` 的 `cluster` 预留）。

### 4.6 改 overlay 的约束

- **C# 5 兼容**（legacy csc.exe，无新语法/string 插值/?.）。
- 改 .cs 后无需清缓存：sha256 内容戳自动触发重编；启动日志
  `logs/overlay/chrome-latest.log` 记录源码指纹 + 完整 argv（"没成功"先看
  这里）。
- overlay 是独立 PE 进程，与 scrcpy 窗跨进程交互（FindWindow by title）；
  改动只能在 Windows 真机验收，WSL 复现不了。
- `test_chrome.py` 对源码有指纹/加固断言，改关键结构先跑它。

## 5. 设置页（SettingsPage.qml）

- 结构：分组卡片流（引擎 / 投屏质量 / 窗口栏（默认）/ 外观），无标题行无返回钮（顶栏胶囊
  即导航）；Esc = 取消（`cancelled` → pop），底部唯一「保存」（`accepted`
  → `ctrl.resolveAdb()` + pop）。
- 数据合同：`settingsApi.load()/save(map)/probe()/loadProblems()`，键同
  `duo/core/settings.py` 的 Settings 字段。**save 按整表构造 Settings——
  map 缺键 = 该键被重置**。已删控件（DPI / 圆角）靠 `dpiPass` /
  `cornerModePass` / `cornerSizePass` 隐形透传（load 读入 → collect 原样带
  回），新增删字段时照此办理。
- 窗口栏两字段（`topBarMode` immersive / `bottomBarMode` none）是**默认值**
  语义，不是立即生效——见 §4.1 链路。
- 外观卡（2026-09-12）：「主题」三选一（`themeMode` light|dark|system，
  objectName themeLight/themeDark/themeSystem；保存后 `ctrl.applyTheme()`
  即时生效，system 经 Qt colorSchemeChanged 实时跟随）+「玻璃材质」开关
  （`glassOn` → settings `glass_enabled`，上巴/下巴/右键菜单统一总开关）。
  文案 KISS（Opus 裁决）：说明一句能说完不用两句，自解释控件零说明。

## 6. 跨切面合同

### 6.1 DPI / 缩放

- Qt6 per-monitor High-DPI 默认开启，`app.py` 刻意不设任何 QT_* 缩放覆盖；
  QML 里所有 px 值都是 DIP。
- `zoomLayer`（Main.qml）：DPR ≥1.25 的屏 `uiScale=1.0`（现状不变）；
  DPR=1.0 的屏取 1.25（低分屏舒适档）。子树内布局坐标保持未缩放 DIP。
- `snapGrid()` 设备像素网格吸附（DPR 1.25 → 4px 格 / 1.5 → 2px 格）：菜单
  定位、毛玻璃几何必须吸附，否则分数 DPR 下玻璃内容漂移（glass-recipe.md
  硬规则 6）。新浮层定位照抄 `ctxMenu.openFor` 的写法。
- C# 侧相反：`SetProcessDPIAware()` 物理像素，`S()` 把逻辑 DIP 换算物理。

### 6.2 玻璃与性能（摘要，全文见 DESIGN.md §4 / glass-recipe.md）

- 全界面零阴影；分层只靠材质对比 + 亮边。动效只允许 140ms 透明度/颜色
  过渡，无常驻动画。
- `Style.glassBlur` 闸门 = `app.py` 注入的 `shadersUsable`（WSL=false）：
  软件路径回退不透明 `menuFill`，出图不破相。新浮层必须双路径都成立。
- 毛玻璃新增/改动只按 glass-recipe.md 的三明治结构改，硬规则（1:1 过采样、
  threshold 0.5、autoPadding 关、网格吸附）不可破。

### 6.3 打包

PyInstaller onefile（`duo.spec`）：QML 侧车目录 `duo/ui/qml` 必须
`--add-data` 进包，否则 `QML_MAIN` 无从加载；预设图标运行时生成无资源要
打包；新静态资源记得确认 spec 覆盖。

## 7. 验收流程（改完必走）

1. **静态门槛**：`ruff` + `mypy` + `pytest` 全绿（合入门槛，AGENTS.md）。
2. **出图对比**：`python scripts/qml_shots.py` → `docs/validation/assets/
   qml-main.png` / `qml-settings.png`，对照 DESIGN.md 检查；面板改动至少
   覆盖窄版（420×660）与宽版（720×480）两档。
3. **视觉裁决**：主观拿不准交视觉顾问（agy Opus），不口头争论（铁律 10）。
4. **真机**：上巴/下巴/毛玻璃改动必须 Windows 真机验收（z 序、DPR、GL
   才暴露）；验收标准见 glass-recipe.md §5 与 window-experience.md。
   分数 DPR（125%/150%）下凑近看四角与边缘是固定动作。

## 8. 红线清单（踩过的坑，勿重蹈）

1. QML 菜单浮层放进 `canvasRoot` 子树 → 毛玻璃自采样环。
2. `Rectangle{radius:w/2; border.width:1}` 做小圆点 → GL 真机 border 错位
   （用 `Dot` 组件的双 Rectangle 叠法）。
3. 多边形/整表模型的批量改动多发 `appsChanged` → 磁贴闪白、长按中消失。
4. 菜单/玻璃坐标不做 `snapGrid` → 分数 DPR 偶发内容漂移。
5. 给 SDL 无边框窗事后补 caption / 跨进程拦截 WM_NCCALCSIZE → 死路（§4.3）。
6. `SetWindowPos` 插入点直接传视频窗 → 整层 chrome 落视频下方（§4.4）。
7. 沉浸栏触发带骑出窗外（半平面判定）→ 闪现即隐；判定带必须钳进窗口矩形。
8. `SettingsApi.save` 丢键 → 用户设置被静默重置（§5 透传合同）。
9. 改预设 SVG 模板不 bump `_TEMPLATE_VERSION` → 改动"不生效"。
10. QML objectName 随手改名 → qml_shots / 测试断言链断裂（§2.4）。
11. 内置品牌 logo / 复制官方图标 → 法律红线（§3.3）。
12. C# 里用 C# 6+ 语法 → csc.exe 编译失败，带 chrome 的会话全部启动即死。
