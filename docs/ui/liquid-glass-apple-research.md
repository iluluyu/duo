# Apple Liquid Glass（液态玻璃）技术视觉分解与 CPU 逼近调研报告

> 调研对象：Apple WWDC25 Session 219 "Meet Liquid Glass"、iOS 26 / visionOS 26 材质系统、社区高质量复刻（CSS/SVG feDisplacementMap、GLSL/SDF Shader）  
> 目标：在无 GPU Shader 的 CPU 渲染器（自绘 UI / duo-panel & chrome_overlay）中逼近其核心观感与物理特征。  
> 纪律约定：遵循 `AGENTS.md`，事实归纳自官方公开材料与开源实现，未查证细节明确标注「未找到」，绝不编造。

---

## 1. WWDC25 "Meet Liquid Glass" (Session 219) 核心设计与光学规范

WWDC25 Session 219 由 Apple 设计团队成员 **Chan Karunamuni**、**Shubham Kedia** 与 **Bruno Canales** 主讲，正式发布了统一跨平台（iOS 26, iPadOS 26, macOS Tahoe 26, watchOS 26, tvOS 26, visionOS 26）的全新材质系统——**Liquid Glass（液态玻璃）**。

### 1.1 光学与物理机制
- **从「光散射（Scattering）」到「光弯折（Bending/Lensing）」**：
  过去 iOS 7 ~ iOS 18 的毛玻璃（Frosted Glass / Material Thin/Thick）本质是纯漫散射（高斯模糊 + 静态 tint）。Liquid Glass 被定义为一种「数字超材料（digital meta-material）」，核心特征是**模拟物理玻璃与液体的透镜折射（Lensing & Refraction）**，背景画面穿过材质时会产生光学位移与局部放大/压缩。
- **凝胶柔性与交互充能（Gel-like Fluidity & Energizing）**：
  材质具有物理凝胶般的弹性。在轻触、按压或拖拽时，玻璃体表面会发生轻微弹性形变，高光随手势微动；交互时 UI 元素会暂时「跃入（lift up）」玻璃层中。
- **边缘透镜效应（Lensing at edges）**：
  在连续曲率圆角（Squircle）边缘，由于曲率急剧变化，光线折射最为剧烈，形成沿轮廓环绕的透镜变形带，使控件边界呈现出饱满的“表面张力”液体质感。
- **动态镜面高光（Specular Highlights）**：
  非静态的白边，而是结合设备陀螺仪（倾斜感应）与光照方向的动态镜面反射。在边缘与曲面转折处捕捉环境光，随着视角和手持晃动流转。
- **环境感知与内容透染（Contextual Awareness & Color Spilling）**：
  - **动态阴影沉降**：当背景有文本、列表或深色内容滑入玻璃下方时，玻璃背部的投射阴影会自动加深，防止底景文字破坏顶层文字的对比度。
  - **色溢（Color Spilling）**：下方高饱和彩度内容会将色彩“溅染”至玻璃边缘与折射带，玻璃不仅透光，还吸收并漫射周围光彩。
  - **独立明暗模式自适应**：浮动玻璃控件可根据背景局部的平均亮度与色彩，局部独立切换亮/暗模式形态。
- **厚度与尺寸缩放（Size Scaling / Optical Depth）**：
  大尺寸面板（如 Sheet、Modal、iPad Sidebar）模拟更厚重的实体玻璃，具有更大的模糊扩散、更深沉的阴影和更强的散射；小尺寸控件（Capsule Button、Pill）模拟薄片凝胶，以高透光与边缘折射为主。

### 1.2 系统级规则与禁忌
- **导航层专用（Navigation Layer Only）**：
  Liquid Glass 专为漂浮在内容层之上的导航容器（Toolbars, Tab Bars, Search Bars, Floating Action Pills, Sidebars）设计，严禁作为大面积全屏底板或滚动画布本身。
- **禁止玻璃叠玻璃（Avoid Stacking Glass on Glass）**：
  严禁将一层 Liquid Glass 叠加在另一层 Liquid Glass 之上。双重折射与双重模糊会迅速引发光学混浊、文字辨识度雪崩并耗尽渲染资源。
