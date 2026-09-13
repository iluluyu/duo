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

### 玻璃配方 v2（2026-09-10 通透化定稿）

沉浸胶囊（`TopWindow.DrawCapsuleAcrylic`）与 native 下巴
（`ChinWindow.DrawAcrylic`）统一为：

- 1:8 上下采样模糊（≈ QML MultiEffect blur 0.75×32 = 24px 档）；
- ×1.20 饱和（原 1.15，白 tint 减量后提一档补偿）；
- tint 55% 白 `#8CFFFFFF`；胶囊无采样帧回退 65% 白 `#A6FFFFFF`；
- 胶囊外描 1px 白亮边 `#5AFFFFFF`，固定态加深 `#A0FFFFFF`；下巴沿巴轮廓
  （耳条平移后的 RoundedPath）整圈内描 14% 黑 `#24000000`（与内容交界
  的 seam 语义，非悬浮轮廓，保留）；
- 字形墨 `#1D1D1F`（rest 0.78 / hover 1.0）不变。

退役的旧材质：胶囊暗烟色 `rgba(28,28,30,~0.55)` + 顶缘 1px 白亮边；
native 下巴 `rgba(248,248,248,184)` + 顶缝 8% 黑 + 内 45% 白双线；
§11 初版 82% 白 menuTintHi 档 + 14% 黑胶囊 hairline（2026-09-10
通透化退役，黑 hairline 在视频上读成"灰线"）。
下巴 pill 明暗自适应保留（55% 白下暗场景可判暗，逻辑自愈）。沉浸下巴
（iOS Home Indicator 白 pill）不在此次范围。

### 毛玻璃化（2026-09-10 晚，gemini-3.8-flash 设计 × claude-opus-4.6 裁决定稿）

用户对 55% 白仍判「更丑了、很白、不是毛玻璃」，拍板方向：「或许不加
白底更好看，仅仅使用毛玻璃特效」。流程：flash 出三套配方 → Opus
裁决。**方案 1【macOS HUD 纯粹态】微调胜出**（lift +0.05→+0.07）；
方案 2 否决（+0.40 lift 数学上是对暗部铺 40% 灰板，伪装的白涂料，
且 flash 自粉的 4.6:1 对比度实算仅 ~2.2:1）；方案 3 否决（12% tint +
10% lift 纯黑底叠出 ~0.22 明度，逼近灰板阈值）。

**胶囊终版配方（唯一实现口径）：**

| 参数 | 值 | 依据 |
|---|---|---|
| 模糊 | 1:8 降采样（等效核 20–24px；Opus 纠正 flash 的"28px"凭空数字） | 1:10 收益边际且升采样锯齿风险 |
| tint | **0%（彻底移除平铺白）** | 用户铁令；白涂感报源 |
| vibrancy 矩阵 | 饱和 ×1.45、scale 0.90、lift +0.07（BT.709）：黑→RGB(18,18,18)，白→RGB(247,247,247)，动态范围保留 90% | macOS 式"背光玻璃"而非"奶皮" |
| 字形 | 自适应：亮态墨 #1D1D1F α0.85 / 暗态白 #FFFFFF α0.90，hover 均 1.0；阈值 0.50±0.04 迟滞（判原始采样 BT.709 亮度，TopWindow.SetSample）；关闭键 hover 恒 #E81123 + 白 ✕ | 0% tint 无安全网，自适应是可读性唯一保障 |
| rim | 自适应 1px：亮态 10%/20% 黑（固定态加倍），暗态 28%/50% 白；rim 跟随字形态同一套阈值（Opus 否决 flash 的独立 rim 状态机） | 零阴影语言下唯一分层手段 |
| hover 洗底 | 亮态 6% 黑 / 暗态 12% 白 | 暗底黑洗不可见 |
| 干底 | #F5F5F7 @88% + 亮态墨字 | 读作"玻璃待命"而非灰板 |

**工程转换注记**：GDI+ ColorMatrix 的平移在第 5 **列**（Matrix04/14/24），
Opus 交付的是第 5 行（Android 约定），采用时已转置；三态复核（纯黑
7.9:1 / 纯白 3.5:1 图标字 / 高频彩色迟滞+低采样双低通）见裁决全文。
面板右键菜单与下巴栏按同一方向逐面改造（后续两节）。

