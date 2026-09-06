# Duo TODO

> 面向接手实现的 AI。坚持 KISS：保留 Python / PyQt6-QML + scrcpy + C# overlay，不重写视频链路。
> 一次只做一个编号任务；没有 Windows 实测证据不宣称完成；不加入用户未要求的功能。

## 边界

- **镜像 / 固定虚拟屏**：窗口贴合视频比例，只做等比缩放；不用拉伸/裁切消黑边。
- **应用会话（flex）**：固定 2560×1440/480 虚拟屏（不可旋转）；窗口自由拖改、永不自调（钉扎）。
- **圆角**：默认系统圆角；G2 连续曲率为长期选开实验（`corner_mode="g2"`）。

## 1. P1 — 动态跟随虚拟屏（窗口驱动显示，无旋转风暴）

> 2026-09-06 全天 A/B（真机 piliplus），已死路径勿重试：
>
> | 路径 | 结果 |
> |---|---|
> | `--flex-display`（任意装饰/尺寸组合） | ~2Hz 旋转风暴（scrcpy 重申窗口形状 vs 应用方向请求） |
> | `--capture-orientation=@` | 内容侧转 90°（用户实际撞过） |
> | `--no-vd-system-decorations` + flex | 视频页仍风暴 |
> | 钉扎弹回 + flex | 弹回→显示跟随弹回→应用再请求，死循环 |
>
> 详见 docs/window-experience.md §3。临时基线=当前 exe（固定屏，稳定）。

**目标**：拖完窗口松手 → 虚拟屏按窗口尺寸重建（离散跟随）。

**方案 A（推荐，人天级）**：overlay 检测拖拽 settle（`TrackExternalChange`）→ 文件通道
（`%TEMP%\duo-display-request-<title>.txt` 写 WxH）→ controller QTimer 轮询 →
`--new-display=WxH --window-x/--window-y` 重启会话（复用竖屏切换重启链路）。
防抖 ≥800ms；尺寸差 <96px 不重建（防边框舍入死循环）。

**方案 B（根治，依赖任务 2）**：自研客户端自管虚拟屏尺寸，无 scrcpy 窗口仲裁层。

**验收**：拖动→松手→≤1.5s 应用以新窗口比例重排；无旋转/风暴/闪烁死循环；
piliplus 首页→视频→全屏来回切窗口纹丝不动。

## 2. 长期 — 自研视频客户端（GPU 解码，需强模型/专项）

> 背景见 docs/mirroring-quality.md §6。scrcpy PC 端纯软解；当前 h264+1440p+60fps 喂饱软解。

**目标**：只用 scrcpy 服务端（socket 协议，参考 QtScrcpy/ws-scrcpy）→ PC 端 D3D11VA
硬解 → 零拷贝渲染进 QML（QQuickItem + swapchain 互操作）。

**收益**：① 4K120 无压力；② 真 AA 任意形状窗口（G2 根治）；③ 视频嵌面板/分屏；
④ 任务 1 方案 B 的前提。

**验收**：延迟不弱于 scrcpy；断线/重连/旋转/控制通道全通；CPU 显著低于软解。
**风险**：协议跟进版本；D3D11 零拷贝与 Qt 渲染线程同步；人周级工作量。

## 待 Windows 实测（收尾清单）

- [ ] 按 docs/windows-setup.md 清单正式回填打包版行为（onefile → `C:\Tools\Duo.exe`）
- [ ] 空 flex 会话（无 `--app`）decorations 开启下的无帧降级体验
- [ ] 中文输入：uhid 候选窗落物理屏是否复现 → 决定 `--display-ime-policy=local`
- [ ] piliplus 全流程（首页→视频→全屏→拖窗）在最新 exe 上的复验记录

## 当前基线（2026-09-06）

- 应用会话：固定 2560×1440/480 虚拟屏 + `--no-window-aspect-ratio-lock` + overlay 钉扎
  （左键豁免+收编）。窗口可拖改、内容直立 letterbox、永不翻转。
- 测试 179 passed；ruff / mypy 全绿；`C:\Tools\Duo.exe` 16:45 构建。
