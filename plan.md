# Duo 开发计划与当前任务

> 本计划与 [`plan/todo.md`](file:///home/luyu/duo/plan/todo.md) 保持同步，后者为长期详细任务与阶段验收的唯一真相。历史 0.x 路线存档于 [`TODO.md`](file:///home/luyu/duo/TODO.md)。

---

## 1. 当前架构与阶段

- **核心实现**：`src/rustduo`（双 Rust 进程：`duo-core` 纯逻辑/CLI + `duo-panel` egui 界面）。
- **参考对照**：`src/pyduo`（Python / PyQt6-QML 原版栈，保读改删禁，测试套件 488 绿）。
- **窗口协议**：收敛至真机验证过的 C# overlay（`chrome.rs` + `chrome_overlay.cs`），移去实验性 win32 宿主；全量子进程使用 `quiet_command`（`CREATE_NO_WINDOW`）。
- **当前阶段**：P4 设置页收口阶段 → 即将进入 P5 Windows 真机全面验收。

---

## 2. 当前待解决的问题

### 2.1 设置页开关与文字对齐微调（✅ 2026-09-17 已完成）

- **问题证据**：`字体错位.png`、`开关错位.png`（归档至 [`docs/validation/assets/settings-switch-font-misaligned.png`](file:///home/luyu/duo/docs/validation/assets/settings-switch-font-misaligned.png)）。
- **根因分析**：`src/rustduo/crates/duo-panel/src/settings.rs` 中 `tso_caption` 在累加 `ROW_H + SP` 后额外增加了 `+ 9.0`（双重间距，达 34px），导致说明文字与标题行严重脱节，开关视觉悬空偏高。
- **解决措施**：
  - [x] 校准 `SettingsLayout::compute`：收紧息屏开关与说明文字垂直间距至 20px（内部 gap 5.5px），与下一条目拉开 36px 区分间距（gap 21.5px）；
  - [x] 消除脱节悬浮感，添加 `settings_layout_y_chain_and_grouping` 单测守护；
  - [x] 更新 `docs/ui/DESIGN.md` §3.10 规格定义。

### 2.2 Windows 真机全链路验收（P5）

- **双 exe 部署与启动**：`C:\Tools\Duo.exe` GUI 启动无黑窗，单实例互斥生效；
- **镜像与 Overlay 协同**：`duo-core.exe mirror --chrome` 正常拉起无边框 scrcpy 与 C# 顶栏/下巴；
- **方向防抖**：flex 会话下发 `wm set-ignore-orientation-request -d <id> 1` 稳定生效；
- **音频仲裁与退出收尾**：音频独占锁正常交接，退出时干净清理所有子进程树；
- **自动化脚本**：运行 [`src/rustduo/scripts/accept_windows.ps1`](file:///home/luyu/duo/src/rustduo/scripts/accept_windows.ps1) 并回填记录。
