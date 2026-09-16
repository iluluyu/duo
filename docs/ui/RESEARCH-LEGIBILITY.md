# 玻璃上的文字可读性：厂商做法与 Duo 对策（2026-09-14 调研 + 定稿）

> 问题：右键菜单毛玻璃好看，但底景与墨色同调时字读不出来。
> 本文记录厂商侧（Apple / Microsoft）与社区的做法、为什么"直接加厚"在
> Duo 不成立，以及最终实装的"文字保底光晕"。配方细节在
> glass-recipe.md §7，代码在 Main.qml 的 MenuLabel + Style.menuInkHalo。

## 0. 问题是真的（本机实测，不是观感之争）

玻璃 = 底景模糊 + 光学增益 + tint 0%（tint 0% 是 2026-09-10 用户拍板的
毛玻璃化定稿）。tint 0% 的代价：**底景的明度原样透上来**，只有"底景 =
画布"时才安全。菜单却会飘到图标、卡片、封面这类方差大的内容上。

`docs/ui/probes/text_halo_probe.py` 在 Qt 软件后端上按配方算玻璃色、渲染
真实文字、逐像素量"字形边界对比度"（相邻像素最大亮度步长的 WCAG 比值）：

| 场景 | 玻璃色 | ink:glass | 字形边界 | 可见边缘像素 |
|---|---|---|---|---|
| 亮色菜单 / 深色图标 | #1B1B1D | **1.02:1** | 1.02:1 | **0（字消失）** |
| 亮色菜单 / 中灰图标 | #858585 | 4.57:1 | 4.56:1 | 1311 |
| 亮色菜单 / 白底画布 | #FFFFFF | 16.83:1 | 16.83:1 | 1547 |
| 暗色菜单 / 白底图标 | #FFFFFF | **1.09:1** | 1.09:1 | 904（几乎不可辨） |
| 暗色菜单 / 中灰图标 | #8D8D8D | 3.06:1 | 3.05:1 | 1499 |
| 暗色菜单 / 暗色画布 | #212123 | 14.80:1 | 14.76:1 | 1504 |

即"菜单盖住深色图标（亮色主题）/ 白底图标（暗色主题）"时字等于没有。
AA 门槛 4.5:1（11–13px 正文）。

## 1. Apple

HIG / Materials 与 Liquid Glass（WWDC25）：

- **变体按语义选，不按颜色选。** "The regular variant blurs and **adjusts
  the luminosity of background content** to maintain legibility of text and
  other foreground elements." — 关键词是 *adjusts the luminosity*，不只是
  模糊。判据："Use the regular variant when background content **might
  create legibility issues**, or when components have **a significant amount
  of text**, such as alerts, sidebars, or **popovers**." 右键菜单正落在这
  一条：文字密集 + 底景不可预测 → 走 regular。
- **clear 变体才需要暗化层，而且按底景明暗决定。** "For optimal contrast
  and legibility, determine whether to add a dimming layer behind components
  with clear Liquid Glass: **If the underlying content is bright, consider
  adding a dark dimming layer of 35% opacity.**"
- **厚度换对比。** "Thicker materials, which are more opaque, can provide
  better contrast for text and other elements with fine features. Thinner
  materials … help people retain their context."
- **前景用 vibrancy。** "Help ensure legibility by using **vibrant colors**
  on top of materials."（普通灰字在材料上对比不足，系统 vibrant 色随材料
  自适应）；visionOS 的同一机制写得更直白："To ensure foreground content
  remains legible when it displays on top of a material, visionOS applies
  **vibrancy** to text, symbols, and fills." + "Glass is an adaptive
  material that **limits the range of background color information** so a
  window can continue to provide contrast for app content."
- **无障碍两条路**：reduce transparency（更厚/实底）、increase contrast
  （更硬的边缘与颜色）。
- 社区侧的现状：Liquid Glass 的文本对比是开发者最常抱怨的点（iOS 26 上线
  后大量帖子/文章），Apple 给的处方是"用 regular 变体、别玻璃叠玻璃、用
  系统组件（系统会做 adaptive shadow / vibrant text）"；第三方自己加
  text-shadow 被视为绕路方案，不是系统药方。

## 2. Microsoft（Windows 11 Acrylic）

"Acrylic material" 官方页 How we designed acrylic 一段，把配方讲成了四层：