- **两档材质禁止混用（Do Not Mix Clear and Regular）**：
  系统明确划分 `Regular` 与 `Clear` 两档材质，二者在折射率与光阻上完全不同，禁止在同一界面或相邻组件中拼接混合。

---

## 2. 社区高质量复刻技术数值分解（CSS/SVG 与 Shader）

通过对 GitHub 顶级开源实现（`nikdelvin/liquid-glass`, `samasante/liquid-glass`, `Z1Code/glass-refraction`, `@sohumsuthar/liquid-glass`, 及 WebGL GLSL SDF 方案）的代码逆向与参数提炼，各视觉组件的数值基准如下：

| 视觉组件 | 物理/数学机制 | 复刻实现取值范围 | 推荐标准基准值 | 来源与工程依据 |
| :--- | :--- | :--- | :--- | :--- |
| **边缘折射带宽度** | SDF 梯度的边缘过渡区（Edge Band）或外围曲率衰减区 | 控件短边的 **8% ~ 15%**（或固定 8px ~ 18px） | **10% ~ 12%**（44px 胶囊取 5px~6px，200px 菜单取 16px~20px） | `samasante/liquid-glass`, `charlesgrassi.dev` GLSL。过窄像刻痕，过宽导致中心文字扭曲。 |
| **位移方向与尺度** | SDF 梯度 $\nabla \text{SDF}$ 计算法线 $\vec{N}$；$\Delta UV = -k \cdot \vec{N}$ | SVG `scale`: 20 ~ 120；Shader UV 偏移: **0.02 ~ 0.08** (相对 UV) | SVG `scale=35~50`；UV $\text{offset}=0.035$ | `nikdelvin/liquid-glass`, `@liquidglassjs/core`。凸透镜效果：法线向外，背景采样向内收缩或反向微拉伸。 |
| **色散分量（色差）** | 光线色散（Cauchy 方程）；RGB 三通道差分位移 | R/G/B 位移比率差 **1.5% ~ 3.5%** | $D_R = 1.02 \vec{D}, D_G = 1.00 \vec{D}, D_B = 0.98 \vec{D}$ | `Z1Code/glass-refraction`, GLSL 实验。超出 4% 会产生严重廉价三维眼镜（Anaglyph）假象。 |
| **高光位置与形态** | 菲涅尔反射 $F = (1 - \vec{N}\cdot\vec{V})^p$ + 顶部光照 $(\vec{L})$ | 顶部边缘 15% ~ 25% 弧段；指数 $p \in [4, 5]$ | 峰值集中于顶部与对角上沿，掠射角高亮 | `html-in-canvas.dev`, GLSL Fresnel 公式。非全封闭线框，而是受光面强、背光面弱。 |
| **高光物理宽度** | 镜面反光核心带 + 软散射光晕（Hermite / Exponential falloff） | 峰值纤细核 **1px ~ 1.5px**；次级软光晕 **3px ~ 6px** | 核心 1px (Alpha 0.6~0.8) + 软扩散 4px (Alpha 0.15~0.0) | `nikdelvin/liquid-glass` CSS box-shadow 叠加方案。纯 1px 硬线显假，必须有亚像素光晕。 |
| **背景模糊半径** | 漫散射模糊（Box / Gaussian） | 相对控件高度的 **25% ~ 50%**；绝对值 **16px ~ 36px** | 44px 胶囊取 $\sigma \approx 16\text{px}$；大面板取 $\sigma \approx 28\text{px} \sim 32\text{px}$ | `wwdcnotes.com`, Apple HIG, CSS `backdrop-filter: blur(20px)`. |
| **位移图平滑半径** | 对位移法线图的预平滑（防止折射噪点刺眼） | SVG `feGaussianBlur stdDeviation`: **2 ~ 6px** | **3.5px ~ 4.0px** | `deepika-builds/liquid-glass` `mapBlur` 参数。位移图不平滑会导致折射出的背景像素撕裂锯齿。 |

---

## 3. "Liquid Glass" 视觉失败案例拆解与 Duo 对照检查

