# 投屏质量

> settings 投屏质量项的设计记录。真机：OPPO OPD2409 / SM8750P / Android 16 / scrcpy 4.1。
> 卡顿诊断：会话日志带 `--print-fps`，设备端 fps 掉=编码侧，fps 正常画面卡=PC 解码侧。

## 1. 编码器（探测缓存 `encoders.json`，TTL 7 天）

| codec | 设备端（实测） | PC 端（纯软解） | 结论 |
|---|---|---|---|
| h264 | `c2.qti.avc.encoder` 硬编 | AVC 解码远轻于 HEVC | **auto 首选 ✅**（2026-09-06 反转：PC 端是瓶颈，AVC 优先） |
| h265 | `c2.qti.hevc.encoder` 硬编（勿钉 `.cq`/`.hdr` 变体） | 软解明显重于 AVC（看视频曾卡顿） | 手动选 |
| av1 | **无硬编**（仅 `c2.android.av1.encoder` 软编） | 需新硬件 | 探测到硬编才可选 |

auto 优先级（`codec.resolve_codec`）：h264 硬 > h265 硬 > av1 硬 > h264 软 > h264 默认。
探测失败 → h264 不钉 encoder，不阻塞开会话。

## 2. 帧率（120Hz 面板基准）

fps 必须与面板**整除**，否则 judder：**60 = 默认**（视频 1:1、解码减半）；
120 = 极致动画档（需 PC 软解余量）；40/30 = 省电；**90 禁用**（不整除，旧默认已改）。

## 3. 音频（settings.audio_policy，默认 latest）

scrcpy 捕获**全局混音**——多会话各带音频必重叠。零损失并行 = 多应用进同一虚拟屏
（`am start --display N` 直达）。策略三态：`latest`（新会话有声时其他自动静音重启，
面板侧 proc.terminate→muted 重启）/ `all`（自担混音）/ `off`。
编码 flac + `--audio-buffer=100`（50ms 实机 crackle）。

## 4. 其他旗标结论

- `--turn-screen-off`：settings 开关（默认 false，黑屏防误触，虚拟屏仅省电意义）；
  与 `--stay-awake` 正交并存。
- `--video-buffer`：拒绝（增加延迟，USB 链路抖动小）。
- `--max-size`：**flex 会话禁用**（2026-09-11 源码定论）：server 端
  `NewDisplayCapture.requestResize` 对 flex 是**逐维钳制**（constrain
  preserveAspectRatio=false），4K 16:9 窗口 + `-m 1920` → 1920×1920 方屏
  → stretched 渲染比例拉歪；镜像会话低端 PC 解码吃紧时可用
  （逐维但比例保持，另有 letterbox 兑底）。
- `--video-orientation`：已移除（4.1 报 unknown option）。
- V4L2：Linux only，排除。

## 5. 虚拟屏尺寸与渲染倍率（2026-09-11 定稿）

应用会话 = `--new-display=<初始形状>/<dpi>` + `--flex-display` 跟随窗口
（native 填满，unscaled；拖拽过渡 `--render-fit=stretched`）。流畅度由
h264 + 60fps 承担；历史实验（固定 2560x1440/480、三档、比例跟随）见
window-experience.md §3。

**渲染倍率 `render_scale`（2026-09-11 回归，接替 2026-09-06 撤除的
`flex_resolution` 档位）**：语义从「选档」改为**窗口÷倍率**（用户定稿：
4K 窗口 ÷2 = 1K 渲染，即倍率作用在线性尺寸）。范围 1.0–3.0 自由取值（预设 1/1.4/2）
（预设 1/1.4/2/3 + 0.1 步进微调），设置页默认 + 右键菜单按应用覆盖（gui_prefs `scale` 节）。

实现路径（唯一正确解，scrcpy 4.1 原生限制下）：倍率 >1.0 时 flex 会话
**换算成固定屏** `--new-display=工作区÷k`（`monitor.apply_render_scale`），
窗口比例锁 + 客户端放大 = 零失真零黑边；固定比例会话不叠加（自带几何）。
整数契约：每维 round 后奇数上调到偶（`aspects.scaled_size`，奇数显示高度
易踩编码器/窗口整数配置），两维独立取整的 ≤1px 比例漂移由比例锁兑底。

## 5b. 虚拟屏密度（2026-09-11 默认改桌面档）

`settings.dpi` 默认 **160**（mdpi 基准，1dp==1px，同屏 dp 最多 = 桌面观感；
此前默认设备密度探测，元素物理尺寸同手机/平板）。「跟随设备」仍是可选项
（显式 `"dpi": null`，老文件不静默翻语义；探测在 CLI `_run_mirror`），
按应用覆盖在右键菜单「DPI ▸」（gui_prefs `density` 节，120–640 自由数值）。
回退链：设置页/覆盖 > 设备探测（仅 null）> 160。

## 6. PC 端解码与长期策略

scrcpy PC 端跨平台使用软件解码，没有可直接开启的 GPU 解码旗标。自研客户端与
fork scrcpy 的 D3D11VA 后端均不纳入当前计划；继续使用已验证的低风险组合：h264
硬件编码 + 60fps + 合理分辨率。出现卡顿时优先查看 `--print-fps` 日志，再按实际
设备和 PC 性能调整编码器、码率或帧率。
