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
- [x] **0.2 Rust 第一档 ✅（2026-09-16 完成代码层，真机验收待跑）**：
      core 下沉 + 面板重写全部落地，产物 = 两个 Rust 进程：
  - [x] 0.2.1 纯逻辑层：engine / aspects / catalog / icon_presets /
        settings / paths 全部对译；parity_check.sh 315/315 逐字节一致。
  - [x] 0.2.2 进程层：devices / watch / session / mirror / apps（含
        像素级图标渲染 devicon+sweep+duo_icons.dex 内嵌）/ set-volume /
        audio-lock 子命令，JSON-lines 协议；duocore.py shim 保留作
        Python 参考栈兼容层。
  - [x] 0.2.3 面板切换：duo-panel（egui）全量替代 PyQt6-QML——
        model/sessions/prefs/pinyin/backend 无 UI 依赖模块 + app.rs
        渲染层（设备卡/固定卡/搜索/网格右键全菜单/运行卡/Toast/设置页）；
        gui_prefs.json 同文件兼容；单实例互斥体；会话 spawn 全走
        duo-core mirror（argv 等价 build_launch_argv）。cargo test
        260 绿（core 219 + panel 41），clippy 零警告，pytest 488 绿。
- [x] **0.3 架构收敛至 C# overlay ✅（2026-09-17，代码层）**：
      实验性 Rust win32 SetParent 宿主窗口（host.rs）退役，全面统一回归
      经真机验证的 C# overlay 机制（chrome.rs + chrome_overlay.cs）；duo-core
      收敛 CLI 移除 --embed/--embed-style 旗标，维持 --chrome；全进程 spawn 增加
      quiet_command（CREATE_NO_WINDOW），杜绝 Windows 侧黑窗闪烁；增加 flex
      会话 orientation_lock（wm set-ignore-orientation-request 1）。
- [x] **0.4 前置打包 ✅**：WSL mingw 交叉构建双 exe（duo-core.exe
      console + Duo.exe GUI 子系统/duo.ico 资源图标/单实例）；
      build_wsl.sh --deploy / build_windows.ps1 -Deploy / install 脚本；
      自接视频流（libmpv）仍按原条件：撞到 scrcpy 呈现天花板再启动。

> **Rust 栈现状**：代码/测试/交叉构建全绿（cargo 260 + pytest 488 + parity 234），
> 但未在 Windows 真机跑过——下列声明到真机验收前都视为未决：面板渲染效果、
> 宿主窗交互、图标渲染实效、音频仲裁行为。Python 栈保留为参考与对照（src/pyduo）。

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

- [ ] **2026-09-18 玻璃 §8 管线真机验收（新）**：①上巴胶囊/下巴换装
      §8.6 增益链（BakeGlassPlate，暗/亮单位档随底自适应）：暗视频上墨
      字可读、亮视频上胶囊不溶底不冲白；②右键菜单亮色档（地板 0.66/
      天花 0.975）：暗底斑块不再发黑、平画布落点 ≈250、色斑仍透出；③
      `--glass 0` 回归（普通材质未受影响）；④DPI 125%/150% 采样边距与
      模糊核不触边（margin=3σ=24 device px）。
- [ ] **2026-09-18 第二批五修复真机验收（新）**：①菜单晃动/二级玻璃
      时有时无（玻璃矩形量化 + 泵清空宽容化）；②镜像贴比闪烁
      （FitWhenArmed 提早到 +200ms 且绕过 500ms 节流）；③设置页改
      返回首页自动保存；④应用退出后运行卡芯片滞留（update 泵补
      sessions.reap + 500ms 重绘）；⑤酷安专属：菜单大 + 子菜单回退
      无玻璃（待量化修复后复测；疑似与磁贴靠边/弹层翻转有关）。
- [ ] **21:9 酷安首两叉会话天折（诊断存档 2026-09-18）**：复现实测
      窗口客户区恰为 3360×1440、视频无 letterbox、贴比收敛正常；
      用户所见“大黑边”= 前两次 scrcpy 启动在 Renderer 前夭折遗留的
      黑色无视频窗口。夭折原因未复现（无错误行，疑 D3D11/设备端
      竞态），若再现：保留会话日志 + tasklist 快照。
- [ ] **2026-09-17 四修复真机验收**：①右键菜单毛玻璃与
      hairline 描边贴合（四角无楔形空隙、无背景缝）；②菜单收紧后观感
      （行间距 0，高度回到 QML 同款）；③比例二级菜单右侧示意矩形
      （10 个，横屏宽 16/竖屏高 14）+ 21:9 机身竖屏启动无两侧大黑边；
      ④镜像/固定窗初开即铺满无 letterbox（✅ 部分验证：overlay 日志
      有 converged 行，用户确认不手调也能缩并）。改动：duo-panel app.rs /
      chrome_overlay.cs；出图复现：`DUO_SHOT_MENU=tile-sub
      DUO_SKIP_SWEEP=1 DUO_SHOT_SEED_APPS=8 duo-panel --shot x.png`。
      若 ③ 仍有黑边：抓会话日志 `INFO: Texture:` 尺寸 vs argv
      `--new-display`，核实设备是否改写分辨率。
- [x] **设置页开关与说明文字错位微调（✅ 2026-09-17 完成）**：根据真机反馈（`字体错位.png` /
      `开关错位.png`），校准 `SettingsLayout` 息屏开关与说明文字垂直间距至 20px（内部 gap 5.5px），
      与下一项拉开 36px 区分间距（gap 21.5px），消除脱节悬浮感，添加单元测试守护。
- [ ] **Rust 栈验收（新，优先）**：deploy 双 exe 后真机跑：面板渲染（设备卡/
      固定卡/搜索/网格/右键全菜单/运行卡/Toast/设置页）、会话 spawn（mirror
      argv 与 Python 版一致）、图标 sweep、音频仲裁、单实例提示、退出拖树。
      src\rustduo\scripts\accept_windows.ps1 回填 Duo.exe 路径后复用。
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
