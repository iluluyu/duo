# 系统下巴浮动岛：去贴边 + 玻璃通道调研定稿（2026-09-19）

> 用户报障：native 下巴「贴边 + 左右下角像素块丑陋」；玻璃要求改
> Windows 原生亚克力（或适合本场景的等价物），既美观又降低渲染压力；
> 玻璃关闭时同样不得有像素块。本文是该改造的唯一论述文档；
> glass-recipe.md §8.6 与 window-experience.md §10-§13 只留指路注释。

## 1. 根因：像素块 = corner ears

native 下巴原设计 = 贴在视频窗**正下方**的通栏 32 DIP 条
（`ChinWindow`，ULW 逐像素 alpha）。视频窗无 G2 region 时 Repair 保留
`DWMWCP_ROUND`，底圆角在拼接缝处「缺一块」。旧解法 = **corner
ears**：条窗向上长高 8 DIP，两个 8×8 直角方块盖在视频窗圆角缺口上
补缝。烘焙玻璃板不透明（A=255），耳朵是实心方角——即用户看到的
「左右下角像素块」。

## 2. Windows 原生背景效果通道（真机像素实证，2026-09-19，Win11 24H2）

复现台：记事本式假视频窗（彩色条纹 + 岛正后方判别红块）+
DuoChromeOverlay.exe 四象限（玻璃/普通 × 亮/暗）+ 抄屏像素分析。

| 通道 | 实测 | 结论 |
|---|---|---|
| `DWMWA_SYSTEMBACKDROP_TYPE = DWMSBT_TRANSIENTWINDOW`（22H2+ 官方文档通道）+ `DwmEnableBlurBehindWindow` 开逐像素 alpha + 黑底写零 alpha | 岛后纯红块对岛内部**零影响**（84/211 全平）——**只采样壁纸，不采样窗口内容**（Win11 任务栏同款「桌面亚克力」语义） | 盖在视频上 = 与内容脱节的死板，**弃用** |
| `SetWindowCompositionAttribute` accent（`ACCENT_ENABLE_ACRYLICBLURBEHIND`，未文档化） | 本机（24H2）返回 `0x1`：分层窗/非分层窗、OnHandleCreated/OnShown 时机**皆失败**（2026-09-10 历史注释正确）。pre-24H2 机器可用（TranslucentTB/TranslucentFlyouts 生产在用）；`ACCENT_ENABLE_BLURBEHIND` 自 22H2 被移除、flags=2「现代配方」忽略 tint | **机会通道**：尝试之，失败即回落（不依赖） |
| §8.6 自烘焙 frost（CopyFromScreen + σ8 CPU blur + 增益链）画在非分层岛窗上 | **生产路径**：条纹边缘 σ8 混色 ✓、红块过 Unit3 增益（饱和 ×1.65 → (237,22,19)）✓、白底过天花（ceiling 0.40/坡 0.15 → ~118）✓、_barDark 明暗探针与自适应药丸照常 | **本机定案** |
| Mica（`DWMSBT_MAINWINDOW`） | 只采样壁纸（文档明示） | 弃用 |

**窗口形态机制链**（jeweg/win32-window-transparency + TranslucentFlyouts
`EffectHelper.hpp` 双源）：普通窗口的重定向表面默认被 DWM 视为不透明；
`DwmEnableBlurBehindWindow`（region `CreateRectRgn(0,0,-1,-1)`）打开
逐像素 alpha；黑画刷/`PatBlt(BLACKNESS)` 写零 alpha = 该像素透出
backdrop/透明；GDI/GDI+ 直绘 DC 会丢 alpha——半透明内容走
`AlphaBlend`（AC_SRC_ALPHA，预乘源），且**双重 alpha**（AlphaBlend 对
透明表面混一次、DWM 对背后内容再混一次，真机像素 163 =
0.75²·255+0.25·84）→ 板 alpha 取 √A 补偿。`DWMWA_WINDOW_CORNER_
PREFERENCE = DWMWCP_ROUND` 的系统圆角与环境柔影对 accent/frost 都
生效（右上/右下角 2D 像素图验证，边缘随行渐进 = 真圆弧）。
`Environment.OSVersion` 无 manifest 会谎报（Win8 档），build 判定走
`RtlGetVersion`。

## 3. 设计定稿（实现事实，chrome_overlay.cs）

### 3.1 几何：浮动岛（三态只动 native）

- native 下巴 = **窗内底部浮动岛**：左右各 12 DIP（`ChinMarginSide`）、
  底部 12 DIP（`ChinMarginBottom`），高 32 DIP 不变（`LogicalHeightNative`）；
- 耳朵/贴缝/inset 任务栏保护（fullscreen/noRoom 分支）全部退役——岛
  永远在视频窗矩形内；窗口过小（高 < 岛高+边距+8 或宽 < 4×岛高）隐藏；
