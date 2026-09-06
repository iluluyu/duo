// Main.qml - Duo 主面板（QML 前端入口，duo.ui.app.run_app 加载）。
//
// 数据合同：根上下文属性 ctrl = duo.ui.controller.PanelController ——
//   属性 devices[{serial,stateText,online}] / statusText(str) /
//        runningSessions[{key,label,running,portrait}] / engineLocked(bool) /
//        apps[{package,label,icon,installed}]（icon 为 file URL 串或空串）
//   槽   startSession(package) / startMirror() / stopSession(key) /
//        startAppOnDisplay(package) / refreshInstalled() /
//        togglePortrait(package) / resolveAdb()
// 交互：磁贴点击启动（已在运行 → 改为直达该会话虚拟屏，见 controller
//   startSession 路由）；运行中芯片点标签 = startAppOnDisplay（把应用拉回
//   虚拟屏，即面板侧的"HOME = 回 Duo 面板"语义，不发 keyevent 3）；
//   右键/长按切换横竖屏（对齐 widgets 版目录图标）
// 设置页：齿轮按钮或 Ctrl+, 把 SettingsPage.qml push 上 StackView；
//   保存成功（accepted）→ ctrl.resolveAdb()（对齐旧 widgets 版
//   _refresh_after_settings 语义：adb 变了就切监控 + 刷新已装列表），
//   返回/取消（cancelled）→ 直接 pop。
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects

