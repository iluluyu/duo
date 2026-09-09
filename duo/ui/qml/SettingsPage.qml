// SettingsPage.qml —— 设置页（真实后端：duo.ui.app.SettingsApi）
//
// 归属：QML 前端设置页，由 Main.qml 经 StackView push，根 Item 宽高由
// StackView 给定（独立出图时由脚本给定），刻意不用 ApplicationWindow。
//
// 数据合同：
//   - 上下文属性 settingsApi（duo.ui.app.SettingsApi）：
//       load() -> QVariantMap             键同 duo/core/settings.py 的 Settings 字段：
//                                         scrcpy_path / adb_path / fps / bitrate_mbps /
//                                         dpi / corner_mode / corner_size_dip / glass_enabled
//       loadProblems() -> QVariantList    读取 settings.json 的问题清单（空 = 正常）；
//                                         页面打开时经 problemBar 红条展示（同 widgets 版）
//       save(QVariantMap) -> QVariantList 问题清单（空数组 = 已保存）
//       probe(tool: str, path: str)       异步检测；完成后发：
//         signal probeDone(string tool, bool ok, string detail)
//   - 本页属性 engineLocked：由 Main.qml 绑到 ctrl.engineLocked（可通知，
//     会话启动/结束后绑定自动刷新）；true 时引擎路径行禁用并显示提示条
//
// 状态出口：保存成功发 accepted()（Main 侧接 ctrl.resolveAdb() + pop，
//           对齐旧 widgets 版 _refresh_after_settings 语义）；
//           Esc 发 cancelled()（Main 侧接 StackView.pop；页面无标题行/
//           返回钮——顶栏胶囊即导航，DESIGN §3.9；底部仅一个保存按钮，
//           返回即放弃）。

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Effects

