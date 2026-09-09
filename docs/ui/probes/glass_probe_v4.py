"""Probe v4: does the glass content shift when the menu sits on an
off-grid logical position at DPR 1.25?

Background: vertical red/white boundary at x=200 (sharp edge).
Menu 128x100 at y=60, x = 87 (off-grid, 87*1.25=108.75 device) vs x=88
(on-grid, 110 device). Gaussian blur preserves edge position; any sub-pixel
SHIFT of the glass content shows as the 50% transition moving.
"""
import sys

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6 import sip
from PyQt6.QtQuick import QQuickWindow

W, H = 360, 260
EDGE_X = 200

TPL = """
import QtQuick
import QtQuick.Effects
Window {{
    width: {W}; height: {H}; visible: true; color: "transparent"
    title: "probe v4"
    Item {{
        id: root
        anchors.fill: parent
        Rectangle {{ anchors.fill: parent; color: "#F5F5F7" }}
        Rectangle {{ x: 200; y: 0; width: {W} - 200; height: {H}; color: "#E0343C" }}
        ShaderEffectSource {{
            id: snap
            visible: false
            sourceItem: root
            live: true
            sourceRect: Qt.rect({SX}, 32, 184, 156)
            width: 184; height: 156
        }}
        MultiEffect {{
            x: {SX}; y: 32; width: 184; height: 156
            source: snap
            blurEnabled: true; blurMax: 32; blur: 0.75
            saturation: 0.15
            autoPaddingEnabled: false
            maskEnabled: true
            maskThresholdMin: 0.5
            maskSpreadAtMin: 0.4
            maskSource: ShaderEffectSource {{
                width: 184; height: 156
                sourceItem: Item {{
                    width: 184; height: 156; visible: false; layer.enabled: true
                    Rectangle {{ x: 28; y: 28; width: 128; height: 100; radius: 12; color: "white" }}
                }}
                live: true
            }}
        }}
        Rectangle {{ x: {MX}; y: 60; width: 128; height: 100; radius: 12
                    color: "#B8F5F5F7"; border.width: 1; border.color: "#14000000" }}
    }}
}}
"""


def edge_x(img, y_dev, dpr):
        """Sub-pixel 50% transition of the GREEN channel near EDGE_X
        (bg G=245 -> red rect G=52; adaptive midpoint, downward crossing)."""
        x0 = int((EDGE_X - 30) * dpr)
        x1 = int((EDGE_X + 30) * dpr)
        greens = [img.pixelColor(x, y_dev).green() for x in range(x0, x1)]
        hi, lo = max(greens), min(greens)
        mid = (hi + lo) / 2.0
        prev = greens[0]
        for i in range(1, len(greens)):
                g = greens[i]
                if prev >= mid > g:
                        frac = (prev - mid) / (prev - g)
                        return (x0 + i - 1) + frac
                prev = g
        return None


def main() -> int:
        app = QGuiApplication(sys.argv)
        results = {}
        dpr_box = [1.0]

        def render(label, mx, cb):
                sx = mx - 28
                eng = QQmlEngine()
                comp = QQmlComponent(eng)
                comp.setData(TPL.format(W=W, H=H, MX=mx, SX=sx).encode("utf-8"),
                             QUrl("file:///probe.qml"))
                if comp.status() != QQmlComponent.Status.Ready:
                        print("QML FAIL", label, comp.errorString())
                        sys.exit(2)
                obj = comp.create()

                def grab():
                        win = sip.cast(obj, QQuickWindow)
                        dpr_box[0] = win.devicePixelRatio()
                        img = win.grabWindow()
                        img.save(f"C:\\duo\\probe\\v4_{label}.png")
                        results[label] = img
                        obj.close()
                        cb()

                QTimer.singleShot(900, grab)

        def report():
                dpr = dpr_box[0]
                y_dev = int(110 * dpr)
                print("devicePixelRatio =", dpr)
                for label in results:
                        ex = edge_x(results[label], y_dev, dpr)
                        dev = ex / dpr if ex else None
                        print(f"{label}: edge at {ex} device px = {dev} logical (true edge = {EDGE_X})")
                app.quit()

        def step():
                if queue:
                        render(*queue.pop(0), step)
                else:
                        report()

        queue = [("offgrid_87", 87), ("grid_88", 88), ("plain", 2000)]
        step()
        return app.exec()


if __name__ == "__main__":
        sys.exit(main())
