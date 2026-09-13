# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the Duo panel as a windowed ONEFILE bundle (win64).

固定产物 = ``C:\\Tools\\Duo.exe``（docs/windows-setup.md 的固化口径）：

    pyinstaller duo.spec --noconfirm        ->  dist\\Duo.exe
    scripts/build_windows.ps1               ->  构建并部署到 C:\\Tools

Build from the repo root on 64-bit Windows (PyInstaller always targets the
running interpreter, so a win64 bundle needs 64-bit Windows Python).
Onefile self-extracts to temp on launch: slower start, single portable
file - the established convention (former onedir layout retired 2026-09-06).
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
# - assets/duo.ico: runtime window icon. The EXE-level icon= below only
#   embeds the exe resource (Explorer view); the taskbar/title-bar icon
#   comes from Qt at runtime, so the file must also ship and app.py sets it.
datas = [
    (os.path.join(REPO, "duo/ui/qml"), "duo/ui/qml"),
    (os.path.join(REPO, "duo/resources/chrome_overlay.cs"), "duo/resources"),
    (os.path.join(REPO, "duo/resources/duo_icons.dex"), "duo/resources"),
    (os.path.join(REPO, "assets/duo.ico"), "assets"),
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
    a.binaries,
    a.datas,
    [],
    name="Duo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed panel; with arguments gui_entry routes to the
                    # CLI, so the panel can spawn "Duo.exe mirror ..." sessions
    icon=os.path.join(REPO, "assets/duo.ico"),  # exe 资源图标；换图标同名覆盖
    # assets/duo.ico（或 scripts/switch_icon.py）后重打即可——exe 资源与
    # 运行时窗口两条链路都会跟着更新。
)