### 菜单与下巴毛玻璃化（2026-09-10 深夜，flash 设计 × Opus 裁决）

胶囊定稿后同流程裁决剩余两表面（Opus 勘误：flash 又虚报暗态描边对比度
4.2:1，实算 2.23:1；对无彩画布上饱和增益的效果过度承诺）：

**面板右键菜单（QML，浅画布 #F5F5F7 上方）**——tint 0%，可辨性改由
光学增益 + 描边承担：blur 0.75（二级 0.85）/ blurMax 32 不变；
saturation 0.45（二级 0.50，MultiEffect 语义即 ×1.45/×1.50）；
brightness 0.02（二级 0.04）；contrast 0.06（二级 0.10，(v−0.5)×(1+c)+0.5
把浅底中高灰推向白饱和，玻璃体始终比底板亮 ~10 级）；描边 1px 黑
12%（二级 14%——Opus 从 flash 的 10% 上调：纯白画布上 12% 给出 31 级
暗刻 vs 10% 的 25 级，为单态表面防溶底留余量）。最坏复核：#FFFFFF 画布
上玻璃输出 clamp 至 255，描边 224，跨边界 62 级视觉事件，不溶底；墨字
16.85:1 AAA。结构硬规则（三明治/1:1/mask/autoPadding/snapGrid）不动，
只改数值；软件回退不透明 menuFill 不变。详见 glass-recipe.md。

**下巴（C#，视频上方通栏 32px）**——100% 复用胶囊管线：tint 0% +
共享 vibrancy 矩阵（NewVibrancyMatrix，胶囊/基底/下巴三处同源）；
药丸自适应判据改**矩阵输出亮度**（非原始视频），阈值 0.50 ± 0.04
迟滞（矩阵输出 0.50 ≈ 原始 0.478，该点白药丸 2.40:1 略优于墨药丸
1.90:1，切换方向正确）；巴 hairline 随明暗翻转：亮 10% 黑 / 暗 22%
白（解决旧 14% 黑描边在暗视频上失明）。药丸本色不变（亮巴马深色
rgba(29,29,31,.40) / 暗巴马白 rgba(255,255,255,.61)，对标 iOS 底部
横条 2.2–2.5:1）。沉浸下巴（ghost 热区 + 贴底白药丸）不在范围。

### 边缘净化（2026-09-10 夜，真机反馈"胶囊边缘一层灰影"）

去奶白后胶囊轮廓暴露两个叠加缺陷：

1. **骑边 rim 的外半圈灰环**：1px 半透明描边默认中心对齐轮廓，外半圈
   画在玻璃外的视频上；premultiply 修正后它如实渲染 = 浮在视频上的
   ~10% 灰雾环。旧奶白底时代它被读作"不透明盘的边"，透明玻璃下
   直接露馅。修复：`PenAlignment.Inset`——描边只画在玻璃内侧。
2. **GDI+ SetClip 是 1 位硬裁切**：玻璃内容轮廓本身是硬台阶（无 AA），
   靠描边掩盖。修复：DrawCapsuleAcrylic 不再裁切（整矩形绘制），
   `MaskSurface` 把 AA 填充的胶囊形状（FillPath 白底抗锯齿）作为
   覆盖率蒙版乘进 alpha——真正平滑的 1px 亚像素轮廓坡，玻璃外零
   溢出，ghost 点击穿透契约不变（alpha 0 = 穿透）。

### 纯粹毛玻璃胶囊定稿（2026-09-10 夜二，用户拍板 + 全链路复现台验证）

用户反馈"外层胶囊 + 内矩形毛玻璃 + 中间阴影环"。全链路复现台（假视频
窗 + 真分层窗 + PrintWindow 采样 + DWM 合成 + 抄屏像素分析）证明当前
管线无此伪影——该视觉是骑边描边版的特征（描边把毛玻璃"框"出一层、
与硬裁切边之间的地带读作阴影）。定稿去层化：

