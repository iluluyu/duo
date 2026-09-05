pragma Singleton
import QtQuick

/*
 * Style.qml - Duo QML 视觉令牌单例（主面板与设置页共享的唯一色板）。
 *
 * 与 duo/ui/tokens.py 同一理念：纯常量、无主题引擎、无运行时切换——
 * 单一中性画布、唯一强调色、玻璃半透明、轻阴影、140ms 过渡。
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

    // ---- 玻璃卡片 ---------------------------------------------------------
    /// 卡片填充 rgba(255,255,255,0.72)。
    readonly property color cardFill: "#B8FFFFFF"
    /// 卡片 1px 亮边 rgba(255,255,255,0.65)。
    readonly property color cardBorder: "#A6FFFFFF"
    /// 卡片阴影色 rgba(0,0,0,0.10)（偏移 0/8、模糊 24，见 Main.qml 的分层近似）。
    readonly property color cardShadow: "#1A000000"
    /// 卡片圆角。
    readonly property int cardRadius: 14

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
     * 玻璃模糊（MultiEffect 采样背景）运行时可用（QtQuick.Effects 已探测），
     * 但软件渲染后端（QT_QUICK_BACKEND=software，预览/CI 出图用）不执行着色器，
     * 模糊自动退化为纯半透明卡片——降级路径无需改代码，视觉不受损。
     * 若某环境确需关闭，将此标志置 false 即走纯半透明分支。
     */
    readonly property bool glassBlur: true

    // ---- 图标占位 ---------------------------------------------------------
    /// 首字圆形占位的底色（icon 为空串时使用；轻到接近画布，字用主文字色）。
    readonly property color placeholderDisc: "#0D000000"

    // ---- 字体 -------------------------------------------------------------
    /// 全局字体族：Windows 首选 Segoe UI，其余平台自动回退。
    readonly property string fontDefault: "Segoe UI"
}
