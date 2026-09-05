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
//       save(QVariantMap) -> QVariantList 问题清单（空数组 = 已保存）
//       probe(tool: str, path: str)       异步检测；完成后发：
//         signal probeDone(string tool, bool ok, string detail)
//   - 本页属性 engineLocked：由 Main.qml 绑到 ctrl.engineLocked（可通知，
//     会话启动/结束后绑定自动刷新）；true 时引擎路径行禁用并显示提示条
//
// 状态出口：保存成功发 accepted()（Main 侧接 ctrl.resolveAdb() + pop，
//           对齐旧 widgets 版 _refresh_after_settings 语义）；
//           返回/取消发 cancelled()（Main 侧接 StackView.pop）。

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

    // --------------------------------------------------------------- 页面状态
    // 引擎锁：Main.qml 绑 ctrl.engineLocked；true 时引擎路径行禁用 + 提示条
    property bool engineLocked: false
    property var problems: []          // save() 返回的问题清单（红条内容）
    property string cornerMode: "system"   // system | g2 | none
    property bool glassOn: true            // 液态玻璃

    // 内容完整展示所需高度（独立出图时自动加高窗口用；应用内由
    // StackView 尺寸 + 滚动接管）
    readonly property real contentNeededHeight: contentCol.implicitHeight + 104

    // ------------------------------------------------------------ 真实合同调用
    // 打开/取消时用 load() 回填（取消即放弃改动）
    // 回填；缺省值同旧 widgets 页（load 永不 raise，缺失键走默认）
    function reloadFromApi() {
        var m = settingsApi.load()
        scrcpyRow.text = (m.scrcpy_path == null) ? "" : m.scrcpy_path
        adbRow.text = (m.adb_path == null) ? "" : m.adb_path
        fpsCell.box.value = (m.fps == null) ? 90 : m.fps          // null 也算缺省
        bitrateCell.box.value = (m.bitrate_mbps == null) ? 30 : m.bitrate_mbps
        var dpi = (m.dpi == null) ? null : m.dpi
        dpiAutoSwitch.checked = (dpi === null)          // 自动 = 无自定义密度
        dpiCell.box.value = (dpi === null) ? 480 : dpi  // 480 仅为禁用态占位值
        root.cornerMode = (m.corner_mode == null) ? "system" : m.corner_mode
        sizeSlider.value = (m.corner_size_dip == null) ? 48 : m.corner_size_dip
        root.glassOn = (m.glass_enabled == null) ? true : m.glass_enabled
    }

    // 收集当前控件值 → mock 合同的保存键名（同 Settings 字段）
    function collect() {
        return {
            "scrcpy_path": scrcpyRow.text.trim(),
            "adb_path": adbRow.text.trim(),
            "fps": fpsCell.box.value,
            "bitrate_mbps": bitrateCell.box.value,
            "dpi": dpiAutoSwitch.checked ? null : dpiCell.box.value,
            "corner_mode": root.cornerMode,
            "corner_size_dip": Math.round(sizeSlider.value),
            "glass_enabled": root.glassOn
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

    // FileDialog 返回 URL，转本地路径（file:///C:/x → C:/x，file:///home → /home）
    function urlToPath(u) {
        var s = decodeURIComponent(u.toString())
        if (/^\/[A-Za-z]:\//.test(s))
            s = s.substring(1)
        return s
    }

    Component.onCompleted: reloadFromApi()

    onCornerModeChanged: previewCanvas.requestPaint()
    onGlassOnChanged: previewCanvas.requestPaint()

    // --------------------------------------------------------------- 页面骨架
    Rectangle {  // 页面底色（推入 StackView 后盖住主面板）
        anchors.fill: parent
        color: Style.bg
    }

    // 顶部返回行：‹ + 标题
    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 48
        anchors.leftMargin: 16
        anchors.rightMargin: 16

        AbstractButton {
            id: backBtn
            width: 32
            height: 32
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            Accessible.name: "返回"
            contentItem: Text {
                text: "‹"
                font.family: Style.fontDefault
                font.pixelSize: 20
                color: Style.ink
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 16
                color: backBtn.down ? Qt.rgba(0, 0, 0, 0.08)
                                    : (backBtn.hovered ? Style.hoverWash : "transparent")
                Behavior on color { ColorAnimation { duration: 140 } }
            }
            onClicked: {
                root.reloadFromApi()   // 返回即放弃改动
                root.cancelled()
            }
        }
        Text {
            text: "设置"
            anchors.left: backBtn.right
            anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            font.family: Style.fontDefault
            font.pixelSize: 20
            font.weight: Font.DemiBold
            color: Style.ink
        }
    }

    // 中部：单列内容，放不下即滚动（480x640 起，窄时单列滚动）
    ScrollView {
        id: scroller
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: footer.top
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.topMargin: 2
        anchors.bottomMargin: 8
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Column {
            id: contentCol
            width: scroller.availableWidth
            spacing: 10

            // 保存问题红条（空清单时隐藏）
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
                Accessible.name: "保存问题"
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
                    radius: 8
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

                Row {
                    width: parent.width
                    spacing: 12

                    NumberCell {
                        id: fpsCell
                        width: (parent.width - 24) / 3
                        title: "FPS"
                        boxFrom: 1
                        boxTo: 240
                        accessName: "最大帧率 FPS"
                    }
                    NumberCell {
                        id: bitrateCell
                        width: (parent.width - 24) / 3
                        title: "码率 Mbps"
                        boxFrom: 1
                        boxTo: 200
                        accessName: "视频码率 Mbps"
                    }

                    // DPI + 自动开关：开 = 跟随显示推荐（dpi 为 null）
                    Column {
                        id: dpiCell
                        property alias box: dpiBox
                        width: (parent.width - 24) / 3
                        spacing: 6
                        Item {
                            width: parent.width
                            height: 20
                            CaptionText {
                                text: "DPI"
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                id: dpiAutoLabel
                                anchors.right: dpiAutoSwitch.left
                                anchors.rightMargin: 6
                                anchors.verticalCenter: parent.verticalCenter
                                text: "自动"
                                font.family: Style.fontDefault
                                font.pixelSize: 12
                                color: Style.ink
                            }
                            GlassSwitch {
                                id: dpiAutoSwitch
                                objectName: "dpiAutoSwitch"
                                small: true
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                Accessible.name: "DPI 自动"
                            }
                        }
                        NumberBox {
                            id: dpiBox
                            width: parent.width
                            from: 120
                            to: 640
                            enabled: !dpiAutoSwitch.checked
                            accessName: "DPI"
                        }
                    }
                }
            }

            // ---------------------------------------------------------- 外观卡片
            GlassCard {
                id: appearanceCard
                objectName: "appearanceCard"
                title: "外观"

                // 圆角模式三选一
                Row {
                    width: parent.width
                    spacing: 8
                    ModeButton {
                        width: (parent.width - 16) / 3
                        text: "系统圆角(默认)"
                        selected: root.cornerMode === "system"
                        Accessible.name: "圆角模式：系统圆角(默认)"
                        onClicked: root.cornerMode = "system"
                    }
                    ModeButton {
                        width: (parent.width - 16) / 3
                        text: "G2 大圆角"
                        selected: root.cornerMode === "g2"
                        Accessible.name: "圆角模式：G2 大圆角（实验）"
                        onClicked: root.cornerMode = "g2"
                    }
                    ModeButton {
                        width: (parent.width - 16) / 3
                        text: "直角"
                        selected: root.cornerMode === "none"
                        Accessible.name: "圆角模式：直角"
                        onClicked: root.cornerMode = "none"
                    }
                }

                // 大小滑块：仅 g2 模式可用（0-96 DIP）
                Item {
                    width: parent.width
                    height: 32
                    opacity: sizeSlider.enabled ? 1 : 0.45
                    Slider {
                        id: sizeSlider
                        objectName: "cornerSlider"
                        anchors.left: parent.left
                        anchors.right: valueLabel.left
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        from: 0
                        to: 96
                        stepSize: 1
                        value: 48
                        padding: 9
                        enabled: root.cornerMode === "g2"
                        Accessible.name: "圆角大小"
                        background: Rectangle {
                            x: sizeSlider.leftPadding
                            y: sizeSlider.topPadding
                               + sizeSlider.availableHeight / 2 - height / 2
                            width: sizeSlider.availableWidth
                            height: 4
                            radius: 2
                            color: Style.hairline
                            Rectangle {
                                width: sizeSlider.visualPosition * parent.width
                                height: parent.height
                                radius: 2
                                color: Style.accent
                                visible: sizeSlider.enabled
                            }
                        }
                        handle: Rectangle {
                            x: sizeSlider.leftPadding
                               + sizeSlider.visualPosition
                                 * (sizeSlider.availableWidth - width)
                            y: sizeSlider.topPadding
                               + sizeSlider.availableHeight / 2 - height / 2
                            width: 18
                            height: 18
                            radius: 9
                            color: "#FFFFFF"
                            border.width: 1
                            border.color: sizeSlider.hovered ? Style.accent : Style.hairline
                            Behavior on border.color { ColorAnimation { duration: 140 } }
                        }
                        onValueChanged: previewCanvas.requestPaint()
                    }
                    Text {
                        id: valueLabel
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: Math.round(sizeSlider.value) + " DIP"
                        font.family: Style.fontDefault
                        font.pixelSize: 13
                        color: Style.ink
                    }
                }

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

                // 预览区：Canvas 按模式/大小即时重绘
                Canvas {
                    id: previewCanvas
                    objectName: "cornerPreview"
                    width: parent.width
                    height: 80
                    antialiasing: true
                    Accessible.role: Accessible.Graphic
                    Accessible.name: "圆角外观预览"

                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.reset()
                        ctx.clearRect(0, 0, width, height)
                        var bx = 14, by = 8
                        var bw = width - 28, bh = height - 30
                        var r = Math.min(Math.round(sizeSlider.value),
                                         bw / 2, bh / 2)
                        ctx.lineWidth = 1
                        ctx.beginPath()
                        if (root.cornerMode === "none") {
                            ctx.rect(bx, by, bw, bh)          // 直角
                        } else if (root.cornerMode === "system") {
                            // system：Windows 系统小圆角的示意半径
                            roundRectPath(ctx, bx, by, bw, bh, 10)
                        } else {
                            // g2：四阶（n=4）超椭圆大圆角，半径跟随滑块
                            squirclePath(ctx, bx, by, bw, bh, r, 4)
                        }
                        // 液态玻璃开 → 淡蓝玻璃感；关 → 平淡灰面
                        ctx.fillStyle = root.glassOn ? "rgba(231,240,251,0.9)" : "#F0F0F3"
                        ctx.fill()
                        ctx.strokeStyle = "#D8D8DC"
                        ctx.stroke()
                        // 底部注记
                        var note = root.cornerMode === "system"
                                   ? "Windows 系统圆角"
                                   : (root.cornerMode === "g2"
                                      ? "G2 超椭圆 · " + Math.round(sizeSlider.value) + " DIP"
                                      : "直角")
                        ctx.font = "12px '" + Style.fontDefault + "', 'Noto Sans CJK SC', sans-serif"
                        ctx.fillStyle = "#86868B"
                        ctx.textAlign = "center"
                        ctx.fillText(note, width / 2, height - 6)
                    }

                    // 普通圆角矩形路径（arcTo 四角）
                    function roundRectPath(ctx, x, y, w, h, r) {
                        ctx.moveTo(x + r, y)
                        ctx.lineTo(x + w - r, y)
                        ctx.arcTo(x + w, y, x + w, y + r, r)
                        ctx.lineTo(x + w, y + h - r)
                        ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
                        ctx.lineTo(x + r, y + h)
                        ctx.arcTo(x, y + h, x, y + h - r, r)
                        ctx.lineTo(x, y + r)
                        ctx.arcTo(x, y, x + r, y, r)
                        ctx.closePath()
                    }

                    // 超椭圆（squircle）路径：每个角是一段四分之一样本曲线
                    function squirclePath(ctx, x, y, w, h, r, n) {
                        var p = 2 / n            // 指数：n=4 → iOS 风格 G2 连续曲率
                        var samples = 24
                        ctx.moveTo(x + r, y)
                        ctx.lineTo(x + w - r, y)
                        // 右上角：中心 (x+w-r, y+r)，从 (0,-r) 扫到 (r,0)
                        for (var i = 0; i <= samples; i++) {
                            var t = (i / samples) * Math.PI / 2
                            ctx.lineTo(x + w - r + r * Math.pow(Math.sin(t), p),
                                       y + r - r * Math.pow(Math.cos(t), p))
                        }
                        ctx.lineTo(x + w, y + h - r)
                        // 右下角：中心 (x+w-r, y+h-r)，从 (r,0) 扫到 (0,r)
                        for (i = 0; i <= samples; i++) {
                            t = (i / samples) * Math.PI / 2
                            ctx.lineTo(x + w - r + r * Math.pow(Math.cos(t), p),
                                       y + h - r + r * Math.pow(Math.sin(t), p))
                        }
                        ctx.lineTo(x + r, y + h)
                        // 左下角：中心 (x+r, y+h-r)，从 (0,r) 扫到 (-r,0)
                        for (i = 0; i <= samples; i++) {
                            t = (i / samples) * Math.PI / 2
                            ctx.lineTo(x + r - r * Math.pow(Math.sin(t), p),
                                       y + h - r + r * Math.pow(Math.cos(t), p))
                        }
                        ctx.lineTo(x, y + r)
                        // 左上角：中心 (x+r, y+r)，从 (-r,0) 扫到 (0,-r)
                        for (i = 0; i <= samples; i++) {
                            t = (i / samples) * Math.PI / 2
                            ctx.lineTo(x + r - r * Math.pow(Math.cos(t), p),
                                       y + r - r * Math.pow(Math.sin(t), p))
                        }
                        ctx.closePath()
                    }
                }
            }
        }
    }

    // 底部操作行：取消（放弃改动） + 保存
    Item {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 32
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.bottomMargin: 12

        Row {
            anchors.right: parent.right
            spacing: 10
            SecButton {
                text: "取消"
                width: 76
                Accessible.name: "取消"
                onClicked: {
                    root.reloadFromApi()   // 放弃改动：用 mock load 回填
                    root.cancelled()
                }
            }
            PrimaryButton {
                objectName: "saveButton"
                text: "保存"
                width: 76
                Accessible.name: "保存设置"
                onClicked: root.saveChanges()
            }
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

    // 次要按钮（浏览/检测/取消）：白底描边，hover 洗色 140ms
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
            radius: 8
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
            radius: 8
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
            radius: 8
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
            radius: 8
            color: nbox.enabled ? "#FFFFFF" : Qt.rgba(0, 0, 0, 0.03)
            border.width: 1
            border.color: nbox.activeFocus ? Style.accent : Style.hairline
            Behavior on border.color { ColorAnimation { duration: 140 } }
        }
        up.indicator: Rectangle {
            x: parent.width - width
            width: 28
            height: parent.height
            radius: 8
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
            radius: 8
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
        property color statusColor: Style.ink2
        width: parent.width
        spacing: 6

        Item {  // 行首字段名，行尾短结果标签（绿“可用 · 版本”/红“未找到”）
            width: parent.width
            height: 20
            CaptionText {
                text: prow.tool + " 路径"
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
            }
            CaptionText {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: prow.probing ? "检测中…" : prow.statusText
                color: prow.probing ? Style.ink2 : prow.statusColor
                visible: text.length > 0
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
                    radius: 8
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

        // 异步检测：结果经 probeDone 信号回流行内标签
        function beginProbe() {
            if (prow.probing)
                return
            prow.probing = true
            prow.statusText = ""
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
                if (ok) {
                    prow.statusText = "可用 · " + detail
                    prow.statusColor = Style.success
                } else {
                    prow.statusText = "未找到"
                    prow.statusColor = Style.danger
                }
            }
        }
    }
}