- immersive（ghost 药丸）与 none 两态不动。

### 3.2 窗口类型与材质矩阵

22H2+（`Controller.OsBuildNumber ≥ 22621`，RtlGetVersion）：
native 下巴 = **非分层普通窗**（去 `WS_EX_LAYERED`，保留
`WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW`）+ `DWMWCP_ROUND` 系统
圆角 + 柔影 + `DWMWA_USE_IMMERSIVE_DARK_MODE` 随 `--bar-theme`：

| 玻璃开 | 玻璃关（--glass 0） |
|---|---|
| ① SWCA accent 亚克力（pre-24H2 机会通道；成功 = DWM 侧零 CPU 真模糊，药丸恒白 + √A AlphaBlend）→ ② 失败回落 **frost 直绘非分层窗**（采样/烘焙/`_barDark` 探针全链照常，板 A=255 不透明直贴）+ **主题混底 tint**（暗 #202020@35% / 亮 #F3F3F3@40%，glm 评审定档，跟 `_barDark` 探针）+ hairline（暗白 14% / 亮黑 8%，玻璃路径新增） | `DWMSBT_NONE` + PlainBar 不透明色（亮 #F3F3F3 / 暗 #202020）+ 常驻 hairline（暗 0.10→0.14） |

< 22H2：维持 ULW 分层窗老路径（frost 管线本体不变，采样/烘焙共享），
几何同浮动岛、四角 8 DIP 圆角蒙版；tint/hairline 与 DWM 窗同配方
（DrawAcrylic 玻璃分支同步改造）。玻璃关同为不透明系统面。

### 3.3 渲染压力账

- 本机（24H2）实际走 frost：CopyFromScreen 采样区从旧「通栏条+3σ」
  缩到「岛矩形+3σ」（约 -35% 面积），300ms 心跳与 σ8 烘焙（<1ms）不变；
- accent 可用的机器（pre-24H2）：零采样零 CPU，DWM 合成器侧模糊；
- 玻璃关：零采样零模糊（两窗型相同）。

## 4. 验收记录（2026-09-19 真机复现台，四象限截图

`docs/validation/assets/chin-{glass,plain}-{light,dark}.png`；复现台
脚本 `src/rustduo/scripts/fake_video_window.ps1`）

1. ✅ 浮动岛：四边 inset，无任何贴边；耳朵代码全数移除；
2. ✅ 四角圆弧（DWMWCP）+ DWM 环境柔影，无方角像素块；
3. ✅ 玻璃开（本机 = frost+tint@岛）：σ8 内容模糊（条纹混色）、增益+
   混底后红块收敛（R 峰 237→169/177，低于 200 阈值 → Unit3 饱和保留）、
   白底落点 87/90（= 0.65·118+0.35·32，数学吻合）、明暗探针、药丸可见；
4. ✅ 玻璃关：暗 (32,32,32)=#202020 / 亮 (243,243,243)=#F3F3F3 精确
   token + hairline 0.14/0.078，同样无像素块；
5. ✅ csc C#5 真编译过；`cargo test`（含 chrome 源标记 23 项）/clippy/
   fmt 全绿；Rust argv 契约零改动；
6. ✅ glm-5.3-flash 视觉替补评审（指定评审员 agy 配额耗尽）：几何 10 /
   圆角柔影 9.5 / 普通材质 8.5 / 药丸 8.5；玻璃质感初判 6 → 修改清单
   （tint/hairline/plain hairline 三项）落地复拍后达结案判据。

## 5. 评审与迭代记录

- **glm-5.3-flash 替补评审**（2026-09-19，agy/gemini-3.8-flash 配额
  耗尽 44h，按项目交叉验证惯例顶位；配额恢复后可重跑 agy 终审）：
  初判玻璃质感 6 分（零 tint 下明暗两档逐像素同图、饱和增益直出
  霓虹 (237,22,19)、玻璃路径无 hairline、plain 暗版 hairline 隐形）；
  修改清单：① frost 消费端主题混底（暗 35%/亮 40%，跟 _barDark，
  克制档——历史 72% 白 tint「奶白」投诉不回潮）② 玻璃路径 hairline
  （暗白 14%/亮黑 8%）③ plain 暗版 hairline 0.10→0.14 ④ 复拍红块
  收敛则 Unit3 饱和保留。三项落地 + 复拍：红峰 169/177 < 200 →
  饱和保留，结案判据达成。
- 遗留观察项：frost 300ms 心跳在动态视频上有拖影延迟（静态截图
  验不出，待真机视频自测）；accent 通道（pre-24H2）药丸恒白在亮
  视频帧上对比边际偏弱（已接受，SWCA 失败即不走该路）。
