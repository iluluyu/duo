# 下一步：窗口体验与设置

> 面向接手实现的 AI。坚持 KISS：保留 Python / PyQt6 + scrcpy + C# overlay，不重写视频链路。
> 技术细节见 [实施说明](docs/window-experience.md)。本文件负责本轮优先级；`plan.md` 保留长期路线及历史实验。

## 目标与边界

- **设备镜像 / 固定虚拟屏**：窗口贴合视频比例，只做等比例缩放；不通过拉伸、裁切画面来消除黑边。
- **flex 应用窗口**：保留自由缩放，由虚拟显示跟随窗口重排；不把设备镜像偷偷改成虚拟显示。
- **Windows 窗口**：主面板与镜像窗口都能可靠调节大小。
- **圆角**：**默认用 Windows 系统圆角**（`DWMWCP_ROUND`，零成本稳定）。G2 连续曲率大圆角转**长期目标**（2026-09-05 实测：运行时卡顿/画面裁切异常/描边模拟观感不足，详见 docs/window-experience.md §3.4；代码保留，`corner_mode="g2"` 选开）。设置页暴露 corner_mode/大小，默认 `system`。
- **界面**：液态玻璃风格、少文字、少层级。保留必要的设置标签、错误提示和无障碍名称。

## 按顺序执行

### 1. P0 — 修复窗口缩放与黑边

- [x] **先行确认视频尺寸事件来源**（已验证，2026-09-05，设备 OPD2409 / scrcpy 4.1 / WSL interop）：
  - ✅ 会话日志（stderr 重定向）在默认 verbosity 下输出 `INFO: Texture: WxH`，**启动与每次尺寸变化（含旋转）都会重发**（实测一次会话内 4 次翻转全部留痕）。尺寸事件通道可用，无需回退方案。
  - ✅ 旋转时 scrcpy 窗口会自行重排（窗口矩形在横/竖间自动翻转，位置锚定不动），但客户区是否精确贴合未验证——overlay 侧用“Texture 变化稳定后收敛”覆盖两种情况。
  - 🐛 **附带发现真实 bug**：`EngineArgs.adb_binary` 生成的 `--adb=<path>` 不是 scrcpy 选项（4.1 报 `unknown option`），WSL 侧 `duo mirror` 因此循环重启、从未成功。scrcpy 定位 adb 的官方机制是 **`ADB` 环境变量**；WSL 下需同时写入 `WSLENV=ADB` 才能穿越 interop（已实测路径回显确认），且路径须为 Windows 形式。✅ 已修复（`adb_pin_env` + `SessionSpec.env`），真实会话验证不再重启。
- [x] 复现 Windows 拖不动的问题（已解决：先修 z 序，后升级为原生标题栏式中央移动带，多轮真机验收）
- [x] 修复 `Controller.SyncStrips()` 的 800px 门槛（尺寸自适应钳制取代固定物理像素门槛）、缺少独立底边热区（补齐 9 热区：4 边 + 4 角 + 顶部移动区）、失效窗口残留热区（死窗分支补 `HideStrips`）及捕获丢失（三处拖拽面全部加 `MouseCaptureChanged` 收尾）；保留轮询式拖拽，无鼠标消息自激。
- [x] 显式传递 `mirror / fixed / flex` 模式；argv 只携带初始尺寸（仅 fixed 已知），实际与旋转后尺寸经会话日志通道更新（`--session-log` + overlay 尾读线程，实测 1.5s 内读到首帧尺寸）。镜像、固定模式拖边/拖角/放大均锁定**视频客户区**比例（`ConstrainToVideo`：边拖锁对侧+垂直居中，角拖锁对角+主轴驱动，DIP 最小尺寸联动），旋转后更新。
- [x] 镜像/固定模式移除“铺满工作区”入口（顶栏仅 最小化/等比放大/关闭 三键，flex 保留四键）；放大按**视频比例**等比拟合工作区并居中；外部改窗（含 Win+Up 原生最大化、PowerToys）在稳定 350ms 后一次性收敛为贴合比例窗口（`ConvergeToVideoAspect`，含 `IsZoomed` 还原），不留内部黑边。
- [x] **全屏语义**（2026-09-05 用户补充）：等比放大铺不满屏幕时，未覆盖区域不占窗口、露出桌面背景（浮动画面效果）——由上述 FakeMaximize 等比拟合 + 收敛逻辑实现：窗口只占视频区域，周围是桌面而非黑边；需 Windows 实测确认观感。
- [x] 主面板取消 `setFixedWidth(400)`（改为最小 360×520，初始 420×660）；全部应用网格按宽度重算列数（`_columns_for`，4–10 列自适应）、运行中芯片可换行（网格重排），不横向溢出。

**完成标准**：Windows 上四边四角均可拖动；替换后的最小尺寸门槛以 DIP 表达并随 DPI 缩放（当前实现为 800 物理像素，200% DPI 下等价于更小的 DIP 值，不得沿物理像素硬编码）✅；横竖屏切换、放大/还原、多显示器拖动后比例正确（待实测）。黑边指 PC 端新增留边，不包含 Android / App 自身绘制的黑色区域。

