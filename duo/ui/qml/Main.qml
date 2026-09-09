// Main.qml - Duo 主面板（QML 前端入口，duo.ui.app.run_app 加载）。
//
// 数据合同：根上下文属性 ctrl = duo.ui.controller.PanelController ——
//   属性 devices[{serial,stateText,online}] / statusText(str) /
//        runningSessions[{key,label,running,portrait}] / engineLocked(bool) /
//        apps[{package,label,key,icon,installed,pinned}]（未置顶条目，拼音序；
//        key 为拼音首字母串，供搜索；icon 为 file URL 串或空串，QML Image
//        原生渲染 SVG/PNG）/
//        pinnedApps[同上]（置顶条目，仅固定卡使用，空则固定卡不出现）
//   槽   startSession(package)（按显示模式记忆启动：fixed 记忆 → 固定分辨率）/
//        startSessionWithAspect(package, aspect_id)（一次性按比例启动，不改
//        记忆；比例 id 如 "16:9"/"body-l"，见冻结表 duo/core/aspects.py）/
//        displayModeFor(package) → {mode,aspect}（右键菜单选中态）/
//        setDisplayFlex(package) / setDisplayFixed(package, aspect_id)
//        （显示模式按应用记忆，写 gui_prefs.json display 节）/
//        barModeFor(package, which) → {explicit,mode}（窗口栏菜单选中态；
//        mode = effective = per-app override 若显式设置，否则设置页默认）/
//        setAppBar(package, which, mode)（窗口栏按应用记忆：mode ∈
//        immersive|native|none 或空串 = 清除 override 跟随默认，写 gui_prefs.json
//        bars 节；none = 该边永不建栏；设置页两字段即默认值）/
//        setDefaultBarMode(which, mode)（镜像卡右键菜单的窗口栏小节：设备
//        镜像无应用包，直接写设置页默认 top/bottom_bar_mode；选毕发
//        barPrefsChanged("") 刷新镜像菜单圆点）/
//        audioExclusiveFor(package) → bool（音频独占勾选态）/
//        setAudioExclusive(package, on)（音频独占按应用记忆，写 gui_prefs.json
//        audio 节；勾选的应用启动时抢音频，其余在跑会话全部静音重启）/
//        keepVdFor(package) → bool（断开保留画面勾选态）/
//        setKeepVd(package, on)（断开保留画面按应用记忆，写 gui_prefs.json
//        behavior 节；勾选的应用启动带 --no-vd-destroy-content，断开后留
//        在虚拟屏不回落手机主屏）/
//        setMediaVolume(index)（设备媒体流音量 0..15，Android 侧 cmd
//        media_session；拖动防抖 200ms 后调用）
//        startMirror() / stopSession(key) / startAppOnDisplay(key) /
//        refreshInstalled() / togglePin(package) / resolveAdb() /
//        toggleTurnScreenOff()
//   信号 displayModeChanged(package)：菜单开着时即时刷新选中圆点
//   信号 barPrefsChanged(package)：同上，刷新「窗口栏 ▸」选中圆点
//   信号 audioPrefsChanged(package)：同上，刷新「音频独占」勾选圆点
//   信号 behaviorPrefsChanged(package)：同上，刷新「断开保留画面」勾选圆点
//   属性 mediaVolume(real)：设备媒体音量 index，-1 = 未知（不预读，首次
//        拖动后已知，notify=mediaVolumeChanged）
//   属性 turnScreenOff(bool)：镜像时关闭设备屏幕（Settings.turn_screen_off，
//        右键镜像卡可切，notify=turnScreenOffChanged）
// 布局顺序（DESIGN.md §3）：顶栏胶囊（两页常驻，StackView 之上）→ 设备卡
//   （纯状态展示）→ 固定应用卡（有置顶才出现）→ 镜像卡（右键弹镜像菜单）
//   → 搜索（纯前端过滤，Ctrl+F 聚焦 / Esc 清空失焦）→ 应用网格（裸排）
//   → 运行卡（无标题）→ Toast。
// 交互：磁贴/固定卡小图标 点击=startSession（已在运行 → 直达该会话虚拟屏，
//   见 controller startSession 路由；fixed 显示记忆 → 固定分辨率启动）；
//   右键/长按 = 上下文菜单（打开 / 置顶 / 显示模式：自适应窗口｜固定比例
//   ▸ 二级菜单——按应用记忆持久化，DESIGN.md §3.7；窗口栏 ▸ 二级菜单——
//   上巴 跟随默认/沉浸/系统，下巴 跟随默认/沉浸/系统/不显示，按应用覆盖
//   设置页默认）；★ 角标置顶
//   常显、hover 露出。未知应用（icon 空串）显示包名哈希取色的首字 squircle
//   （Style.fallbackPalette）。
// 设置页：顶栏胶囊「设置」段或 Ctrl+, 把 SettingsPage.qml push 上 StackView；
//   保存成功（accepted）→ ctrl.resolveAdb()（对齐旧 widgets 版
//   _refresh_after_settings 语义：adb 变了就切监控 + 刷新已装列表），
//   返回/Esc（cancelled）→ 直接 pop（页面自身无标题行/返回钮，§3.9）。
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

    // 设备像素网格吸附（glass-recipe.md §2-6）：分数 DPR（1.25/1.5）下，
    // 只有落在设备像素整数上的逻辑坐标才能让快照/显示两级采样完全对齐；
    // 偏网格 1 逻辑像素会重采样出 ~0.5-1 设备像素的玻璃内容漂移。
    function deviceGrid() {
        var d = Screen.devicePixelRatio
        var q = 1
        while (q <= 16 && Math.abs(Math.round(d * q) - d * q) > 1e-9)
            q++
        return q
    }
    function snapGrid(v) { return Math.round(v / deviceGrid()) * deviceGrid() }

    // 屏幕可用尺寸（DIP）；offscreen/无工作区平台返回不设限的大值，
    // 让测试与出图不被钳制。
    function availWidthDip() { return Screen.availableWidth > 0 ? Screen.availableWidth : 1000000 }
    function availHeightDip() { return Screen.availableHeight > 0 ? Screen.availableHeight : 1000000 }

    // 未知应用 fallback 取色：色板[包名字符码和 % 12]（DESIGN.md §3.1，
    // 柔和 12 色托得住白字；同包名恒同色，换图标缓存不跳色）。
    function fallbackColor(pkg) {
        var sum = 0
        for (var i = 0; i < pkg.length; i++)
            sum += pkg.charCodeAt(i)
        return Style.fallbackPalette[sum % Style.fallbackPalette.length]
    }

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

        // ================= 画布根（bgLayer + stack 快照包装层） =================
        // 画布包装层：毛玻璃快照源（菜单浮层不在其子树内，无自采样环）；
        // 算法与配方见 docs/ui/glass-recipe.md
        Item {
            id: canvasRoot
            objectName: "canvasRoot"
            anchors.fill: parent
            layer.enabled: true   // 毛玻璃纹理源（脏跟踪：静止时零重绘）

            Item {
                id: bgLayer
                anchors.fill: parent

                Rectangle { anchors.fill: parent; color: Style.bg }

                // 装饰色斑（同心三层逼近径向衰减，参数依据见 glass-recipe.md §3）
                Rectangle { x: -272; y: -212; width: 504; height: 504; radius: 252; color: "#09007AFF" }
                Rectangle { x: -180; y: -120; width: 320; height: 320; radius: 160; color: "#0E007AFF" }
                Rectangle { x: -132; y: -72; width: 224; height: 224; radius: 112; color: "#16007AFF" }
                Rectangle { x: 205; y: 385; width: 570; height: 570; radius: 285; color: "#0734C759" }
                Rectangle { x: 310; y: 490; width: 360; height: 360; radius: 180; color: "#0C34C759" }
                Rectangle { x: 370; y: 550; width: 240; height: 240; radius: 120; color: "#1434C759" }
            }

            StackView {
                id: stack
                objectName: "pageStack"
                anchors.fill: parent
                initialItem: panelComp
            }
        }

        // ================= 顶栏胶囊（两页常驻，DESIGN.md §3.2） =================
        // 亚克力分段控件，悬浮于 StackView 之上（设置页也常驻覆盖）：当前段
        // 白 72% 圆角 14 + 主文字，未选中透明 + 次色 + hover 洗色；点击 =
        // push/pop 设置页。页面不再有大标题行与齿轮按钮（窗口标题栏已表达
        // 身份），Ctrl+, 与 Esc 快捷键保留。零阴影（铁律 8）：分层靠
        // flyoutFill 60% vs 画布 + cardBorder 亮边，不画任何阴影矩形。
        Item {
            id: topCapsule
            objectName: "topCapsule"
            // 通栏顶栏（DESIGN.md §3.2）：整行覆盖页面横向（左右让出页面
            // 留白 20），段落「首页」「设置」各占通栏一半、文字居中（视觉
            // 平整）；点击 = push/pop 设置页
            anchors.left: parent.left
            anchors.leftMargin: 20
            anchors.right: parent.right
            anchors.rightMargin: 20
            y: 16
            height: 32

            Rectangle {
                anchors.fill: parent
                radius: 16
                color: Style.flyoutFill
                border.width: 1
                border.color: Style.cardBorder
            }

            CapsuleSegment {
                objectName: "capsuleHome"
                x: 2
                width: parent.width / 2 - 4
                height: parent.height - 4
                y: 2
                text: "首页"
                selected: stack.depth === 1
                onClicked: {
                    if (stack.depth > 1)
                        stack.pop()
                }
            }
            // objectName 沿用 gearButton：scripts/qml_shots.py 以该名驱动设置
            // 页出图（脚本冻结，语义 = 打开设置）
            CapsuleSegment {
                objectName: "gearButton"
                x: parent.width / 2 + 2
                width: parent.width / 2 - 4
                height: parent.height - 4
                y: 2
                text: "设置"
                selected: stack.depth > 1
                onClicked: root.openSettings()
            }
        }
        // ================= 右键上下文菜单（磁贴/固定卡共用） =================
        // 菜单浮层挂 zoomLayer（canvasRoot 之上、与页面同原点同坐标）而
        // 非 panel 内：毛玻璃快照源是 canvasRoot（画布 + 内容包装层），
        // 菜单不在其子树才无自采样环。
        // 点击外部关闭的透明拦截层（打开时吃掉一次点击，不透传）；
        // 两枚菜单（应用/镜像）共用，任一打开即拦截并双双收起
        MouseArea {
            id: ctxScrim
            anchors.fill: parent
            z: 90
            enabled: ctxMenu.openState || mirrorMenu.openState
            visible: ctxMenu.opacity > 0.01 || mirrorMenu.opacity > 0.01
            onPressed: function (mouse) {
                ctxMenu.dismiss()
                mirrorMenu.dismiss()
            }
            onClicked: function (mouse) { }   // 吃掉
        }

        Item {
            id: ctxMenu
            objectName: "appContextMenu"
            z: 100
            property var entry: null
            property bool openState: false
            // 当前条目的显示模式记忆（{mode,aspect}；openFor 时拉取，
            // displayModeChanged 即时刷新）——驱动两枚选中圆点
            property var mDisplay: ({ mode: "flex" })
            // 当前条目的窗口栏记忆（{top:{explicit,mode}, bottom:{…}}；
            // openFor 时拉取，barPrefsChanged 即时刷新）——驱动「窗口栏 ▸」
            // 的选中圆点（无 explicit 时圆点在「跟随默认」）
            property var mBars: ({
                top: { explicit: false, mode: "immersive" },
                bottom: { explicit: false, mode: "immersive" }
            })
            // 当前条目的音频独占记忆（openFor 时拉取，audioPrefsChanged
            // 即时刷新）——驱动「音频独占」勾选圆点
            property bool mAudio: false
            // 当前条目的断开保留画面记忆（openFor 时拉取，
            // behaviorPrefsChanged 即时刷新）——驱动「断开保留画面」
            // 勾选圆点
            property bool mKeepVd: false
            // 空档位兜底（菜单未打开时不渲染出 undefined）
            readonly property var mEntry: entry ?? ({ package: "", label: "", key: "", icon: "", installed: false, pinned: false })

            width: 128
            // 4 + 打开 32 + 置顶 32 + hairline 9 + 自适应 32 + 固定比例 32
            // + 窗口栏 32 + 音频独占 32 + 断开保留画面 32 + 4
            height: menuCol.implicitHeight + 8
            opacity: openState ? 1.0 : 0.0
            visible: opacity > 0.01
            enabled: openState
            Behavior on opacity { NumberAnimation { duration: Style.durFast } }
            focus: openState
            Keys.onEscapePressed: dismiss()

            // 打开于 panel 坐标 (px, py)（调用方 mapToItem 换算），钳制
            // 在面板内（窄窗/高菜单不溢出窗口）；选中态按当前条目包名
            // 从持久化记忆拉取（二级菜单默认收起）
            function openFor(e, px, py) {
                entry = e
                mDisplay = ctrl.displayModeFor(String(e.package))
                mBars = {
                    top: ctrl.barModeFor(String(e.package), "top"),
                    bottom: ctrl.barModeFor(String(e.package), "bottom")
                }
                mAudio = ctrl.audioExclusiveFor(String(e.package))
                mKeepVd = ctrl.keepVdFor(String(e.package))
                x = snapGrid(Math.max(4, Math.min(px, stack.width - width - 4)))
                y = snapGrid(Math.max(4, Math.min(py, stack.height - height - 4)))
                ctxPlate.open(x, y)
                aspectSub.dismiss()
                barSub.dismiss()
                openState = true
                forceActiveFocus()
            }
            function dismiss() {
                openState = false
                aspectSub.dismiss()   // 两级一起收（Esc/选毕/点外部）
                barSub.dismiss()
            }

            // 浮层板 = MenuGlassPlate（真·高斯毛玻璃三明治，软件回退
            // 不透明 menuFill）；零阴影（铁律 8），hairline 亮边同前
            MenuGlassPlate { id: ctxPlate }

            Column {
                id: menuCol
                y: 4
                width: parent.width

                MenuRow {
                    objectName: "menuOpen"
                    text: "打开"
                    onClicked: {
                        ctxMenu.dismiss()
                        ctrl.startSession(ctxMenu.mEntry.package)
                    }
                }
                MenuRow {
                    objectName: "menuPin"
                    text: ctxMenu.mEntry.pinned ? "取消置顶" : "置顶到固定栏"
                    onClicked: {
                        ctxMenu.dismiss()
                        ctrl.togglePin(ctxMenu.mEntry.package)
                    }
                }
                // hairline 分隔（上下各 4px 留白）
                Item {
                    x: 12
                    width: parent.width - 24
                    implicitHeight: 9
                    Rectangle { y: 4; width: parent.width; height: 1; color: Style.hairline }
                }

                // ---- 显示模式区（DESIGN.md §3.7，按应用记忆）：
                // 自适应窗口 = flex 记忆（选中态 = 左侧 4px 强调色圆点）；
                // 固定比例 ▸ 展开二级菜单选比例。选毕/自适应即写
                // gui_prefs.json display 节并关菜单（普通点击按记忆启动）。
                MenuCheckRow {
                    objectName: "menuDisplayFlex"
                    text: "自适应窗口"
                    marked: ctxMenu.mDisplay.mode !== "fixed"
                    onClicked: {
                        ctxMenu.dismiss()
                        ctrl.setDisplayFlex(ctxMenu.mEntry.package)
                    }
                }
                MenuSubmenuRow {
                    objectName: "menuDisplayFixed"
                    text: "固定比例"
                    active: aspectSub.openState
                    onHoveredChanged: if (hovered) aspectSub.open()
                    onClicked: aspectSub.open()
                }
                // ---- 窗口栏 ▸（窗口栏按应用设置）：上巴/下巴各自
                // 跟随默认/沉浸/系统；选中圆点 = explicit 记忆，选毕写
                // gui_prefs.json bars 节并关菜单（设置页两字段是默认值）
                MenuSubmenuRow {
                    objectName: "menuBarModes"
                    text: "窗口栏"
                    active: barSub.openState
                    onHoveredChanged: if (hovered) barSub.open()
                    onClicked: barSub.open()
                }
                // ---- 音频独占（一级勾选，窗口栏 ▸ 之后）：勾选的应用
                // 启动时抢走音频——其余在跑会话（含设备镜像）全部静音
                // 重启，独占者退出后其余保持静音（手动重启即回）；
                // 点选即写 gui_prefs.json audio 节。勾选切换不收菜单
                // （圆点即时可见，同镜像菜单勾选行）。
                MenuCheckRow {
                    objectName: "menuAudioExclusive"
                    text: "音频独占"
                    marked: ctxMenu.mAudio
                    onClicked: ctrl.setAudioExclusive(
                        ctxMenu.mEntry.package, !ctxMenu.mAudio)
                }
                // ---- 断开保留画面（一级勾选，音频独占之后）：勾选的应用
                // 启动带 --no-vd-destroy-content——会话断开后应用留在虚拟
                // 屏不回落手机主屏（下次打开原地续用）；点选即写
                // gui_prefs.json behavior 节。勾选切换不收菜单（圆点即时
                // 可见，同音频独占行）。
                MenuCheckRow {
                    objectName: "menuKeepVd"
                    text: "断开保留画面"
                    marked: ctxMenu.mKeepVd
                    onClicked: ctrl.setKeepVd(
                        ctxMenu.mEntry.package, !ctxMenu.mKeepVd)
                }
            }
        }

        // ================= 固定比例二级菜单（DESIGN.md §3.7） =================
        // 独立亚克力浮层：一级菜单右侧、错位 4，窄窗右侧放不下时向左展开
        // （防溢出）；小节头横屏/竖屏 + 各 5 项（含机身，比例集与
        // duo/core/aspects.py 的 ASPECT_PRESETS 冻结表一致——该文件为唯一
        // 真源，改比例先改那边）；每项 = 比例名 + 右侧示意矩形（既有画法），
        // 当前记忆的 aspect 项带选中圆点。点选 = setDisplayFixed + 关两级。
        Item {
            id: aspectSub
            objectName: "aspectSubmenu"
            z: 110   // 二级浮层盖在一级菜单（z 100）之上，点外部由拦截层兜住
            property bool openState: false

            width: 128
            // 4 + 2×(小节头 20 + 5×28) + 4
            height: aspectSubCol.implicitHeight + 8
            opacity: openState ? 1.0 : 0.0
            visible: opacity > 0.01
            enabled: openState
            Behavior on opacity { NumberAnimation { duration: Style.durFast } }

            // 展开位置：一级右侧留 4，垂直错位 4；窄窗右侧放不下 → 向左
            // 展开（仍留 4 边距）；高菜单钳制在面板内。同时收起窗口栏二级
            // （同一时刻只有一条展开链）
            function open() {
                barSub.dismiss()
                var gap = 4
                var right = ctxMenu.x + ctxMenu.width + gap
                x = right + width <= stack.width - gap
                   ? right
                   : Math.max(gap, ctxMenu.x - width - gap)
                y = Math.max(gap, Math.min(ctxMenu.y + gap,
                                           stack.height - height - gap))
                x = snapGrid(x)
                y = snapGrid(y)
                aspectPlate.open(x, y)
                openState = true
            }
            function dismiss() { openState = false }

            // 浮层板同款毛玻璃；elevated = 二级阶梯（见 glass-recipe.md）
            MenuGlassPlate { id: aspectPlate; elevated: true }

            Column {
                id: aspectSubCol
                y: 4
                width: parent.width

                MenuSectionLabel { label: "横屏" }
                Repeater {
                    model: [
                        { id: "21:9", label: "21:9", gw: 16, gh: 6.9 },
                        { id: "16:9", label: "16:9", gw: 16, gh: 9 },
                        { id: "4:3", label: "4:3", gw: 16, gh: 12 },
                        { id: "1:1", label: "1:1", gw: 16, gh: 16 },
                        { id: "body-l", label: "机身", gw: 16, gh: 7.6 },
                    ]
                    delegate: AspectPickEntry { }
                }
                MenuSectionLabel { label: "竖屏" }
                Repeater {
                    model: [
                        { id: "3:4", label: "3:4", gw: 10.5, gh: 14 },
                        { id: "2:3", label: "2:3", gw: 9.3, gh: 14 },
                        { id: "5:7", label: "5:7", gw: 10, gh: 14 },
                        { id: "9:16", label: "9:16", gw: 7.9, gh: 14 },
                        { id: "body-p", label: "机身", gw: 6.7, gh: 14 },
                    ]
                    delegate: AspectPickEntry { }
                }
            }
        }

        // ================= 窗口栏二级菜单（窗口栏按应用设置） =================
        // 与「固定比例 ▸」同款亚克力浮层、同展开规则：小节头 上巴/下巴，
        // 上巴 3 项（跟随默认/沉浸/系统）、下巴 4 项（跟随默认/沉浸/系统/
        // 不显示——2026-09-09 第三态 none：该边永不建栏，scrcpy 右键已是
        // 返回，下巴可选）。选中圆点 = 该应用的 explicit 记忆（无 explicit
        // 时点在「跟随默认」）；点选 = setAppBar(pkg, which, mode)（跟随默
        // 认传空串清 override）并关两级。设置页两字段是默认值：只有这里
        // 显式选择过的应用才覆盖它（controller 按包取 effective 注入
        // 启动 argv）。
        Item {
            id: barSub
            objectName: "barSubmenu"
            z: 110   // 二级浮层盖在一级菜单（z 100）之上，点外部由拦截层兜住
            property bool openState: false

            width: 128
            // 4 + 2×小节头 20 + (上巴 3×28 + 下巴 4×28) + 4（高度由
            // barSubCol.implicitHeight 撑开，此处只留布局注释）
            height: barSubCol.implicitHeight + 8
            opacity: openState ? 1.0 : 0.0
            visible: opacity > 0.01
            enabled: openState
            Behavior on opacity { NumberAnimation { duration: Style.durFast } }

            // 展开位置与 aspectSub 同规则：一级右侧留 4、垂直错位 4；窄窗
            // 放不下向左展开；高菜单钳制在面板内；开窗栏即收比例二级
            function open() {
                aspectSub.dismiss()
                var gap = 4
                var right = ctxMenu.x + ctxMenu.width + gap
                x = right + width <= stack.width - gap
                   ? right
                   : Math.max(gap, ctxMenu.x - width - gap)
                y = Math.max(gap, Math.min(ctxMenu.y + gap,
                                           stack.height - height - gap))
                x = snapGrid(x)
                y = snapGrid(y)
                barPlate.open(x, y)
                openState = true
            }
            function dismiss() { openState = false }

            // 浮层板同款毛玻璃；elevated = 二级阶梯（见 glass-recipe.md）
            MenuGlassPlate { id: barPlate; elevated: true }

            Column {
                id: barSubCol
                y: 4
                width: parent.width

                MenuSectionLabel {
                    label: "上巴"
                    headerName: "barSectionHeader"
                    textName: "barSectionHeaderText"
                }
                Repeater {
                    model: [
                        { which: "top", mode: "", label: "跟随默认" },
                        { which: "top", mode: "immersive", label: "沉浸" },
                        { which: "top", mode: "native", label: "系统" },
                    ]
                    delegate: BarPickEntry { }
                }
                MenuSectionLabel {
                    label: "下巴"
                    headerName: "barSectionHeader"
                    textName: "barSectionHeaderText"
                }
                Repeater {
                    model: [
                        { which: "bottom", mode: "", label: "跟随默认" },
                        { which: "bottom", mode: "immersive", label: "沉浸" },
                        { which: "bottom", mode: "native", label: "系统" },
                        // none = 不显示：该边永不建栏（overlay 跳过创建，
                        // 仅留调整大小操作面）；下巴默认即此（设置页默认）
                        { which: "bottom", mode: "none", label: "不显示" },
                    ]
                    delegate: BarPickEntry { }
                }
            }
        }

        // 显示模式记忆变化 → 菜单开着时即时刷新选中圆点（信号带包名，
        // 只刷当前条目的菜单；选毕即关菜单，此路主要服务同包名长按切换）
        Connections {
            target: ctrl
            function onDisplayModeChanged(package) {
                if (ctxMenu.openState && ctxMenu.mEntry.package === package)
                    ctxMenu.mDisplay = ctrl.displayModeFor(String(package))
            }
            // 窗口栏记忆变化 → 同款即时刷新（选毕即关菜单，此路主要服务
            // 开着菜单时经其它入口改记忆的场景）
            function onBarPrefsChanged(package) {
                if (ctxMenu.openState && ctxMenu.mEntry.package === package)
                    ctxMenu.mBars = {
                        top: ctrl.barModeFor(String(package), "top"),
                        bottom: ctrl.barModeFor(String(package), "bottom")
                    }
                // 空包名 = 镜像键：镜像卡菜单的窗口栏选中点（设置页默认）
                if (mirrorMenu.openState && package === "")
                    mirrorMenu.mBars = {
                        top: ctrl.barModeFor("", "top"),
                        bottom: ctrl.barModeFor("", "bottom")
                    }
            }
            // 音频独占记忆变化 → 同款即时刷新（勾选行不收菜单，圆点跟着走）
            function onAudioPrefsChanged(package) {
                if (ctxMenu.openState && ctxMenu.mEntry.package === package)
                    ctxMenu.mAudio = ctrl.audioExclusiveFor(String(package))
            }
            // 断开保留画面记忆变化 → 同款即时刷新（勾选行不收菜单）
            function onBehaviorPrefsChanged(package) {
                if (ctxMenu.openState && ctxMenu.mEntry.package === package)
                    ctxMenu.mKeepVd = ctrl.keepVdFor(String(package))
            }
        }

        // ================= 镜像卡右键菜单（DESIGN.md §3.5） =================
        // 与磁贴菜单同浮层语言（亚克力 + 圆角 12 + 条目高 32）：
        // 打开投屏 / hairline / 镜像时关闭设备屏幕（勾选写回设置）。
        // 勾选切换不收菜单（勾选态即时可见），点外部/Esc/打开投屏后关。
        Item {
            id: mirrorMenu
            objectName: "mirrorContextMenu"
            z: 100
            property bool openState: false
            // 窗口栏选中态（镜像无应用包 → 读的是设置页默认；openAt
            // 时拉取，barPrefsChanged("") 即时刷新）
            property var mBars: ({
                top: { explicit: false, mode: "immersive" },
                bottom: { explicit: false, mode: "none" }
            })

            width: 160
            // 高度由 Column 撑开（4 + 条目 + 4）
            height: mirrorMenuCol.implicitHeight + 8
            opacity: openState ? 1.0 : 0.0
            visible: opacity > 0.01
            enabled: openState
            Behavior on opacity { NumberAnimation { duration: Style.durFast } }
            focus: openState
            Keys.onEscapePressed: dismiss()

            // 打开于 panel 坐标 (px, py)，钳制在面板内（窄窗不溢出）
            function openAt(px, py) {
                x = snapGrid(Math.max(4, Math.min(px, stack.width - width - 4)))
                y = snapGrid(Math.max(4, Math.min(py, stack.height - height - 4)))
                mBars = {
                    top: ctrl.barModeFor("", "top"),
                    bottom: ctrl.barModeFor("", "bottom")
                }
                mirrorPlate.open(x, y)
                openState = true
                forceActiveFocus()
            }
            function dismiss() { openState = false }

            // 浮层板 = MenuGlassPlate（与右键菜单同款毛玻璃）
            MenuGlassPlate { id: mirrorPlate }

            Column {
                id: mirrorMenuCol
                y: 4
                width: parent.width

                MenuCheckRow {
                    objectName: "mirrorMenuOpen"
                    text: "打开投屏"
                    onClicked: {
                        mirrorMenu.dismiss()
                        ctrl.startMirror()
                    }
                }
                // hairline 分隔（上下各 4px 留白）
                Item {
                    x: 12
                    width: parent.width - 24
                    implicitHeight: 9
                    Rectangle { y: 4; width: parent.width; height: 1; color: Style.hairline }
                }
                // ---- 窗口栏（2026-09-09 用户反馈：镜像卡右键菜单不够完整）：
                // 设备镜像没有应用包，这里直接写设置页默认（top/bottom_
                // bar_mode，setDefaultBarMode）——与设置页同一对字段，
                // 两处永远一致；选项集与设置页对齐（上巴 沉浸/系统；
                // 下巴 沉浸/系统/不显示）。生效于下一次投屏。
                MenuSectionLabel {
                    label: "上巴"
                    headerName: "mirrorBarSectionHeader"
                    textName: "mirrorBarSectionHeaderText"
                }
                MenuCheckRow {
                    objectName: "mirrorTopImmersive"
                    text: "沉浸"
                    marked: mirrorMenu.mBars.top.mode === "immersive"
                    onClicked: {
                        ctrl.setDefaultBarMode("top", "immersive")
                        mirrorMenu.dismiss()
                    }
                }
                MenuCheckRow {
                    objectName: "mirrorTopNative"
                    text: "系统"
                    marked: mirrorMenu.mBars.top.mode === "native"
                    onClicked: {
                        ctrl.setDefaultBarMode("top", "native")
                        mirrorMenu.dismiss()
                    }
                }
                MenuSectionLabel {
                    label: "下巴"
                    headerName: "mirrorBarSectionHeader"
                    textName: "mirrorBarSectionHeaderText"
                }
                MenuCheckRow {
                    objectName: "mirrorBottomImmersive"
                    text: "沉浸"
                    marked: mirrorMenu.mBars.bottom.mode === "immersive"
                    onClicked: {
                        ctrl.setDefaultBarMode("bottom", "immersive")
                        mirrorMenu.dismiss()
                    }
                }
                MenuCheckRow {
                    objectName: "mirrorBottomNative"
                    text: "系统"
                    marked: mirrorMenu.mBars.bottom.mode === "native"
                    onClicked: {
                        ctrl.setDefaultBarMode("bottom", "native")
                        mirrorMenu.dismiss()
                    }
                }
                MenuCheckRow {
                    objectName: "mirrorBottomNone"
                    text: "不显示"
                    marked: mirrorMenu.mBars.bottom.mode === "none"
                    onClicked: {
                        ctrl.setDefaultBarMode("bottom", "none")
                        mirrorMenu.dismiss()
                    }
                }
                Item {
                    x: 12
                    width: parent.width - 24
                    implicitHeight: 9
                    Rectangle { y: 4; width: parent.width; height: 1; color: Style.hairline }
                }
                MenuCheckRow {
                    objectName: "mirrorMenuScreenOff"
                    text: "镜像时关闭设备屏幕"
                    marked: ctrl.turnScreenOff
                    onClicked: ctrl.toggleTurnScreenOff()
                }
            }
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
    // 小圆点（直径 ≤8 一律此法）：零 border——`Rectangle{radius:w/2;
    // border.width:1}` 在小尺寸非整数像素下 border 会错（半像素跨边界/
    // 锯齿，Windows GL 真机偶发）。改为同心两个原生实心 Rectangle 叠加：
    // 外层 = 环色实心圆（ringWidth > 0 时，如状态点的白环），内层 = 点色
    // 实心圆（内径 = 外径 − 2×环宽），全部整数尺寸、anchors.centerIn 居中；
    // ringWidth 0 = 纯实心点（内层不重复绘制）。dotSize 取偶数保证半径也是整数。
    component Dot: Rectangle {
        id: dot
        property color dotColor: Style.accent      // 点色（内层实心圆）
        property int dotSize: 8                     // 点径（偶数）
        property int ringWidth: 0                   // 环宽（0 = 无环）
        property color ringColor: "#FFFFFF"         // 环色（状态点白环，与玻璃卡分离）

        width: dotSize + 2 * ringWidth
        height: width
        radius: width / 2
        color: ringWidth > 0 ? ringColor : dotColor

        Rectangle {
            anchors.centerIn: parent
            visible: dot.ringWidth > 0
            width: dot.dotSize
            height: width
            radius: width / 2
            color: dot.dotColor
        }
    }

    // 顶栏胶囊分段（DESIGN.md §3.2）：选中 = **不透明纯白**圆角 14 +
    // 主文字（与胶囊底 flyoutFill 60% 的强材质差即分层——72% 白与 60%
    // 白色差太弱，真机看不出在哪页，2026-09-08 改不透明）；
    // 未选中 = 透明 + 次色 + hover 洗色 + 手型光标。无图标。
    component CapsuleSegment: AbstractButton {
        id: cseg
        property bool selected: false

        hoverEnabled: true
        // 手型光标：acceptedButtons NoButton 的 MouseArea 只设光标不抢点击
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
        }

        background: Item {
            // 未选中：hover/press 洗色（内缩 2 与选中段同心）
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: 14
                visible: !cseg.selected
                color: cseg.pressed ? Style.pressWash
                                    : (cseg.hovered ? Style.hoverWash : "transparent")
                Behavior on color { ColorAnimation { duration: Style.durFast } }
            }
            // 选中段填充：不透明纯白（内缩 2、同心 16−2=14）——与胶囊
            // 半透明底拉出明显色差，选中态一眼可见
            Rectangle {
                visible: cseg.selected
                x: 2; y: 2; width: parent.width - 4; height: parent.height - 4
                radius: 14
                color: Style.segmentFill
            }
        }
        contentItem: Text {
            text: cseg.text
            font.pixelSize: 13
            font.weight: cseg.selected ? Font.DemiBold : Font.Normal
            color: cseg.selected ? Style.ink : Style.ink2
            Behavior on color { ColorAnimation { duration: Style.durFast } }
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        Accessible.role: Accessible.Button
        Accessible.name: cseg.text
    }

    // 应用图标 + 未知应用 fallback：真实/预设图标（file URL，Image 原生
    // 渲染 PNG/SVG）优先，icon 空串 → 首字 squircle（圆角 23%）+ 包名
    // 哈希取色的柔和色板 + 白字首字（DESIGN.md §3.1，禁止灰底圆）。
    component AppGlyph: Item {
        id: glyph
        required property var modelData   // {package,label,key,icon,...}
        property real size: 60
        width: size
        height: size

        Image {
            anchors.fill: parent
            visible: glyph.modelData.icon !== ""
            source: visible ? glyph.modelData.icon : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
        }
        Rectangle {
            objectName: "fallbackIcon"
            anchors.fill: parent
            radius: Math.round(glyph.size * 0.23)   // squircle 23%（60→14 / 44→10）
            visible: glyph.modelData.icon === ""
            color: root.fallbackColor(String(glyph.modelData.package))
            Text {
                anchors.centerIn: parent
                text: String(glyph.modelData.label).charAt(0)
                font.pixelSize: Math.round(glyph.size * 0.32)   // 60→19 / 44→14
                font.weight: Font.DemiBold
                color: "#FFFFFF"
            }
        }
    }

    // 应用磁贴：图标（AppGlyph）+ 短标签；未安装降透明且禁点；hover 只洗
    // 60px 图标区（不洗整格）；右键/长按 = 上下文菜单；★ 角标置顶常显、
    // hover 露出
    component AppTile: Item {
        id: tile
        required property var modelData   // {package,label,key,icon,installed,pinned}

        width: grid.cellWidth
        height: grid.cellHeight
        opacity: modelData.installed ? 1.0 : 0.4
        Behavior on opacity { NumberAnimation { duration: Style.durFast } }

        // 图标区洗色块（60×60 圆角 14）：只覆盖图标，不洗整格
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 10
            width: 60; height: 60; radius: 14
            color: tileMa.pressed ? Style.pressWash
                                  : (tileMa.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        AppGlyph {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 10
            modelData: tile.modelData
            size: 60
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
            // 补发 clicked（长按=菜单 + 单击=启动 连发），onClicked 必须
            // 吞掉并复位；onPressed 复位保证下一次按压不受上一次残留影响。
            property bool held: false
            onPressed: function () { held = false }
            onClicked: function (mouse) {
                if (held) { held = false; return }   // 长按收尾，不是单击
                var p = mapToItem(panel, mouse.x, mouse.y)
                if (mouse.button === Qt.RightButton)
                    ctxMenu.openFor(tile.modelData, p.x, p.y)
                else
                    ctrl.startSession(tile.modelData.package)
            }
            // 触屏长按 = 右键等价（单次触发，clicked 由 held 吞掉）
            onPressAndHold: function (mouse) {
                held = true
                var p = mapToItem(panel, mouse.x, mouse.y)
                ctxMenu.openFor(tile.modelData, p.x, p.y)
            }
            Accessible.role: Accessible.Button
            Accessible.name: tile.modelData.label
        }

        // 置顶角标：叠在 tileMa 之上（后声明 → 更高堆叠序，点击不穿透到
        // 磁贴），位于图标右上角。已置顶常显 ★（强调色），未置顶仅在 hover
        // 时露出 ☆ 提示可点；不安装也允许置顶（与模型状态一致，装好即在顶）。
        AbstractButton {
            id: pinBtn
            objectName: "pinButton"
            width: 26
            height: 26
            anchors.top: parent.top
            anchors.topMargin: 1
            anchors.horizontalCenter: parent.horizontalCenter
            // 图标 60px 居中：角标压在图标右上角，右侧略微悬出
            anchors.horizontalCenterOffset: 21
            opacity: tile.modelData.pinned || tileMa.containsMouse ? 1.0 : 0.0
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: Style.durFast } }

            background: Rectangle {
                radius: 13
                color: pinBtn.pressed ? Style.pressWash
                                      : (pinBtn.hovered ? Style.hoverWash : Style.cardFill)
                border.width: 1
                border.color: Style.cardBorder
                Behavior on color { ColorAnimation { duration: Style.durFast } }
            }
            contentItem: Text {
                text: tile.modelData.pinned ? "★" : "☆"
                font.pixelSize: 12
                font.weight: Font.DemiBold
                color: tile.modelData.pinned ? Style.accent : Style.ink2
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: ctrl.togglePin(tile.modelData.package)
            Accessible.role: Accessible.Button
            Accessible.name: tile.modelData.pinned
                              ? "取消置顶 " + tile.modelData.label
                              : "置顶 " + tile.modelData.label
        }
    }

    // 固定卡小图标（44px）：无文字，点击/右键/长按语义与磁贴一致
    component PinnedIcon: Item {
        id: pic
        required property var modelData

        width: 44
        height: 44
        opacity: modelData.installed ? 1.0 : 0.4

        // 图标区洗色块（44×44 圆角 10），不洗整卡
        Rectangle {
            anchors.fill: parent
            radius: 10
            color: picMa.pressed ? Style.pressWash
                                 : (picMa.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        AppGlyph {
            anchors.centerIn: parent
            modelData: pic.modelData
            size: 44
        }

        MouseArea {
            id: picMa
            anchors.fill: parent
            hoverEnabled: true
            enabled: pic.modelData.installed
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.PointingHandCursor
            property bool held: false   // 同磁贴：长按吞掉补发的 clicked
            onPressed: function () { held = false }
            onClicked: function (mouse) {
                if (held) { held = false; return }
                var p = mapToItem(panel, mouse.x, mouse.y)
                if (mouse.button === Qt.RightButton)
                    ctxMenu.openFor(pic.modelData, p.x, p.y)
                else
                    ctrl.startSession(pic.modelData.package)
            }
            onPressAndHold: function (mouse) {
                held = true
                var p = mapToItem(panel, mouse.x, mouse.y)
                ctxMenu.openFor(pic.modelData, p.x, p.y)
            }
            Accessible.role: Accessible.Button
            Accessible.name: pic.modelData.label
        }
    }

    // 菜单毛玻璃底板（四枚菜单共用）：三明治 = 过采样快照 → 模糊+蒙版 →
    // 染色；算法、配方与迭代史见 docs/ui/glass-recipe.md
    // 菜单毛玻璃底板（四枚菜单共用）：整窗单纹理架构——源 = canvasRoot
    // 自身的 layer（原点恒 (0,0)，对齐由构造保证），模糊层/蒙版整窗大小，
    // 白块直接放菜单位置；算法与实测见 docs/ui/glass-recipe.md
    component MenuGlassPlate: Item {
        id: plate
        anchors.fill: parent

        property bool elevated: false   // 二级浮层阶梯（见 glass-recipe.md §3）
        property real menuX: 0          // 菜单在 canvasRoot 坐标系中的位置
        property real menuY: 0

        // open(px, py)：登记菜单位置（驱动模糊层对位与蒙版白块）
        function open(px, py) {
            if (!Style.glassBlur)
                return
            plate.menuX = px
            plate.menuY = py
        }

        // ①+② 模糊 + 裁切：整窗尺寸，与源纹理同大小同原点 → 1:1 由构造
        // 保证；autoPadding 关（真机实测它会把蒙版拉大到裁切窗外）；
        // 蒙版 = 整窗透明底上与菜单同位的圆角白块（threshold 0.5 开裁 +
        // spread 0.4 亚像素坡）
        MultiEffect {
            visible: Style.glassBlur
            source: canvasRoot
            x: -plate.menuX
            y: -plate.menuY
            width: canvasRoot.width
            height: canvasRoot.height
            blurEnabled: true
            blurMax: 32
            blur: 0.75
            saturation: 0.15
            autoPaddingEnabled: false
            maskEnabled: true
            maskThresholdMin: 0.5
            maskSpreadAtMin: 0.4
            maskSource: ShaderEffectSource {
                width: canvasRoot.width
                height: canvasRoot.height
                sourceItem: Item {
                    width: canvasRoot.width
                    height: canvasRoot.height
                    visible: false
                    layer.enabled: true
                    Rectangle {
                        x: plate.menuX
                        y: plate.menuY
                        width: plate.width
                        height: plate.height
                        radius: Style.flyoutRadius
                        color: "white"
                    }
                }
                live: true
            }
        }

        // ③ 霜面染色 + hairline；软件回退 = 不透明 menuFill
        Rectangle {
            anchors.fill: parent
            radius: Style.flyoutRadius
            color: {
                if (!Style.glassBlur)
                    return Style.menuFill
                return plate.elevated ? Style.menuTintHi : Style.menuTint
            }
            border.width: 1
            border.color: {
                if (!Style.glassBlur)
                    return Style.menuFillBorder
                return plate.elevated ? Style.menuBorderHi : Style.menuBorder
            }
        }
    }

    // 上下文菜单普通条目：高 32、圆角 10、hover 洗色、无图标（文字自解释，
    // DESIGN.md §3.6）。文字左对齐菜单边缘 12（x4 + 内 8）。
    component MenuRow: AbstractButton {
        id: mrow
        x: 4
        width: parent.width - 8
        implicitHeight: 32
        background: Rectangle {
            radius: Style.controlRadius
            color: mrow.pressed ? Style.pressWash
                                : (mrow.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        contentItem: Item {
            Text {
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                text: mrow.text
                font.pixelSize: 13
                color: Style.ink
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: mrow.text
    }

    // 镜像菜单条目（§3.5/§3.7 同浮层语言）：与 MenuRow 同度量，但左侧留出
    // 固定勾选凹槽（文字统一 x 20，Win11 式栅格）——勾选时凹槽里显 4px
    // 强调色圆点（与比例菜单“当前项”同语义），未勾选不占视觉。
    component MenuCheckRow: AbstractButton {
        id: crow
        // 自定义勾选标记（AbstractButton.checked 是 FINAL，不可覆写）
        property bool marked: false
        x: 4
        width: parent.width - 8
        implicitHeight: 32
        background: Rectangle {
            radius: Style.controlRadius
            color: crow.pressed ? Style.pressWash
                                : (crow.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        contentItem: Item {
            Dot {
                objectName: "menuCheckDot"
                visible: crow.marked
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                dotSize: 4
                dotColor: Style.accent
            }
            Text {
                x: 20
                anchors.verticalCenter: parent.verticalCenter
                text: crow.text
                font.pixelSize: 13
                color: Style.ink
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: crow.text
        Accessible.description: crow.marked ? "已勾选" : ""
    }

    // 二级展开条目（§3.7「固定比例 ▸」）：与勾选行同度量/同凹槽栅格（文字
    // x 20），右侧细箭头 › 文字字符；hover 或点击展开二级菜单，二级展开
    // 期间保持 hover 洗色（联动，指示两者同属一个弹出链）
    component MenuSubmenuRow: AbstractButton {
        id: srow
        property bool active: false   // 二级展开中（保持洗色）
        hoverEnabled: true
        x: 4
        width: parent.width - 8
        implicitHeight: 32
        background: Rectangle {
            radius: Style.controlRadius
            color: srow.pressed ? Style.pressWash
                                : ((srow.hovered || srow.active) ? Style.hoverWash
                                                                 : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        contentItem: Item {
            Text {
                x: 20
                anchors.verticalCenter: parent.verticalCenter
                text: srow.text
                font.pixelSize: 13
                color: Style.ink
            }
            Text {
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: "›"
                font.pixelSize: 15
                color: Style.ink2
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: srow.text + "，展开比例选择"
    }

    // 小节头（横屏/竖屏，DESIGN.md §3.6 菜单度量）：11px 次色、高 20、
    // 左 12（与条目文字对齐）；objectName 可换——窗口栏二级菜单的小节头
    // 用独立名字，不混入比例菜单的断言集
    component MenuSectionLabel: Item {
        id: msl
        property string label: ""
        property string headerName: "aspectSectionHeader"
        property string textName: "aspectSectionHeaderText"
        objectName: msl.headerName
        x: 4
        width: parent.width - 8
        implicitHeight: 20
        Text {
            objectName: msl.textName
            x: 8
            anchors.verticalCenter: parent.verticalCenter
            text: msl.label
            font.pixelSize: 11
            color: Style.ink2
        }
    }

    // 比例条目（§3.7 菜单度量）：高 28、圆角 8（同心 12−4）；比例名左、
    // 内联 SVG 示意右——1.5px ink2 描边圆角 2，横屏最大边 = 宽 16、竖屏
    // 最大边 = 高 14，按真实比例缩放；机身项画 2:≈2.1 的示意矩形
    // （真值由 controller 从 wm size 派生，此处只是示意）。左侧留 4px 凹槽
    // （文字统一 x 20，与 MenuCheckRow 同栅格）：当前记忆的 aspect 项在凹
    // 槽里显 4px 强调色圆点（选中态，未选不占视觉）
    component AspectMenuRow: AbstractButton {
        id: arow
        property string aspectId: ""
        property real glyphW: 16
        property real glyphH: 9
        property bool marked: false   // = 当前记忆的 aspect（选中圆点）

        objectName: "aspectRow"
        x: 4
        width: parent.width - 8
        implicitHeight: 28
        background: Rectangle {
            radius: 8
            color: arow.pressed ? Style.pressWash
                                : (arow.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        // 示意 SVG 按 2× 尺寸生成再缩到显示尺寸：1.5px 描边在软件后端
        // 出图仍锐利（sourceSize 需整数）
        readonly property string glyphSource: {
            var s = 2
            var w = arow.glyphW * s
            var h = arow.glyphH * s
            var sw = 1.5 * s
            return "data:image/svg+xml;utf8," +
                   "<svg xmlns='http://www.w3.org/2000/svg' width='" + w + "' height='" + h + "'>" +
                   "<rect x='" + (sw / 2) + "' y='" + (sw / 2) + "' width='" + (w - sw) +
                   "' height='" + (h - sw) + "' rx='4' fill='none' stroke='%2386868B' stroke-width='" + sw + "'/></svg>"
        }
        contentItem: Item {
            Dot {
                objectName: "aspectPickDot"
                visible: arow.marked
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                dotSize: 4
                dotColor: Style.accent
            }
            Text {
                x: 20
                anchors.verticalCenter: parent.verticalCenter
                text: arow.text
                font.pixelSize: 13
                color: Style.ink
            }
            Image {
                objectName: "aspectGlyph"
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                width: arow.glyphW
                height: arow.glyphH
                sourceSize.width: Math.round(arow.glyphW * 2)
                sourceSize.height: Math.round(arow.glyphH * 2)
                source: arow.glyphSource
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: "以" + arow.text + "常驻"
    }

    // 比例区 Repeater 委托（二级菜单）：注入冻结表条目 {id,label,gw,gh}；
    // 点击 = ctrl.setDisplayFixed(package, id)（按应用记忆固定比例，此后普通
    // 点击即按此常驻启动）并关两级菜单；id 传控制器，机身项由它按设备
    // wm size 派生真值（无设备时它只报状态不落库）
    component AspectPickEntry: AspectMenuRow {
        required property var modelData
        aspectId: modelData.id
        text: modelData.label
        glyphW: modelData.gw
        glyphH: modelData.gh
        marked: ctxMenu.mDisplay.mode === "fixed"
                && ctxMenu.mDisplay.aspect === modelData.id
        onClicked: {
            ctxMenu.dismiss()
            ctrl.setDisplayFixed(ctxMenu.mEntry.package, modelData.id)
        }
    }

    // 窗口栏条目（「窗口栏 ▸」二级菜单，窗口栏按应用设置）：与比例条目同
    // 度量/同凹槽栅格（高 28、圆角 8、圆点槽 x8、文字 x20），但右侧无示意
    // 矩形。barMode 空串 = 「跟随默认」项（清除该应用该条栏的 override）
    component BarPickRow: AbstractButton {
        id: brow
        property string barWhich: ""   // top | bottom
        property string barMode: ""    // immersive | native | none | ""（跟随默认）
        property bool marked: false    // = 该应用该条栏的选中态

        objectName: "barRow"
        x: 4
        width: parent.width - 8
        implicitHeight: 28
        background: Rectangle {
            radius: 8
            color: brow.pressed ? Style.pressWash
                                : (brow.hovered ? Style.hoverWash : "transparent")
            Behavior on color { ColorAnimation { duration: Style.durFast } }
        }
        contentItem: Item {
            Dot {
                objectName: "barPickDot"
                visible: brow.marked
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                dotSize: 4
                dotColor: Style.accent
            }
            Text {
                x: 20
                anchors.verticalCenter: parent.verticalCenter
                text: brow.text
                font.pixelSize: 13
                color: Style.ink
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: brow.text
    }

    // 窗口栏区 Repeater 委托（二级菜单）：注入 {which, mode, label}；点击 =
    // ctrl.setAppBar(package, which, mode)（「跟随默认」传空串清 override）
    // 并关两级菜单。选中圆点：空串项 = 无 explicit 时选中（圆点在「跟随
    // 默认」）；其余 = explicit 且值相等才选中
    component BarPickEntry: BarPickRow {
        required property var modelData
        barWhich: modelData.which
        barMode: modelData.mode
        text: modelData.label
        marked: modelData.mode === ""
                ? !ctxMenu.mBars[modelData.which].explicit
                : ctxMenu.mBars[modelData.which].explicit
                  && ctxMenu.mBars[modelData.which].mode === modelData.mode
        onClicked: {
            ctxMenu.dismiss()
            ctrl.setAppBar(ctxMenu.mEntry.package, modelData.which, modelData.mode)
        }
    }

    // 运行中芯片（DESIGN.md §3.7）：8px 绿点 + 标签（点击 = 拉回该会话
    // 虚拟屏），无方向小标签；hover 露出 ✕（opacity 0→1 140ms，位宽预留
    // 不换行）；✕ hover = 危险色洗底 + 危险色线条
    component SessionChip: Rectangle {
        id: chip
        required property var modelData   // {key,label,running,portrait}

        objectName: "sessionChip"
        height: 32
        radius: 16
        width: chipRow.implicitWidth + 24
        color: Style.cardFill
        border.width: 1
        border.color: Style.cardBorder

        // 整枚芯片的 hover（驱动 ✕ 显隐；HoverHandler 不抢按钮点击）
        HoverHandler { id: chipHover }

        Row {
            id: chipRow
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 12
            spacing: 8

            // 绿点 + 标签整体可点：把应用拉回该会话的虚拟屏
            // （am start --display N，不重建会话）。设备镜像无虚拟屏，
            // 退化为纯展示。HOME 在虚拟屏被系统全局拦截（落物理屏），
            // 面板侧的"回主页"就是这个按钮，永不发 keyevent 3。
            AbstractButton {
                id: chipMain
                objectName: "chipMainButton"
                enabled: chip.modelData.key !== ctrl.mirrorKey
                opacity: enabled ? 1.0 : 0.7
                implicitHeight: 24
                implicitWidth: dotRow.implicitWidth
                anchors.verticalCenter: parent.verticalCenter
                contentItem: Row {
                    id: dotRow
                    spacing: 8
                    Dot {
                        objectName: "chipDot"
                        dotSize: 8
                        ringWidth: 1   // 8px 绿核 + 1px 白环（与设备状态点同构，§3.7）
                        dotColor: Style.running
                        anchors.verticalCenter: parent.verticalCenter
                    }
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
            // 停止 ✕：芯片 hover 才露出（透明度 140ms，位宽预留 Flow 不因
            // 显隐换行）；自身 hover = 危险色洗底 + 危险色线条
            AbstractButton {
                id: stopBtn
                objectName: "chipStopButton"
                implicitWidth: 24
                implicitHeight: 24
                anchors.verticalCenter: parent.verticalCenter
                opacity: chipHover.hovered ? 1.0 : 0.0
                Behavior on opacity { NumberAnimation { duration: Style.durFast } }
                background: Rectangle {
                    radius: 12
                    color: stopBtn.pressed ? Style.pressWash
                                           : (stopBtn.hovered ? Style.dangerWash : "transparent")
                    Behavior on color { ColorAnimation { duration: Style.durFast } }
                }
                contentItem: Item {
                    Rectangle { width: 10; height: 1.6; radius: 0.8; rotation: 45
                                anchors.centerIn: parent
                                color: stopBtn.hovered ? Style.danger : Style.ink2 }
                    Rectangle { width: 10; height: 1.6; radius: 0.8; rotation: -45
                                anchors.centerIn: parent
                                color: stopBtn.hovered ? Style.danger : Style.ink2 }
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

            // 搜索过滤结果：纯 QML 侧数组过滤（不动 ctrl.apps 模型）。
            // 命中 = 标签原文包含（小写化）或拼音首字母串前缀（key.startsWith，
            // 输入 wx 命中微信）；query 为空 → 原样。
            readonly property var filteredApps: {
                var q = searchField.text.trim().toLowerCase()
                if (q === "")
                    return ctrl.apps
                return ctrl.apps.filter(function (e) {
                    return String(e.label).toLowerCase().indexOf(q) >= 0
                           || String(e.key).startsWith(q)
                })
            }

            // 背景画布在 zoomLayer（StackView 之下、两页共用），见上；
            // 本页其余层次直接铺在其上。

            // 无标题行/齿轮按钮：顶栏胶囊即导航（DESIGN.md §3.2），
            // 窗口标题栏已表达身份。

            // ================= 设备卡（玻璃，纯状态展示；零阴影，铁律 8） =================
            // DESIGN.md §3.5：投屏按钮已移入镜像卡，设备卡不带按钮 ——
            // 绿点 + 在线/离线 + serial 即全部内容，右侧留白；分层只靠
            // cardFill vs 画布 + cardBorder 亮边（半透明卡的阴影会透过
            // 玻璃互叠成暗晕，内容卡一律零阴影）
            Rectangle {
                id: deviceCard
                objectName: "deviceCard"
                x: 20
                y: 64   // 胶囊（16+32）下方留 16 间距
                width: parent.width - 40
                height: 76
                radius: Style.cardRadius
                color: Style.cardFill
                border.width: 1
                border.color: Style.cardBorder

                Row {
                    id: devRow
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 14
                    spacing: 10

                    // 在线状态点（#34C759 仅用于运行/在线语义）：在线绿，
                    // 有设备但离线/未授权/recovery → 琥珀警示，无设备灰。
                    // 绿点白环 = Dot（8px 点核 + 1px 不透明白环，与玻璃卡
                    // 分离；菜单选中态 4px 点无环）
                    Dot {
                        objectName: "deviceDot"
                        dotSize: 8   // 绿核与芯片点同尺寸（§3.7）
                        ringWidth: 1
                        dotColor: root.device !== null ? Style.running
                                                       : (root.fallbackDevice !== null ? Style.warn : "#C7C7CC")
                        anchors.verticalCenter: parent.verticalCenter
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
            }

            // ================= 固定应用卡（有置顶才出现） =================
            Item {
                id: pinnedWrap
                readonly property bool hasPinned: ctrl.pinnedApps.length > 0
                x: 20
                y: deviceCard.y + deviceCard.height + 12
                width: parent.width - 40
                height: hasPinned ? 68 : 0
                opacity: hasPinned ? 1.0 : 0.0
                visible: opacity > 0.01
                Behavior on opacity { NumberAnimation { duration: Style.durFast } }

                Rectangle {
                    id: pinnedCard
                    objectName: "pinnedCard"
                    anchors.fill: parent
                    radius: Style.cardRadius
                    color: Style.cardFill
                    border.width: 1
                    border.color: Style.cardBorder

                    // 44px 小图标横排（间距 12，窄窗口换行），无文字
                    Flow {
                        x: 12; y: 12
                        width: parent.width - 24
                        spacing: 12
                        Repeater {
                            model: ctrl.pinnedApps
                            delegate: PinnedIcon { }
                        }
                    }
                }
            }

            // ================= 镜像卡（固定卡与搜索之间，DESIGN.md §3.5） =================
            // 同语言玻璃卡（高 64、内边距 12、圆角 16）：左「设备镜像」15px
            // DemiBold + 媒体音量条（调 Android 侧媒体流，离线隐藏），右
            //「投屏」强调按钮（唯一启动路径，卡本体不可点）；
            // 右键卡 = 镜像上下文菜单（打开投屏 / 镜像时关闭设备屏幕）。
            // 运行中的镜像会话仍出现在底部运行卡，不在此卡内。
            Item {
                id: mirrorWrap
                objectName: "mirrorCard"
                x: 20
                // 固定卡折叠时直接贴设备卡（否则零高占位会多出一行 12px 间距）
                y: pinnedWrap.y + (pinnedWrap.hasPinned ? pinnedWrap.height + 12 : 0)
                width: parent.width - 40
                height: 64

                Rectangle {
                    id: mirrorCardRect
                    anchors.fill: parent
                    radius: Style.cardRadius
                    color: Style.cardFill
                    border.width: 1
                    border.color: Style.cardBorder

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: "设备镜像"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                        color: Style.ink
                    }

                    // 扬声器图标（2026-09-09 用户反馈：光秃秃一根滑杆看起来
                    // 像画质调节）：Canvas 画单体矢量扬声器（单色 ink2，
                    // 14×14），放在滑杆左侧，一眼读成“音量”。
                    Canvas {
                        id: volumeGlyph
                        objectName: "mediaVolumeGlyph"
                        anchors.left: parent.left
                        anchors.leftMargin: 78
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14
                        height: 14
                        visible: root.device !== null   // 与滑杆同离线隐藏
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            ctx.fillStyle = Style.ink2
                            ctx.strokeStyle = Style.ink2
                            ctx.lineWidth = 1.4
                            ctx.beginPath()
                            ctx.moveTo(1, 5)
                            ctx.lineTo(4, 5)
                            ctx.lineTo(8, 1)
                            ctx.lineTo(8, 13)
                            ctx.lineTo(4, 9)
                            ctx.lineTo(1, 9)
                            ctx.closePath()
                            ctx.fill()
                            ctx.beginPath()
                            ctx.arc(8.5, 7, 3, -0.85, 0.85)
                            ctx.stroke()
                            ctx.beginPath()
                            ctx.arc(8.5, 7, 5.5, -0.85, 0.85)
                            ctx.stroke()
                        }
                    }

                    // 媒体音量条（极简，无文字标签）：拖动调 Android 侧
                    // 媒体流音量（cmd media_session volume --stream 3
                    // --set N——真机实测 `media volume` 在 OEM ROM 上不
                    // 存在；采集源在设备端，Windows 端增益救不了提示音
                    // 偏小）。预读已放弃（--get 无数字可解析）：
                    // ctrl.mediaVolume = -1 时为中性态（无填充无拇指，
                    // 语义「未知，拖动即设定」），首次拖动后进入已知态。
                    // 视觉跟手指实时，命令防抖 200ms；设备离线时隐藏。
                    Slider {
                        id: mediaVolumeSlider
                        objectName: "mediaVolumeSlider"
                        anchors.left: parent.left
                        anchors.leftMargin: 100
                        anchors.right: mirrorBtn.left
                        anchors.rightMargin: 14
                        anchors.verticalCenter: parent.verticalCenter
                        implicitHeight: 16
                        from: 0
                        to: 15
                        stepSize: 1
                        visible: root.device !== null
                        enabled: visible
                        // 已拖过：视觉先行（填充/拇指立刻跟上手指），
                        // 不等防抖后的控制器回写
                        property bool touched: false
                        readonly property bool known: ctrl.mediaVolume >= 0
                        // 未知态：值居中但不画填充/拇指；首次拖动会打断
                        // 此绑定改由滑杆自持（滑杆是唯一写入者，无碍）
                        value: known ? ctrl.mediaVolume : to / 2

                        background: Rectangle {
                            objectName: "volumeTrack"
                            x: mediaVolumeSlider.leftPadding
                            y: mediaVolumeSlider.topPadding
                               + mediaVolumeSlider.availableHeight / 2 - height / 2
                            width: mediaVolumeSlider.availableWidth
                            height: 4
                            radius: 2
                            color: Style.hairline
                            // accent 填充只跟 visualPosition（手指），未知
                            // 且未拖过时整条中性
                            Rectangle {
                                width: (mediaVolumeSlider.touched
                                        || mediaVolumeSlider.known)
                                       ? mediaVolumeSlider.visualPosition * parent.width
                                       : 0
                                height: parent.height
                                radius: 2
                                color: Style.accent
                            }
                        }
                        handle: Rectangle {
                            objectName: "volumeThumb"
                            x: mediaVolumeSlider.leftPadding
                               + mediaVolumeSlider.availableWidth
                               * mediaVolumeSlider.visualPosition - width / 2
                            y: mediaVolumeSlider.topPadding
                               + mediaVolumeSlider.availableHeight / 2 - height / 2
                            width: 12
                            height: 12
                            radius: 6
                            color: Style.accent
                            visible: mediaVolumeSlider.touched
                                     || mediaVolumeSlider.known
                        }
                        // 拖动实时（视觉跟手指），命令 200ms 防抖落一次
                        onMoved: {
                            touched = true
                            volumeDebounce.restart()
                        }
                        Timer {
                            id: volumeDebounce
                            interval: 200
                            onTriggered: ctrl.setMediaVolume(
                                Math.round(mediaVolumeSlider.value))
                        }
                        Accessible.role: Accessible.Slider
                        Accessible.name: "媒体音量"
                    }

                    // 主按钮：投屏（唯一强调色；hover/press 以透明度分级，
                    // 不引入新色相）。objectName/启用逻辑沿用设备卡时代：
                    // 设备在线 && 引擎未锁，禁用 40%
                    AbstractButton {
                        id: mirrorBtn
                        objectName: "mirrorButton"
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        implicitWidth: 68
                        implicitHeight: 32
                        enabled: root.device !== null && !ctrl.engineLocked
                        opacity: enabled ? (pressed ? 0.8 : (hovered ? 0.9 : 1.0)) : 0.4
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

                // 右键卡本体 = 镜像上下文菜单（仅右键；左键留给按钮，
                // 卡本体不可启动）
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.RightButton
                    onClicked: function (mouse) {
                        var p = mapToItem(panel, mouse.x, mouse.y)
                        mirrorMenu.openAt(p.x, p.y)
                    }
                }
            }

            // ================= 搜索（胶囊，全宽） =================
            Rectangle {
                id: searchCapsule
                objectName: "searchCapsule"
                x: 20
                y: mirrorWrap.y + mirrorWrap.height + 12
                width: parent.width - 40
                height: 36
                radius: 18
                // 常态与卡一致；聚焦 = 亚克力浮层感（DESIGN.md §3.4）
                color: searchField.activeFocus ? Style.flyoutFill : Style.cardFill
                Behavior on color { ColorAnimation { duration: Style.durFast } }
                border.width: 1
                border.color: Style.cardBorder

                // 内联 SVG 细线放大镜（16px，1.5 线宽，ink2）
                Image {
                    id: searchGlass
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: 16; height: 16
                    sourceSize.width: 16
                    sourceSize.height: 16
                    source: "data:image/svg+xml;utf8," +
                            "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>" +
                            "<circle cx='7' cy='7' r='4.5' fill='none' stroke='%2386868B' stroke-width='1.5'/>" +
                            "<line x1='10.6' y1='10.6' x2='14' y2='14' stroke='%2386868B' stroke-width='1.5' stroke-linecap='round'/></svg>"
                }
                TextField {
                    id: searchField
                    objectName: "searchField"
                    anchors.left: searchGlass.right
                    anchors.leftMargin: 8
                    anchors.right: parent.right
                    anchors.rightMargin: searchClear.visible ? 36 : 12
                    anchors.verticalCenter: parent.verticalCenter
                    placeholderText: "搜索"
                    placeholderTextColor: Style.ink2
                    color: Style.ink
                    font.family: Style.fontDefault
                    font.pixelSize: 13
                    selectByMouse: true
                    verticalAlignment: TextInput.AlignVCenter
                    leftPadding: 0
                    rightPadding: 0
                    background: null
                    // Esc 清空并失焦（Ctrl+F 聚焦见下方 Shortcut）
                    Keys.onEscapePressed: {
                        text = ""
                        focus = false
                    }
                    Accessible.name: "搜索应用"
                }
                // 清空钮：仅输入非空时露出（140ms 透明度）
                AbstractButton {
                    id: searchClear
                    objectName: "searchClearButton"
                    anchors.right: parent.right
                    anchors.rightMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    implicitWidth: 28
                    implicitHeight: 28
                    opacity: searchField.text !== "" ? 1.0 : 0.0
                    visible: opacity > 0.01
                    enabled: visible
                    Behavior on opacity { NumberAnimation { duration: Style.durFast } }
                    background: Rectangle {
                        radius: 14
                        color: searchClear.pressed ? Style.pressWash
                                                   : (searchClear.hovered ? Style.hoverWash : "transparent")
                        Behavior on color { ColorAnimation { duration: Style.durFast } }
                    }
                    contentItem: Item {
                        Rectangle { width: 10; height: 1.6; radius: 0.8; rotation: 45
                                    anchors.centerIn: parent; color: Style.ink2 }
                        Rectangle { width: 10; height: 1.6; radius: 0.8; rotation: -45
                                    anchors.centerIn: parent; color: Style.ink2 }
                    }
                    onClicked: {
                        searchField.text = ""
                        searchField.forceActiveFocus()
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: "清空搜索"
                }
            }

            // Ctrl+F 聚焦搜索（设置页在栈顶时不动）
            Shortcut {
                sequences: ["Ctrl+F"]
                enabled: stack.depth === 1
                onActivated: {
                    searchField.forceActiveFocus()
                    searchField.selectAll()
                }
            }

            // ================= 应用网格（裸排，过滤结果） =================
            GridView {
                id: grid
                objectName: "appsGrid"
                x: 20
                anchors.top: searchCapsule.bottom
                anchors.topMargin: 16
                width: parent.width - 40
                height: chipsZone.visible ? chipsZone.y - grid.y - 14 : (panel.height - 40 - grid.y)
                clip: true
                // ↑ 窄窗口/多行时网格内部滚动；芯片区常驻，运行状态不被滚走
                interactive: contentHeight > height
                model: panel.filteredApps
                visible: root.installedCount > 0

                // 列数随宽度自适应；保证每格 ≥72px 见方（目标 92px）
                cellWidth: width / Math.max(2, Math.floor(width / 92))
                cellHeight: 102

                ScrollIndicator.vertical: ScrollIndicator { }

                delegate: AppTile { }
            }

            // 搜索无结果：网格区一行辅助文字（无空态插画）
            Text {
                objectName: "noMatchLabel"
                visible: root.installedCount > 0 && panel.filteredApps.length === 0
                anchors.top: searchCapsule.bottom
                anchors.topMargin: 24
                anchors.horizontalCenter: parent.horizontalCenter
                text: "无匹配应用"
                font.pixelSize: 13
                color: Style.ink2
            }

            // 无已装应用空态
            Column {
                anchors.top: searchCapsule.bottom
                anchors.topMargin: 24
                anchors.horizontalCenter: parent.horizontalCenter
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

            // ================= 运行卡（有会话才出现，DESIGN.md §3.7） =================
            // 与固定卡同语言的玻璃卡（圆角 16 内边距 12），卡内芯片 Flow 换行；
            // 底距 56 为 Toast 让位；网格高度据此让位（见 grid.height）。
            Item {
                id: chipsZone
                anchors.left: parent.left
                anchors.leftMargin: 20
                anchors.right: parent.right
                anchors.rightMargin: 20
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 56
                height: runFlow.implicitHeight + 24
                opacity: ctrl.runningSessions.length > 0 ? 1.0 : 0.0
                visible: opacity > 0.01
                Behavior on opacity { NumberAnimation { duration: Style.durFast } }

                Rectangle {
                    id: runningCard
                    objectName: "runningCard"
                    anchors.fill: parent
                    radius: Style.cardRadius
                    color: Style.cardFill
                    border.width: 1
                    border.color: Style.cardBorder

                    Flow {
                        id: runFlow
                        x: 12
                        y: 12
                        width: parent.width - 24
                        spacing: 8
                        Repeater {
                            model: ctrl.runningSessions
                            delegate: SessionChip { }
                        }
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