> "We started with translucency, blur, and noise to add visual depth and
> dimension to flat surfaces. **We added an exclusion blend mode layer to
> ensure contrast and legibility of UI placed on an acrylic background.**
> Finally, we added color tint for personalization opportunities."

两处要点：

- **exclusion blend 层**就是微软的"逐像素对比保底"（exclusion = |a−b| 型
  混合，会让前景与底景互相排斥），不是靠不透明度。再叠 TintOpacity /
  TintLuminosityOpacity 做主题化。
- **用途钉死在 transient surface**："Use background acrylic for transient
  UI elements … context menus, flyouts, non-modal popups, or light-dismiss
  panes"；"We've optimized the acrylic resources such that text meets
  contrast ratios on top of acrylic."；"**Don't place accent-colored text
  over acrylic surfaces**"；"Avoid layering multiple acrylic surfaces"。
- **降级即实底**：关闭"透明效果"、省电模式、高对比模式、低端硬件 → 纯色。

## 3. Web 社区（玻璃拟态的生产实践）

- 结论一致：**blur 只负责"糊掉细节"，不负责对比**。可读性必须在"能遇到的
  最亮与最暗底景"上验（web.dev 的 backdrop-filter 一文也是这个口径）。
- 通用配方 = 光晕（halo）：W3C 的对比度技法把"文字外描边/光晕"当作在
  变化或图案底上的合法保底；浅字 → 暗光晕（`0 1px 2px` 硬 + `0 0 8px`
  软），深字 → 亮光晕；忌讳又大又虚的阴影（反而脏）。
- 必要时再叠一层不透明度/tint 与实底 fallback（`@supports not
  (backdrop-filter)`）。

## 4. 为什么"直接加厚/加 tint"在 Duo 不成立

AA（4.5:1）对底景明度是硬约束：

- **亮色**：ink #1D1D1F（L 0.0129）→ 底景 L ≥ 0.233，即合成后至少要
  #858585 那么亮。要让**纯黑底景**也达标，固定白 veil 至少 ~55% 白——
  正是 2026-09-10 被否掉的"奶白"路径（当时是 72%/88%）。
- **暗色**：ink #F5F5F7（L 0.913）→ 底景 L ≤ 0.124，即最亮只能到
  约 #646464。压住白底图标需要 85%+ 的暗 veil，等于近实底的黑色菜单
  （macOS 暗色菜单确实是这个路子，但与 Duo 已定的"暗色海拔阶梯"和
  用户要的玻璃感冲突）。
- 也就是说：**固定参数只能"要么奶白、要么没救"**。Apple 说的
  "adjusts the luminosity"、微软的 exclusion blend，本质都是**逐像素**
  调整（visionOS 的原话：limits the range of background color
  information）——逐像素在 Qt 里要么自定义 fragment shader，要么
  MultiEffect 的全局仿射变换（后者会把底景方差一起压平，仍走向奶白）。
- 自定义 shader 的实际门槛：开发机在 WSL（无 GL，验证不了光栅）；Windows
  真机走 D3D11 后端，Qt 6 内联 GLSL ShaderEffect 不保证可用（预编译
  .qsb 又要引入构建期工具链）。这类"本地看不见、真机才出问题"的东西在
  本仓库有前科（autoPadding 直角 bug）。

结论：**把保底做在文字侧**——局部、方向性、零 pass、本地可实测。

## 5. 定稿：文字保底光晕（vibrancy，软光晕版）

第一版实装是 1px 反向硬描边（Qt 的 `Text.Outline`）。它能救可读性，但用户
反馈「**变粗 + 廉价**」，量化后原因很清楚：

| 方案 | 字形覆盖率面积 | 边缘形态 |
|---|---|---|
| 清晰文字（基线） | — | 正常光栅 |
| 1px 硬描边（alpha 85%） | **+146%** | 硬边，浅玻璃上读作白圈 |
| 1px 硬描边（alpha 40%） | **+146%**（与 alpha 无关） | 硬边但略淡 |
| 软光晕（模糊剪影） | **+0%** | 无硬边，渐变退让 |

描边改的是**字形轮廓**（Pen 居中于轮廓、向外扩半个像素），细笔画上面积
接近翻倍，13px 正文足以把 Regular 读成 Medium；alpha 只影响边缘深浅，
不改变"变粗"。

