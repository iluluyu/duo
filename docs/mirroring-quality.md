# 投屏质量：探测数据与底层旗标调研

配套代码：`duo/core/codec.py`（探测/缓存/择优）、`duo/core/settings.py`
（`audio_policy` / `video_codec` / `turn_screen_off`）、
`duo/core/engine.py`（EngineArgs 编译）。本文是 settings 投屏质量各设置项的
设计记录 + scrcpy 4.1 性能相关旗标的逐项采纳/拒绝结论。所有“实测”均在真机
OPD2409 上完成（2026-09-06）。

## 1. 真机编码器探测结果（scrcpy --list-encoders）

设备：OPPO OPD2409，Android 16，SoC SM8750P（骁龙 8s Gen 4，`c2.qti.*` 证实
高通编码器栈），面板 2400x3392。`scrcpy --list-encoders --serial=4444bd6b`
实测输出（视频部分，去掉别名行的重复）：

| codec | encoder | 硬件 |
|---|---|---|
| h264 | `c2.qti.avc.encoder` | ✅ hw |
| h264 | `c2.android.avc.encoder` | sw |
| h265 | `c2.qti.hevc.encoder`（另有 `.cq` / `.hdr` 变体，均 hw） | ✅ hw |
| h265 | `c2.android.hevc.encoder` | sw |
| av1  | `c2.android.av1.encoder` | ❌ 仅 sw |
| vp8 / vp9 | `c2.android.*` | sw |

要点：

- **h265 硬编在**（`c2.qti.hevc.encoder`），auto 模式因此落在这个档位；
  不钉 encoder 时 scrcpy 的默认选择可能落到 `.cq`/`.hdr` 变体，钉基础款
  语义最稳。
- **av1 硬编无**：只有软编 `c2.android.av1.encoder`。这直接验证了设计
  约束"av1 若有硬件编码器才可选"——在这台设备上显式选 av1 会降级回 h264。
- OMX.* 行是 `c2.qti.*`/`c2.android.*` 的别名（`--list-encoders` 标注
  `alias for`），解析时跳过，避免钉到重复项。
- 探测结果缓存于 `data_dir/encoders.json`（含 `probed_at` 时间戳 +
  `serial`），TTL 7 天；换设备（serial 不同）、过期、文件损坏都会重新探测；
  探测失败（设备未连/超时）→ 回退 h264 且不钉 encoder，绝不阻塞开会话。

## 2. 多会话音频策略（settings.audio_policy）

### 多音频同时不降质的正解：多应用进同一虚拟屏

scrcpy 捕获的是**设备全局混音**：两个会话各自捕到同一份混音，同时播放必然重叠/回声——这是捕获模型限制，与码率/音质无关。真正零损失的并行音频是**两个应用放在同一个虚拟屏会话**（任务 7 的直达机制：运行中点另一应用磁贴 → `am start --display N`）：Android 自己混音、单路串流，天然多应用音频且无损。多开应用请优先用这个用法，而不是多会话并行音频。

现状问题：两个 flex 会话各带音频 → 设备混音被两路捕获争抢，结果嘈杂。

三态设计（默认 `latest`）：

| 值 | 行为 |
|---|---|
| `latest` | 新会话带音频启动时，其他运行中的音频会话**自动重启为 --no-audio** |
| `all` | 不做单音频仲裁，全部会话出声（用户显式要求，自担混音） |
| `off` | 全部 `--no-audio` |

**latest 的最终实现方式与理由**——两级配合：

1. **面板侧（真正完成切换的地方）**：`PanelController` 是会话子进程的唯一
   持有者，重启用的是与 stopSession/startSession 完全相同的机制：
   `proc.terminate()`（CLI 的 SIGTERM 处理器在退出时释放音频锁）→
   `wait(5s)` → 用原 `build_launch_argv`/`build_device_mirror_argv`
   加 `muted=True`（追加 `--no-audio`）重新 spawn。顺序上先重启旧会话、
   后启动新会话，保证新会话 spawn 时 `audio.lock` 已空、能干净拿到音频。
   面板把每次 spawn 的会话是否带音频记在 `_audio_keys`，重启只翻动这个集合。
2. **CLI 侧（独立 `duo mirror` 运行的兜底）**：无面板持有权时无法重启别的
   窗口，退化为现有的 `AudioLock` 先到先得——新会话拿不到锁则静音并在
   日志打印原因。这就是设计降级路径"新会话禁止并行音频+提示"。

策略在每次启动时**新鲜读取** settings.json：面板决策与 CLI 决策读同一个
文件，不会在启动瞬间分叉。状态栏回报重启结果（"X、Y 已静音重启"）。

音频编码保持 `flac`（无损、USB 带宽廉价）+ `--audio-buffer=100`（见 §4）。

## 3. 镜像时关闭设备屏幕（settings.turn_screen_off，默认 false）

- EngineArgs 按此加/不加 `--turn-screen-off`；CLI 的 `--no-screen-off`
  作为显式覆盖（旗标 > 设置，与 fps/bitrate 的优先级一致）。
