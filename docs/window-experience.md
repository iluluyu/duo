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
3. overlay 钉扎（`EnforceFlexPin`）：任何外部改窗弹回用户矩形；豁免=拖拽中
   （`_moving/_resizing`/左键按住）；收编=松手 1.5s 内的新矩形。

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
