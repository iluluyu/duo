"""Windows GL probe v3: DPR-aware sampling + maskInverted variants."""
import json
import sys

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6 import sip
from PyQt6.QtQuick import QQuickWindow

W, H = 360, 260
MX0, MY0, MX1, MY1 = 60, 60, 260, 160

TPL = """
import QtQuick
import QtQuick.Effects
Window {{
    width: {W}; height: {H}; visible: true; color: "transparent"
    title: "duo glass probe v3"
    Item {{
        id: root
        anchors.fill: parent
        Rectangle {{ anchors.fill: parent; color: "#F5F5F7" }}
        Rectangle {{ x: 150; y: 30; width: 180; height: 120; color: "#E0343C" }}
        Rectangle {{ x: 20; y: 170; width: 120; height: 80; color: "#3470C7" }}
        ShaderEffectSource {{
            id: snap
            visible: false
            sourceItem: root
            live: true
            sourceRect: Qt.rect(32, 32, 256, 156)
            width: 256; height: 156
        }}
        {EFFECT}
        ShaderEffectSource {{
            id: theMask
            width: 256; height: 156
            sourceItem: Item {{
                width: 256; height: 156; visible: false; layer.enabled: true
                Rectangle {{ x: 28; y: 28; width: 200; height: 100; radius: 12; color: "white" }}
            }}
            live: true
        }}
    }}
}}
"""

EFF_A = """
        MultiEffect {
            x: 32; y: 32; width: 256; height: 156
            source: snap
            blurEnabled: true; blurMax: 32; blur: 0.75
            saturation: 0.15
            maskEnabled: true
            maskThresholdMin: 0.5
            maskSpreadAtMin: 0.4
            maskSource: theMask
        }
        Rectangle { x: 60; y: 60; width: 200; height: 100; radius: 12
                    color: "#B8F5F5F7"; border.width: 1; border.color: "#14000000" }
"""
EFF_D = EFF_A.replace("maskThresholdMin: 0.5", "maskThresholdMin: 0.5\n            maskInverted: true")
EFF_E = EFF_A.replace("MultiEffect {", "MultiEffect {\n            autoPaddingEnabled: false")
EFF_F = EFF_D.replace("MultiEffect {", "MultiEffect {\n            autoPaddingEnabled: false")

VARIANTS = [("A_prod", EFF_A), ("D_inverted", EFF_D), ("E_nopad", EFF_E), ("F_nopad_inv", EFF_F)]


def main() -> int:
        app = QGuiApplication(sys.argv)
        imgs = {}
        dpr_box = [1.0]

        def render(label, eff, cb):
                eng = QQmlEngine()
                comp = QQmlComponent(eng)
                comp.setData(TPL.format(W=W, H=H, EFFECT=eff).encode("utf-8"),
                             QUrl("file:///probe.qml"))
                if comp.status() != QQmlComponent.Status.Ready:
                        print("QML FAIL", label, comp.errorString())
                        sys.exit(2)
                obj = comp.create()

                def grab():
                        win = sip.cast(obj, QQuickWindow)
                        dpr_box[0] = win.devicePixelRatio()
                        imgs[label] = win.grabWindow()
                        imgs[label].save(f"C:\\duo\\probe\\v3_{label}.png")
                        obj.close()
                        cb()

                QTimer.singleShot(1000, grab)

        def analyze():
                dpr = dpr_box[0]
                print("devicePixelRatio =", dpr)

                def sc(v):
                        return int(v * dpr)

                base = imgs["base"]
                report = {}
                for label, _ in VARIANTS:
                        img = imgs[label]
                        counts = {"inside": 0, "ring": 0, "far": 0}
                        for y in range(0, int(H * dpr)):
                                for x in range(0, int(W * dpr)):
                                        if img.pixelColor(x, y).rgba() != base.pixelColor(x, y).rgba():
                                                if sc(MX0) <= x <= sc(MX1) and sc(MY0) <= y <= sc(MY1):
                                                        counts["inside"] += 1
                                                elif sc(4) <= x <= sc(W - 4) and sc(4) <= y <= sc(H - 4):
                                                        counts["ring"] += 1
                                                else:
                                                        counts["far"] += 1
                        # menu-center sample (over red bg): should be tinted pink if glass paints there
                        center = img.pixelColor(sc(160), sc(110)).name()
                        base_center = base.pixelColor(sc(160), sc(110)).name()
                        report[label] = {"diff": counts,
                                         "center": center, "base_center": base_center}
                print(json.dumps(report, indent=1))
                app.quit()

        def step():
                if queue:
                        render(*queue.pop(0), step)
                else:
                        analyze()

        queue = [("base", "")] + VARIANTS
        step()
        return app.exec()


if __name__ == "__main__":
        sys.exit(main())