### 2. G2 大圆角 → 已回退，转长期目标

- [x] 最小 Windows 原型验证：像素级证明 `SetWindowRgn` 可裁切 scrcpy 视频窗口（块级对比 1.7 vs 48.8，见 [验证记录](docs/validation/window-experience.md)；**推翻 plan.md 旧结论**）。实现已落地：四次超椭圆 G2 多边形区域 + 抗锯齿描边遮罩 + 去双层边框。
- [x] **回退决策（2026-09-05）**：用户实测反遗缩放卡顿未根治、区域套用后画面只显示一半、描边模拟观感“走偏”。默认回退系统圆角（`corner_mode="system"`），G2 代码保留为选开实验项。阻塞清单与重启条件见 [docs/window-experience.md §3.4](docs/window-experience.md)。**后续不再在本轮迭代内继续 G2。**

**后续**：设置页（任务 3）暴露 corner_mode（system/g2/none）与大小；G2 重启属长期目标，需先证明卡顿/裁切异常可彻底解决。

### 3. P1 — 增加最小设置页

- [x] 新增 Qt-free `duo/core/settings.py`：JSON 读取（永不抛异常，缺字段/坏类型/超范围→字段默认+问题清单）、校验、原子保存（tmp+os.replace）；沿用 `data_dir()`，保留旧 gui_prefs.json。✅ 9 项测试。
- [x] CLI 贯通：`mirror` 的 fps/bitrate/dpi/corner-radius 改为 None 默认，优先级 CLI > settings > 内置默认；scrcpy/adb 路径支持 settings 覆盖（`resolve_tool`），`--adb` 非法旗标已改为 `ADB` 环境变量钉死。
- [x] 设置页（已由 QML 实现，见任务 4）：引擎/外观两组、引擎锁、后台探测、保存/取消、圆角预览；齿轮 + Ctrl+,。
- [x] `run_app()` adb 解析：settings > probe > 回退（`resolve_adb_path`），GUI/CLI/会话同源。
- [x] 圆角预览即时更新；默认 system，g2 选开实验。

**完成标准**：含中文/空格的路径可用；非法值不会覆盖有效配置；重启保留设置；修改 adb 不引发不同版本 server 相互重启。已有会话运行时不切换引擎路径，提示先关闭会话。

### 4. P1 — UI 层重构为 QML（吸收原玻璃视觉任务）

> 2026-09-05 用户决策：widgets 面板重构为 **Python + QML**（PyQt6 自带 QtQuick/Controls2，已验证 offscreen+software 可用）。core 层与视频链路（scrcpy 原生窗口 + C# overlay）不动。

- [x] 抽取 `duo/ui/controller.py`（widgets 退役，逻辑单一来源）。
- [x] QML 主面板：设备卡、应用网格、运行芯片、toast、齿轮 + Ctrl+,；液态玻璃（含 100% 屏 1.25× 舒适缩放）。
- [x] QML 设置页 + `run_app()` QML 引擎；widgets 版已删。
- [x] offscreen + software 测试与截图存档（docs/validation/assets/qml-*.png）。

### 5. 验证与交接

- [x] 增补配置、参数透传、比例几何等测试（120 passed；行为断言非关键字匹配）。
- [ ] 正式 Windows 实测回填（exe 已多轮非正式实测：拖动/双屏/设备闪烁/灵动岛；按清单正式化）。
- [ ] 回填 `docs/validation/window-experience.md`：环境、命令、截图/录像、失败项。

### 6. Windows QML 上屏验证与打包（本轮任务 2）

> QML 面板已合并（任务 4）；WSL 无显示，上屏与打包产物只能在 Windows 侧实测。本轮先把准备物料入库：`duo.spec`（onedir/win64，datas 含 duo/ui/qml 与 chrome_overlay.cs，`assets/duo.ico` 占位图标）、`scripts/build_windows.ps1`（venv + `.[dev,gui,build]` + pyinstaller）、pyproject `build` extra、windows-setup 验证清单。

