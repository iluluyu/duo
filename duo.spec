# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the Duo panel as a windowed onedir bundle (win64).

Build from the repo root on 64-bit Windows (PyInstaller always targets the
running interpreter, so a win64 bundle needs 64-bit Windows Python):

    .venv\\Scripts\\pyinstaller duo.spec --noconfirm

Output layout (PyInstaller >= 6 onedir): ``dist\\Duo\\Duo.exe`` plus
``dist\\Duo\\_internal\\`` - the two always travel together. One-click
wrapper: ``scripts/build_windows.ps1``.
"""

import os

# PyInstaller resolves spec-relative paths against SPECPATH (the spec's
# directory), not the CWD - a pitfall already recorded in plan.md for
# --add-data. Anchor everything to SPECPATH explicitly.
REPO = SPECPATH

# Data files that frozen code looks up via __file__-relative paths:
# - duo/ui/qml: app.py loads Main.qml next to its own module, so the whole
#   directory must ship (Main/SettingsPage/Style.qml + qmldir declaring the
#   Style singleton). Missing these = "error: Main.qml 加载失败" on launch.
# - duo/resources/chrome_overlay.cs: chrome.py compiles it with csc.exe on
#   the first --chrome window (result cached by source sha256).
datas = [
    (os.path.join(REPO, "duo/ui/qml"), "duo/ui/qml"),
    (os.path.join(REPO, "duo/resources/chrome_overlay.cs"), "duo/resources"),
]

# QML 面板所需的隐藏导入：
# - PyQt6.QtQml / PyQt6.QtQuick 是真实存在的 Python 扩展模块，必须显式列出
#   （面板代码只 import QtQml，静态分析看不到 QML 文件里的 import QtQuick…）。
#   引入它们后，PyInstaller 的 PyQt6 hooks 会连带收集对应 Qt DLL 与插件目录
#   （platforms/qwindows.dll + PyQt6/Qt6/qml/ 下的 QtQuick 插件）。
# - QtQuickControls2 / QtQuick.Effects 没有对应 Python 模块（SettingsPage 与
#   Main.qml 里 import 的 Controls.Basic / Effects 是 QML 侧插件），无需也
#   不能写进 hiddenimports；它们随上面 QtQml/QtQuick 触发的 qml 目录收集进包。
# - QML 源文件本身不随插件走，全靠上面的 datas。
hiddenimports = [
    "PyQt6.QtQml",    # QQmlApplicationEngine
    "PyQt6.QtQuick",  # QtQuick/Controls2/Effects 的 QML 插件随之入包
]

a = Analysis(
    ["gui_entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Duo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed panel; with arguments gui_entry routes to the
                    # CLI, so the panel can spawn "Duo.exe mirror ..." sessions
    icon=os.path.join(REPO, "assets/duo.ico"),  # 占位图标（蓝底圆环）；正式
    # 图标到位后只需替换 assets/duo.ico 同名文件并重打，无需改 spec。
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Duo",
)