- 默认 **false**（不关屏）。该开关的意义是**黑屏防误触**，主要对
  mirror（整机镜像）有意义；**虚拟屏会话本就与物理屏无关**，关屏对
  flex/fixed 只是省电与防误触。设置页注明这一点，避免用户以为虚拟屏
  依赖物理屏。
- 注意与 `--stay-awake`（保持开启，防 USB 断连休眠）并存：关屏显示与
  CPU 保持唤醒是两回事，scrcpy 两者正交支持。

## 4. scrcpy 4.1 性能旗标逐项调研

结论表（✅ 采纳 / ❌ 拒绝 / ➖ 不需要）：

| 旗标 | 结论 | 理由 |
|---|---|---|
| `--max-fps` | ✅ 已内建 | fps 设置（1–240，默认 90）经它传递。官方支持 Android 10+；SM8750P 实机 90fps 正常。虚拟屏上它限制的是合成帧率，对流畅度足够。 |

### 动画流畅度（设备端流畅、串流只有几帧的归因）

该症状的典型根因是**软编**：未选择编码器时 scrcpy 可能落到 `c2.android.*` 软编，2.4K 分辨率下全屏重绘的动画会把它打到个位数帧。本版起 auto 默认钉硬件编码器（h265硬 > h264硬），大概率直接根治。复测步骤：设置里切 video_codec=auto 后开一个动画重的应用（如启动器翻页），观察是否仍掉帧；仍掉帧再依次调：fps→120 对齐面板、bitrate→40+、`--max-size 1920` 降负担（USB 带宽紧张时）。硬编 vs 软编的帧率对比若需量化：`adb shell dumpsys media.codec | grep -A2 "c2.qti"` 观察实时负载，或 scrcpy 日志的 fps 统计行。
| `--video-buffer=ms` | ❌ 拒绝 | 4.1 help 原文："增加延迟以补偿抖动"。USB 有线链路抖动小，Duo 面向低延迟操作感，默认 0ms 是对的。仅录制场景才有价值。 |
| `--display-buffer` | ➖ 不存在 | scrcpy ≥2.x 已移除（4.1 help 无此项、传参报 unknown option）。旧资料里对它的讨论一律改读 `--video-buffer`。 |
| `--audio-buffer` | ✅ 已内建 | 默认 50ms 在实机上会 crackle，Duo 实验定档 **100ms**（engine.py 注释即此结论）；flac 无损下 100ms 延迟无感知问题。 |
| `--video-source` | ➖ 不需要 | `display` 本来就是默认值，显式传是冗余；`camera` 与 Duo 场景无关。 |
| `--no-video-playback` | ❌ 拒绝 | Duo 的产品就是"看到画面"；该旗标用于录制/v4l2 无头输出，开了就没窗口了。 |
| `--max-size`（`-m`） | ⏸ 暂不默认，留作降级档 | 见下方带宽分析：瓶颈不在 USB 带宽而在编码吞吐。对 2400x3392 的整机镜像，`-m 1600` 像素量降到 ~44%（2400→1600），编码器负载与 PC 解码负载同步下降；但 flex 虚拟屏由 `--new-display` 定分辨率（通常 ≤1600 级别），本就吃不到这条旗标。**只有在低端 PC 上 mirror 模式解码吃紧时**才值得加，做成后续可选项而非默认。 |
| `--video-orientation` | ❌ 已移除 | **实测**：scrcpy 4.1 传 `--video-orientation=0` 报 `unknown option`（本机 2026-09-06）。替代品是 `--display-orientation`/`--orientation`。Duo 不需要：mirror 跟随设备旋转（scrcpy 自动），flex 的方向由 `--new-display=WxH` 定义。 |
| V4L2（`--v4l2-sink`） | ❌ 不可用 | 4.1 help 明确 "only available on Linux"；Duo 目标平台 Windows 10/11，直接排除。 |
| `--turn-screen-off` | ✅ 已内建 | 见 §3，settings 开关控制。 |

**USB 带宽影响（--max-size 对 2K 屏的实际收益）**：
镜像码率由 `--video-bit-rate` 决定（默认 30 Mbps ≈ 3.75 MB/s），USB2
有效吞吐 ~35 MB/s（约 280 Mbps），**带宽占用不到 15%**，所以 `--max-size`
不是为了省 USB 带宽。它的真实收益在两端算力：设备端编码器逐帧处理的像素
量（2400x3392 → 1600x2261，像素 -56%）与 PC 端解码/GPU 上传量同比例下降。
90fps 高刷 + 老机器时这是有效的降载手段；正常配置下不加，保留原生分辨率。

**h264 / h265 / av1 在 Snapdragon 平台的解码端成本**（含设备端编码方向，
两侧都列因为 codec 选择同时影响两端）：

| codec | 设备端（编码，Snapdragon） | PC 端（解码） | 结论 |
|---|---|---|---|
| h264 | 专硬编（`c2.qti.avc.encoder`），成熟低耗 | 近十年任何 GPU 均硬解 | 兼容兜底档 ✅ |
| h265 | 专硬编，同画质比 h264 省 ~30–50% 码率；30Mbps 下画质显著优于 h264 | 任何近十年 GPU 硬解（HEVC 覆盖率与 H.264 相当） | **默认档 ✅**（auto 首选） |
| av1 | SM8750P **无硬编**（实测 sw-only）；软编 2.4K@90fps 会吃满大核、发热掉帧 | 需 RTX30+/Intel 11th+/RDNA2+/骁龙 X 才硬解；老 PC 软解吃满 CPU | 双端都不利，**仅在探测确认硬编时可选** |