- **静止态零描边**：纯粹毛玻璃轮廓（AA 蒙版），无任何边框层；
- **描边只在固定态出现**（亮 35% 黑 / 暗 65% 白，内缩）= pin 指示器
  （旧反馈 10%→20% 弱到不可见，用户判"固定失效"——逻辑链完好，
  右键→ToggleTopPin→日志/pin 文件/showTop 门控均正常）；
- 管线卫生：MaskSurface 移到 Graphics 释放后执行；蒙版光栅化补
  PixelOffsetMode.Half 与内容/描边对齐。

注：面板进程不重启会继续用缓存 overlay（onefile 资源在进程启动时解包），
改版后必须全部关掉重开再验收。

### 虚化强度审美回调：统一 σ6（2026-09-10 午后八，用户反馈）

σ4 在 59px 高胶囊中仍接近未虚化。亮暗统一调至 **σ6.0 DIP**：150%
DPI 下标准差约 9 物理像素，底层硬边峰值相对 σ4 理论降低约三分之一，
能明确打散文字轮廓，同时不回到 σ8 的厚雾面感。亮态增白矩阵、α、
背光及暗态深色配方均不变；3σ overscan 自动扩大，内容边缘不触采样边界。

### 亮底增白 + 统一 σ4（2026-09-10 午后七，用户反馈 × Opus 裁定）

用户反馈：亮色背景仍略灰，希望增加可读性，并将亮暗一致的虚化半径
从 3 增到 4。Opus 裁定：

- σ：亮暗统一 **4.0 DIP**；暗态其他参数不动。
- 亮态矩阵：0.94/+0.08 → **0.98/+0.10**；近白更容易夹到洁白，
  但中间调仍保留后层色调，不做奶白不透明板。
- 亮态板体 α：0.85 → **0.86**（14% 活底）；顶部背光 8%→1.5%
  不变。更强模糊配合微增不透明度，补回字形对比。
- 离屏：纯白玻璃约 251.6/255；L218 亮底变为 L233.8（+15.8）。

### 真活底渗透 + 双态统一半径（2026-09-10 午后六，用户定向）

用户反馈：白底仍不够白；虚化半径减半且亮暗必须一致。

根因不是亮态矩阵，而是合成顺序：`DrawCapsuleAcrylic` 先铺
`#1C1C1E@92%` 干板，再把 α0.85 frost 叠上去，所谓 15% 渗透实际
落在暗干板上；白 frost 约变成 `0.85×255 + 0.15×28 = 221`，最终板体
α≈0.99，真实视频仅透约 1%。

- 有采样时不再铺干板：frost 以自身 RGB/α 直接进入透明 overlay，窗口层
  真正合成为 `0.85×frost + 0.15×live`；干板只在无采样时回退。
- σ 统一为 **3.0 DIP**（亮暗一致，约为此前一半），overscan 仍为 3σ；
  暗态矩阵 0.90/+0.02 与 α0.90 不变，仅半径同步缩小。
- 白底离屏实测：原始 255，胶囊 252.1；实际板体 α0.858、活底渗透
  14.2%（顶部背光略增不透明度）。

### 亮态再通透 + 毛边收尾（2026-09-10 午后五，用户定向）

用户反馈：亮色还能再通透；毛边不明显了但可以再加强。

- **亮态配方**：σ 6.5→6.0（底下形状更可辨）、底板 α 0.90→0.85
  （活底渗透 15%）、矩阵 0.92/+0.10→0.94/+0.08（少奶白）。暗态
  （σ6.5 / α0.90 / 0.90+0.02）零改动。离屏：形状可辨度 0.92；
  平底亮底上胶囊几乎隐身（字形浮空 = macOS 亮色 vibrancy 观感）。
- **超采样 2×→3×**（DownscaleNx N×N 盒平均）：轮廓坡级数更细。
  暗底实测：53 行全检出、暗 dip 0、边缘二阶导 1.0、进入坡单调
  无下冲。

### 轮廓暗线根治 + 固定态常驻（2026-09-10 午后四，用户定向）

用户反馈：边缘仍有锯齿；固定态也会消失。

