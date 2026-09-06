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

**目标**：拖完窗口松手 → 虚拟屏按窗口尺寸重排（离散跟随）。

**方案（就地跟随，已部分真机验证）**：overlay 检测拖拽 settle（≥800ms，`MaybeResizeFlexDisplay`）
→ 直接 `adb shell wm size WxH -d <id>` 改当前虚拟屏（id 来自会话日志 "New display"
行），不重启 scrcpy/应用；目标尺寸=窗口宽高比适配进启动显示框（跨方向交换框），
两轴差 <96px 不下发（防边框舍入死循环）；单向离散跟随，不响应纹理反馈，无风暴环。
已验证：`wm size -d` 真机生效（Texture 即时更新，2026-09 设备 OPD2409/Android 16）。
防抖/阈值/启动框 seed 均已实现。
**端到端真机验证（2026-09-06）**：合成+真人拖拽（右/左缘、宽屏、拖成竖窗跨方向交换）
均 ≤1.5s 内重排并收敛，pin 仅单次弹回，无风暴；竖屏过渡有 ~1s 短暂抖动后稳定。

**验收**：拖动→松手→≤1.5s 应用以新窗口比例重排；无旋转/风暴/闪烁死循环；
piliplus 首页→视频→全屏来回切窗口纹丝不动（全流程仍需 Windows 真机复验，
见"待 Windows 实测"清单）。

## 待 Windows 实测（收尾清单）

- [ ] 按 docs/windows-setup.md 清单正式回填打包版行为（onefile → `C:\Tools\Duo.exe`）
- [ ] 空 flex 会话（无 `--app`）decorations 开启下的无帧降级体验
- [ ] 中文输入：uhid 候选窗落物理屏是否复现 → 决定 `--display-ime-policy=local`
- [ ] piliplus 全流程（首页→视频→全屏→拖窗就地跟随）：拖窗链路已真机验证
      （2026-09-06 源码构建），首页→视频→全屏及打包 exe 复验待回填

## 当前基线（2026-09-06）

- 应用会话：启动固定 2560×1440/480 虚拟屏 + `--no-window-aspect-ratio-lock` + overlay 钉扎
  （左键豁免+收编）。窗口自由拖改；缩放手势直通系统原生 size loop（flex 专属）；
  松手稳定 ≥800ms 后虚拟屏就地 `wm size -d` 跟随窗口比例（任务 1，已真机验证）。
- 测试 179 passed；ruff / mypy 全绿；overlay 经 csc.exe 真机编译通过。