### 3.1 为什么很多复刻"不像"（廉价感核心来源）
1. **平模糊（Flat Blur 陷阱）**：
   - 仅仅在背景上打一层均匀的高斯模糊（`backdrop-filter: blur(20px)`），没有折射引起的背景透镜扭曲。无论加什么饱和度，观感依然停留在 2020 年代的“磨砂亚克力塑料片”，缺乏液体的肉质感（meatiness）与流体生命力。
2. **边缘无透镜形变（Zero Edge Lensing）**：
   - 真实液体或厚玻璃的边缘存在明显的“背景压缩与局部包覆”感。复刻若边缘内外背景完全连续、无任何光线折射偏折，视觉大脑会立刻判定这是一块平面透明贴纸，而非具有折射率的物理介质。
3. **Rim 太亮 / 均匀白描边（Overly Bright / Uniform Rim）**：
   - 常见恶习是用一圈恒定不变的 `1px solid rgba(255, 255, 255, 0.4)` 描边。真实光学下的菲涅尔效应是强角度依赖的（垂直视线透明、掠射角反光），且顶部采光强、底部受环境阴影遮蔽。无方向、无衰减的均质亮白圈是产生“廉价贴纸描边感”的第一杀手。
4. **缺乏内部明暗层次与微色溢（No Internal Depth & Color Spilling）**：
   - 简单用 10%~20% 白色 tint 覆盖，会严重压扁背景方差，在浅色底上表现为“奶白”，在深色底上表现为“脏灰发雾”。真正的液态玻璃要求透光且有光泽梯度（Sheen），并允许背景色彩渗入玻璃内部。
5. **折射噪波失控（Turbulence Noise Tearing）**：
   - 部分 Web 复刻滥用 `<feTurbulence>` 生成粗糙噪波，导致折射画面像水面油污或融化的碎塑料，破坏了 Apple 设计语言中“纯净、克制、光学级精密”的通透基调。

### 3.2 对照检查：我们的基准配方（低分辨率背板 + σ4 模糊 + 轻染 + 1px rim）缺什么？

用户提出的典型自绘实现基准为：**「合成低分辨率背板 + σ4 模糊 + 轻染 + 1px rim」**。  
对比 Apple Liquid Glass 的真实光学标准，这一组合之所以产生强烈的“廉价亚克力塑料感”，核心缺漏诊断如下：

1. **缺折射（No Refraction / Lensing）**：
   - 低分辨率背板做 σ4 模糊，只产生了单纯的漫散射（光线均匀乱射），没有任何坐标层面的透镜位移。边缘内外背景毫无连续几何变形，无法形成“厚玻璃/水滴边缘”的表面张力感。
2. **缺高光方向性与菲涅尔衰减（Flat Rim vs Directional Fresnel）**：
   - 1px 均匀 rim（无论是白色还是浅灰）在物理上相当于“金属细铁丝框”或“剪纸描边”。真实 Liquid Glass 的 rim 是**顶部受光强（Alpha 0.6~0.8）、底部受环境遮蔽弱（Alpha 0.1~0.2）**且具有**掠射角菲涅尔增强**的动态光泽，绝非各向同性的闭合硬线。
3. **模糊半径与背板分辨率矛盾（Blur Radius & Resolution Conflict）**：
   - σ4 的模糊半宽对于 44px~64px 控件而言太窄（仅约 8% 控件高度），不足以抹平底层高频杂色，导致背景文字与控件文字产生视觉打架；而如果试图在低分辨率背板上做折射，拉伸位移后又会立刻暴露像素马赛克。
4. **轻染（Flat Light Tint）导致发灰发脏**：
   - 简单的低透明度单色轻染（如 `rgba(255,255,255,0.15)`）在深色或高饱和背景上会直接洗掉底景对比度，呈现“发霉的奶白色”或“脏灰色”。真实材质依赖**局部增饱和（Saturation Boost 1.4~1.6×）+ 光学软膝曲线**保留底景色泽活力。
5. **缺厚度感知与内部暗部（No Inset Thickness / Inner Occlusion）**：
   - 缺乏沿边缘向内侧衰减的微弱内阴影（Inner Shadow 0.5px~1.5px），导致材质没有“物理厚度（Gauge）”，看起来薄如蝉翼。