- **轮廓暗线（锯齿读感的真凶）**：超采样降采样用 HighQualityBicubic，
  其负瓣在蒙版坡上下冲 = 沿轮廓的暗色级 (~9 级)，圆头处读作锯齿；
  预乘只修了直通 alpha 混色（a² 双暗），振铃仍在。修复链：预乘在
  降采样前（SourceCopy 直拷，不再经 SourceOver 二次混）+ 精确
  **2×2 盒平均**降采样（Downscale2x，自写 LockBits 路径）——对 2×
  超采样数学理想（无振铃无混叠）。离屏实测：轮廓暗 dip 行 11 → 0。
- **固定态常驻**：原语义"engaged 期间常驻"，失活（切窗口）即隐藏。
  用户拍板：固定 = 常驻——engaged 判定加 `|| _topPinned`；窗口可见
  即露出（最小化/关闭仍由 IsIconic/IsWindowVisible 上游门拦住）；
  被其他窗口盖住时三明治 z 序天然遮挡，不浮在别人窗口上。

### 亮色模式去灰：矩阵双态化（2026-09-10 午后三，用户定向微调）

用户澄清：暗色模式现在通透好看（不动）；亮色模式下玻璃有点发灰。
根因：亮态 backdrop 上 scale 0.90 + lift 0.07 让 frost 比周围内容
**暗**（离屏实测 −4 级）= 灰纱。修复：矩阵双态——亮态
scale 0.92 / lift 0.10（白底顶到 clamp、浅底玻璃比背景亮 +17 级 =
发光玻璃），暗态 0.90 / 0.07 原配方零变化；判据复用 _capsuleDark
迟滞（0.50±0.04），亮暗切换无抽搐。

### 暗底死板根治：ColorMatrix 死格子 + 顶光渐变（2026-09-10 午后二，真机探针 × opus-4.6）

用户反馈"现在怎么变灰了"（暗色 app 底上胶囊 = 死板暗灰板）+ rim 锯齿
"还可以优化"。离屏探针发现**历史级 bug**：

- **lift +0.07 从未上过屏**：GDI+ ColorMatrix 是行向量约定（out = in·M），
  平移在第 5 **行**（Matrix40/41/42）；初版把 Opus 交付的矩阵转置时把
  lift 放进了第 5 列（Matrix04/14/24）——死格子。饱和块恰好在列位置
  （双重转置=正确），唯独平移丢失。真机探针：黑入 → 黑出（应为 18 级）。
  黑底 frost 一直是纯黑，无设计中的 #121212 基底——"暗底死板"的真根因。
  修复：lift 移到 Matrix40/41/42（探针复测：灰 128 → 133，+18 级 =
  设计值；白 → 247 不变）。亮底内容 frost 同步 +18 级 = 更通透。
- **顶光渐变**（Opus 裁决 A）：暗底玻璃的生命感——内容无关的白色纵向
  ramp（8% 顶 → 1.5% 底）画在玻璃内；亮底上 +8% 被 clamp 不可见，
  亮态行为不变；干底同样适用。否决自适应 lift（B：均匀提亮仍是死板
  的平板，投诉的是"死"不是"暗"）。
- **暗态 pin rim 65% → 50% 白**：渐变接管体量感后 rim 放松。
- **轮廓蒙版升级到超采样分辨率**：MaskSurface 重构为 MaskPath(w,h)，
  2× 位图先套 2× 蒙版再降采样——轮廓坡从 1× 8-bit 量化升级为高质量
  降采样坡（"细线锯齿还可以优化"的收尾）。

离屏复测（纯黑底）：玻璃垂直坡 31→19（修复前死平 ~2）；rim 峰值行
抖动 0px、100% 单行；轮廓坡平滑无台阶。

### 通透化 + rim 锯齿修复（2026-09-10 午后，用户真机反馈 × opus-4.6 裁决）

新构建上屏后用户反馈："通透性有点差" + "周围一圈细线有锯齿"（固定态
pin 描边）。Opus 双通路裁决：

