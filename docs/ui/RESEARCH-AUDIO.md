# 多会话并发音频爆音调研（2026-09-08）

> 调研 scrcpy 多会话并发音频时的爆破音/爆音问题与修复方案。
> 信息源：scrcpy GitHub issues/PR/releases、rom1v 博客（scrcpy 音频架构作者）、
> SDL 源码与 issue、QtScrcpy/escrcpy 社区、中文社区文章。
> 本文档只调研不改代码；Duo 现状仅作对照引用。
> 决策与规范见 DESIGN.md；姊妹篇：docs/mirroring-quality.md（画质/卡顿侧）。

## 0. 结论速览

1. **音频爆音的头号官方 issue 是 #3793**（"robotic" and glitchy sound，2023-03 提出，
   2026-04-17 由 PR #6775 修复关闭）：根因是 SDL 音频输出缓冲 5ms 低于任何
   Windows 后端实际支持的最小值（≥10ms）。修复 = `--audio-output-buffer=10`，
   且 **v4.0 起默认值已从 5ms 改为 10ms**。Duo 用的 4.1 已含此修复。
2. 任务提示中的 **#5859 与音频无关**——它是 `MediaCodec.dequeueOutputBuffer` 的
   Java 异常（视频 SurfaceEncoder 崩溃），2025-02 已关。编号应为记忆偏差。
3. 多实例并发音频爆音**没有单独的官方 issue 定级为 bug**：Android 允许多个
   scrcpy server 各自开 `AudioRecord(REMOTE_SUBMIX)`，但同一混音器被并发读取会
   争用（Duo `duo/core/audio_lock.py` 的 docstring 已实测记录该结论）。官方姿势
   是"多实例可行，但音频流各自独立"（#4206：一路 output、一路 mic）。
4. Android 侧：**submix 一被捕获，设备端外放即被系统强制静音**（#3875），且
   scrcpy 不参与音频焦点（AUDIOFOCUS 只管播放侧）。多会话"一有声一静音"在
   设备侧本来就是全有或全无，唯一正确架构是**同一时刻只允许一个进程带
   `--audio`**——Duo 现有 audio_lock 仲裁方向正确。
5. 交接/启动瞬间的 pop 属于路由切换 + 缓冲冷启动（scrcpy 无交叉淡入、无
   首样本丢弃开关），只能在宿主侧（Duo）做淡入或接受；持续 crackle 则有
   官方参数解（见 §5 推荐）。

---

## 1. 已知音频爆音 issue 地图

### 1.1 主 issue：#3793（已修复，进入 v4.0）

