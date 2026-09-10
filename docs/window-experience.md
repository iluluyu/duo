# 窗口体验

> 行为规范 + 真机调研存档。代码是实现的事实来源；本文只记录**当前语义**与**为什么**。

## 1. 当前窗口行为

| 模式 | 窗口 | 缩放 |
|---|---|---|
| 整机镜像 | 跟随设备画面，scrcpy 自管 | 等比锁定（`ConvergeToVideoAspect`：外部改窗 350ms 后收敛） |
| 固定虚拟屏（竖屏等） | 同上 | 同上 |
| 应用会话（flex） | **纯 Windows 窗口**：拖哪是哪；缩放异步下发（SWP_ASYNCWINDOWPOS，不阻塞于目标窗口重排）；`window_aspect=locked`（设置项，2026-09-06 定稿）可改为约束在内容比例内（像视频播放器） | 自由缩放（默认）或比例锁定（设置）；虚拟屏恒定 2560×1440，方向由 APP 自主请求，APP 转屏时 scrcpy 原生把窗口贴合新内容（无黑边）；我们零干预 |

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

## 2. 虚拟屏调研存档（2026-09-05 真机，OPD2409 / Android 16 / scrcpy 4.1）

- `--new-display` 建屏自带 `FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS` → 副屏自动拉起
  AOSP `SecondaryDisplayLauncher`（`CATEGORY_SECONDARY_HOME` 唯一 handler）——
  即用户看到的"应用选择器"。ColorOS 桌面不参与副屏。
- **HOME 全局拦截落物理屏**：`input -d <id> keyevent HOME` 焦点立即跳 display 0，
  虚拟屏画面不变、应用 paused。display 定向注入无解（系统语义）。
  故虚拟屏上永不发 keyevent 3；HOME 替代 = 回 Duo 面板。
- `--start-app` 无 `+` 前缀时应用已有 task 不落新屏（"delivered to running instance"）。
  一律带 `+`（force-stop 后启动）。
- 会话退出 = 屏销毁 = 内容销毁（`FLAG_DESTROY_CONTENT_ON_REMOVAL`）；
  `--no-vd-destroy-content` 可改搬回物理屏（未启用）。
- `am start --display <id>` 可跨屏搬移已运行 task（面板"运行中点应用"直达机制的基础）。
- 复现命令：

```bash
scrcpy -s <serial> --new-display=1200x1600/280 --no-window --no-audio --record=exp.mp4
adb shell dumpsys display displays | grep -E 'Display id|FLAG_'
adb shell cmd package query-activities -a android.intent.action.MAIN -c android.intent.category.SECONDARY_HOME --brief
adb shell am start --display <id> -n <pkg>/<activity>
```

注意：ColorOS 上 `screencap -d <逻辑id>` 报 not valid，需 SurfaceFlinger 虚拟屏 id。

## 3. 横竖屏风暴实验记录（2026-09-06，piliplus 真机）

背景：用户要求"拖窗口→应用实时重排"（flex 跟随）。定向应用（piliplus 视频页
强制竖屏）与跟随互斥。全天 A/B 结论：

| # | 尝试 | 结果 |
|---|---|---|
| 1 | `--flex-display` + 吸附（fit to texture） | 窗口小跳；oscillation 风险（陈旧纹理触发） |
| 2 | fit + 25% 尺寸护栏 + 比例护栏 | piliplus 仍"一直旋转"（显示↔应用 2Hz 乒乓） |
| 3 | 钉扎 v1（缺拖动豁免） | **瞬移回弹 bug**（用户撞） |
| 4 | 解耦：固定屏不跟随 | 用户否决（要可调整） |
| 5 | 单向跟随 + nudge | nudge 教不会 scrcpy，仍翻转 |
| 6 | 钉扎 v2（拖动豁免） | 用户实测仍风暴（显示自身在旋转，与窗口无关） |
| 7 | `--no-vd-system-decorations` | 首页稳、视频页仍风暴 |
| 8 | `--capture-orientation=@` 锁向 | **内容侧转 90°**（用户撞） |
| 9 | 固定屏 + 比例解锁 + 钉扎 v3（左键豁免+收编） | ✅ 稳定（现基线） |

**根因链**：应用方向请求 → WindowManager 旋转虚拟屏（`ROTATES_WITH_CONTENT` 建屏
即带）→ scrcpy flex 重申窗口形状 → 无限乒乓。scrcpy 4.1 无"可跟随但禁旋转"旗标。

后续：就地跟随已实现并真机验证，同日按用户决策整体回退（信 APP，不跟随不切屏）；
存档见 TODO.md 任务 1。

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

## §11 上巴/下巴换装右键菜单同款毛玻璃 + 胶囊右键固定（2026-09-09）

> 用户反馈：右上角胶囊（上巴）与下面突出的 native 下巴美观与面板右键
> 菜单不一致，要求同款毛玻璃；另要求胶囊右键固定/取消固定。

### 玻璃配方（对齐 docs/ui/glass-recipe.md 二级浮层档）

沉浸胶囊（`TopWindow.DrawCapsuleAcrylic`）与 native 下巴
（`ChinWindow.DrawAcrylic`）统一为：

- 1:8 上下采样模糊（≈ QML MultiEffect blur 0.75×32 = 24px 档）；
- ×1.15 饱和（QML saturation 0.15）；
- tint 82% 白 `#D0FFFFFF`（menuTintHi 档）；
- 14% 黑 hairline `#24000000`，1px：胶囊沿轮廓外描，下巴沿巴轮廓
  （耳条平移后的 RoundedPath）整圈内描；
- 浅玻璃 ⇒ 字形白→墨 `#1D1D1F`（rest 0.78 / hover 1.0）；胶囊无采样
  帧回退 88% 白（flyoutFill 档）。

退役的旧材质：胶囊暗烟色 `rgba(28,28,30,~0.55)` + 顶缘 1px 白亮边；
native 下巴 `rgba(248,248,248,184)` + 顶缝 8% 黑 + 内 45% 白双线。
下巴 pill 明暗自适应保留（82% 白下恒判暗，逻辑自愈）。沉浸下巴
（iOS Home Indicator 白 pill）不在此次范围。

### 顺带修复：mirror/fixed 会话关闭键无红 hover

旧 `DrawHoverFill` 按 `Kind == 5` 判关闭键——只有 flex 四键布局的关闭键
是 Kind 5；mirror/fixed 三键布局关闭键是 Kind 4，从未拿到 Win11 红
`#E81123` hover。改为 `NavButton.Danger` 显式标记（构造时最后一个槽位），
其余键的 hover 洗色同步换为 hoverWash 4% 黑（浅玻璃上白洗不可见）。

### 胶囊右键固定（按 APP 持久化）

`Controller._topPinned`（`ToggleTopPin`，胶囊任意处右键切换）：

- 固定时 `showTop` 恒真（engaged 期间常驻露出；采样心跳照跑，
  SampleTop ~300ms 零额外成本）；
- 固定态视觉：胶囊 hairline 36→70 加深，其余不动（无新增动效）；
- disengage（视频窗被盖/失活/最小化）仍照旧隐藏——固定语义是
  "engaged 期间不再依赖顶缘近距"，不是 always-on-top；
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