Item {
    id: root

    implicitWidth: 480
    implicitHeight: 640
    objectName: "settingsPageQml"

    // 整合时由容器给出宽高（StackView push）；这里只作为独立预览的默认尺寸
    signal accepted()
    signal cancelled()

    // Esc 关页 = 取消（QDialog reject 的既有习惯；Shortcut 不依赖焦点；
    // sequences 复数形式，避免 StandardKey.Cancel 多键绑定的告警）
    Shortcut {
        sequences: [StandardKey.Cancel]
        onActivated: root.cancelChanges()
    }

    // --------------------------------------------------------------- 页面状态
    // 引擎锁：Main.qml 绑 ctrl.engineLocked；true 时引擎路径行禁用 + 提示条
    property bool engineLocked: false
    property var problems: []          // save()/loadProblems() 的问题清单（红条内容）
    property bool glassOn: true            // 液态玻璃
    // 投屏质量（docs/mirroring-quality.md）：键同 Settings 字段
    property string videoCodec: "auto"     // auto | h264 | h265 | av1
    property string audioPolicy: "latest"  // latest | all | off
    property bool turnScreenOff: false     // --turn-screen-off
    // 窗口栏（串流窗口上巴/下巴）——默认值语义：应用未单独设置（右键菜单
    // 「窗口栏 ▸」写 gui_prefs.json bars 节）时生效，按应用的选择优先于此。
    // immersive = 无边框 + overlay 悬浮控件；native = 系统原生栏；none =
    // 该边永不建栏（2026-09-09 第三态：scrcpy 右键已是返回，下巴常显冗
    // 余——新默认上巴 immersive、下巴 none）。
    // 键同 Settings 字段，经 --chrome-top/--chrome-bottom 下发到 overlay。
    property string topBarMode: "immersive"          // immersive | native
    property string bottomBarMode: "none"  // immersive | native | none
    // flex 虚拟屏分辨率档位已撤（2026-09-06 用户决策）：一律原始分辨率，
    // 性能由 codec=h264+fps=60 承担，无往返字段。

    // ---- 隐形透传（DESIGN §3.8 后端兼容）----------------------------------
    // DPI / 圆角控件已从页面删除，但 SettingsApi.save(values) 按整表构造
    // Settings：map 里缺哪一键，那一键就落回默认值——丢键等于把用户的
    // dpi / corner_mode / corner_size_dip 静默重置。load() 读入的原值存放
    // 在此，collect() 原样带回，全程不经任何控件（settings.json 手改仍生效）。
    property var dpiPass: null                  // int | null（null = 跟随显示密度）
    property string cornerModePass: "system"    // system | g2 | none
    property int cornerSizePass: 48             // DIP，仅 g2 模式有意义

    // ------------------------------------------------------------ 真实合同调用
    // 打开/取消时用 load() 回填（取消即放弃改动）；载入问题一并上红条。
    // 回填；缺省值同旧 widgets 页（load 永不 raise，缺失键走默认）
    function reloadFromApi() {
        root.problems = settingsApi.loadProblems()
        var m = settingsApi.load()
        scrcpyRow.text = (m.scrcpy_path == null) ? "" : m.scrcpy_path
        adbRow.text = (m.adb_path == null) ? "" : m.adb_path
        fpsCell.box.value = (m.fps == null) ? 60 : m.fps          // null 也算缺省（60：120Hz 面板整除节拍）
        bitrateCell.box.value = (m.bitrate_mbps == null) ? 30 : m.bitrate_mbps
        root.glassOn = (m.glass_enabled == null) ? true : m.glass_enabled
        root.videoCodec = (m.video_codec == null) ? "auto" : m.video_codec
        root.audioPolicy = (m.audio_policy == null) ? "latest" : m.audio_policy
        root.turnScreenOff = (m.turn_screen_off == null) ? false : m.turn_screen_off
        root.topBarMode = (m.top_bar_mode == null) ? "immersive" : m.top_bar_mode
        root.bottomBarMode = (m.bottom_bar_mode == null) ? "none" : m.bottom_bar_mode
        // 隐形透传：被删控件的字段只存不发，collect() 原样带回
        root.dpiPass = (m.dpi === undefined || m.dpi == null) ? null : m.dpi
        root.cornerModePass = (m.corner_mode == null) ? "system" : m.corner_mode
        root.cornerSizePass = (m.corner_size_dip == null) ? 48 : m.corner_size_dip
    }

    // 收集当前控件值 → mock 合同的保存键名（同 Settings 字段）
    function collect() {
        return {
            "scrcpy_path": scrcpyRow.text.trim(),
            "adb_path": adbRow.text.trim(),
            "fps": fpsCell.box.value,
            "bitrate_mbps": bitrateCell.box.value,
            "glass_enabled": root.glassOn,
            "video_codec": root.videoCodec,
            "audio_policy": root.audioPolicy,
            "turn_screen_off": root.turnScreenOff,
            "top_bar_mode": root.topBarMode,
            "bottom_bar_mode": root.bottomBarMode,
            // 隐形透传：控件已删但 SettingsApi.save 按整表构造，丢键即
            // 重置为默认值——load 读进来的原值必须原样带回
            "dpi": root.dpiPass,
            "corner_mode": root.cornerModePass,
            "corner_size_dip": root.cornerSizePass
        }
    }

    // 保存：问题清单留在页内红条；空清单 = 已保存（accepted + 约定日志）
    function saveChanges() {
        var res = settingsApi.save(collect())
        if (res && res.length > 0) {
            root.problems = res
            return
        }
        root.problems = []
        root.accepted()
    }

    // 取消：回填放弃改动 + 发 cancelled（返回键与 Esc 共用；取消按钮已删）
    function cancelChanges() {
        root.reloadFromApi()
        root.cancelled()
    }

    // FileDialog 返回 URL，转本地路径（file:///C:/x → C:/x，file:///home → /home）
    function urlToPath(u) {
        var s = decodeURIComponent(u.toString())
        if (/^\/[A-Za-z]:\//.test(s))
            s = s.substring(1)
        return s
    }

    Component.onCompleted: reloadFromApi()

    // --------------------------------------------------------------- 页面骨架
    Rectangle {  // 页面底色（推入 StackView 后盖住主面板）
        anchors.fill: parent
        color: Style.bg
    }

    // 无标题行/返回按钮（DESIGN.md §3.9）：顶栏胶囊即导航（「设置」段亮起），
    // Esc = 取消返回；内容自画布顶端起排（胶囊 16+32 下方让位）。
    ScrollView {
        id: scroller
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: footer.top
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.topMargin: 64
        anchors.bottomMargin: 8
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Column {
            id: contentCol
            width: scroller.availableWidth
            spacing: 12

            // 保存/载入问题红条（空清单时隐藏）
            Rectangle {
                id: problemBar
                objectName: "problemBar"
                width: parent.width
                radius: 10
                visible: root.problems.length > 0
                color: Qt.alpha(Style.danger, 0.10)
                border.width: 1
                border.color: Qt.alpha(Style.danger, 0.35)
                height: visible ? problemText.implicitHeight + 20 : 0
                Accessible.name: "设置问题"
                Text {
                    id: problemText
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 10
                    text: root.problems.join("\n")
                    wrapMode: Text.Wrap
                    font.family: Style.fontDefault
                    font.pixelSize: 13
                    color: Style.danger
                }
            }

            // ---------------------------------------------------------- 引擎卡片
            GlassCard {
                id: engineCard
                objectName: "engineCard"
                title: "引擎"

                PathRow {
                    id: scrcpyRow
                    tool: "scrcpy"
                }
                PathRow {
                    id: adbRow
                    tool: "adb"
                }

                // 会话运行中：路径行禁用 + 提示条
                Rectangle {
                    width: parent.width
                    height: visible ? lockHint.implicitHeight + 16 : 0
                    radius: 10
                    visible: root.engineLocked
                    color: Qt.alpha(Style.warn, 0.14)
                    Text {
                        id: lockHint
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 8
                        text: "镜像会话运行中，引擎路径暂不可改（先关闭会话）"
                        wrapMode: Text.Wrap
                        font.family: Style.fontDefault
                        font.pixelSize: 12
                        color: Style.warn
                    }
                }

                // FPS / 码率两格等分（DPI 控件已删：flex 会话自动取设备
                // 密度，面板路径不再手动指定；CLI --dpi 仍可用——DESIGN §3.8）
                Row {
                    width: parent.width
                    spacing: 12

                    NumberCell {
                        id: fpsCell
                        width: (parent.width - 12) / 2
                        title: "FPS"
                        boxFrom: 1
                        boxTo: 240
                        accessName: "最大帧率 FPS"
                    }
                    NumberCell {
                        id: bitrateCell
                        width: (parent.width - 12) / 2
                        title: "码率 Mbps"
                        boxFrom: 1
                        boxTo: 200
                        accessName: "视频码率 Mbps"
                    }
                }
            }

            // ------------------------------------------------------ 投屏质量卡片
            GlassCard {
                id: qualityCard
                objectName: "qualityCard"
                title: "投屏质量"

                // 视频编码四选一（auto = 探测设备硬件编码器择优，
                // 结果缓存后后续会话免探测）
                Row {
                    width: parent.width
                    spacing: 8
                    ModeButton {
                        width: (parent.width - 24) / 4
                        text: "自动(推荐)"
                        selected: root.videoCodec === "auto"
                        Accessible.name: "视频编码：自动（推荐）"
                        onClicked: root.videoCodec = "auto"
                    }
                    ModeButton {
                        width: (parent.width - 24) / 4
                        text: "H.264"
                        selected: root.videoCodec === "h264"
                        Accessible.name: "视频编码：H.264"
                        onClicked: root.videoCodec = "h264"
                    }
                    ModeButton {
                        width: (parent.width - 24) / 4
                        text: "H.265"
                        selected: root.videoCodec === "h265"
                        Accessible.name: "视频编码：H.265"
                        onClicked: root.videoCodec = "h265"
                    }
                    ModeButton {
                        width: (parent.width - 24) / 4
                        text: "AV1"
                        selected: root.videoCodec === "av1"
                        Accessible.name: "视频编码：AV1（需硬件编码器）"
                        onClicked: root.videoCodec = "av1"
                    }
                }

                // 音频策略三选一：行首行标签与 FPS/码率同构（选项名已
                // 自说明，不配说明文字——DESIGN §3.8）
                Item {
                    width: parent.width
                    height: 20
                    CaptionText {
                        objectName: "audioRowLabel"
                        text: "音频"
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Row {
                    width: parent.width
                    spacing: 8
                    ModeButton {
                        objectName: "audioLatest"
                        width: (parent.width - 16) / 3
                        text: "仅最新会话"
                        selected: root.audioPolicy === "latest"
                        Accessible.name: "音频：仅最新会话"
                        onClicked: root.audioPolicy = "latest"
                    }
                    ModeButton {
                        objectName: "audioAll"
                        width: (parent.width - 16) / 3
                        text: "全部会话"
                        selected: root.audioPolicy === "all"
                        Accessible.name: "音频：全部会话"
                        onClicked: root.audioPolicy = "all"
                    }
                    ModeButton {
                        objectName: "audioMute"
                        width: (parent.width - 16) / 3
                        text: "静音"
                        selected: root.audioPolicy === "off"
                        Accessible.name: "音频：静音"
                        onClicked: root.audioPolicy = "off"
                    }
                }

                // 镜像时关闭设备屏幕
                Item {
                    width: parent.width
                    height: 32
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "镜像时关闭设备屏幕"
                        font.family: Style.fontDefault
                        font.pixelSize: 13
                        color: Style.ink
                    }
                    GlassSwitch {
                        objectName: "turnScreenOffSwitch"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: root.turnScreenOff
                        Accessible.name: "镜像时关闭设备屏幕"
                        onToggled: root.turnScreenOff = checked
                    }
                }
                CaptionText {
                    width: parent.width
                    text: "黑屏防误触；主要对整机镜像有意义——虚拟屏会话本就与物理屏无关"
                    wrapMode: Text.Wrap
                }
            }

            // ------------------------------------------------------ 窗口栏卡片
            // 串流窗口上巴/下巴模式（默认值）：两行分段控件，同音频行的行标签
            // + 分段按钮构型。immersive = 无边框 + overlay 悬浮控件；native =
            // 系统原生栏；none = 该边永不建栏（下巴默认即此）。应用未单独
            // 设置（右键菜单「窗口栏 ▸」按应用覆盖）时生效，随启动 argv 注入
            // --chrome-top/--chrome-bottom。
            GlassCard {
                id: windowBarCard
                objectName: "windowBarCard"
                title: "窗口栏（默认）"

                // 上巴：沉浸 | 系统
                Item {
                    width: parent.width
                    height: 20
                    CaptionText {
                        objectName: "topBarRowLabel"
                        text: "上巴"
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Row {
                    width: parent.width
                    spacing: 8
                    ModeButton {
                        objectName: "topBarImmersive"
                        width: (parent.width - 8) / 2
                        text: "沉浸"
                        selected: root.topBarMode === "immersive"
                        Accessible.name: "上巴：沉浸"
                        onClicked: root.topBarMode = "immersive"
                    }
                    ModeButton {
                        objectName: "topBarNative"
                        width: (parent.width - 8) / 2
                        text: "系统"
                        selected: root.topBarMode === "native"
                        Accessible.name: "上巴：系统"
                        onClicked: root.topBarMode = "native"
                    }
                }

                // 下巴：沉浸 | 系统 | 不显示（none 三选，等分三格）
                Item {
                    width: parent.width
                    height: 20
                    CaptionText {
                        objectName: "bottomBarRowLabel"
                        text: "下巴"
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Row {
                    width: parent.width
                    spacing: 8
                    ModeButton {
                        objectName: "bottomBarImmersive"
                        width: (parent.width - 16) / 3
                        text: "沉浸"
                        selected: root.bottomBarMode === "immersive"
                        Accessible.name: "下巴：沉浸"
                        onClicked: root.bottomBarMode = "immersive"
                    }
                    ModeButton {
                        objectName: "bottomBarNative"
                        width: (parent.width - 16) / 3
                        text: "系统"
                        selected: root.bottomBarMode === "native"
                        Accessible.name: "下巴：系统"
                        onClicked: root.bottomBarMode = "native"
                    }
                    ModeButton {
                        objectName: "bottomBarNone"
                        width: (parent.width - 16) / 3
                        text: "不显示"
                        selected: root.bottomBarMode === "none"
                        Accessible.name: "下巴：不显示"
                        onClicked: root.bottomBarMode = "none"
                    }
                }
                CaptionText {
                    width: parent.width
                    text: "应用未单独设置时生效；沉浸 = 无边框悬浮控件，系统 = 保留系统原生栏，" +
                          "不显示 = 该边不建栏（scrcpy 右键已是返回，下巴常显冗余）"
                    wrapMode: Text.Wrap
                }
            }

            // ------------------------------------------------------ 外观卡片
            GlassCard {
                id: appearanceCard
                objectName: "appearanceCard"
                title: "外观"

                // 液态玻璃开关
                Item {
                    width: parent.width
                    height: 32
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "液态玻璃"
                        font.family: Style.fontDefault
                        font.pixelSize: 13
                        color: Style.ink
                    }
                    GlassSwitch {
                        objectName: "glassSwitch"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: root.glassOn
                        Accessible.name: "液态玻璃风格"
                        onToggled: root.glassOn = checked
                    }
                }
            }
        }
    }

    // 底部：仅一个保存主按钮（Esc/顶栏胶囊返回即放弃——DESIGN §3.9，
    // 取消按钮与返回钮冗余）
    Item {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 32
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.bottomMargin: 12

        PrimaryButton {
            objectName: "saveButton"
            anchors.right: parent.right
            text: "保存"
            width: 76
            Accessible.name: "保存设置"
            onClicked: root.saveChanges()
        }
    }

    // ================================================================ 内联组件
    // 卡片：玻璃拟态 + 柔和投影（0 8 24 rgba(0,0,0,0.10)，140ms 无多余动画）
    component GlassCard: Item {
        id: gcard
        property string title: ""
        default property alias contentData: innerCol.data
        // 实例都在竖向 Column 里：宽度跟父，高度由内容撑开
        width: parent.width
        readonly property int cardPad: 12
        // 阴影外扩：上 3 / 左右 8 / 下 10（投影偏移 +8、模糊 24 的可视范围）
        implicitHeight: 3 + cardPad * 2 + innerCol.implicitHeight + 10

        Item {
            id: shadowHost
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.topMargin: 3
            anchors.leftMargin: 8
            anchors.rightMargin: 8
            anchors.bottomMargin: 10

            Rectangle {
                id: cardBg
                anchors.fill: parent
                radius: 14
                color: Style.cardFill
                border.width: 1
                border.color: Style.cardBorder
            }
            MultiEffect {
                anchors.fill: cardBg
                source: cardBg
                // TODO(观感)：MultiEffect 与可见的 cardBg 兄弟节点叠加，GPU
                // 后端上半透明卡被画两次（透明度复合后偏深），与跳过着色器的
                // 软件后端观感不一致。低风险修法：把源换成一份 visible:false
                // 的快照（ShaderEffectSource）再喂给 MultiEffect。
                shadowEnabled: true
                shadowColor: Style.cardShadow
                shadowBlur: 0.65
                shadowVerticalOffset: 8
            }
            Column {
                id: innerCol
                x: gcard.cardPad
                y: gcard.cardPad
                width: parent.width - gcard.cardPad * 2
                spacing: 9
                Text {
                    text: gcard.title
                    font.family: Style.fontDefault
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    font.letterSpacing: 1
                    color: Style.ink
                }
            }
        }
    }

    // 次要按钮（浏览/检测）：白底描边，hover 洗色 140ms
    component SecButton: AbstractButton {
        id: sbtn
        implicitHeight: 32
        padding: 12
        contentItem: Text {
            text: sbtn.text
            font.family: Style.fontDefault
            font.pixelSize: 13
            color: sbtn.enabled ? Style.ink : Style.ink2
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: !sbtn.enabled ? Qt.rgba(0, 0, 0, 0.03)
                                 : (sbtn.down ? Qt.rgba(0, 0, 0, 0.08)
                                              : (sbtn.hovered ? Style.hoverWash : "#FFFFFF"))
            border.width: 1
            border.color: sbtn.enabled ? Style.hairline : Qt.rgba(0, 0, 0, 0.06)
            Behavior on color { ColorAnimation { duration: 140 } }
        }
    }

    // 主按钮（保存）：强调色实底
    component PrimaryButton: AbstractButton {
        id: pbtn
        implicitHeight: 32
        padding: 12
        contentItem: Text {
            text: pbtn.text
            font.family: Style.fontDefault
            font.pixelSize: 13
            font.weight: Font.DemiBold
            color: "#FFFFFF"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            // 派生 hover/pressed 色（整合时换 Style singleton）
            color: pbtn.enabled ? (pbtn.down ? Style.accentPress
                                             : (pbtn.hovered ? Style.accentHover : Style.accent))
                                : Qt.alpha(Style.accent, 0.4)
            Behavior on color { ColorAnimation { duration: 140 } }
        }
    }

    // 圆角模式单选按钮（分段样式）
    component ModeButton: AbstractButton {
        id: mbtn
        property bool selected: false
        implicitHeight: 32
        Accessible.role: Accessible.RadioButton
        Accessible.checked: mbtn.selected
        contentItem: Text {
            text: mbtn.text
            font.family: Style.fontDefault
            font.pixelSize: 13
            font.weight: mbtn.selected ? Font.DemiBold : Font.Normal
            color: mbtn.selected ? Style.accent : Style.ink2
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 10
            color: mbtn.selected ? Qt.alpha(Style.accent, 0.14)
                                 : (mbtn.hovered ? Style.hoverWash : "transparent")
            border.width: 1
            border.color: mbtn.selected ? Qt.alpha(Style.accent, 0.45) : "transparent"
            Behavior on color { ColorAnimation { duration: 140 } }
        }
    }

    // 纯开关（无文字）：轨道贴右；文字由调用方自行放在轨道左侧
    component GlassSwitch: Switch {
        id: gsw
        property bool small: false
        height: 32
        font.family: Style.fontDefault
        font.pixelSize: gsw.small ? 12 : 13
        spacing: 6
        // 无文字时 Control 的 implicitWidth 不含轨道，必须手动抬底防溢出
        implicitWidth: Math.max(implicitContentWidth + leftPadding + rightPadding,
                                gsw.indicator.width + spacing)
        indicator: Rectangle {
            implicitWidth: gsw.small ? 36 : 40
            implicitHeight: gsw.small ? 22 : 24
            x: gsw.availableWidth - width          // 轨道恒贴右缘
            y: (gsw.availableHeight - height) / 2
            radius: height / 2
            color: gsw.checked ? Style.accent : Qt.rgba(0, 0, 0, 0.16)
            Behavior on color { ColorAnimation { duration: 140 } }
            Rectangle {
                x: gsw.checked ? parent.width - width - 2 : 2
                anchors.verticalCenter: parent.verticalCenter
                width: parent.height - 4
                height: parent.height - 4
                radius: height / 2
                color: "#FFFFFF"
            }
        }
        contentItem: Item {
            implicitWidth: 0
            implicitHeight: 0
        }
    }

    // 说明文字（12px 次要色）
    component CaptionText: Text {
        font.family: Style.fontDefault
        font.pixelSize: 12
        color: Style.ink2
    }

    // 数字输入框：左右 −/+ 步进（高 32 点击区），中間可编辑
    component NumberBox: SpinBox {
        id: nbox
        property string accessName: ""
        height: 32
        editable: true
        font.family: Style.fontDefault
        font.pixelSize: 13
        leftPadding: 30
        rightPadding: 30
        Accessible.name: nbox.accessName
        contentItem: TextInput {
            // 照 Basic 官方样式：displayText（text 在创建期可能为 undefined）
            text: nbox.displayText
            font: nbox.font
            color: nbox.enabled ? Style.ink : Style.ink2
            selectionColor: Style.accent
            selectedTextColor: "#FFFFFF"
            horizontalAlignment: TextInput.AlignHCenter
            verticalAlignment: TextInput.AlignVCenter
            readOnly: !nbox.editable
            validator: nbox.validator
            inputMethodHints: Qt.ImhFormattedNumbersOnly
        }
        background: Rectangle {
            radius: 10
            color: nbox.enabled ? "#FFFFFF" : Qt.rgba(0, 0, 0, 0.03)
            border.width: 1
            border.color: nbox.activeFocus ? Style.accent : Style.hairline
            Behavior on border.color { ColorAnimation { duration: 140 } }
        }
        up.indicator: Rectangle {
            x: parent.width - width
            width: 28
            height: parent.height
            radius: 10
            color: nbox.up.pressed ? Style.hoverWash : "transparent"
            Text {
                anchors.centerIn: parent
                text: "+"
                font.pixelSize: 14
                color: nbox.enabled ? Style.ink2 : Qt.rgba(0, 0, 0, 0.15)
            }
        }
        down.indicator: Rectangle {
            x: 0
            width: 28
            height: parent.height
            radius: 10
            color: nbox.down.pressed ? Style.hoverWash : "transparent"
            Text {
                anchors.centerIn: parent
                text: "−"
                font.pixelSize: 14
                color: nbox.enabled ? Style.ink2 : Qt.rgba(0, 0, 0, 0.15)
            }
        }
    }

    // 数字单元格：标题行 + NumberBox（FPS / 码率 / DPI 共用）
    component NumberCell: Column {
        id: ncell
        property alias box: nbox2
        property string title: ""
        property string accessName: ""
        property int boxFrom: 1
        property int boxTo: 240
        spacing: 6
        Item {
            width: parent.width
            height: 20
            CaptionText {
                text: ncell.title
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        NumberBox {
            id: nbox2
            width: parent.width
            from: ncell.boxFrom
            to: ncell.boxTo
            accessName: ncell.accessName
        }
    }

    // 路径行：字段名 +（行尾检测结果）；TextField + 浏览 + 检测
    component PathRow: Column {
        id: prow
        property string tool: ""
        property alias text: field.text
        property bool probing: false
        property string statusText: ""
        property real statusOpacity: 0   // 瞬时提示：结果波起→淡出（见 fadeTimer）
        width: parent.width
        spacing: 6

        // 行首字段名；行尾检测结果为瞬时提示——深底胶囊波起，约 2.5s
        // 后淡出，不留常驻绿/红字（DESIGN §3.8；保存失败仍走红条）
        Item {
            width: parent.width
            height: 26
            CaptionText {
                text: prow.tool + " 路径"
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
            }
            Rectangle {
                id: statusPill
                objectName: prow.tool + "ProbeStatus"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: statusLabel.implicitWidth + 20
                height: 26
                radius: 14
                color: "#E61D1D1F"   // 深底胶囊，白字；成败只靠 ✓/✗ 区分
                visible: statusLabel.text.length > 0
                opacity: prow.statusOpacity
                Behavior on opacity { NumberAnimation { duration: Style.durFast } }
                Text {
                    id: statusLabel
                    objectName: prow.tool + "ProbeStatusLabel"
                    anchors.centerIn: parent
                    text: prow.probing ? "检测中…" : prow.statusText
                    color: "#FFFFFF"
                    font.family: Style.fontDefault
                    font.pixelSize: 12
                }
            }
            // 结果可见约 2.36s 后开始 140ms 淡出，整体在 2.5s 收场；
            // 重复探测 restart，计时重置
            Timer {
                id: fadeTimer
                interval: 2360
                onTriggered: prow.statusOpacity = 0
            }
        }
        Row {
            width: parent.width
            spacing: 8
            TextField {
                id: field
                objectName: prow.tool + "PathField"
                width: parent.width - browseBtn.width - detectBtn.width - 16
                height: 32
                enabled: !root.engineLocked          // 会话运行中整行禁用
                selectByMouse: true
                placeholderText: "留空自动探测"
                placeholderTextColor: Style.ink2
                color: enabled ? Style.ink : Style.ink2
                font.family: Style.fontDefault
                font.pixelSize: 13
                leftPadding: 10
                rightPadding: 10
                Accessible.name: prow.tool + " 路径"
                background: Rectangle {
                    radius: 10
                    color: field.enabled ? "#FFFFFF" : Qt.rgba(0, 0, 0, 0.03)
                    border.width: 1
                    border.color: field.activeFocus ? Style.accent : Style.hairline
                    Behavior on border.color { ColorAnimation { duration: 140 } }
                }
            }
            SecButton {
                id: browseBtn
                text: "浏览"
                enabled: !root.engineLocked
                Accessible.name: "浏览 " + prow.tool + " 路径"
                onClicked: dlg.open()
            }
            SecButton {
                id: detectBtn
                text: "检测"
                enabled: !root.engineLocked && !prow.probing   // 检测中禁用按钮
                Accessible.name: "检测 " + prow.tool
                onClicked: prow.beginProbe()
            }
        }

        // 异步检测：结果经 probeDone 信号回流行内胶囊；“检测中…”属进行
        // 状态，不计时；只有结果才启动淡出计时
        function beginProbe() {
            if (prow.probing)
                return
            prow.probing = true
            prow.statusText = ""
            prow.statusOpacity = 1
            fadeTimer.stop()
            settingsApi.probe(prow.tool, field.text.trim())
        }

        // 选可执行文件（QtQuick.Dialogs）
        FileDialog {
            id: dlg
            title: "选择 " + prow.tool + " 可执行文件"
            nameFilters: ["所有文件 (*)"]
            fileMode: FileDialog.OpenFile
            onAccepted: field.text = root.urlToPath(selectedFile)
        }

        Connections {
            target: settingsApi
            function onProbeDone(doneTool, ok, detail) {
                if (doneTool !== prow.tool)
                    return
                prow.probing = false
                // 文案对齐 widgets 版：区分“填了路径但无法运行”与
                // “PATH 里没有，可手动填写”两种可指导性错误；波起后淡出
                if (ok) {
                    prow.statusText = detail !== "" ? "✓ " + detail : "✓ 可执行"
                } else if (field.text.trim() !== "") {
                    prow.statusText = "✗ 无法运行，请检查路径"
                } else {
                    prow.statusText = "✗ 未在 PATH 找到，可手动填写路径"
                }
                prow.statusOpacity = 1
                fadeTimer.restart()
            }
        }
    }
}