- **底板透明度 α255 → 0.90**：10% 活底渗透。旧裁决"10% 渗透会把硬边
  以 0px 模糊重引入"的前提已消失——高斯已把内容边熔到 0.012/px，
  10% × 0.012 = 0.0012/px 的重影不可感；300ms 陈旧采样与活底的错位
  同样被磨碎高频后不可辨。双通路 = 90% σ 磨砂（材质感）+ 10% 锐利
  活底（纵深线索），对标 NSVisualEffectView 的"几乎可见"。
- **σ8 → 6.5**：活底承担一部分通透后，磨砂层无需独自工作；6.5 让
  大尺度色彩结构存活（熔化坡 ≈19px、边缘梯度 0.017/px，仍比伪影
  阈值 0.026 软 1.5×）。
- **矩阵不动**：两项已大幅改变材质，同步调矩阵会让回归不可归因。
- **锯齿根因**：固定态 rim = 1px Pen 沿路径描边，150% DPI 下落在
  像素间 = 经典 GDI+ 锯齿。修复 = ghost 表面 **2× 超采样渲染**
（`Supersample`：PaintBar 在 2× 网格光栅化后高质量降回，字形/hover
  圆同步受益；离屏渲染台实测 rim 峰值行沿线抖动 0px、100% 单行）。

**部署链铁律（今日三轮"改了没效果"的根因）**：面板是 PyInstaller
onefile（`C:\Tools\Duo.exe`），overlay 源码在打包时嵌入 exe——改
chrome_overlay.cs 后必须：① 同步到 `C:\duo`（构建仓）② 重跑
`scripts/build_windows.ps1` ③ 用户关旧会话重开。只改仓库/venv/
缓存 exe，面板永远用嵌入的旧管线。

### 矩形伪影根治：真高斯毛玻璃（2026-09-10，用户反馈 × opus-4.6 裁决）

用户再次反馈同形视觉："胶囊内有矩形的色阶断层，矩形边框和内外部完全
不是一个设计，矩形外胶囊内灰隙脔"。上轮复现台判"骑边描边版特征"结论
不完整——复现台的假视频窗填满了彩色内容，没有复现真机的关键底景：

**真根因（像素仿真定测）**：

1. **1/8 重采样"模糊"太弱**：1/8 双线性降采样 + 8× 双三次升回 ≈ 仅
   ~9px 宽的坡。等比适配（aspect-fit）视频的边界（黑边 letterbox
   与视频内容的直线交界）恰好从胶囊底下穿过，以近乎全锐度存活在
   底板里 = "矩形色阶断层"；letterbox 侧过矩阵后是 #121212 级的
   平坦暗灰 = "矩形外灰隙脔"。仿真量化：旧管线边界残存梯度
   0.026/px（坡宽 9px）；σ8 高斯 = 0.012/px（24px 坡），σ10 =
   0.009/px（30px 坡）。
2. **采样落地不重绘**：`TopWindow.SetSample` 只存采样，唯一重绘驱动
   是鼠标事件——视频播放中胶囊内矩形永远冻结，读作 UI 边框而非
   光学。修复：SetSample 烘焙 frost 缓存后立即 `Render()`。
3. **采样零余量**：裁剪恰等于胶囊边界，模糊核直接触及采样纹理
   边界（违反 glass-recipe.md 硬规则 2）。修复：`FrostMargin`
   = 3σ overscan（与窗矩形求交），核永远采到胶囊外真实内容。
4. **干底奶雾**：PrintWindow 失败时 88% #F5F5F7 = "灰隙脔"另一来源。
   opus 裁决换暗玻璃 #1C1C1E @92%（"glass at rest"），初始
   `_capsuleDark = true` 保证干底白字形。

**终版管线（唯一实现口径）**：采样（胶囊 + 3σ margin）→ Kovesi
3×box 高斯（σ = 8 DIP 物理 ×DPI，边缘外推，实测逼近真高斯误差
0.4%且零能量泄漏）→ core 区 **1:1** 直贴过 vibrancy 矩阵（可见
内容零重采样，无任何重采样伪影能进入玻璃）→ frost 缓存（hover
重绘 = 纯贴图）→ MaskSurface AA 轮廓不变。**Opus 裁决参数**：
σ8（与菜单 24px 档同族；σ6 边缘半可辨，σ10+ 小胶囊失色）；底板
不透明 α255（blur 即透明性，10% 活底渗透会以 0px 模糊重引入硬边
+ 300ms 陈旧采样双重曝光）；矩阵不变（饱和 ×1.45 抵消模糊去饱和，
σ8 平均面积更大后余量更足）；采样 300ms 平坦节拍（冻结假象源自
不重绘而非节拍，150ms 突发只加成本）；干底暗玻璃。