## 5. 编码器选择与码率建议（settings.video_codec / bitrate_mbps）

auto 优先级（`duo.core.codec.resolve_codec`，探测缓存命中时零开销）：

```
h265 硬件 > h264 硬件 > av1 硬件 > h264 软件 > h264（scrcpy 默认）
```

- 硬件命中时同时钉 `--video-encoder=<名>`（从探测结果取，跳过 `.cq`/`.hdr`
  变体）；探测失败 → h264 且不钉，会话照常启动。
- 显式 `h264`/`h265` 无硬编时保持该 codec 不钉；显式 `av1` 无硬编时降级
  h264（绝不让设备软编 av1）。
- 码率沿用 `bitrate_mbps`（默认 30），各 codec 建议区间（2400x3392 级别
  面板）：

| codec | 建议区间 | 说明 |
|---|---|---|
| h265 | 12–30 Mbps | 30 = 高画质档；12–20 已很干净 |
| h264 | 20–40 Mbps | 同画质比 h265 费 ~1.5 倍码率 |
| av1（仅硬编） | 8–20 Mbps | 压缩效率最高，当前无实机硬编可验证 |

注意 USB2 下 40 Mbps 依然宽松（<15% 带宽），码率上限更多受编码器吞吐与
热设计约束而非线材。

## 5. 帧率设计（120Hz 面板基准）

采集 fps 必须与面板刷新率**整除**，否则节拍不均（judder）：120Hz 下 90fps 会以 2:1 交替间距抽帧，观感即"卡"。推荐表：**60 = 通用默认**（60fps 视频 1:1、动画良好、解码负担减半）；120 = UI 动画极致（需 PC 软解余量）；40/30 = 低配与省电；**90 禁用**（不整除）。默认值已从 90 改 60（2026-09-06，曾致节拍抖动）。

## 6. PC 端 GPU 解码：现状与取舍

scrcpy PC 端为跨平台纯软解（libavcodec + SDL 内存帧），无 GPU 解码旗标。自建 backend 三档评估：fork 加 D3D11VA（高维护成本，弃）；自研客户端（scrcpy-server + socket 协议 + D3D11VA + QML 渲染，即 plan.md 长期目标“宿主自拥有视频表面”，收益含 GPU 解码/真 AA 圆角/窗口嵌入，需单独立项）；**当前最优 = 喂饱软解**：h264 + 60fps，flex 虚拟屏一律原始分辨率（2026-09-06 用户决策，见 §7），现代 CPU 无压力。

## 7. flex 虚拟屏尺寸来源（撤档决策）与日志 fps 诊断

### 进应用动画卡顿的确诊、修复与撤档

确诊（2026-09-06）：flex 会话拼裸 `--new-display`（无尺寸），scrcpy 会把
虚拟屏建成**主屏全尺寸**——2400x3392 面板上开会话，虚拟屏即 2400x3392。
全屏动画（应用内翻页、列表惯性滚动）时设备端硬编与 PC 端软解**双端吃满**，
进应用动画必卡。当日先以 `settings.flex_resolution` 基准档位
（1440p/1080p/native，engine.py `FLEX_SIZES`）修复卡顿；同日用户决策
**撤除档位**：三档下拉选择造成困扰，flex 一律**原始分辨率**——裸
`--new-display`（有自定义 dpi 则 `--new-display=/dpi`）+ `--flex-display`，
流畅度由 `video_codec=h264` + `fps=60` 承担（见 §5/§6）。档位机制的全部
代码与设置项已删除；旧 settings.json 里残留的 `flex_resolution` 键被
无害忽略（`_sanitize` 只读已知键）。

- flex 仍跟随窗口缩放（one-way follow：窗口驱动虚拟屏，反向绝不移动
  窗口）；mirror（整机镜像）永远用物理屏原分辨率。

### 用日志 fps 行诊断卡顿

会话默认带 `--print-fps`（engine.py `print_fps`）：scrcpy 周期（约每秒）把
fps 行打到 stderr，会话日志本就在收集——面板管理的会话在
`data_dir/logs/panel-<包名>.log`，CLI 会话在 `data_dir/logs/<时间戳>-<应用>.log`。
卡顿复现时打开日志看 fps 行，二分定位瓶颈在哪一端：

- **设备端 fps 明显掉**（如 90→个位数）：瓶颈在**编码/采集侧**。依次调：
  `fps` 降档（60→30）、`bitrate_mbps` 降档；确认
  `video_codec=auto` 钉到的是硬件编码器（软编必卡，见 §1）。
- **设备端 fps 正常但画面卡**：瓶颈在 **PC 解码侧**。调法：降
  `fps`、换更强的 PC；软解下 HEVC 明显重于 AVC，可显式切
  `video_codec=h264`（见 §6）。
