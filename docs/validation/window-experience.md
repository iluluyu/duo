# 窗口体验验证记录

- **日期**: 2026-09-05
- **环境**: WSL2 (archlinux) + Windows 侧 interop；scrcpy 4.1 (scoop, 自带 adb)；adb 37.0.1 (scoop shim)；设备 OPD2409 (Android 16, 4444bd6b)；pwsh 7（用户建议，规避 WinPS 5.1 解析怪癖与 MTA WinForms 问题）
- **代码**: 提交于 `8587626`（比例缩放）与 G2 提交（本轮）
- **素材**: `assets/g2v5-*.png`（裸窗口 300px 内缩椭圆区域三连拍）、`assets/g2v6-*.png`（生产路径 160 DIP G2 三连拍）

## 1. 视频尺寸事件通道（Task 1 前置）

| 实验 | 结果 |
|---|---|
| probe2/probe3 直跑 scrcpy | `INFO: Texture: WxH` 启动 + 每次旋转都重发（一次会话 4 次翻转留痕） |
| 旋转时窗口行为 | 窗口矩形自行在横/竖间翻转（722x1038 ↔ 1014x745），位置锚定 |
| 生产冒烟（`duo mirror --chrome`） | overlay 日志：窗口发现→修复→**1.5s 内** `video size from log: 3392x2400`，40s 心跳稳定 |
| `--adb` 旗标 | scrcpy 4.1 报 `unknown option`（旧实现循环重启）；`ADB` 环境变量 + `WSLENV=ADB` 实测穿透 interop（错误回显路径佐证） |

## 2. G2 圆角裁切（Task 2）

### 方法（无需人眼，块级统计对比）

窗口角点 120–200px 方块截图三连：**最小化**（桌面参考）→ **还原**（视频）→ **施加区域后**。平均像素差判定，壁纸渐变不再是干扰（v1–v4 单点采样因暗壁纸 vs 暗视频不可判）。

### 结果

| 阶段 | 对比 | 数值 | 判定 |
|---|---|---|---|
| v5 裸窗口 + 300px 内缩椭圆 | after-vs-desktop / after-vs-video | **1.7 / 48.8** | 裁切生效，桌面透出 |
| v6 生产路径（`--chrome --corner-radius 160`） | corner-vs-desktop / corner-vs-video | **13.7 / 104.2** | 生产路径生效 |

### 结论

- **`SetWindowRgn` 对 scrcpy SDL3/D3D11 视频窗口可以视觉裁切**——plan.md 2026-09-03 "返回成功但无视觉效果"的记录不成立（当时无像素级证据，或条件不同）。视频在区域内正常渲染（中心块持续为视频内容）。
- 实现为四次超椭圆 `|x/a|⁴+|y/a|⁴=1` 多边形区域（16 采样/角，G2 连续：轴端切线与直边一致、曲率趋 0）；区域随窗口矩形变化自动重施加（GDI 区域不随窗口缩放）。

### 抗锯齿与单层边框（用户反馈优化，同日）

用户实测反馈：硬边锯齿明显 + 双层边框。依据调研的前人方案优化：

| 来源 | 技术 | 采纳结果 |
|---|---|---|
| [SO #4425595](https://stackoverflow.com/questions/4425595/smooth-out-rounded-window-corners)（Trinidad，2010） | "Win32 只有硬边窗口区域；用带 Alpha 透明度的位图叠加**模拟**抗锯齿" | ✅ **采纳技法**（未抄代码）：四角小型点击穿透分层窗口，沿同一条超椭圆画描边 |
| [MS 文档：窗口圆角](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/apply-rounded-corners) | `DWMWA_WINDOW_CORNER_PREFERENCE`（含 ROUNDSMALL=3，均无自定义半径）、`DWMWA_BORDER_COLOR=DWMWA_COLOR_NONE` | ✅ 区域生效时 DONOTROUND + 去边框色，消除双层边框 |
| [Windhawk mod "Custom Window Corner Radius"（m417z）](https://windhawk.net/mods/custom-corner-radius) | 注入 dwm.exe 补丁内部半径常量，系统级真 AA | ❌ 需补丁系统进程，用户应用不可接受；也反证 DWM 内部裁切是 AA 的、仅未开放 API |
| Electron/Chromium 透明窗方案 | 宿主自合成全部内容 | ❌ 等于重写播放器（超出范围） |

最终边缘处理：区域切口上 1.1px 发丝线（α150）+ 向外三层递减软阴影弧（α80/45/22，宽 2.2–4.5px），阶梯带（±1px）被软阴影吞没，读作自然窗口投影；遮罩向角外扩 12 DIP 避免阴影弧被位图边界硬切。

### 已知限制

- 软阴影是描边模拟，非真 AA；直边附近阴影沿窗口外沿短暂延伸（α≤45，随弧端圆帽自然收尾），观感待实拍评审。
- 默认 48 DIP（小窗 ≈11%、大窗 ≈4% 屏宽，iPhone↔iPad 区间）；测试可至 160。
- 中止条件达成记录：未尝试桌面截图盖角、未重写播放器、未注入系统进程。

## 3. 未验证项（移交 Windows 实测）

- 拖拽手感（边/角比例锁定）、收敛观感（350ms 稳定后一次性重排）、外部窗口管理器拉锯
- DPI 100/150/200% 与混合 DPI 移屏；两个重叠会话；负坐标显示器
- G2 观感（32 DIP 默认值）、硬边锯齿接受度、DWM 阴影形态
- flex 应用模式（`--app`）全链路 + fixed 竖屏预设
- 打包版（PyInstaller）行为

## 4. 过程坑（给验证者）

- `Form.Show()` 后无消息泵 → WM_PAINT 不处理，背板永远黑色；pwsh 7 默认 MTA，WinForms 背板直接静默失败 → 改用“最小化窗口采桌面参考”彻底绕开背板
- WinPS 5.1 对 `@($w-9,8)` 嵌套数组字面量 + `New-Object TypeName(a,b)` 有解析陷阱；pwsh 7 + `::new()` + 整数预计算规避
- WSL `terminate()` 不杀 Windows 侧子进程 → 探测残留 scrcpy；收尾必须 `taskkill.exe`
- PowerShell stderr 是本地化 GBK，`text=True` 默认 UTF-8 解码会炸：`errors="replace"` 或丢弃

## 5. G2 回退记录（2026-09-05）

用户实测反馈（预览 1–7 轮，提交序列 8df005f → cc05092）：

| 症状 | 处置过程 | 结果 |
|---|---|---|
| 硬边锯齿 + 双层边框 | 描边遮罩 + `DWMWA_BORDER_COLOR=NONE`/`DONOTROUND` | 有改善，但描边模拟仍不达标 |
| 缩放卡顿 | 区域去重、缩放期摘除+300ms settle、遮罩仅尺寸变化重渲染（c4b2753） | 部分缓解，未根治 |
| 拖不动窗口 | 顶边热区 z 序被盖住（move 条与顶边条重叠）；区域逻辑位于 engaged 分支内导致永不落回 | 已修（cc05092：z 序每 tick 断言 + 区域逻辑移出 engaged） |
| 画面只显示一半 | 区域与 SDL 重布局/收敛竞争，未定位到根因 | 未解决 |
| 圆角观感“走偏” | 阴影弧收窄后仍不理想 | 用户决策回退 |

**决策**：默认 `corner_mode="system"`（Windows 系统圆角），G2 代码保留为选开实验项（`--corner-radius` / `corner_mode="g2"`），转长期目标。回退提交 `79cd93a`。重启条件见 docs/window-experience.md §3.4。
