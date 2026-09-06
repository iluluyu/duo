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
- `--max-size`：不默认。flex 由 `--new-display` 定分辨率吃不到；mirror 低端 PC
  解码吃紧时再 `-m 1600`（像素 -56%，双端降载；USB2 带宽仅用 <15% 非瓶颈）。
- `--video-orientation`：已移除（4.1 报 unknown option）。
- V4L2：Linux only，排除。

## 5. flex 虚拟屏尺寸（2026-09-06 定稿）

应用会话 = **固定 `--new-display=2560x1440/480`**（原始分辨率/三档/flex 跟随均已试废，
原因见 docs/window-experience.md §3）。流畅度由 h264 + 60fps 承担。
分辨率档位设置（`flex_resolution`）已撤除，旧 settings.json 残键被无害忽略。

## 6. PC 端解码与长期策略

scrcpy PC 端跨平台使用软件解码，没有可直接开启的 GPU 解码旗标。自研客户端与
fork scrcpy 的 D3D11VA 后端均不纳入当前计划；继续使用已验证的低风险组合：h264
硬件编码 + 60fps + 合理分辨率。出现卡顿时优先查看 `--print-fps` 日志，再按实际
设备和 PC 性能调整编码器、码率或帧率。