验收：等比适配窗口（letterbox 边界穿过胶囊）下看胶囊——边界应熔为
柔和色坡，无任何可辨矩形/直线；视频播放时玻璃随采样呼吸；PrintWindow
失败场景（D3D）干底为暗玻璃白字形，无奶雾。

### 通透化修订（2026-09-10）：两个渲染缺陷 + 去烟熏

用户反馈三连：毛玻璃不通透、按钮字形（─/⤢/✕）锯齿、胶囊外缘灰线
锯齿；拍板"烟熏不需要，要更加通透好看的 UI"。逐项根因：

1. **直通 alpha 上屏（灰线锯齿 + 不通透元凶）**：`UpdateLayeredWindow`
   的 `AC_SRC_ALPHA` 要求**预乘 alpha**（BLENDFUNCTION 文档：rgb 必须
   先乘 a/255 再传入）；GDI+ 位图是直通 alpha，`GetHbitmap` 原样拷贝。
   所有半透明像素超亮：胶囊轮廓 AA 渐变坡被抬成硬白边（="外缘灰线
   锯齿"）、干底 87% 白渲染成近实心白板、白 pill/hover 洗色全部过曝。
   修复 = `OverlayWindow.PremultiplyAlpha`（LockBits 逐像素 rgb×a/255，
   不透明行跳过），三处 ULW 上屏（`OverlayWindow.PushLayered` /
   `EdgeStrip.PushGhost` / `CornerMask.PushGhostBitmap`）全部必经。
2. **字形锯齿**：`TextRenderer.DrawText` 是 GDI 文本、alpha 盲——在
   分层位图上灰度 AA 塌缩成硬锯齿。换 GDI+ `DrawGlyph`（`DrawString` +
   `TextRenderingHint.AntiAliasGridFit`，GenericTypographic 紧致测宽
   居中，光学位置与 TextRenderer 持平）。
3. **配方 v2（去烟熏）**：tint 82%→55% 白、饱和 1.15→1.20、干底
   88%→65%、胶囊 hairline 黑 14%/27%→白 35%/63%（固定态加深的指标
   同步换轨）。

真机验收注意：通透后墨色字形对比依赖 1:8 模糊把暗场景极值凝结，
暗视频场景（全屏黑边播放）下检查字形可辨；premultiply 修复使白
pill、hover 洗色、干底首次按真实 alpha 渲染（此前全部偏亮），
采样/干底/悬停/固定四态都要过一遍；125%/150% DPI 凑近看胶囊轮廓
与字形边缘是固定动作。

### 顺带修复：mirror/fixed 会话关闭键无红 hover

旧 `DrawHoverFill` 按 `Kind == 5` 判关闭键——只有 flex 四键布局的关闭键
是 Kind 5；mirror/fixed 三键布局关闭键是 Kind 4，从未拿到 Win11 红
`#E81123` hover。改为 `NavButton.Danger` 显式标记（构造时最后一个槽位），
其余键的 hover 洗色同步换为 hoverWash 4% 黑（浅玻璃上白洗不可见）。

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

## §13 下巴材质统一 + 玻璃材质总开关（2026-09-12，claude-opus-4-6 裁决）

### 下巴 native 毛玻璃统一（与胶囊/菜单同族）

用户要求下巴与上巴、右键菜单玻璃同族。Opus 判定（唯一实现口径，
`ChinWindow.BuildChinFrost`）：

