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
- [ ] 复现 Windows 拖不动的问题，记录窗口尺寸、缩放比例、scrcpy 版本、overlay 日志。（静态部分已定位并修复；剩交互实测）
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
- [ ] 新增 `duo/ui/settings_page.py` 设置页（两组：引擎路径+检测/画质/圆角/玻璃开关，预览即时、保存后新会话生效），主面板右上角齿轮 + Ctrl+, 进入。
- [ ] `run_app()` 的 `adb.exe` 硬编码改为 settings/probe 解析。
- [ ] 本页圆角预览即时更新；默认 system（Windows 系统圆角），g2 为选开实验项（观感问题未解决，长期目标）。

**完成标准**：含中文/空格的路径可用；非法值不会覆盖有效配置；重启保留设置；修改 adb 不引发不同版本 server 相互重启。已有会话运行时不切换引擎路径，提示先关闭会话。

### 4. P1 — UI 层重构为 QML（吸收原玻璃视觉任务）

> 2026-09-05 用户决策：widgets 面板重构为 **Python + QML**（PyQt6 自带 QtQuick/Controls2，已验证 offscreen+software 可用）。core 层与视频链路（scrcpy 原生窗口 + C# overlay）不动。

- [ ] 抽取 `duo/ui/controller.py`：QObject 面向 QML（设备列表/应用目录/会话管理/状态信号/竖屏偏好/argv 拼装），逻辑从 main_window.py 迁出，无 widgets 依赖。
- [ ] QML 主面板：设备卡、应用网格（按宽度算列数）、运行中芯片、状态 toast、齿轮 + `Ctrl+,`；液态玻璃风（半透明面板、细亮边、MultiEffect/FastBlur，不透明降级）。
- [ ] QML 设置页：引擎/外观两组，行为对齐原 settings_page（引擎锁、后台探测、保存/取消、圆角预览）；`run_app()` 改 QML 引擎；删除 widgets 版 main_window/settings_page 及对应 widget 测试。
- [ ] offscreen + software 渲染测试；截图自检存档 `docs/validation/assets/`。

### 5. 验证与交接

- [ ] 增补配置、参数透传、比例几何、G2 曲率与 Windows 热区行为测试；不能仅断言源码包含关键字。
- [ ] 分别验证源码运行和 Windows 打包版；覆盖 100% / 150% / 200% DPI、两个重叠窗口、断开设备、退出清理。
- [ ] 回填 `docs/validation/window-experience.md`：环境、命令、自动测试结果、截图/录像、失败项和未测项。只有通过 Windows 实测的任务才能勾选视觉/交互验收。

## 当前基线

- 2026-09-05：Task 1 全部落地（比例锁定/热区/收敛/日志尾读）；G2 圆角已验证可裁切但**已回退**（见上）；设置核心层（settings.py + CLI 贯通）完成。默认系统圆角，无区域/遮罩开销。
- `.venv/bin/python -m pytest -q`：**85 passed**；ruff / mypy 全绿。仍不证明 Windows 交互（拖拽手感、收敛观感、DPI）通过——见验证清单。
- 旧 `plan.md` 的“已完成”与当前用户反馈存在差异：以当前 Windows 复现和新证据为准。

## 给执行模型的提示词

> 阅读 `TODO.md` 和 `docs/window-experience.md`。一次只做一个编号任务，优先修现有实现，不重写架构。Python 使用 8 空格缩进，C# 保持现有编译器兼容。报告修改文件、测试结果及未验证项。没有 Windows 实测证据时，不宣称圆角或窗口交互已完成；G2 裁切原型不通过就停止扩展，记录阻塞。不要顺手加入预设编辑器、托盘、无线向导、自动更新等无关功能。
