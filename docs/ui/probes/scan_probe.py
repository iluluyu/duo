"""Row/column scans of the saved probe PNGs: where does the glass paint?"""
import sys
from PyQt6.QtGui import QImage, QGuiApplication

app = QGuiApplication(sys.argv)
MX0, MY0, MX1, MY1 = 60, 60, 260, 160

imgs = {k: QImage(f"C:\\duo\\probe\\{k}.png") for k in ("base", "A", "B", "C")}

def row(img, y, x0, x1, step):
        return [1 if img.pixelColor(x, y).rgba() != imgs["base"].pixelColor(x, y).rgba() else 0
                for x in range(x0, x1, step)]

print("legend: 1 = differs from base")
for label in ("A", "B", "C"):
        img = imgs[label]
        print(f"--- {label} ---")
        # horizontal scan through menu middle (y=110)
        r = "".join(map(str, row(img, 110, 10, 350, 5)))
        print(" y=110:", r)
        # horizontal scan above menu (y=40)
        r = "".join(map(str, row(img, 40, 10, 350, 5)))
        print(" y= 40:", r)
        # vertical scan through menu middle (x=160)
        r = "".join(map(str, row_column(img, 160, 10, 250, 5) if False else [1 if img.pixelColor(160, y).rgba() != imgs["base"].pixelColor(160, y).rgba() else 0 for y in range(10, 250, 5)]))
        print(" x=160:", r)
        # specific points: menu center (over red bg), ring point, corner
        for name, (x, y) in {"menu_center(160,110)": (160, 110), "menu_flat(80,80)": (80, 80),
                             "ring_left(40,110)": (40, 110), "ring_top(160,40)": (160, 40),
                             "ring_right(300,110)": (300, 110)}.items():
                c = img.pixelColor(x, y).name()
                b = imgs["base"].pixelColor(x, y).name()
                mark = "DIFF" if c != b else "same"
                print(f" {name}: {c} vs base {b} [{mark}]")