- [x] 打包与验证准备：`duo.spec` + `scripts/build_windows.ps1` + `build` extra + 上屏/产物校验清单（本仓完成，无需 Windows）。
- [ ] **需 Windows 实测**：原生运行 `.venv\Scripts\python -m duo --gui` 上屏，按 [docs/windows-setup.md](docs/windows-setup.md)「QML 面板上屏验证清单」核对：设备列表、投屏开窗/芯片关闭、设置页（齿轮/Ctrl+,）、竖屏长按切换。
- [ ] **需 Windows 实测**：`scripts/build_windows.ps1` 构建 `dist\Duo\`，按同文档「打包构建与产物校验」清单核对（QML/overlay 资源入包、Duo.exe 直启与带参路由、设置持久化、图标）。
- [ ] **需 Windows 实测**：按 [docs/validation/window-experience.md](docs/validation/window-experience.md) §3 清单实测并回填验证记录（拖拽手感、DPI、多窗口、断线清理、打包版行为）。

### 7. P1 — 虚拟屏 HOME 语义与直达应用（已实现）

> 2026-09-05 真机调研（OPD2409 / Android 16 / scrcpy 4.1）完成，根因与方案见 [docs/window-experience.md §7](docs/window-experience.md)。要点：选择器 = AOSP SecondaryDisplayLauncher（`CATEGORY_SECONDARY_HOME` 唯一 handler），HOME 键全局落物理屏（display 定向注入已证伪），`--start-app` 无 `+` 在应用已运行时不落虚拟屏（真实 bug）。

- [x] `engine.py`：flex/fixed 会话默认加 `--no-vd-system-decorations`；`--start-app` 一律带 `+` 前缀；更新 argv 断言测试（两个旗标都不能只断言源码包含关键字，要断言拼装结果）。✅ 2026-09-06（scrcpy 官方文档复核：`--start-app=+pkg` = 先 force-stop，`+`/`?` 可叠加且顺序固定）
- [x] 会话日志解析 display id（`[server] INFO: New display: ... (id=N)`，尾读通道现成），面板"运行中点应用"改走 `am start --display N -n <resolved>`（resolve-activity 预解析），不重建会话；日志缺失时降级提示。✅ 2026-09-06：CLI `--session-log` 让面板拿到固定日志路径，`session.parse_display_id/display_id_from_log` 点击时尾读，`Controller.startAppOnDisplay` 工作线程 resolve + am start，全部失败路径降级为状态栏提示；面板日志每次启动前截断防旧 id 串台
- [x] chin ○ / HOME 按钮语义保持：虚拟屏上永不发 keyevent 3（长按=关窗现状不变），tooltip 注明"HOME = 回 Duo 面板"；scrcpy 内置 MOD+h/中键的焦点漂移记为已知限制。✅ 2026-09-06（零 C# 改动；语义写入 docs/window-experience.md §7.6；芯片 tooltip 已注明）
- [ ] **需 Windows 实测**：空 flex 会话（无 `--app`）在 decorations 关闭后整屏无帧，确认 Texture 通道静默时窗口/overlay 的降级体验，再决定空 flex 是否保留 decorations（现默认关）。
- [ ] **需 Windows 实测**：中文输入乱象是否复现（uhid 键盘下候选窗落物理屏），决定是否加 `--display-ime-policy=local`。

**完成标准**：flex/fixed 会话从启动到退出全程不出现 SecondaryDisplayLauncher（应用选择器）；重复开会话（应用已在物理屏运行）仍能直达应用；HOME/chin 语义有 UI 说明。

### 8. 长期目标 — 自研视频客户端（GPU 解码，需强模型/专项）

> 背景与取舍见 docs/mirroring-quality.md §6。现状：scrcpy PC 端纯软解（libavcodec+SDL 内存帧，跨平台设计使然，无 GPU 旗标）；当前用 h264+1440p+60fps 喂饱软解已够用。本项目是架构级升级，需更强模型或专项投入。

**目标**：替换 scrcpy 客户端为自研 —— 只用它的服务端：`adb push scrcpy-server.jar` → socket 协议（video/control 流，参考 scrcpy 源码 app/src/server + client 协议文档/QtScrcpy、ws-scrcpy 等成熟实现）→ PC 端 D3D11VA 硬解 → 零拷贝渲染进 QML 窗口（QQuickItem + swapchain 互操作）。

**解锁收益**（一项工程三项红利）：
1. GPU 解码：4K120 原生分辨率无压力（当前软解只能到 ~1440p90 富余）
2. 真抗锯齿圆角/任意形状窗口：视频表面归我们所有，alpha mask 直接 GPU 合成（G2 回退的根治路径，见 §3.4/plan.md）
3. 视频嵌入主面板/多窗分屏等窗口形态自由

**验收基线**：不弱于 scrcpy 的端到端延迟；断线/重连/旋转/控制通道（touch/key/clipboard/uaed）全通；CPU 占用显著低于软解基线。

**风险**：协议跟进 scrcpy 版本升级；D3D11 零拷贝与 Qt 渲染线程的同步；工作量估计为人周级而非人天级。

## 当前基线

- 2026-09-06：任务 7 软件侧落地（decorations 关闭、`+` 前缀、display id 直达通道、HOME 语义文档化）；剩 Windows 实测两项（空 flex 无帧体验、IME 候选窗）。
- `.venv/bin/python -m pytest -q`：**132 passed**；ruff / mypy 全绿。
- 剩余开发工作：Windows 实测回填（任务 5/6/7 的实测清单）。

## 给执行模型的提示词

> 阅读 `TODO.md` 和 `docs/window-experience.md`。一次只做一个编号任务，优先修现有实现，不重写架构。Python 使用 8 空格缩进，C# 保持现有编译器兼容。报告修改文件、测试结果及未验证项。没有 Windows 实测证据时，不宣称圆角或窗口交互已完成；G2 裁切原型不通过就停止扩展，记录阻塞。不要顺手加入预设编辑器、托盘、无线向导、自动更新等无关功能。