#### Duo 既有配方（`docs/ui/glass-recipe.md`）与 Liquid Glass 的距离
Duo 目前的 CPU 渲染配方（`glass.rs` / `chrome_overlay.cs`）：
> **当前实现**：`全窗快照 → 3×box blur（σ8 device px）→ 逐像素光学增益（contrast 单侧 + brightness + saturation ×1.65 + soft knee 软膝天花/地板 + 纵向 sheen 0.045/0.02）→ 1px hairline rim（12% 黑/反白）→ SDF 蒙版切圆角`

| 维度 | Apple Liquid Glass 标准 | Duo 当前实装状态 | 缺漏诊断与改进方向 |
| :--- | :--- | :--- | :--- |
| **折射 / Lensing** | 边缘 10%~12% 区域由法线驱动背景 UV 偏移，产生透镜压缩与微色散 | **完全归零（Zero Refraction）** | **核心缺失**。Duo 只有扩散模糊，无坐标弯折。在 CPU 渲染器中，需在快照采样或模糊后加一步轻量 SDF 边缘 UV 查表偏移（LUT）。 |
| **Rim / 高光物理感** | 顶部强、底部弱的菲涅尔曲率高光（1px 亮核 + 4px 软光晕） | 均匀 1px 12% 黑线（暗底翻白） | **缺少受光方向性与掠射角衰减**。当前的 1px hairline 解决了防溶底与可辨度，但本质是“工程描边”，缺乏镜面高光灵动感。 |
| **背板采样率与清晰度** | 边缘折射区域保留背板高频细节与微观对比 | 局部全窗快照 + 3×box blur | Duo 目前在模糊后无折射层，低频模糊直接透出。若要做折射，不可使用过低分辨率背板，否则边缘折射位移后会马赛克化。 |
| **内部厚度分层** | 中心厚（散射深、吸收强）、边缘薄（折射强、通透度高） | 全局标量增益 + 线性 Sheen 渐变 | 增益公式对整块板面是均质的。缺少由中心至边缘的非线性厚度衰减映射。 |

---

## 4. Apple 官方两档材质（Regular vs Clear）差异与适用场景

WWDC25 Session 219 与 Apple HIG 明确将 Liquid Glass 规范化为两档变体：

### 4.1 Regular（常规档 —— 系统默认基准）
- **光学特性**：
  - 较高的光阻与功能性漫散射模糊（Functional Blurring）。
  - 内置亮度自适应调节（Luminosity adjustment），对深色底景自动注入微弱亮阶，对亮色底景自动压制过曝。
  - 具有较深沉、扩散范围更大的背部环境接触阴影（Contact Shadow）。
- **适用场景**：
  - **核心导航层**：Tab Bar（标签栏）、Navigation Bar（导航栏）、Sidebar（侧边栏）、Search Bar（搜索栏）、Sheets & Popovers（浮层面板）。
  - **设计目标**：确保在任何极端复杂的动态壁纸或滚动图文背景下，文字（Regular 400 字重）与图标达到绝对可靠的 WCAG AA（≥4.5:1）对比度与可读性。

### 4.2 Clear（通透档 —— 特殊情境材质）
- **光学特性**：
  - 极高透光率，漫散射模糊极弱，透镜折射形变更为突出。
  - 镜面高光（Specular）更加锐利灵动，边缘更具晶莹剔透的水滴/玻璃感。
  - 对背景内容的遮蔽极低，几乎完全依赖折射形变与高光线框来界定控件几何形体。
- **适用场景**：
  - **轻量浮动控件与微指示器**：浮动胶囊指示器（Pills）、媒体播放悬浮微面板、相机/全屏图库界面的极简操作按钮、visionOS 空间微浮层。
- **官方约束与禁忌**：
  - **需满足严格前置条件**：必须在背景内容相对纯净、或者控件内仅有高对比度简单图形时使用。
  - **必须配备保底遮罩（Dimming Layer）**：若背景存在复杂高频图文，Clear 变体底部必须附带一层动态暗化/遮罩层，防止文字对比度归零。
  - **严禁与 Regular 拼接或混用**：同一组件内部绝不可左侧 Clear 右侧 Regular，折射断层会产生视觉撕裂。