**定稿 = 软光晕**：把同文字的模糊剪影（同向低 alpha）垫在清晰文字后面，
清晰文字走与普通 Text 完全相同的渲染路径（零 FBO、零 layer）。参数：亮
38% 白 / 暗 50% 黑，σ ≈ 2.8px（`menuHaloBlur 0.35 × blurMax 16`）。

选型证据（`docs/ui/probes/menu_halo_candidates.py`，可重跑）：

| 方案 | 最坏落点光晕对比度 | 主题画布可见度 | 字重 |
|---|---|---|---|
| A 硬描边 85% | 14.8:1 | 1.00 | **+146%** |
| B 硬描边 40% | 12.3:1 | 1.00 | **+146%** |
| C 软光晕 2.5px 55% | 11.0:1 | 1.00 | +0% |
| D 软光晕 5px 40% | 10.2:1 | 1.00 | +0%（评审：太宽发虚） |
| E 无保底 | 2.4–2.9:1（字丢） | — | +0% |
| **实装：软光晕 2.8px，亮 38% / 暗 50%** | **10.2:1 / 13.2:1** | **1.00 / 1.05** | **+0%** |

两份品味评审各自独立选中了软光晕（zai 视觉：图侧，指出 A/B 的硬白圈与
B 同样变粗；agy Opus：设计侧，建议同向低 alpha、σ 3~3.5、并提出铁律 8
的例外条款）。α 由两侧建议与实测共同定：亮色 38%（≤ 40%），暗色 50%
（黑晕在暗玻璃上实测不可见，可见度 1.05，故额度可以更高）。

## 5.5 呼出菜单时保底光晕的边界（诚实记录）

平铺极端（整块纯黑/纯白垫在菜单下——现实里不存在，滑窗实测最坏是
ink:glass 2.43 / 2.27）软光晕只能把字形边界托到 3.9–4.5:1，是"吃力可读"
而不是稳稳 AA。要让这种极端也过 AA 需要 α ≈ 0.93（白晕）——那就是硬描边
的量级，会把用户刚否掉的"廉价"请回来。取舍：保现实落点（实测 10:1 量级），
极端记录在案。

## 6. 为什么不用另外两条路（留档）

- **整菜单一层 MultiEffect shadow（而不是每标签）**：省 pass，但保底层会
  同时糊到分隔线、hover 洗色与圆点上（haze 痕迹），取舍后按标签做
  （每个标签一个小 FBO，菜单关闭即无）。
- **呼出菜单时全窗降噪**（iOS 长按菜单会全屏模糊 + 压暗；Opus 的第六路是
  "按区域明度动态补 tint"）：Duo 的亮色画布本来就亮，压暗反而伤墨色；
  提亮则整窗发白，而且抓屏测均值的异步链路在真机上不好验收（本仓库吃过
  "本地看不见、真机才出问题"的亏）。留作后续，若真要做得配一条实测验收
  链（滑窗最坏落点 + 主题画布可见度两个数字）。

## 7. 已知残留

- 比例二级菜单里的"示意小矩形"是 `Image`（SVG），描不了边；它靠 ink2
  描边自身与玻璃的差，深色底景上会掉对比（可读性损失小于文字，暂留）。
- 勾选圆点（4px 强调色）压在同色底景上也会糊；选中态另有文字栅格可辨。
- 真机 GL 路径（玻璃合成 + 1.25×/1.5× DPR 下的描边光栅）仍需在 Windows
  上复核：探针可原样重跑（`docs/ui/probes/text_halo_probe.py`）。

## 8. 来源

- Apple HIG, Materials — regular/clear 变体、luminosity、dimming layer、
  thickness、vibrancy、无障碍降级：
  https://developer.apple.com/design/human-interface-guidelines/materials
- Apple HIG, Context menus（菜单克制的行为规范）：
  https://developer.apple.com/design/human-interface-guidelines/context-menus
- NSVisualEffectView（material / blendingMode / vibrancy 语义）：
  https://developer.apple.com/documentation/appkit/nsvisualeffectview
- WWDC25 "Meet Liquid Glass"（常规变体动态调明度、vibrant text）：
  https://developer.apple.com/videos/play/wwdc2025/219/
- Microsoft, Acrylic material（exclusion blend 层、transient 用途、降级）：
  https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic
- W3C WAI, Contrast (Minimum) 1.4.3 与 G145（光晕/描边技法）：
  https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum
- web.dev, Create OS-style backgrounds with backdrop-filter（blur 不保证
  对比，需在极端底景上验）：
  https://web.dev/articles/backdrop-filter