| 参数 | 值 | 依据 |
|---|---|---|
| 模糊 | Kovesi 3×box 真高斯，**σ = 10 DIP×DPI**；性能路径 **1:2 预降采样**（σ_down = σ/2 → 2× 双三次升采样与矩阵应用同 pass 融合） | 通栏 ~1920px 远宽于胶囊 ~150px，σ6 模糊力度不足；σ10 才达到胶囊 σ6 的「读不出内容」观感。1:2 路径核心像素循环 ~4ms/帧（全分辨率 ~15-25ms 逼近预算），降采样等效预模糊 <0.3% 可忽略 |
| 采样 | CopyFromScreen（条 bounds + **3σ = 30 DIP margin** overscan；条自身覆盖行仍用邻带替换防自反馈；屏幕边缘被 VirtualScreen 裁剪时钳位） | PrintWindow 不适用（条下是桌面非视频窗）；3σ 覆盖高斯能量 99.7% |
| 节拍 | 300ms 心跳（SampleMs 220→300，与胶囊一致） | 桌面变化频率低于视频 |
| 亮度判据 | **原始采样**（模糊前、邻带替换后）中心区 BT.709 均值，0.50±0.04 迟滞 | 与胶囊同源（旧判据是矩阵输出亮度，废）；药丸/矩阵双态/顶光全部跟随同一 `_barDark` |
| ColorMatrix 双态 | 暗态 scale 0.90 / lift +0.02 / Matrix33 α0.90；亮态 0.98 / +0.10 / α0.86（真活底：α<1 让屏幕物理透入 10%/14%） | 与胶囊完全同值——大面积下偏差感知更敏感，独立微调反破坏家族感 |
| 顶光渐变 | 暗 2.4%→0.4% / 亮 8%→1.5%（纵向，跨 32DIP 矮条 = 顶缘受光） | 与胶囊同值 |
| 描边 | **静止态零描边**（旧常驻 hairline 10% 黑/22% 白废除） | 与胶囊「纯粹毛玻璃」定稿一致 |
| 干底 | #1C1C1E@92%（DryGlass，与胶囊共享常量） | CopyFromScreen 失败（锁屏/UAC）回退 |

Opus 预判的翻车点与对策：LockBits stride 寻址（现有 ReadPixels32 统一
stride）；预乘安全（PremultiplyAlpha 保证 R≤A，结构性满足）；活底不得用
两次 DrawImage 叠加（α 分离由 Matrix33 单 pass 完成）；屏幕底边无邻带时
邻带替换退化为上带（现状保留，σ10 平滑充分）。

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

### 终审修订（2026-09-12，claude-opus-4-6 终审 × 人工核对）

- **MUST-FIX #3（已修）**：BuildChinFrost 的 2× 升采样源矩形必须取整到
  半分辨率像素网格（分数 DPI 下 mx/2 落在 .5 像素，bicubic 4×4 核会采到
  margin 填充行 = 底边 1px 暗带）。
- **MUST-FIX #7a（已修）**：面板侧 theme 的 colorSchemeChanged 接线改为
  生命周期管理——进 system 模式接线、离开拆线（`_wire_system_scheme`）；
  回调内再守卫 `_theme_mode != "system"` 双保险。中途切到 system 不再
  丢失实时跟随（回归测试 test_theme_state_and_apply_theme_wiring）。
- **#5（终审意见被否，保留实现）**：终审要求 native 第 4 键改随
  `--bar-theme`，但其前提误读了几何（第 4 键骑在**系统自绘 caption** 上，
  不在下巴上）——caption 底材由 Windows 按系统主题绘制，字色必须跟系统
  注册表色（面板暗 + 系统亮时 caption 仍是亮的）。保留
  `Controller.SystemThemeDark`，代码注释存档本结论。
- **#7b（文档级，已注明）**：下巴管线全程直通 alpha（预乘只在
  PushLayered 一次完成），ColorMatrix 的 scale/lift 即非预乘语义；
  BuildChinFrost 注释存档。
- **PASS 确认**：暗色卡填充极性（白 10% 抬升面）、暗色菜单玻璃参数
  （bright +0.05/+0.07、contrast +0.08/+0.10）、玻璃开关的「菜单即时 /
  窗口栏 per-launch」不同步性（独立进程 argv 语义，与窗口栏模式一致）、
  音量图标 ink2 4.7:1 与未知态滑块无拇指（均保留）。