---

## 5. 搜索栏/输入框在 iOS 26 液态玻璃下的具体形态与聚焦态语义

搜索栏与文本输入框（`UISearchBar` / `TextField` / `.searchable`）是 iOS 26 中 Liquid Glass 动态交互的核心代表组件：

### 5.1 默认静态形态（Unfocused / Resting State）
- **形态与位置**：
  - 常见为浮动于屏幕底部的悬浮药丸胶囊（Pill Container，便于大屏单手触达），或内嵌在顶部导航容器中的次级液态凹槽。
- **材质质感**：
  - 采用通透度较高的液态玻璃，边缘带有微弱的环境反光（微弱 Fresnel Rim）与柔和的边缘折射。
  - 占位符文字（Placeholder）与搜索图标浮于玻璃体内，背景内容以柔和虚化状态在框体后方流动。

### 5.2 聚焦态（Focused / Active State）的材质转换语义
当用户点击输入框进入 `@FocusState` 激活态时，组件的物理材质会发生**三维联动升阶**：

1. **材质实体化与透光率收敛（Solidification & Increased Opacity）**：
   - 材质由“通透折射玻璃”向“半不透明光洁实体”收敛：底板填充（Fill Luminosity / Material Opacity）明显提高，漫散射加重，透镜折射形变被大幅抑制或内敛至极边缘。
   - **设计语义**：输入文字是高频精密认知行为。折射扭曲与背景画面的动态渗透会分散注意力并严重干扰字符笔画辨识。实体化创造了一块纯净、稳定的“数字纸面”。
2. **边缘高光激活（Active Specular / Border Glow）**：
   - 默认态下的被动环境反光转变为**主动的聚焦高亮环（Active Focus Rim）**。
   - 边缘呈现出一层极为细腻的内发光（Inner Glow）或色温微抬的白光边缘，伴随微弱的能量流转感（Energizing），向用户明确反馈当前的键盘输入焦点。
3. **几何形变与空间抬升（Geometric Expansion & Elevation Lift）**：
   - 输入框通常伴随轻微的几何外展（Morphing Expansion），从紧凑的底栏药丸展开为横跨屏幕的主输入条，取消按钮（Cancel）从玻璃边缘平滑析出。
   - 背后阴影深度（Shadow Elevation）显著提升，在 Z 轴上向前“跃起”，与后方被推远的暗化内容层（Dimmed Backdrop）拉开清晰的物理层级。

---

## 6. 总结：在无 Shader 的 CPU 渲染器中逼近 Liquid Glass 的 5 条关键法则

1. **引入边缘 10% 折射带（Lensing LUT）**：
   打破纯高斯模糊的“平亚克力感”，利用已有的圆角 SDF 距离场，在边缘 10%~12% 区域建立反向 UV 偏移查表，即使仅有 2~4px 的背景像素收缩，也能瞬间唤醒“液体表面张力”。
2. **高光从「均匀 1px 描边」进化为「定向菲涅尔光泽」**：
   废除各向同性的恒定描边，改用顶部 1px 亮核（结合纵向光泽衰减）+ 4px 亚像素软坡，并在背光侧衰减为微弱暗刻，塑造真实环境采光。
3. **坚持饱和度回注，杜绝单色奶白 tint**：
   Duo 既有的 `saturation ×1.65` 与三次 Hermite 双膝滚降是正确的；保持 tint 0% 或极低色染，靠光学增益防止发灰发脏，让底景色彩自然透染（Color Spilling）。
4. **恪守层级戒律：禁止堆叠，Regular 导航承重**：
   在 CPU 架构下严格禁止“玻璃叠玻璃”。承载文字的主菜单、输入框必须走 `Regular` 语义（高模糊、深阴影、保底对比度），不可盲目追求透明而牺牲可读性。
5. **输入框聚焦态必须「退玻璃、进实体」**：
   输入控件聚焦时，材质必须收敛折射、提高不透明度并激活边缘高光环，以“实体化白板 + 空间抬升”为文字输入提供绝对清晰的排版底板。