ApplicationWindow {
    id: root

    // ============ 混合 DPI 双屏：舒适缩放 + 按屏幕可用尺寸自适应 ============
    // Qt6 默认开启 per-monitor High-DPI 缩放（app.py 不设任何 QT_* 缩放
    // 覆盖）：下面所有 px 值都是 DIP，由 Qt 按每屏 DPR 渲染——这解释了
    // "4K 屏（125-200% 缩放）布局正确"。而 100% 缩放的屏（DPR=1.0，常见
    // 于 2K 屏）对固定 DIP 设计不做任何放大，12-13px 字号原样渲染，观感
    // 拥挤。uiScale 给低 DPR 屏一档最高 125% 的舒适缩放：DPR≥1.25 的屏
    // 恒为 1.0（4K 现状不变），DPR=1.0 的屏取 1.25（与 Windows 对 2K 屏
    // 的建议缩放一致）。绑定到窗口所在屏，跨屏拖动时随每屏 DPR 实时重算。
    readonly property real uiScale: Math.max(1.0, Math.min(1.25, 1.25 / Screen.devicePixelRatio))

    // 屏幕可用尺寸（DIP）；offscreen/无工作区平台返回不设限的大值，
    // 让测试与出图不被钳制。
    function availWidthDip() { return Screen.availableWidth > 0 ? Screen.availableWidth : 1000000 }
    function availHeightDip() { return Screen.availableHeight > 0 ? Screen.availableHeight : 1000000 }

    // 初始/最小尺寸 = 设计值 × 舒适档位，再钳制到所在屏可用范围。
    width: Math.min(Math.round(420 * uiScale), Math.round(availWidthDip() * 0.92))
    height: Math.min(Math.round(660 * uiScale), Math.round(availHeightDip() * 0.92))
    minimumWidth: Math.min(Math.round(360 * uiScale), Math.round(availWidthDip() * 0.92))
    minimumHeight: Math.min(Math.round(520 * uiScale), Math.round(availHeightDip() * 0.92))
    visible: true
    title: "Duo"
    color: Style.bg

    // 主设备 = 首个在线设备（widgets 版 _on_devices 的 online[0]，与
    // startSession/startMirror 实际选中的 monitor.online[0] 同一台；
    // devices[0] 可能是另一台离线/未授权设备）
    readonly property var device: ctrl.devices.find(function (d) { return d.online; }) ?? null
    // 无在线设备时回退展示首个离线/未授权设备（状态点转警示色提示原因）
    readonly property var fallbackDevice: device ?? ctrl.devices[0] ?? null
    // 已安装应用数（决定网格 vs 空态）
    readonly property int installedCount: ctrl.apps.filter(function (a) { return a.installed; }).length

    function openSettings() {
        if (stack.depth === 1)
            stack.push(settingsComp)
    }

    Shortcut {
        sequences: ["Ctrl+,"]
        onActivated: root.openSettings()
    }

    // 内容缩放层：子树内布局坐标保持未缩放 DIP（面板/设置页零改动），
    // 整层按 uiScale 从左上角放大后恰好铺满窗口。文字与矢量按最终尺寸
    // 栅格化，缩放后不糊。手动调整过窗口大小时用户接管尺寸，缩放档位
    // 仍随所在屏跟随。
    Item {
        id: zoomLayer
        width: Math.ceil(parent.width / root.uiScale)
        height: Math.ceil(parent.height / root.uiScale)
        scale: root.uiScale
        transformOrigin: Item.TopLeft

        StackView {
            id: stack
            anchors.fill: parent
            initialItem: panelComp
        }
    }

    Component {
        id: settingsComp

        SettingsPage {
            engineLocked: ctrl.engineLocked
            onAccepted: {
                ctrl.resolveAdb()   // 重解析 adb；变了则 controller 切监控+刷新列表
                stack.pop()
            }
            onCancelled: stack.pop()
        }
    }

    // ================= 可复用部件 =================
    // 圆形图标按钮：hover/press 洗色、tooltip、Accessible、点击区 ≥32px
    component IconButton: AbstractButton {
        id: ib
        property string glyph: ""
        implicitWidth: 36
        implicitHeight: 36
        background: Rectangle {
            radius: width / 2
            color: ib.pressed ? Style.pressWash : (ib.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        contentItem: Text {
            text: ib.glyph
            font.pixelSize: 17
            color: Style.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // 轻阴影：三层低透明度圆角矩形近似 0/8/24 rgba(0,0,0,0.10)。
    // 不用 MultiEffect 阴影——它会二次绘制玻璃源导致透明度叠加，且分层法
    // 在软件渲染后端（出图）与 GPU 端表现一致。
    component SoftShadow: Item {
        id: sh
        property real yOff: 6
        property real rad: Style.cardRadius
        Rectangle { x: -7; y: sh.yOff - 5; width: sh.width + 14; height: sh.height + 14
                    radius: sh.rad + 7; color: "#05000000" }
        Rectangle { x: -4; y: sh.yOff - 2; width: sh.width + 8; height: sh.height + 8
                    radius: sh.rad + 4; color: "#07000000" }
        Rectangle { x: -1; y: sh.yOff; width: sh.width + 2; height: sh.height + 2
                    radius: sh.rad + 1; color: "#09000000" }
    }

    // 应用磁贴：圆形图标（icon 为空 → 首字占位）+ 短标签；未安装降透明
    // 且禁点（widgets 版 setEnabled(False)）；右键/长按切换横竖屏
    component AppTile: Item {
        id: tile
        required property var modelData   // {package,label,icon,installed}

        width: grid.cellWidth
        height: grid.cellHeight
        opacity: modelData.installed ? 1.0 : 0.4
        Behavior on opacity { NumberAnimation { duration: Style.durFast } }

        // 图标：controller 传来的已是 QUrl file URL（处理过盘符/空格/中文），
        // 为空则走首字圆形占位分支
        Image {
            id: appIcon
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 10
            width: 60; height: 60
            visible: tile.modelData.icon !== ""
            source: visible ? tile.modelData.icon : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
        }
        Rectangle {
            visible: tile.modelData.icon === ""
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 10
            width: 60; height: 60; radius: 30
            color: tileMa.pressed ? Style.pressWash
                                  : (tileMa.hovered ? Style.hoverWash : Style.placeholderDisc)
            Behavior on color { ColorAnimation { duration: Style.durFast } }
            Text {
                anchors.centerIn: parent
                text: tile.modelData.label.charAt(0)
                font.pixelSize: 19
                font.weight: Font.DemiBold
                color: Style.ink
            }
        }

        // 短标签：6 字截断（同 widgets 版），再由最大宽度兜底省略
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 76
            width: tile.width - 8
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            text: tile.modelData.label.length > 6
                  ? tile.modelData.label.slice(0, 6) + "…"
                  : tile.modelData.label
            font.pixelSize: 12
            color: Style.ink
        }

        MouseArea {
            id: tileMa
            anchors.fill: parent
            hoverEnabled: true
            enabled: tile.modelData.installed   // 未安装应用不可启动
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.PointingHandCursor
            // 长按已触发标志：pressAndHold 之后部分平台/触屏合成路径仍会
            // 补发 clicked（长按=切竖屏 + 单击=启动 连发，磁贴被会话启动
            // 拖入重建），onClicked 必须吞掉并复位；onPressed 复位保证
            // 下一次按压不受上一次残留影响。
            property bool held: false
            onPressed: function () { held = false }
            onClicked: function (mouse) {
                if (held) { held = false; return }   // 长按收尾，不是单击
                if (mouse.button === Qt.RightButton)
                    ctrl.togglePortrait(tile.modelData.package)   // 右键切横竖屏
                else
                    ctrl.startSession(tile.modelData.package)
            }
            // 触屏长按 = 右键等价（单次触发，clicked 由 held 吞掉）
            onPressAndHold: function (mouse) {
                held = true
                ctrl.togglePortrait(tile.modelData.package)
            }
            Accessible.role: Accessible.Button
            Accessible.name: tile.modelData.label + "（右键切换横竖屏）"
            ToolTip.visible: tileMa.containsMouse
            ToolTip.text: tile.modelData.label + "（右键切换横竖屏）"
        }
    }

    // 运行中芯片：标签 + 方向（点击切换，镜像会话除外）+ 停止 ✕，可换行
    component SessionChip: Rectangle {
        id: chip
        required property var modelData   // {key,label,running,portrait}

        height: 32
        radius: 16
        width: chipRow.implicitWidth + 20
        color: Style.cardFill
        border.width: 1
        border.color: Style.cardBorder

        Row {
            id: chipRow
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 12
            spacing: 8

            // 状态点 + 标签整体可点：把应用拉回该会话的虚拟屏
            // （am start --display N，不重建会话）。设备镜像无虚拟屏，
            // 退化为纯展示。HOME 在虚拟屏被系统全局拦截（落物理屏），
            // 面板侧的"回主页"就是这个按钮，永不发 keyevent 3。
            AbstractButton {
                id: chipMain
                enabled: chip.modelData.key !== ctrl.mirrorKey
                opacity: enabled ? 1.0 : 0.7
                implicitHeight: 24
                implicitWidth: dotRow.implicitWidth
                anchors.verticalCenter: parent.verticalCenter
                contentItem: Row {
                    id: dotRow
                    spacing: 8
                    Rectangle { width: 6; height: 6; radius: 3
                                anchors.verticalCenter: parent.verticalCenter
                                color: Style.running }
                    Text {
                        text: chip.modelData.label
                        font.pixelSize: 12
                        color: chipMain.hovered ? Style.accent : Style.ink
                        Behavior on color { ColorAnimation { duration: Style.durFast } }
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                background: Rectangle {
                    radius: 12
                    color: chipMain.pressed ? Style.pressWash
                                            : (chipMain.hovered ? Style.hoverWash : "transparent")
                    Behavior on color { ColorAnimation { duration: Style.durFast } }
                }
                onClicked: ctrl.startAppOnDisplay(chip.modelData.key)
                Accessible.role: Accessible.Button
                Accessible.name: "在虚拟屏打开 " + chip.modelData.label
                ToolTip.visible: chipMain.hovered && chipMain.enabled
                ToolTip.text: "在虚拟屏中打开应用（HOME = 回 Duo 面板）"
            }
            // 方向标签：点击 = ctrl.togglePortrait(key)（影响下次启动）。
            // 设备镜像无横竖屏概念，隐藏。
            AbstractButton {
                id: orientBtn
                visible: chip.modelData.key !== ctrl.mirrorKey
                implicitHeight: 22
                implicitWidth: orientLabel.implicitWidth + 14
                anchors.verticalCenter: parent.verticalCenter
                contentItem: Text {
                    id: orientLabel
                    text: chip.modelData.portrait ? "竖屏" : "横屏"
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    color: Style.ink2
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 11
                    color: orientBtn.pressed ? Style.pressWash
                                             : (orientBtn.hovered ? Style.hoverWash : "transparent")
                    Behavior on color { ColorAnimation { duration: Style.durFast } }
                }
                onClicked: ctrl.togglePortrait(chip.modelData.key)
                Accessible.role: Accessible.Button
                Accessible.name: "切换 " + chip.modelData.label
                                 + " 横竖屏，当前" + orientLabel.text
                ToolTip.visible: hovered
                ToolTip.text: "点击切换下次启动方向"
            }
            AbstractButton {
                id: stopBtn
                implicitWidth: 24
                implicitHeight: 24
                anchors.verticalCenter: parent.verticalCenter
                background: Rectangle {
                    radius: 12
                    color: stopBtn.pressed ? Style.pressWash
                                           : (stopBtn.hovered ? Style.hoverWash : "transparent")
                    Behavior on color { ColorAnimation { duration: Style.durFast } }
                }
                contentItem: Item {
                    Rectangle { width: 10; height: 1.6; radius: 0.8; rotation: 45
                                anchors.centerIn: parent; color: Style.ink2 }
                    Rectangle { width: 10; height: 1.6; radius: 0.8; rotation: -45
                                anchors.centerIn: parent; color: Style.ink2 }
                }
                onClicked: ctrl.stopSession(chip.modelData.key)
                Accessible.role: Accessible.Button
                Accessible.name: "停止 " + chip.modelData.label
                ToolTip.visible: hovered
                ToolTip.text: "停止会话"
            }
        }
    }

    Component {
        id: panelComp

        Item {
            id: panel

            // ================= 背景（玻璃模糊的采样源） =================
            Item {
                id: bgLayer
                anchors.fill: parent

                Rectangle { anchors.fill: parent; color: Style.bg }

                /* 极淡装饰色斑：给玻璃卡一点可被模糊的内容，也打破纯平画布。
                 * 同心三层逼近柔和衰减，避免硬边圆在半透明卡下露出"污渍感"。 */
                Rectangle { x: -230; y: -170; width: 420; height: 420; radius: 210; color: "#04007AFF" }
                Rectangle { x: -180; y: -120; width: 320; height: 320; radius: 160; color: "#06007AFF" }
                Rectangle { x: -130; y: -70; width: 220; height: 220; radius: 110; color: "#0A007AFF" }
                Rectangle { x: 250; y: 430; width: 480; height: 480; radius: 240; color: "#0334C759" }
                Rectangle { x: 310; y: 490; width: 360; height: 360; radius: 180; color: "#0534C759" }
                Rectangle { x: 370; y: 550; width: 240; height: 240; radius: 120; color: "#0934C759" }
            }

            // 背景快照：玻璃卡经 MultiEffect 对其轻模糊（自身 visible:false 不上屏）
            ShaderEffectSource {
                id: bgSource
                anchors.fill: parent
                sourceItem: bgLayer
                visible: false
                live: true
                smooth: true
            }

            // ================= 顶部标题行 =================
            Text {
                id: title
                x: 20; y: 20
                text: "Duo"
                font.pixelSize: 22
                font.weight: Font.DemiBold
                color: Style.ink
            }
            IconButton {
                id: gearBtn
                objectName: "gearButton"
                anchors.verticalCenter: title.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: 16
                glyph: "⚙"
                onClicked: root.openSettings()
                Accessible.role: Accessible.Button
                Accessible.name: "设置"
                ToolTip.visible: hovered
                ToolTip.text: "设置（Ctrl+,）"
            }

            // ================= 设备卡（玻璃） =================
            SoftShadow {
                anchors.fill: deviceCard
                yOff: 6
            }
            Rectangle {
                id: deviceCard
                x: 20
                y: 68
                width: parent.width - 40
                height: 76
                radius: Style.cardRadius
                color: Style.cardFill
                border.width: 1
                border.color: Style.cardBorder

                // 玻璃：全幅背景快照反向平移对齐到卡片位置后轻模糊。
                // 本卡是 panel 直接子项，坐标链固定，纯属性绑定即可随窗口缩放
                // 对齐；软件渲染后端不执行着色器 → 自动只剩 cardFill 半透明
                //（降级见 Style.glassBlur）。
                Item {
                    anchors.fill: parent
                    clip: true
                    visible: Style.glassBlur
                    MultiEffect {
                        width: bgSource.width
                        height: bgSource.height
                        x: -deviceCard.x
                        y: -deviceCard.y
                        source: bgSource
                        blurEnabled: true
                        blurMax: 32
                        blurMultiplier: 1.0
                    }
                }

                Row {
                    id: devRow
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    spacing: 10

                    // 在线状态点（#34C759 仅用于运行/在线语义）：在线绿，
                    // 有设备但离线/未授权/recovery → 琥珀警示，无设备灰
                    Rectangle {
                        width: 10; height: 10; radius: 5
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.device !== null ? Style.running
                                                    : (root.fallbackDevice !== null ? Style.warn : "#C7C7CC")
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            text: root.fallbackDevice !== null ? root.fallbackDevice.stateText : "未连接设备"
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                            color: Style.ink
                        }
                        Text {
                            text: root.fallbackDevice !== null ? root.fallbackDevice.serial : "连接设备后可启动应用与投屏"
                            font.pixelSize: 12
                            color: Style.ink2
                        }
                    }
                }

                // 主按钮：投屏（唯一强调色；hover/press 以透明度分级，不引入新色相）
                AbstractButton {
                    id: mirrorBtn
                    objectName: "mirrorButton"
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    implicitWidth: 68
                    implicitHeight: 32
                    enabled: root.device !== null && !ctrl.engineLocked
                    opacity: enabled ? (pressed ? 0.8 : (hovered ? 0.9 : 1.0)) : 0.35
                    Behavior on opacity { NumberAnimation { duration: Style.durFast } }
                    background: Rectangle { radius: 16; color: Style.accent }
                    contentItem: Text {
                        text: "投屏"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: "#FFFFFF"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: ctrl.startMirror()
                    Accessible.role: Accessible.Button
                    Accessible.name: "投屏镜像"
                    ToolTip.visible: hovered
                    ToolTip.text: "投屏镜像"
                }
            }

            // ================= 应用网格 =================
            Text {
                id: appsCaption
                x: 20
                y: deviceCard.y + deviceCard.height + 18
                text: "应用"
                font.pixelSize: 12
                font.weight: Font.DemiBold
                color: Style.ink2
            }

            GridView {
                id: grid
                x: 20
                y: appsCaption.y + 22
                width: parent.width - 40
                height: chipsZone.visible ? chipsZone.y - grid.y - 14 : (panel.height - 40 - grid.y)
                clip: true
                // ↑ 窄窗口/多行时网格内部滚动；芯片区常驻，运行状态不被滚走
                interactive: contentHeight > height
                model: ctrl.apps
                visible: root.installedCount > 0

                // 列数随宽度自适应；保证每格 ≥72px 见方（目标 92px）
                cellWidth: width / Math.max(2, Math.floor(width / 92))
                cellHeight: 102

                ScrollIndicator.vertical: ScrollIndicator { }

                delegate: AppTile { }
            }

            // 无已装应用空态
            Column {
                anchors.horizontalCenter: parent.horizontalCenter
                y: appsCaption.y + 56
                spacing: 6
                visible: root.installedCount === 0
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "没有已安装的应用"
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    color: Style.ink
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "在设备上安装应用后，点击刷新检查"
                    font.pixelSize: 12
                    color: Style.ink2
                }
                AbstractButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    implicitWidth: 120
                    implicitHeight: 32
                    background: Rectangle {
                        radius: 16
                        color: refreshMa.pressed ? Style.pressWash
                                                 : (refreshMa.hovered ? Style.hoverWash : "transparent")
                        Behavior on color { ColorAnimation { duration: Style.durFast } }
                    }
                    contentItem: Text {
                        text: "刷新已装应用"
                        font.pixelSize: 13
                        color: Style.accent
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    MouseArea {
                        id: refreshMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: ctrl.refreshInstalled()
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: "刷新已装应用"
                }
            }

            // ================= 运行中芯片区（网格下方、常驻底部） =================
            Column {
                id: chipsZone
                anchors.left: parent.left
                anchors.leftMargin: 20
                anchors.right: parent.right
                anchors.rightMargin: 20
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 56
                spacing: 8
                visible: ctrl.runningSessions.length > 0

                Text {
                    text: "运行中"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    color: Style.ink2
                }
                Flow {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        model: ctrl.runningSessions
                        delegate: SessionChip { }
                    }
                }
            }

            // ================= 底部状态 toast =================
            Rectangle {
                id: toast
                objectName: "statusToast"
                readonly property bool hasMessage: ctrl.statusText !== ""
                property bool expired: false

                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 16
                width: toastLabel.implicitWidth + 32
                height: 36
                radius: 18
                color: "#E61D1D1F"

                // 仅 statusText 非空时显示，2.5s 自动淡出；140ms 过渡与全局一致
                opacity: hasMessage && !expired ? 1.0 : 0.0
                visible: opacity > 0.01
                onHasMessageChanged: expired = false
                Timer {
                    id: toastTimer
                    interval: 2500
                    running: toast.hasMessage
                    onTriggered: toast.expired = true
                }
                Behavior on opacity { NumberAnimation { duration: Style.durFast } }

                Text {
                    id: toastLabel
                    objectName: "statusToastLabel"
                    anchors.centerIn: parent
                    text: ctrl.statusText
                    font.pixelSize: 13
                    color: "#FFFFFF"
                    // 2.5s 内连发时后一条要重置自己的完整时限
                    onTextChanged: toastTimer.restart()
                }
            }
        }
    }
}
