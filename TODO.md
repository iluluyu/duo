# Duo TODO

> 面向接手实现的 AI。坚持 KISS：保留 Python / PyQt6-QML + scrcpy + C# overlay，不重写视频链路。
> 一次只做一个编号任务；没有 Windows 实测证据不宣称完成；不加入用户未要求的功能。

## 0. 性能与流畅度升级路线（2026-09-13 拍板）

> 核心判断：**SetParent 嵌入是对 overlay 机制的思想升级，不依赖技术栈切换**。
> C# overlay 已经在做 FindWindow——把它从“贴一层悬浮窗”改成“建自己的
> 宿主窗口 + 嵌入”，用现有 C# 就能先验证效果。执行顺序：

- [x] **0.1 嵌入验证 ✅（2026-09-16 用户真机拍板：“确实新的会更加好用”）**：
      SetParent 方案在现有 C#/Python 上验证通过——overlay `--embed` 模式
      建自己的宿主窗口，scrcpy 窗口嵌入为子窗口铺满客户区。**默认沉浸式
      宿主**（无边框 + 原生可缩放 + 隐形拖动带/悬停胶囊）；
      `--embed-style native` 为系统标题栏变体。设计与验收存档：
      docs/window-experience.md §14。
- [ ] **0.2 Rust 第一档（当前任务）**：core 下沉。产物 = 单一 Rust 进程
      `duo-core`（面板经 stdio JSON-lines 调用，对齐现有进程模型；0.3 的
      宿主窗口直接住进同一进程，不再另起炉灶）。分三步：
  - [ ] 0.2.1 纯逻辑层（机械劳动）：engine argv 装配 / aspects / catalog /
        icon_presets / settings / paths 入 Rust workspace，pytest 逐条对译
        cargo test（rust/ 目录，零外部依赖）
        **进度（2026-09-16）**：engine / aspects / catalog / icon_presets
        （含 apps.py 的 g2_outline 纯数学部分）已对译，43 条 cargo test
        过；对拍机制落地——`rust/scripts/parity_check.sh` 同输入 Python/Rust
        逐字节比对，315/315 行一致（含全部 27 张 SVG）。剩 settings/paths
        （filesystem/JSON，随 0.2.2 serde/IPC 一起进）。
  - [x] **0.2.2 进程层 ✅（2026-09-16，部分范围）**：devices（解析 + 守约
        状态机，含查询超时）/ session 监督（spawn+崩溃重启+日志 append+
        display-id 解析）/ paths 入 Rust；`duo-core` 二进制落地
        （devices / watch / session 三子命令，JSON-lines 协议，真机烟测
        过）；Python 客户端 shim duo/core/duocore.py（11 测试）；Windows
        构建脚本 rust/scripts/build_windows.ps1（需 Windows 侧 rustup）。
        **未下沉**（暂留 Python）：codec 探测、apps 枚举（2190 行，随需
        求评估）、chrome overlay 启动器（0.3 一并处理）。
  - [ ] 0.2.3 面板切换：**第一刀已落**——`duo mirror --duo-core` 把
        会话监督下沉到 Rust 二进制（语义对齐 Session.run，默认关，
        待 Windows 实测后转默认）。剩余：面板全面改调 duo-core（设备
        监控走 watch、会话 spawn 走 session）、Python core 退役。
- [ ] **0.3 1.5 完成**：沉浸式宿主窗口（win32 crate）住进 duo-core 进程，
      C# overlay 退役——停在这里就是完整、可长期维护的产品形态。
- [ ] **0.4 Rust 第二档（条件启动）**：自接视频流（libmpv 喂裸 H.264/H.265
      + 最小控制协议实现）**只在真的撞到 scrcpy 呈现天花板时**再启动；
      启动时 pin 住 server 版本，协议跟不跟新完全由自己节奏决定——QtScrcpy
      一个 part-time 维护的 C++ 客户端跟了 scrcpy 协议好几年，证明这个
      维护面是博士生兼职扛得动的。

## 边界

- **镜像 / 固定虚拟屏**：窗口贴合视频比例，只做等比缩放；不用拉伸/裁切消黑边。
- **应用会话（flex）**：固定 2560×1440/480 虚拟屏；窗口自由拖改；不跟随、不切横竖屏（方向信 APP）。
- **圆角**：默认系统圆角；G2 连续曲率为长期选开实验（`corner_mode="g2"`）。

## 1. ~~P1 — 动态跟随虚拟屏~~（已回退，2026-09-06 用户拍板）

> 实现+真机验证后同日整体回退：不要"显示跟随窗口"、不要横竖屏切换/防转钉扎，
> **信 APP**：虚拟屏恒定 2560×1440，方向由 APP 自主请求（app 全屏→Android 转屏
> →scrcpy 窗口随视频自然重排），我们零干预。保留：自由窗口（拖拽缩放，异步
> SetWindowPos 跟手）。全天 A/B 死路径与旋转风暴实验存档见 docs/window-experience.md §3。
> 曾实现的就地跟随（`wm size -d` settle 下发，真机可用）已删——历史代码见 git
> 9720fe7，勿凭"已验证"复活。

## 待 Windows 实测（收尾清单）

- [ ] 按 docs/windows-setup.md 清单正式回填打包版行为（onefile → `C:\Tools\Duo.exe`）
- [ ] 空 flex 会话（无 `--app`）decorations 开启下的无帧降级体验
- [ ] 中文输入：uhid 候选窗落物理屏是否复现 → 决定 `--display-ime-policy=local`
- [ ] piliplus 全流程（首页→视频→全屏→拖窗缩放不中断播放）：拖窗链路已真机验证
      （2026-09-06，源码+打包 exe），播放中缩放复验待回填
- [ ] 面板新交互真机回归：顶栏胶囊/固定卡/搜索/右键菜单（含按比例打开
      16:9 与 9:16 各一例）/运行卡 hover 关闭；出图基线 qml_shots 已过
- [ ] 机身比例预设：wm size 派生的横/竖预设真机验证（aspects.py 逻辑已就位）
- [ ] 固定比例会话（--width/--height fixed 模式）与 flex 自由窗口的混用回归

## 当前基线（2026-09-07 UI 重构后）

- 面板结构：顶栏胶囊（首页/设置两页常驻）→ 设备卡 → 固定应用卡（置顶，
  玻璃卡）→ 搜索（拼音首字母+标签过滤）→ 应用网格（裸排，拼音序）→
  运行卡（底部玻璃卡，hover 露出 ✕，无横竖屏字样）→ Toast。
- 图标：预设品牌色 squircle + 单字（duo/core/icon_presets.py，Qt SVG 基线
  光学居中）；未知应用色板哈希 fallback；真实 APK 图标到达后覆盖。
- 目录 28 应用（duo/core/catalog.py，覆盖预装 QQ/QQ NT/TIM 等
  `pm list -3` 漏掉的应用）；右键菜单：打开/置顶/按比例打开（横竖各 4
  档+机身，duo/core/aspects.py，复用 `--display fixed` 既有管线）。
- 设置页：无标题行（胶囊即导航）、删 DPI/圆角控件（隐形透传）、
  音频「仅最新会话/全部会话/静音」、探测瞬时提示 2.5s、单保存钮。
- 测试 242 passed；ruff / mypy 全绿；UI 规范 docs/ui/DESIGN.md 为验收
  标准（两轮 agy Opus 品味评审已消化）；方案稿 docs/ui/mockups/。
- 镜像/会话链路无改动（flex 自由窗口、方向信 APP 维持）。
