"""Probe v5: window-layer architecture - alignment by construction.

source = root's own layer (window-sized, origin 0,0); effect window-sized;
mask = white rounded rect at the menu position. No sourceRect, no snapshot
item, no second sampling stage. If the measured background edge inside the
glass is IDENTICAL for menus at x=87 vs x=88 (and matches the no-glass
position within blur tolerance), the drift class is eliminated.
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
    title: "probe v5"
    Item {{
        id: root
        anchors.fill: parent
        layer.enabled: true
        Rectangle {{ anchors.fill: parent; color: "#F5F5F7" }}
        Rectangle {{ x: 200; y: 0; width: {W} - 200; height: {H}; color: "#E0343C" }}
        MultiEffect {{
            anchors.fill: parent
            source: root
            blurEnabled: true; blurMax: 32; blur: 0.75
            saturation: 0.15
            autoPaddingEnabled: false
            maskEnabled: true
            maskThresholdMin: 0.5
            maskSpreadAtMin: 0.4
            maskSource: ShaderEffectSource {{
                width: {W}; height: {H}
                sourceItem: Item {{
                    width: {W}; height: {H}; visible: false; layer.enabled: true
                    Rectangle {{ x: {MX}; y: 60; width: 128; height: 100; radius: 12; color: "white" }}
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
        x0 = int((EDGE_X - 40) * dpr)
        x1 = int((EDGE_X + 40) * dpr)
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
                eng = QQmlEngine()
                comp = QQmlComponent(eng)
                comp.setData(TPL.format(W=W, H=H, MX=mx).encode("utf-8"),
                             QUrl("file:///probe.qml"))
                if comp.status() != QQmlComponent.Status.Ready:
                        print("QML FAIL", label, comp.errorString())
                        sys.exit(2)
                obj = comp.create()

                def grab():
                        win = sip.cast(obj, QQuickWindow)
                        dpr_box[0] = win.devicePixelRatio()
                        img = win.grabWindow()
                        img.save(f"C:\\duo\\probe\\v5_{label}.png")
                        results[label] = img
                        obj.close()
                        cb()

                QTimer.singleShot(900, grab)

        def report():
                dpr = dpr_box[0]
                y_dev = int(110 * dpr)
                print("devicePixelRatio =", dpr)
                # ring check for the odd-position variant
                base = results["plain"]
                img = results["offgrid_87"]
                ring = 0
                for y in range(0, int(H * dpr)):
                        for x in range(0, int(W * dpr)):
                                inmenu = (int(87 * dpr) <= x <= int(215 * dpr)
                                          and int(60 * dpr) <= y <= int(160 * dpr))
                                if not inmenu and img.pixelColor(x, y).rgba() != base.pixelColor(x, y).rgba():
                                        ring += 1
                print("offgrid ring px (must be 0):", ring)
                # DECISIVE: interior of both menus (away from tint borders and
                # the background-edge band) must be pixel-identical if the glass
                # content is position-locked.
                a, b = results["offgrid_87"], results["grid_88"]
                x0, x1 = int(92 * dpr), int(196 * dpr)   # avoid edges at 200
                y0, y1 = int(66 * dpr), int(154 * dpr)
                diff = sum(1 for y in range(y0, y1) for x in range(x0, x1)
                           if a.pixelColor(x, y).rgba() != b.pixelColor(x, y).rgba())
                total = (x1 - x0) * (y1 - y0)
                print(f"interior pixel diff 87 vs 88: {diff}/{total}")
                for label in ("offgrid_87", "grid_88", "offgrid_83"):
                        if label in results:
                                ex = edge_x(results[label], y_dev, dpr)
                                print(f"{label}: glass edge {ex} device = {ex / dpr if ex else None} logical")
                ex = edge_x(base, y_dev, dpr)
                print(f"plain edge {ex} device = {ex / dpr if ex else None} logical (true 200)")
                app.quit()

        def step():
                if queue:
                        render(*queue.pop(0), step)
                else:
                        report()

        queue = [("offgrid_87", 87), ("grid_88", 88), ("plain", 3000)]
        step()
        return app.exec()


if __name__ == "__main__":
        sys.exit(main())