| 项 | 内容 |
|---|---|
| issue | ["robotic" and glitchy sound #3793](https://github.com/Genymobile/scrcpy/issues/3793)（2023-03-12 开，Closed） |
| 症状 | Win10/Android 12：实时播放声音机械、咔哒（clicks）、含杂质；`--record` 落盘的文件回放完全正常 → 问题在 PC 端播放链路而非采集/编码 |
| 官方结论 | rom1v 置顶批注：`scrcpy --audio-output-buffer=10`；并注明 "the default value is 10ms since #6775" |
| 修复 PR | [Set default audio-output-buffer to 10ms #6775](https://github.com/Genymobile/scrcpy/pull/6775)（2026-04-17 合并入 dev）：音频输出后端实际不支持 <10ms 的缓冲；设 5ms 时 SDL 回调每 ≥10ms 才被调一次却要连续补多次；5ms 在部分电脑上直接产生毛刺。关联上游 SDL issue [SDL#13319](https://github.com/libsdl-org/SDL/issues/13319)、[SDL#13397](https://github.com/libsdl-org/SDL/issues/13397) |
| 发布版本 | [v4.0 release notes](https://github.com/Genymobile/scrcpy/releases) 明确列出 "Set default audio-output-buffer to 10ms (#6775, #3793)"；v4.1 文档 [doc/audio.md](https://github.com/Genymobile/scrcpy/blob/v4.1/doc/audio.md) 写明默认 10ms 并链接 #3793 |

### 1.2 同族"robotic/crackling"变体（诊断话术与参数验证）

| issue | 环境 | 结论 |
|---|---|---|
| [#4055](https://github.com/Genymobile/scrcpy/issues/4055) Audio crackling really bad | Win、scrcpy 2.0 | rom1v 长评解释音频播放器两级缓冲模型，给出 `--audio-output-buffer=10` |
| [#4668](https://github.com/Genymobile/scrcpy/issues/4668) Audio cracking during tcpip | 无线 adb | 与 USB 无线带宽抖动相关；官方调参法：`--audio-buffer=1000` 起步确认无毛刺，再逐步下调找最小可用值 |
| [#4705](https://github.com/Genymobile/scrcpy/issues/4705) / [#6081](https://github.com/Genymobile/scrcpy/issues/6081) | 多机型 | 机械音复发咨询；回答仍是 output-buffer=10（#6081 那台设备另有采集侧问题） |
| [#6724](https://github.com/Genymobile/scrcpy/issues/6724) Audio Cracking, Robotic Sound | Win11 / Android 16 | rom1v 问诊顺序即官方推荐优先级：先试 `--audio-buffer=100`（加延迟），再试 `--audio-output-buffer=10`；用户确认后者即解决 |
| [#6835](https://github.com/Genymobile/scrcpy/issues/6835) | USB | 音质劣化个案，排查指南 |

### 1.3 多实例/并发相关

| issue | 内容 |
|---|---|
| [#4206](https://github.com/Genymobile/scrcpy/issues/4206) Hear audio and mic at the same time | 官方确认**两个 scrcpy 实例并发可行**（一个转发设备音频、一个 `--audio-source=mic`）——即多实例音频在"各占不同源"时合法 |
| [#6907](https://github.com/Genymobile/scrcpy/issues/6907) Audio glitches and instability W11（open，2026-06） | Win11 + scrcpy 4.0 + Android 16：单实例音视频同开必爆音，换 codec/缓冲/fps 无效；**拆成两个进程后完全稳定**（视频 `--no-audio`；音频 `--no-video --audio-source=output --audio-codec=aac --audio-buffer=400 --audio-output-buffer=100`）。对 Duo 最有参考价值：进程拆分 + 音频侧加大两级缓冲 = 社区实证有效组合 |
| [#897](https://github.com/Genymobile/scrcpy/issues/897) | 多实例并发打开的工程问题（batch 需 `START`），与音质无关但佐证多实例是支持场景 |
| Duo 实测 | `duo/core/audio_lock.py` 模块注释："Android lets several scrcpy clients capture the device mixer at once, but the captures contend and the result crackles"——多客户端并发采集同一 submix 必爆音，是 Duo 做锁仲裁的动因 |

### 1.4 播放侧欠载/过载的日志形态：#4268

[#4268](https://github.com/Genymobile/scrcpy/issues/4268)（Choppy audio）贴出完整
verbose 日志，是爆音在 scrcpy 内部机制上的两种形态（48kHz 下 480/960 样本 ≈
10/20ms）：

```
DEBUG: [Audio] Buffer underflow, inserting silence: 240 samples        ← 欠载→插静音
DEBUG: [Audio] Buffering threshold exceeded, skipping 480/960 samples  ← 过载→跳样本
VERBOSE: [Audio] Buffering: target=2400 avg=5400 compensation=-3000    ← swresample 时钟漂移补偿
```

"skipping N samples" 就是周期性爆音（咔哒）的直接来源；对应 rom1v 在
[Scrcpy 2.0 audio 博客](https://blog.rom1v.com/2023/03/scrcpy-2-0-with-audio/) 中
描述的播放器策略（见 §4.1）。

### 1.5 切换音频设备/音频服务器错误类

| issue | 内容 |
|---|---|
| [#3799](https://github.com/Genymobile/scrcpy/issues/3799) / [#3825](https://github.com/Genymobile/scrcpy/issues/3825) | Win7 WASAPI `CoInitialize has not been called`（SDL 缺陷 [SDL#7478](https://github.com/libsdl-org/SDL/issues/7478)，后续 SDL 版本已修）；临官方时解法 `set SDL_AUDIODRIVER=directsound` 或 `winmm` |
| [#3856](https://github.com/Genymobile/scrcpy/issues/3856) | 同上的官方回复里同时警告：**directsound 不支持低延迟音频**（引 #3793），切后端须配合更大输出缓冲 |
| [#3876](https://github.com/Genymobile/scrcpy/issues/3876) | `WASAPI can't find requested audio endpoint`：PC 默认输出设备切换/不存在时启动失败 |
| [#4111](https://github.com/Genymobile/scrcpy/issues/4111) | 系统无任何音频设备时启动崩溃 |

注意：这些是**启动失败**而非爆音，但与"切换设备"场景同根：scrcpy 启动时打开
当时默认的 WASAPI 端点，中途拔插/切换设备不在 scrcpy 的处理范围内（SDL 层行为）。

### 1.6 启动瞬间 pop

官方仓库**没有**专门针对"启动一瞬 pop"的 issue。可归因的机制（据 #3875 与
#3793 推断，无直接 issue 背书）：

- Android 侧：`AudioRecord(REMOTE_SUBMIX)` 建立/拆除 = 全局输出路由切换，混音器
  直流工作点突变且系统不做淡入淡出 → click（经典 DC 偏移爆音）；
- PC 侧：SDL 设备冷启动时 scrcpy 目标缓冲（50ms）尚未填满，首段欠载插静音
  （#4268 日志形态），静音→有声边界即毛刺。
- scrcpy 无交叉淡入/首样本丢弃参数（§4.1），宿主侧掩盖是唯一手段。

---

## 2. 参数与 Windows 音频后端

### 2.1 缓冲/编码参数（v4.1，[官方 doc/audio.md](https://github.com/Genymobile/scrcpy/blob/v4.1/doc/audio.md)）

| 参数 | 默认 | 作用 | 对爆音的影响 |
|---|---|---|---|
| `--audio-buffer` | 50ms | 播放器**目标**缓冲（样本池水位） | 抗欠载主力；调大平滑但加延迟。欠载时实际可能达不到目标。#6724/#4668 建议排查值 100→1000 |
| `--audio-output-buffer` | **10ms（v4.0 起；v2.x~v3.x 为 5ms）** | SDL 音频**输出设备**缓冲 | #3793 的官方解；文档明示"没有好理由不要改"（<10ms 后端不支持） |
| `--audio-codec` | opus | 可选 `opus`/`aac`/`flac`/`raw`(PCM16) | 编码差异一般不致爆音，但：opus 有历史坑——解码出的"静音"是 denormal 小数导致重采样慢 40×、CPU 飙升（v4.0 换 FFmpeg 8.1.1 修复，[#6715](https://github.com/Genymobile/scrcpy/issues/6715)）；flac 无损软编、CPU 稍高；raw 零编码可排除编码因素（USB 带宽够时） |
| `--audio-bit-rate` | 128K | 仅对 opus/aac 有效 | 与爆音无关，只影响音质/带宽 |
| `--audio-dup` | 关 | `--audio-source=playback`（Android 13+，Audio Policy API）下设备端**继续外放**同时转发（[#4380](https://github.com/Genymobile/scrcpy/issues/4380)/[#5102](https://github.com/Genymobile/scrcpy/pull/5102)） | 改变捕获语义：不抢 submix、不受设备音量/耳机影响；代价是 app 可 opt-out 不被采集 |
| `--audio-source` | output(=REMOTE_SUBMIX) | output/playback/mic 等 12 种（v3.2 起 #5870） | 多会话架构选型关键（§3、§5） |

### 2.2 后端选择：没有 `--audio-backend`，只有 `SDL_AUDIODRIVER`

- scrcpy **不存在** `--audio-backend` 参数（[man page scrcpy.1](https://github.com/Genymobile/scrcpy/blob/master/app/scrcpy.1) 全参数表中无此项）。
- SDL 的音频后端由环境变量 `SDL_AUDIODRIVER` 决定。rom1v 在 #3799 亲自给出用法：
  `set SDL_AUDIODRIVER=directsound`（或 winmm）后运行 scrcpy。
- **scrcpy 4.0 起迁移 SDL3**（#6216）。SDL3 移除了 winmm 后端
  （[SDL 提交记录](https://discourse.libsdl.org/t/sdl-removed-arts-esd-fusionsound-nas-paudio-sndio-sunaudio-winmm-audio-backends/40395)），
  Windows 上仅剩 **wasapi（默认）与 directsound**（[SDL 音频驱动清单](https://deepwiki.com/libsdl-org/SDL/4.4-audio-drivers-and-platform-support)）。
- **WASAPI 共享模式**：SDL 的 WASAPI 实现（[SDL_wasapi.c](https://github.com/libsdl-org/SDL/blob/main/src/audio/wasapi/SDL_wasapi.c)）走 `IAudioClient` 共享模式 + Avrt 把回调线程标记 "Pro Audio" 降延迟；**SDL 不支持 WASAPI 独占模式**，scrcpy 亦无法请求。
- directsound 是遗留兜底：#3856 官方确认其低延迟能力差，切它必须配合大输出缓冲（即同时更容易欠载，只适合"个别声卡 WASAPI 驱动有毛病"的机器做诊断/兜底）。

### 2.3 Duo 现状对照

Duo 引擎（`duo/core/engine.py`）当前编译出：`--audio-codec=flac --audio-buffer=100`
（注释 "kills crackling"，与 #6724 官方建议一致），未显式传
`--audio-output-buffer`（吃 4.1 的 10ms 默认，正确）。仲裁：`audio_policy=latest`
时 UI 面板 terminate 旧音频会话 → 等 SIGTERM 释放锁 → 以 `--no-audio` 重启旧会话
（`duo/ui/controller.py` `_restart_others_muted`）；独立 CLI 抢不到锁则直接静音
启动（`duo/__main__.py`）。即 Duo 已实现"任一时刻至多一个 `--audio` 进程"。

---

## 3. Android 侧：多实例采集的行为

1. **捕获机制**：scrcpy server 以 shell 权限建 `AudioRecord`，source=
   `REMOTE_SUBMIX`（Android 11+；Android 11 需假弹窗保前台）
   （[rom1v 博客](https://blog.rom1v.com/2023/03/scrcpy-2-0-with-audio/)）。
   submix 是**全局混音器级**的捕获：把整机输出"搬"给采集端。
2. **捕获即设备静音**：[#3875](https://github.com/Genymobile/scrcpy/issues/3875)
   中 rom1v 确认："As soon as the remote submix audio source is captured on
   Android, audio is automatically disabled on the device. I didn't find any
   way to configure this behavior." —— 所以"一有声一静音"不是 scrcpy 可调的
   per-session 状态，而是 submix 的天然语义；多会话各自想要独立音频在
   `output` 源下**不存在**。
3. **并发争用**：两个 scrcpy server = 两个 AudioFlinger 客户端并发读同一
   submix 设备。系统不报错（都能出声），但供给速率被分抢、路由反复重协商，
   表现即双方爆音——Duo `audio_lock.py` 实测结论与之吻合。#4206 的双实例
   稳定案例是"output + mic"两个**不同**源，不构成反例。
4. **音频焦点无关**：AUDIOFOCUS 是播放侧（AudioTrack/MediaPlayer）的协商机制；
   shell 的 AudioRecord 采集不申请焦点，焦点变化也不影响 submix 供给。多虚拟
   显示（`--new-display`）与音频采集互不相干：音频始终采整机混音，不存在
   "按 display 分流"。
5. **静音切换瞬间的直流偏移 pop**：submix 路由建立/拆除瞬间，输出流在 DAC/
   混音器工作点间跳变，系统不做 ramp——这是启动/停止/交接爆音的 Android 侧
   成因（通用音频工程问题；scrcpy/Android 均无开关，只能宿主侧淡入或接受）。
6. **替代路线（Android 13+）**：`--audio-source=playback`（Audio Policy API，
   [#4380](https://github.com/Genymobile/scrcpy/issues/4380)）：不抢 submix，
   天然支持设备端继续播放（`--audio-dup`），不受系统音量/耳机影响；多会话
   并发友好性更好（每个实例各自 capture playback，app 可 opt-out 是代价）。

---

## 4. 缓解实践（scrcpy 之外与社区）

### 4.1 scrcpy 内建策略（rom1v 播放器，不可配置）

[博客](https://blog.rom1v.com/2023/03/scrcpy-2-0-with-audio/) 明确设计取舍：
- 欠载 → **插入静音**（并明确拒绝"丢掉被静音替代的样本"，因为会加剧欠载、
  造成更明显的毛刺）；
- 过载 → 超过阈值时跳样本（#4268 的 skipping 日志）；
- 时钟漂移 → `swr_set_compensation()` 重采样补偿，不用 PTS。
结论：**没有交叉淡入、没有首样本丢弃、没有 glitch 掩蔽开关**——这些只能在
宿主（Duo 的 chrome/会话层）做，scrcpy 参数面上不存在。

### 4.2 社区实证配方

| 来源 | 配方 |
|---|---|
| [#6907](https://github.com/Genymobile/scrcpy/issues/6907)（4.0/W11/A16） | 拆进程：视频 `--no-audio`；音频独立进程 `--no-video --audio-buffer=400 --audio-output-buffer=100` |
| [#6724](https://github.com/Genymobile/scrcpy/issues/6724) | `--audio-output-buffer=10` 单参数解决（官方问诊首选） |
| [#4668](https://github.com/Genymobile/scrcpy/issues/4668)（tcpip） | `--audio-buffer=1000` 确认无毛刺后逐步下调 |
| [#3799](https://github.com/Genymobile/scrcpy/issues/3799)/[#3856](https://github.com/Genymobile/scrcpy/issues/3856) | 声卡驱动异常时 `SDL_AUDIODRIVER=directsound` 兜底（牺牲低延迟） |
| [scrcpyapp.org（中文）](https://scrcpyapp.org/guides/audio/) / [Escrcpy 中文文档](https://viarotel.eu.org/zhhans/reference/scrcpy/audio) / [CSDN 指南](https://blog.csdn.net/gitblog_00790/article/details/157671700) | 与官方文档同口径：`--audio-buffer` 40~1000、机械音才动 `--audio-output-buffer`、编码器不兼容换 aac；无线场景加大缓冲 |

### 4.3 其他镜像工具

- **QtScrcpy**（barry-ran）：**至今无内建音频转发**（[官方讨论 #446](https://github.com/barry-ran/QtScrcpy/discussions/446)），
  社区靠外挂 sndcpy 或蓝牙（[CSDN/FAQ 汇总](https://adg.csdn.net/696f44a4437a6b403369d0c4.html)）——
  即它对爆音问题是"绕开"而非"处理"，无可借鉴的音频实践。
- **escrcpy**（viarotel）：scrcpy 的 GUI 封装，音频完全透传 scrcpy 参数，
  无自有处理；其文档即 scrcpy audio.md 的翻译。
- 上游 sndcpy（playback capture API，scrcpy 前身实验）：机制对比见
  [#4954](https://github.com/Genymobile/scrcpy/issues/4954)（sndcpy 可设备+PC
  双出声，因为用的是 playback capture 而非 submix）。

---

## 5. 结论：给 Duo 的推荐（按可行性排序）

1. **【立即，零风险】显式钉住 `--audio-output-buffer=10`。**
   4.1 默认已是 10ms，但显式写出可防两点：将来升级/换版本时旧默认（5ms）回归；
   以及帮助日志排查时参数自证。同时保留现有 `--audio-buffer=100`（与 #6724
   官方首选一致）。两行 argv 的事，收益/成本比最高。

2. **【架构维持，收紧交接时序】任一时刻仅一个进程带 `--audio`，交接走
   "先全静、后开声"。**
   Duo 的 audio_lock + `_restart_others_muted`（terminate 旧音频会话→等锁释放
   →旧会话以 `--no-audio` 复活）已是对抗 Android submix 争用（§3.2/3.3）的
   正确架构，不要退回"多进程同时采集 + PC 端各自 mute"。可收紧处：确保新
   音频会话在旧进程**完全退出后**（锁确认释放）再启动，杜绝两个 AudioRecord
   短暂共存的交接窗口——那正是交接爆音的最大来源。

3. **【启动 pop 的处置分层】先用 `-V verbose` 日志定性，再决定治不治。**
   Duo 会话日志已收 scrcpy stderr：若看到 `[Audio] Buffer underflow/skipping`
   刷屏 → 缓冲问题，走 1/4；若仅启动一瞬 pop、日志干净 → 路由切换型
   （§1.6），scrcpy 参数无解，可在 Duo Windows chrome 层给会话音量做
   ~300ms 淡入（`ISimpleAudioVolume`），属可选增强。

4. **【诊断与兜底】准备两条备选参数路径。**
   a) 个别声卡 WASAPI 驱动毛刺：`SDL_AUDIODRIVER=directsound` 环境变量兜底
   （须配大 buffer，仅作用户机诊断项，不作默认）；
   b) 疑似编码因素时用 `--audio-codec=raw` 短时排查（USB 带宽足够），或按
   #6907 配方把音频独立成专用进程（`--no-video --audio-buffer=400
   --audio-output-buffer=100`）服务全部视频会话。

5. **【版本与 codec 策略】钉住 4.1，codec 维持 flac，多会话/CPU 紧张时降级。**
   4.1 已含：10ms 输出缓冲默认（#6775）、opus 静音 denormals CPU 修复
   （#6715，FFmpeg 8.1）、SDL 3.4.12——不要回退旧版。flac 无损 + USB 带宽
   富余是 Duo 现状的合理选择；若未来出现"一有声一静音"之外的并发音频需求
   （每个会话各自出声），评估 Android 13+ 的 `--audio-source=playback`
   （Audio Policy API，§3.6）作为下一代架构，而非放开多路 submix。

---

## 附：来源清单

**scrcpy 官方**
- #3793 robotic/glitchy sound：<https://github.com/Genymobile/scrcpy/issues/3793>
- PR #6775 默认 10ms：<https://github.com/Genymobile/scrcpy/pull/6775>
- Releases（v4.0/v4.1 changelog）：<https://github.com/Genymobile/scrcpy/releases>
- v4.1 doc/audio.md：<https://github.com/Genymobile/scrcpy/blob/v4.1/doc/audio.md>
- man scrcpy.1（参数默认值）：<https://github.com/Genymobile/scrcpy/blob/master/app/scrcpy.1>
- rom1v：Scrcpy 2.0, with audio（音频架构与播放器策略）：<https://blog.rom1v.com/2023/03/scrcpy-2-0-with-audio/>
- #4055 / #4268 / #4668 / #4705 / #6081 / #6724 / #6835 / #6907（爆音家族）
- #4206（双实例 output+mic）/ #897（多实例打开）
- #3799 / #3825 / #3856（SDL_AUDIODRIVER 切换、directsound 低延迟警告）
- #3876 / #4111（音频设备切换/缺失）
- #3875（submix 捕获即设备静音）/ #4954（sndcpy 机制对比）
- #4380 / PR #5102（Audio Policy API、--audio-dup）
- #5859（核实：MediaCodec Java 异常，与音频无关）
- #6715（opus 静音 denormals CPU 40× 修复）

**SDL**
- SDL#7478（Win7 WASAPI CoInitialize）：<https://github.com/libsdl-org/SDL/issues/7478>
- SDL#13319 / SDL#13397（输出缓冲最小值）：<https://github.com/libsdl-org/SDL/issues/13319> <https://github.com/libsdl-org/SDL/issues/13397>
- SDL3 移除 winmm 后端：<https://discourse.libsdl.org/t/sdl-removed-arts-esd-fusionsound-nas-paudio-sndio-sunaudio-winmm-audio-backends/40395>
- SDL_wasapi.c（共享模式 + Avrt）：<https://github.com/libsdl-org/SDL/blob/main/src/audio/wasapi/SDL_wasapi.c>
- SDL 音频驱动清单（DeepWiki）：<https://deepwiki.com/libsdl-org/SDL/4.4-audio-drivers-and-platform-support>

**社区**
- QtScrcpy 讨论 #446（无内建音频）：<https://github.com/barry-ran/QtScrcpy/discussions/446>
- Escrcpy 中文文档·Audio：<https://viarotel.eu.org/zhhans/reference/scrcpy/audio>
- scrcpyapp.org 中文音频指南：<https://scrcpyapp.org/guides/audio/>
- CSDN scrcpy 进阶指南：<https://blog.csdn.net/gitblog_00790/article/details/157671700>
