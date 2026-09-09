pragma Singleton
import QtQuick

/*
 * Style.qml - Duo QML 视觉令牌单例（主面板与设置页共享的唯一色板）。
 *
 * 与 duo/ui/tokens.py 同一理念：纯常量、无主题引擎、无运行时切换——
 * 单一中性画布、唯一强调色、玻璃半透明、零阴影（铁律 8）、140ms 过渡。
 * 令牌值以本文件为准（QML 侧重构用新色板），tokens.py 属旧 Widgets 界面。
 */
QtObject {
    id: root

    // ---- 画布与文字 -------------------------------------------------------
    /// 中性画布背景。
    readonly property color bg: "#F5F5F7"
    /// 主文字。
    readonly property color ink: "#1D1D1F"
    /// 次级文字（说明、serial、计数）。
    readonly property color ink2: "#86868B"

    // ---- 语义色（仅限指定用途）--------------------------------------------
    /// 唯一强调色：主按钮 / 选中 / 链接。
    readonly property color accent: "#007AFF"
    /// 强调色 hover/press 阶梯（仅实底主按钮使用）。
    readonly property color accentHover: "#2E90FF"
    readonly property color accentPress: "#0066D6"
    /// 运行指示点专用绿：仅用于"在线 / 运行中"语义，禁止装饰性使用。
    readonly property color running: "#34C759"
    /// 探测可用（绿）：probe 成功语义。
    readonly property color success: "#30D158"
    /// 警示（琥珀）：引擎锁定提示等。
    readonly property color warn: "#FF9F0A"
    /// 错误 / 停止类语义。
    readonly property color danger: "#FF3B30"
    /// 危险动作 hover 洗底 rgba(255,59,48,0.08)（运行芯片 ✕ 等，§3.7）。
    readonly property color dangerWash: "#14FF3B30"

    // ---- 玻璃卡片 ---------------------------------------------------------
    /// 卡片填充 rgba(255,255,255,0.72)。
    readonly property color cardFill: "#B8FFFFFF"
    // 分段选中段：不透明纯白（与胶囊底 60% 白拉强色差——选中态可见性）
    readonly property color segmentFill: "#FFFFFFFF"
    /// 卡片 1px 亮边 rgba(255,255,255,0.65)。
    readonly property color cardBorder: "#A6FFFFFF"
    /// 旧卡片阴影色 rgba(0,0,0,0.10)：主面板已全界面零阴影（铁律 8，分层
    /// 只靠材质对比 + 亮边）；仅设置页旧卡投影仍引用，随其下线一并删除。
    readonly property color cardShadow: "#1A000000"
    /// 卡片圆角（DESIGN.md §2：卡 16）。
    readonly property int cardRadius: 16
    /// 浮层填充 rgba(255,255,255,0.60)（聚焦搜索的亚克力）。
    readonly property color flyoutFill: "#99FFFFFF"
    /// 菜单浮层（软件回退底色，不透明保可读）
    readonly property color menuFill: "#FFF7F7F9"
    /// 毛玻璃霜面染色（依据见 docs/ui/glass-recipe.md）
    readonly property color menuTint: "#B8F5F5F7"
    /// 二级浮层霜面染色（elevation 阶梯）
    readonly property color menuTintHi: "#D0FFFFFF"
    /// 菜单 1px 描边（浅色画布必须深色 hairline）
    readonly property color menuBorder: "#14000000"
    /// 二级浮层描边（阶梯差）
    readonly property color menuBorderHi: "#24000000"
    /// 菜单 1px 描边（软件回退路径）
    readonly property color menuFillBorder: "#1A000000"
    /// 浮层圆角（DESIGN.md §2：浮层 12）。
    readonly property int flyoutRadius: 12
    /// 控件圆角（条目 / 洗色块等，DESIGN.md §2：控件 10）。
    readonly property int controlRadius: 10

    // ---- 交互状态 ---------------------------------------------------------
    /// 悬停洗色 rgba(0,0,0,0.04)。
    readonly property color hoverWash: "#0A000000"
    /// 按下洗色 rgba(0,0,0,0.08)。
    readonly property color pressWash: "#14000000"
    /// 输入框 / 滑槽描边 rgba(0,0,0,0.12)。
    readonly property color hairline: "#1F000000"
    /// 全局标准过渡时长（ms）；无常驻动画。
    readonly property int durFast: 140

    // ---- 玻璃模糊开关 -----------------------------------------------------
    /*
     * 右键菜单毛玻璃开关（双路径门槛）：
     *   GL（Windows 真机 ANGLE/OpenGL）= 真·高斯模糊——内容快照
     *   （ShaderEffectSource，live:false 按需抓帧）+ 模糊 + **圆角 alpha
     *   蒙版**把输出裁成圆角，见 Main.qml 的 MenuGlassPlate。蒙版是关键：
     *   当年直角 bug 的根因是方形容器（采样层无遮罩，圆角卡被盖成直角），
     *   alpha 蒙版让模糊层只在圆角内存在，根治之。
     *   软件渲染后端 / WSL（Mesa 栈着色器异常）= 回退不透明 menuFill，
     *   可读优先、出图不破相。
     * 门槛 = app.py 注入的 shadersUsable 上下文属性（not is_wsl()）；
     * 无注入环境（测试装载）按 undefined → false 走软件路径。
     */
    readonly property bool glassBlur: typeof shadersUsable !== "undefined" ? shadersUsable : false

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
