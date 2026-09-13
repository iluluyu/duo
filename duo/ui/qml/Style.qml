pragma Singleton
import QtQuick

/*
 * Style.qml - Duo QML 视觉令牌单例（主面板与设置页共享的唯一色板）。
 *
 * 亮/暗双值令牌（2026-09-12 暗色模式）：dark 绑定 ctrl.effectiveDark
 * （app.py 注入的 PanelController；无 ctrl 的装载环境折叠为亮色），
 * theme = light|dark|system 的解析与系统跟随在 controller 侧完成。
 * 暗色数值与依据见 docs/ui/DESIGN.md §2 对照表（设计：glm-5.3-flash ×
 * gemini-3.8-flash 交叉，Opus 终审）。
 */
QtObject {
    id: root

    // ---- 主题状态 ---------------------------------------------------------
    // 保存/系统切换即时生效：绑定 ctrl.themeChanged → effectiveDark 通知链
    property bool dark: typeof ctrl !== "undefined" && ctrl ? ctrl.effectiveDark : false

    // ---- 画布与文字 -------------------------------------------------------
    /// 中性画布背景。
    readonly property color bg: root.dark ? "#1C1C1E" : "#F5F5F7"
    /// 主文字。
    readonly property color ink: root.dark ? "#F5F5F7" : "#1D1D1F"
    /// 次级文字（说明、serial、计数）。
    readonly property color ink2: root.dark ? "#98989D" : "#86868B"

    // ---- 语义色（仅限指定用途）--------------------------------------------
    /// 唯一强调色：主按钮 / 选中 / 链接（暗色提亮防近黑发闷）。
    readonly property color accent: root.dark ? "#0A84FF" : "#007AFF"
    /// 强调色 hover/press 阶梯（仅实底主按钮使用）。
    readonly property color accentHover: root.dark ? "#409EFF" : "#2E90FF"
    readonly property color accentPress: "#0066D6"
    /// 运行指示点专用绿：仅用于"在线 / 运行中"语义，禁止装饰性使用。
    readonly property color running: root.dark ? "#30D158" : "#34C759"
    /// 探测可用（绿）：probe 成功语义。
    readonly property color success: "#30D158"
    /// 警示（琥珀）：引擎锁定提示等。
    readonly property color warn: "#FF9F0A"
    /// 错误 / 停止类语义。
    readonly property color danger: root.dark ? "#FF453A" : "#FF3B30"
    /// 危险动作 hover 洗底 rgba(255,59,48,0.08)（运行芯片 ✕ 等，§3.7）。
    readonly property color dangerWash: "#14FF3B30"

    // ---- 玻璃卡片 ---------------------------------------------------------
    /// 卡片填充：亮 rgba(255,255,255,0.72) / 暗 rgba(255,255,255,0.10)
    /// （暗色海拔语义：抬升面亮于画布，合成底 ≈ #323234；zai 视觉验收
    /// 定稿——黑透卡“发闷”被否，见 docs/ui/DESIGN.md §2）。
    readonly property color cardFill: root.dark ? "#1AFFFFFF" : "#B8FFFFFF"
    /// 分段选中段：不透明实底（亮纯白 / 暗 systemGray3——与胶囊底拉强色差）。
    readonly property color segmentFill: root.dark ? "#48484A" : "#FFFFFFFF"
    /// 卡片 1px 亮边：亮 65% 白 / 暗 16% 白（低亮轮辋光）。
    readonly property color cardBorder: root.dark ? "#29FFFFFF" : "#A6FFFFFF"
    /// 旧卡片阴影色 rgba(0,0,0,0.10)：主面板已全界面零阴影（铁律 8）；仅
    /// 设置页旧卡投影仍引用，随其下线一并删除。
    readonly property color cardShadow: "#1A000000"
    /// 卡片圆角（DESIGN.md §2：卡 16）。
    readonly property int cardRadius: 16
    /// 浮层填充（聚焦搜索的亚克力）：亮 60% 白 / 暗 7% 白（介于画布与卡之间）。
    readonly property color flyoutFill: root.dark ? "#12FFFFFF" : "#99FFFFFF"
    /// 菜单浮层（软件回退底色，不透明保可读；暗色介于画布与卡之间）
    readonly property color menuFill: root.dark ? "#2C2C2E" : "#F7F7F9"
    /// 毛玻璃染色：tint 0%（玻璃即材质，光学增益在模糊层
    /// brightness/contrast/saturation，见 glass-recipe.md）
    readonly property color menuTint: "#00FFFFFF"
    /// 二级浮层染色（elevation 由 blur/sat/bright/contrast/描边阶梯表达）
    readonly property color menuTintHi: "#00FFFFFF"
    /// 菜单 1px 描边（暗底翻白；纯画布上 ~35 级刻痕防溶底）
    readonly property color menuBorder: root.dark ? "#24FFFFFF" : "#1F000000"
    /// 二级浮层描边（阶梯差）
    readonly property color menuBorderHi: root.dark ? "#2EFFFFFF" : "#24000000"
    /// 菜单毛玻璃参数（GL 路径）：一级 / 二级阶梯；暗底玻璃略亮于底板
    /// （暗色海拔语义），contrast 0.5 轴心拖暗近黑由 brightness 加倍补偿
    readonly property real menuBlur: 0.75
    readonly property real menuBlurHi: 0.85
    readonly property real menuSat: 0.45
    readonly property real menuSatHi: 0.50
    readonly property real menuBright: root.dark ? 0.05 : 0.02
    readonly property real menuBrightHi: root.dark ? 0.07 : 0.04
    readonly property real menuContrast: root.dark ? 0.08 : 0.06
    readonly property real menuContrastHi: root.dark ? 0.10 : 0.10
    /// 菜单 1px 描边（软件回退路径；暗底翻白）
    readonly property color menuFillBorder: root.dark ? "#1AFFFFFF" : "#1A000000"
    /// 浮层圆角（DESIGN.md §2：浮层 12）。
    readonly property int flyoutRadius: 12
    /// 控件圆角（条目 / 洗色块等，DESIGN.md §2：控件 10）。
    readonly property int controlRadius: 10

    // ---- 控件底材 ---------------------------------------------------------
    /// 输入框 / 次按钮实底：亮纯白 / 暗沉入卡内的抬升面（比卡低一档）。
    readonly property color controlFill: root.dark ? "#28282A" : "#FFFFFF"
    /// 画布级搜索胶囊（不在卡上）：亮 = 卡语言半透明（DESIGN §3.6），
    /// 暗 = 输入底沉一档（zai 验收：与卡拉开层级）。
    readonly property color searchFill: root.dark ? "#28282A" : "#B8FFFFFF"
    /// 禁用控件底（洗色）。
    readonly property color controlFillDisabled: root.dark ? "#0FFFFFFF" : "#08000000"
    /// 深底胶囊（Toast / 探测结果）：白字，两主题同构（暗色换灰底）。
    readonly property color pillFill: root.dark ? "#E648484A" : "#E61D1D1F"

    // ---- 画布装饰色斑（三层同心衰减；暗色降 1–2pt 防"嗡"）----------------
    readonly property color spotBlueOut: root.dark ? "#07007AFF" : "#09007AFF"
    readonly property color spotBlueMid: root.dark ? "#0A007AFF" : "#0E007AFF"
    readonly property color spotBlueCore: root.dark ? "#12007AFF" : "#16007AFF"
    readonly property color spotGreenOut: root.dark ? "#0534C759" : "#0734C759"
    readonly property color spotGreenMid: root.dark ? "#0934C759" : "#0C34C759"
    readonly property color spotGreenCore: root.dark ? "#1034C759" : "#1434C759"

    // ---- 交互状态 ---------------------------------------------------------
    /// 悬停洗色（暗底翻白：瞬态反馈在近黑上需略强才可感）。
    readonly property color hoverWash: root.dark ? "#0FFFFFFF" : "#0A000000"
    /// 按下洗色（保持 1:2 层级）。
    readonly property color pressWash: root.dark ? "#1FFFFFFF" : "#14000000"
    /// 输入框 / 滑槽描边。
    readonly property color hairline: root.dark ? "#24FFFFFF" : "#1F000000"
    /// 全局标准过渡时长（ms）；无常驻动画。
    readonly property int durFast: 140

    // ---- 玻璃模糊开关 -----------------------------------------------------
    /*
     * 菜单毛玻璃双闸门：着色器能力（glassBlur）× 用户玻璃材质开关
     * （glassWanted，settings glass_enabled——上巴/下巴/右键菜单统一总开关，
     * 2026-09-12 实装）。任一为假 = 不透明 menuFill 回退：
     *   GL（Windows 真机 ANGLE/OpenGL）= 真·高斯模糊（三明治结构见
     *   Main.qml 的 MenuGlassPlate）；软件渲染 / WSL / 用户关玻璃 = 回退。
     * 门槛 = app.py 注入的 shadersUsable 上下文属性（not is_wsl()）；
     * 无注入环境（测试装载）按 undefined → false 走软件路径。
     */
    readonly property bool glassBlur: typeof shadersUsable !== "undefined" ? shadersUsable : false
    readonly property bool glassWanted: typeof ctrl !== "undefined" && ctrl ? ctrl.glassMaterial : true
    readonly property bool menuGlass: glassBlur && glassWanted

    // ---- 图标占位 ---------------------------------------------------------
    /// 未知应用 fallback：首字 squircle 的柔和 12 色板（包名哈希取色）。
    /// 相对亮度 ≤ 0.42，托得住白字（DESIGN.md §3.1）；禁止灰底圆。
    readonly property var fallbackPalette: [
        "#5F7292", "#6F8468", "#96725D", "#7D6B94", "#628C87", "#946363",
        "#6C7FA3", "#829462", "#936280", "#628192", "#8C7163", "#746A92",
    ]

    // ---- 字体 -------------------------------------------------------------
    /// 全局字体族：Windows 首选 Segoe UI，其余平台自动回退。
    readonly property string fontDefault: "Segoe UI"
}
