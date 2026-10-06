# 投屏质量

> settings 投屏质量项的设计记录。真机：OPPO TESTPAD / SM8750P / Android 16 / scrcpy 4.1。
> 卡顿诊断：会话日志带 `--print-fps`，设备端 fps 掉=编码侧，fps 正常画面卡=PC 解码侧。

## 1. 编码器（探测缓存 `encoders.json`，TTL 7 天）

| codec | 设备端（实测） | PC 端（纯软解） | 结论 |
|---|---|---|---|
| h264 | `c2.qti.avc.encoder` 硬编 | AVC 解码远轻于 HEVC | **auto 首选 ✅**（2026-09-06 反转：PC 端是瓶颈，AVC 优先） |
| h265 | `c2.qti.hevc.encoder` 硬编（勿钉 `.cq`/`.hdr` 变体） | 软解明显重于 AVC（看视频曾卡顿） | 手动选 |
| av1 | **无硬编**（仅 `c2.android.av1.encoder` 软编） | 需新硬件 | 探测到硬编才可选 |

auto 优先级（`codec.resolve_codec`）：h264 硬 > h265 硬 > av1 硬 > h264 软 > h264 默认。
探测失败 → h264 不钉 encoder，不阻塞开会话。

## 1b. 硬件解码（scrcpy 5.0，2026-10 适配）

scrcpy 5.0（2026-10-05）新增电脑侧硬解，默认开启（`--hwdec=auto`：能硬解
则硬解，失败自动回退软解），官方标称 CPU 占用约降 10×——直接缓解上表
「PC 端是瓶颈」的结论文曾压低 h265/av1 优先级的局面。

Duo 接入（`settings.hwdec`，投屏质量卡「硬件解码」三态）：

- `auto`（默认）：**不发射任何旗标**——5.0 上即上游默认硬解，且 4.1
  安装不报 unknown option（`--hwdec` 是 5.0 新选项，向上兼容靠沉默）；
- `disabled`：发射 `--hwdec=disabled`，强制软解（硬解驱动异常/观感排障
  用；软解现也成为可长期驻留档，上游 auto 已承担回退）;
- `d3d11va`：发射 `--hwdec=d3d11va`，钉死 Windows D3D11 后端（auto 未
  命中硬解时的手动兜底）。

发射逻辑在 `engine.rs::to_argv`（仅非 auto 值出旗），装配在
`mirror.rs::build_engine_args`。硬解命中与否看会话日志的 hwdec 行。

真机证据（2026-10-06，无线会话）：`scrcpy 5.0` + auto（零旗标）
→ `Renderer: direct3d11` / `Interop: d3d11va` / `Video decoding: d3d11va`，
flex 虚拟屏 1920x1080 (id=3) 60fps。

## 2. 帧率（120Hz 面板基准）

fps 必须与面板**整除**，否则 judder：**60 = 默认**（视频 1:1、解码减半）；
120 = 极致动画档（需 PC 软解余量）；40/30 = 省电；**90 禁用**（不整除，旧默认已改）。

## 3. 音频（settings.audio_policy，默认 latest）

scrcpy 捕获**全局混音**——多会话各带音频必重叠。零损失并行 = 多应用进同一虚拟屏
（`am start --display N` 直达）。策略三态：`latest`（新会话有声时其他自动静音重启，
面板侧 proc.terminate→muted 重启）/ `all`（自担混音）/ `off`。
编码 flac + `--audio-buffer=100` + `--audio-output-buffer=10`（防回退 5ms 爆音）。
仲裁锁 `audio.lock`：
- Windows 侧利用 `OpenProcess` + `WaitForSingleObject` 判定 PID 真实存活，并校验
  进程映像名防 PID 回收重用；避免陈锁把后续会话永久判为静音（退回平板物理外放）。
- `AudioLock` 实现 RAII `Drop`；`duo-panel` 在 terminate/stop 旧音频会话时联动清理锁。
调研过程与备选方案存档：`docs/history/research-audio.md`。

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
docs/history/window-overlay-experiments.md §3。

**渲染倍率 `render_scale`（2026-09-11 回归，接替 2026-09-06 撤除的
`flex_resolution` 档位）**：语义从「选档」改为**窗口÷倍率**（用户定稿：
4K 窗口 ÷2 = 1K 渲染，即倍率作用在线性尺寸）。范围 1.0–3.0 自由取值（预设 1/1.4/2）
（预设 1/1.4/2/3 + 0.1 步进微调），设置页默认 + 右键菜单按应用覆盖（gui_prefs `scale` 节）。

实现路径（唯一正确解，scrcpy 4.1 原生限制下）：倍率 >1.0 时 flex 会话
**换算成固定屏** `--new-display=工作区÷k`（`monitor.apply_render_scale`），
窗口比例锁 + 客户端放大 = 零失真零黑边；固定比例会话不叠加（自带几何）。
整数契约：每维 round 后奇数上调到偶（`aspects.scaled_size`，奇数显示高度
易踩编码器/窗口整数配置），两维独立取整的 ≤1px 比例漂移由比例锁兑底。

## 5b. 虚拟屏密度（2026-09-11 默认改桌面档；2026-10-06 flex 自适应）

`settings.dpi` 默认 **160**（mdpi 基准，1dp==1px，同屏 dp 最多 = 桌面观感；
此前默认设备密度探测，元素物理尺寸同手机/平板）。「跟随设备」仍是可选项
（显式 `"dpi": null`，老文件不静默翻语义；探测在 CLI `_run_mirror`），
按应用覆盖在右键菜单「DPI ▸」（gui_prefs `density` 节，120–640 自由数值）。

**flex 显示器自适应默认（2026-10-06，4K 真机反馈「像素太多」）**：出厂
160 是按 1080p 短边标定的（~1080dp 画布）；flex 像素跟随窗口，高分屏
最大化时 dp 同倍膨胀（4K 实测 2096dp → 内容相对窗口过小）。仅当无
`--dpi` 且设置为出厂默认时，按主屏工作区短边自适应
（`display_adaptive_dpi`：dpi = 160×短边px÷1080，1080p=160 零变化、
2K≈201、4K≈304-309）。优先级：--dpi 钉扎 > 自定义设置 > 跟随设备
（null+探测成功）> flex 自适应；fixed 模式不介入（平行视窗保障独立）。
真机：4K 工作区 3840x2088 → 309，最大化画布 ~1088dp。
回退链：设置页/覆盖 > 设备探测（仅 null）> 160。

## 6. PC 端解码与长期策略

scrcpy PC 端跨平台使用软件解码，没有可直接开启的 GPU 解码旗标。自研客户端与
fork scrcpy 的 D3D11VA 后端均不纳入当前计划；继续使用已验证的低风险组合：h264
硬件编码 + 60fps + 合理分辨率。出现卡顿时优先查看 `--print-fps` 日志，再按实际
设备和 PC 性能调整编码器、码率或帧率。
