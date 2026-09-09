"""Windows-side GL probe: render the EXACT MenuGlassPlate structure on the
real ANGLE/D3D11 backend, grab pixels, and report where the glass actually
paints. Evidence first - no theory.

Variants:
  base - background only (no glass)
  A    - current production recipe (threshold 0.5 + spread 0.4, autopadding default)
  B    - same but autoPaddingEnabled: false
  C    - no mask (uncropped reference)

Menu rect: (60,60)-(260,160), radius 12. Effect overscan margin 28.
Analysis: per-variant pixel diff vs base, bucketed into
  inside / ring_outside_menu (the halo that must NOT exist) / corner profile.
"""
import json
import math
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
    title: "duo glass probe"
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
            live: false
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
            live: false
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
EFF_B = EFF_A.replace("MultiEffect {", "MultiEffect {\n            autoPaddingEnabled: false")
EFF_C = EFF_A.replace("maskEnabled: true", "maskEnabled: false")


def qml_for(eff):
        return TPL.format(W=W, H=H, EFFECT=eff)


def main() -> int:
        app = QGuiApplication(sys.argv)
        imgs = {}

        def render(label, eff):
                eng = QQmlEngine()
                comp = QQmlComponent(eng)
                comp.setData(qml_for(eff).encode("utf-8"), QUrl("file:///probe.qml"))
                if comp.status() != QQmlComponent.Status.Ready:
                        print("QML FAIL", label, comp.errorString())
                        sys.exit(2)
                obj = comp.create()
                eng.rootContext()  # keep alive

                def grab():
                        win = sip.cast(obj, QQuickWindow)
                        imgs[label] = win.grabWindow()
                        imgs[label].save(f"C:\\duo\\probe\\{label}.png")
                        obj.close()
                        step()

                QTimer.singleShot(900, grab)

        queue = [("base", ""), ("A", EFF_A), ("B", EFF_B), ("C", EFF_C)]

        def step():
                if queue:
                        render(*queue.pop(0))
                else:
                        analyze()

        def analyze():
                base = imgs["base"]
                out = {}
                for label in ("A", "B", "C"):
                        img = imgs[label]
                        counts = {"inside": 0, "ring": 0, "far": 0}
                        for y in range(0, H):
                                for x in range(0, W):
                                        if img.pixelColor(x, y).rgba() != base.pixelColor(x, y).rgba():
                                                if MX0 <= x <= MX1 and MY0 <= y <= MY1:
                                                        counts["inside"] += 1
                                                elif 4 <= x <= W - 4 and 4 <= y <= H - 4:
                                                        counts["ring"] += 1
                                                else:
                                                        counts["far"] += 1
                        # corner profile on bottom-right corner arc
                        cx, cy, r = MX1 - 12, MY1 - 12, 12
                        prof = []
                        for d in range(-3, 6):
                                px = int(cx + (r + d) * math.sqrt(0.5))
                                py = int(cy + (r + d) * math.sqrt(0.5))
                                diff = img.pixelColor(px, py).rgba() != base.pixelColor(px, py).rgba()
                                prof.append(1 if diff else 0)
                        out[label] = {"diff": counts, "corner_diag_changed": prof}
                print(json.dumps(out, indent=1))
                QCoreApplication.instance().quit()

        from PyQt6.QtCore import QCoreApplication  # noqa: E402
        step()
        return app.exec()


if __name__ == "__main__":
        sys.exit(main())
