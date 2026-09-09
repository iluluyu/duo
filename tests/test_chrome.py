"""Tests for the window chrome overlay launcher (C# overlay + csc build)."""

from __future__ import annotations

from pathlib import Path

import pytest

import duo.core.chrome as chrome
from duo.core.chrome import (
        ChromeError,
        compile_command,
        overlay_command,
)


def test_overlay_source_shipped_and_hardened():
        """The C# overlay source exists and carries the known traps' antidotes."""
        assert chrome.OVERLAY_SOURCE.is_file()
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # DPI trap: overlay coordinates must be physical pixels.
        assert "SetProcessDPIAware" in text
        # Per-pixel alpha acrylic (SetWindowCompositionAttribute is dead on 24H2).
        assert "UpdateLayeredWindow" in text
        assert "PrintWindow" in text
        # Top-level minimum-width clamp would distort the capsule.
        assert "WM_GETMINMAXINFO" in text
        # Borderless repairs: native resize frame + Win11 rounded corners.
        assert "0x00040000" in text  # WS_THICKFRAME
        assert "DwmSetWindowAttribute" in text
        # Taskbar-safe emulated maximize with DWM frame insets.
        assert "FrameInsets" in text
        assert "GetMonitorInfoW" in text
        # FakeMaximize fits the WINDOW's monitor: the work area resolves
        # from the window rect's center (straddle-proof), never from
        # MonitorFromWindow (flips to the other monitor at boundaries).
        assert "straddle-proof" in text
        assert "SystemInformation.WorkingArea" in text
        # MonitorFromPoint's flag must be MONITOR_DEFAULTTONEAREST (2) in
        # both center-resolution sites: 1 is MONITOR_DEFAULTTOPRIMARY, which
        # jumps an off-desktop center (window dragged past the screen edge)
        # to the primary's work area - the wrong-screen bug class again.
        assert text.count("2 /*MONITOR_DEFAULTTONEAREST*/") == 2
        assert "MonitorFromPoint(center, 1" not in text
        # Persistent chin tracks window moves in realtime.
        assert "SetWinEventHook" in text
        # 2026-09-08 agy verdict: the chin back affordance is a 4px PILL
        # (iOS Home Indicator language) - chevron/ring/disc glyphs removed.
        assert "pill.AddArc" in text
        assert "DrawChevron" not in text
        assert "DrawRing" not in text
        assert "0xE921" in text and "0xE8BB" in text
        # Navigation keys for the chin (BACK=4, HOME=3).
        # mBack chin: tap = BACK (keyevent 4), long-press = HOME (keyevent 3,
        # physical mirroring) / session close (virtual displays). One ring
        # glyph for every mode - a mode-switching glyph read as a regression
        # and was reverted (2026-09-06).
        assert "AdbKey(4)" in text
        assert "AdbKey(3)" in text
        assert "--home" in text
        assert "AdbKey" in text
        # Ratio-locked resize: display mode channel, live video sizes tailed
        # from the session log ("INFO: Texture: WxH"), aspect convergence,
        # DIP minimums and capture-loss guards on every drag surface.
        assert "--display-mode" in text
        assert "--session-log" in text
        assert "Texture:" in text
        assert "ConvergeToVideoAspect" in text
        assert "LogicalMinW" in text and "LogicalMinH" in text
        assert "MouseCaptureChanged" in text
        # All four edges and corners have their own hot zones.
        assert "new EdgeStrip[9]" in text
        assert "IsZoomed" in text
        # G2 corners: quartic superellipse polygon region (pixel-verified).
        assert "--corner-radius" in text
        assert "CreatePolygonRgn" in text
        assert "ApplyCornerRegion" in text
        # 2026-09-06 回归：钉扎（EnforceFlexPin）是用户验证过的原始行为
        # （窗口形状是用户财产，APP 转屏的 scrcpy 自动改窗被弹回），误删
        # 后同日恢复；显示跟随/横竖屏机器已删，不得复活。
        assert "EnforceFlexPin" in text
        assert "flex pin restored" in text
        assert "MaybeResizeFlexDisplay" not in text
        assert "wm size " not in text
        # Resize path: self-managed drag with ASYNC SetWindowPos (never
        # blocks the overlay thread on the SDL window's slow relayout -
        # the sync-send variant was the original laggy, sticky resize).
        # Resize path: self-managed drag with ASYNC SetWindowPos (never
        # blocks the overlay thread on the SDL window's slow relayout -
        # the sync-send variant was the original laggy, sticky resize).
        assert "SWP_ASYNCWINDOWPOS" in text
        assert "0x4000" in text


def test_native_sandwich_flags_and_parsing():
        """--chrome-top/--chrome-bottom select the sandwich (native) bar
        modes; parsing mirrors --display-mode (argv pair loop) and both
        default to immersive."""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # argv loop: value arrives in the next slot, like --display-mode.
        assert 'argv[i] == "--chrome-top"' in text
        assert 'argv[i] == "--chrome-bottom"' in text
        # 2026-09-09 起三态：immersive|native|none（none = 该边永不建栏）。
        assert "[--chrome-top immersive|native|none]" in text
        assert "[--chrome-bottom immersive|native|none]" in text
        # Defaults are immersive; argv values normalize through
        # NormalizeBarMode (three-member enum, immersive fallback).
        assert 'topMode = "immersive", bottomMode = "immersive"' in text
        assert "_topMode = NormalizeBarMode(topMode);" in text
        assert "_bottomMode = NormalizeBarMode(bottomMode);" in text
        # Controller stores both modes and hands them to the bar ctors
        # (the chin also learns whether Repair keeps DWMWCP_ROUND on the
        # video window -> its corner-ear eligibility).
        assert "_topMode" in text and "_bottomMode" in text
        assert "new ChinWindow(this, home, _displayMode, _bottomMode, VideoRounded)" in text
        assert 'new TopWindow(this, _displayMode.Equals("flex"), _topMode)' in text
        assert "TopNative" in text and "BottomNative" in text


def test_native_sandwich_paint_markers():
        """C1 native CHIN spec values stay (screen-sampled acrylic, adaptive
        pill, 32px bar) while the TOP went real-system in C2 (see the
        caption test below); immersive branches keep their markers."""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # Native acrylic chin: rgba(248,248,248,184) tint over the screen
        # backdrop, saturation x1.15 (ColorMatrix), blur via 1/8 downscale,
        # top hairline 0.08 + inner light edge 0.45, 32px tall.
        assert "FromArgb(184, 248, 248, 248)" in text
        assert "FromArgb(20, 0, 0, 0)" in text
        assert "FromArgb(115, 255, 255, 255)" in text
        assert "1.15f" in text and "ColorMatrix" in text
        assert "CopyFromScreen" in text
        assert "LogicalHeightNative = 32" in text
        # Adaptive pill: dark rgba(29,29,31,102) / white rgba(255,255,255,
        # 155), luminance cut 0.52 on the tinted bar, seated 14px above the
        # bar bottom (centered in the 32px band).
        assert "FromArgb(102, 29, 29, 31)" in text
        assert "FromArgb(155, 255, 255, 255)" in text
        assert "0.52" in text
        assert "Height - 14f * Dpi" in text
        # Native plumbing reuses the existing tracking/resize engines.
        assert "SyncTop" in text and "SampleNativeChin" in text
        assert "SyncFourthButton" in text and "SetNativeSample" in text
        assert "_owner.BeginMoveAt" in text     # immersive caption-band move engine
        # Immersive branches stay: ghost hot zones, hover capsule (now on
        # the sampled dark-acrylic base plate - see the trio test below),
        # proximity reveal bands, mBack pill language.
        assert "GhostBackdrop = true" in text
        assert "DrawCapsuleAcrylic" in text
        assert "ComputeTopVisibility" in text
        assert "ComputeChinVisibility" in text
        assert "TriggerTop" in text


def test_native_top_is_real_system_caption():
        """C2 redo: --chrome-top=native is a REAL system caption - WS_CAPTION
        on the scrcpy window + DWM backdrop (Mica -> #F3F3F3 caption color
        -> default), the system's own caption buttons and title, and one
        overlay 4th button (aspect-preserving maximize) left of the cluster
        reusing the emulated maximize. The C1 self-drawn mica bar is gone."""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # 2026-09-09 架构定稿（真机三轮）：native 上巴不再给无边框窗
        # “补” caption 样式——CLI 根本不传 --window-borderless，scrcpy
        # 建自己的带框窗口，系统标题栏从一开始就在。overlay 只在窗口
        # 带着不完整家族出现时防御性补齐（且已完整时不动，免 FRAMECHANGED
        # 闪帧）；SDL 无边框窗的 WM_NCCALCSIZE 接管让补样式永远无效，
        # 跨进程子类化被 Windows 禁用（真机 ERROR_ACCESS_DENIED）。
        assert "WS_CAPTION" in text and "0x00C00000" in text
        assert "WS_SYSMENU = 0x00080000" in text
        assert "WS_MINIMIZEBOX = 0x00020000" in text
        assert "WS_MAXIMIZEBOX = 0x00010000" in text
        assert "bool complete = (style & captionMask2) == captionMask2" in text
        assert "if (!complete)" in text
        assert "(style & ~WS_POPUP) | caption" in text
        assert "caption styles completed (was incomplete)" in text
        assert "FRAMECHANGED" in text
        # The periodic re-assert watches the FULL family (a bare WS_CAPTION
        # strip is the regression) plus WS_POPUP re-growth.
        assert "captionMask = WS_CAPTION | WS_SYSMENU" in text
        assert "((s & captionMask) != captionMask" in text
        assert "(s & WS_POPUP) != 0" in text
        # 跨进程子类化路线已废弃：不得回流（真机 SetWindowLongPtr(GWL_
        # WNDPROC) = ERROR_ACCESS_DENIED，Vista 起禁用）。
        assert 'EntryPoint = "SetWindowLongPtrW"' not in text
        assert "CallWindowProc" not in text
        assert "WM_NCCALCSIZE" in text   # 保留在注释里解释根因
        assert "ERROR_ACCESS_DENIED" in text
        # DWM material: Win11 22H2+ Mica with the HRESULT fallback chain
        # (caption color #F3F3F3, then DWM default).
        assert "DWMWA_SYSTEMBACKDROP_TYPE" in text
        assert "DWMBT_MAINWINDOW" in text
        assert "DWMWA_CAPTION_COLOR" in text
        assert "0xFFF3F3F3" in text
        # The C1 fake-mica top bar is gone: no self-drawn fill, no overlay
        # title painting, no self-drawn caption-button row.
        assert "FromArgb(230, 243, 243, 243)" not in text
        assert "PaintNativeBar" not in text
        assert "TitleFont" not in text and "TitleText" not in text
        assert "NativeAction" not in text and "NativeHeight" not in text
        # 4th button: anchors left of the system cluster (DWM-visible right
        # edge minus 3 caption buttons), metrics from GetSystemMetrics with
        # a 46x32 logical floor, y centered ~16 logical px into the caption.
        assert "SyncFourthButton" in text
        assert "GetSystemMetrics" in text and "SM_CXSIZE" in text
        assert "LogicalCapButtonW = 46" in text
        assert "LogicalCapCenterY = 16" in text
        assert "EXTENDED_FRAME_BOUNDS" in text
        # The click reuses the existing emulated (aspect-preserving) maximize.
        assert "owner.TopAction(1)" in text
        # Corner policy (2026-09-09 matrix): DONOTROUND is gated on the
        # G2 region ONLY - Windows' native rounding (DWMWCP_ROUND) owns
        # every bar-mode combination; a native top is a real caption that
        # rounds exactly like any system window, and the native chin's
        # corner ears patch the bottom seam in every native-bottom combo.
        assert "int round = (_cornerDip > 0)" in text
        assert "(TopNative && BottomNative)" not in text
        assert "_cornerDip > 0 || BottomNative" not in text
        assert "DWMWCP_ROUND" in text
        # Hidden move band / 4th-button cluster must not fight the real
        # caption or its buttons.
        assert "band's duties" in text
        assert "CapButtonWidth" in text


def test_corner_round_matrix_and_chin_corner_ears():
        """圆角矩阵（2026-09-09 组合定稿：Windows 自带圆角无处不在）：
        DWM 圆角策略只看 G2 区域，不再看上巴模式——任何窗口栏组合都保持
        DWMWCP_ROUND（Windows 11 原生圆角），真 caption 的顶角与系统窗口
        一样圆；下巴 native 时拼缝处的底角缺口由下巴补角耳修补：

            沉浸×沉浸  DWMWCP_ROUND（照旧全圆角）
            沉浸×原生  DWMWCP_ROUND + 下巴补角耳（照旧）
            沉浸×无    DWMWCP_ROUND（照旧）
            原生×沉浸  DWMWCP_ROUND（真 caption 自带圆角，照旧）
            原生×原生  DWMWCP_ROUND + 下巴补角耳（本次修复：旧版
                       DONOTROUND 把真 caption 的顶角也平方了，丢失
                       Windows 原生圆角；耳资格旧被 !TopNative 锁死）
            原生×无    DWMWCP_ROUND（照旧）

        只有 G2 区域（corner_mode="g2" 实验）平方视频窗。

        耳：DWM 圆角四角一刀切，下巴原生时视频窗底部两角会在拼缝处露
        缺角（当初 DONOTROUND 的动机）——ChinWindow 命中/绘制 Region 在顶
        部两角各加 8×8 DIP 方形耳，向上延伸补在视频窗圆角缺口的正后方
        （耳的 z 在视频窗上方，现有 z 序即如此），亚克力绘制区域同步含
        耳（Region+GraphicsPath 都含）。"""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # 1) The matrix: DONOTROUND requires a G2 region - and NOTHING
        #    else. Every bar-mode combination (native top included) keeps
        #    DWMWCP_ROUND: Windows' own rounding is always in play.
        assert "int round = (_cornerDip > 0)" in text
        assert "(TopNative && BottomNative)" not in text
        assert "_cornerDip > 0 || BottomNative" not in text
        assert text.count("DWMWCP_ROUND") >= 3      # policy + masks comment
        # Ear eligibility mirrors the policy: no G2 region -> DWM rounds
        # the video window -> ears eligible, under BOTH top modes.
        assert "get { return _cornerDip <= 0; }" in text
        assert "!TopNative && _cornerDip <= 0" not in text
        # 2) The ears: 8 DIP, two squares at the top corners, part of the
        #    clip Region (paint + hit) AND the acrylic draw area.
        assert "LogicalEar = 8" in text
        assert "SetEars" in text and "VideoRounded && !inset" in text
        assert "region.Union(new Rectangle(0, 0, _ear, _ear))" in text
        assert "region.Union(new Rectangle(Width - _ear, 0, _ear, _ear))" in text
        # Render clips through the overridable ClipRegion (body path + ears).
        assert "using (Region region = ClipRegion())" in text
        assert "protected virtual Region ClipRegion()" in text
        # The bar body sits BELOW the ear strip in the clip (translated,
        # not stretched): RoundedPath(Width, Height - _ear, ...) + shift.
        assert "Width, Height - _ear, _radiusTop, _radiusBottom" in text
        # The window grows 8 DIP upward when ears turn on; the bar height
        # itself is ear-independent bookkeeping (BarHeight).
        assert "Size = new Size(Width, _ear + h)" in text
        assert "BarHeight" in text
        # Hairlines ride the seam (bar top edge), not the ear-topped window.
        assert "g.FillRectangle(hair, 0, _ear, Width, 1)" in text
        # Geometry anchors use the BAR height, never the ear-inflated
        # window Height (taskbar guard, side bands); the below-video glue
        # shifts up by exactly the ear: client.Bottom - _chin.Ear.
        assert "client.Bottom + _chin.BarHeight > work.Bottom" in text
        assert "client.Bottom - _chin.Ear" in text
        # Inset mode (taskbar guard) rides mid-video: no seam, no ears.
        assert "SetEars(VideoRounded && !inset)" in text


def test_immersive_capsule_acrylic_full_band_and_hold_move():
        """用户反馈三件套 (immersive path only - the native C2 caption is
        untouched):

        1. the hover capsule gets REAL sampled acrylic: video content
           behind the pill, 1/8 down/up blur, saturation x1.15, dark tint
           rgba(28,28,30,~0.55) + 1px top inner light edge 0.10, painted
           into the capsule GraphicsPath base plate (no more dry glass);
        2. the caption move band spans the FULL top band (the old
           central-half split is gone) - press+drag anywhere moves;
        3. press-and-hold 250ms on the band (non-button area) enters
           move-follow via the EXISTING move engine (BeginMoveAt).
        """
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # 1) capsule acrylic base plate: sampled backdrop -> 1/8 blur ->
        #    saturation -> dark tint, clipped to the capsule GraphicsPath.
        assert "DrawCapsuleAcrylic" in text
        assert "FromArgb(140, 28, 28, 30)" in text      # rgba(28,28,30,~0.55)
        assert "FromArgb(26, 255, 255, 255)" in text    # top 1px edge 0.10
        assert "SetClip" in text                        # pill-shaped clip
        assert "sat = 1.15f" in text and "ColorMatrix" in text
        # sampled on reveal + ~300ms cadence refresh, never per frame.
        assert "SampleTop" in text
        assert "CapsuleSampleMs = 300" in text
        assert "SampleTop(true)" in text                # reveal-time capture
        # the dry smoked glass base is retired.
        assert "FromArgb(180, 10, 10, 12)" not in text
        assert "FromArgb(180, 28, 28, 30)" in text      # pre-sample fallback
        # 2) full-band move: the central-half zoning judgment is gone, the
        #    band spans the whole width between the corner resize zones.
        assert "CENTRAL HALF" not in text
        assert "bandW = Math.Max(0, wr.Width / 2)" not in text
        assert "bandW = Math.Max(0, wr.Width - 2 * corner)" in text
        # 3) hold-to-move: a 250ms timer armed on band press; on tick, if
        #    still held, the EXISTING move engine takes over (BeginMoveAt
        #    at the press point + UpdateMove follow); a small horizontal
        #    slip (4 DIP) commits to a move early so quick drags stay
        #    instant.
        assert "HoldMoveMs = 250" in text
        assert "_holdMove" in text
        assert "HoldMoveSlipPx" in text and "return S(4)" in text
        assert "BeginMoveAt(_downScreen)" in text       # engine reuse
        # 4) native C2 path must not regress: the real-caption markers stay
        #    (full detail in test_native_top_is_real_system_caption).
        assert "(style & ~WS_POPUP) | caption" in text
        assert "captionMask = WS_CAPTION | WS_SYSMENU" in text
        assert "DWMWA_SYSTEMBACKDROP_TYPE" in text
        assert "DWMBT_MAINWINDOW" in text


def test_immersive_side_bands_drag_to_move():
        """沉浸模式侧带拖动：窗口左右两侧边带也能拖动移动窗口（此前只有
        顶带可拖）。SideBandWindow 是一对贴视频窗左右缘的隐形热区条
        （GhostBackdrop 零绘制、alpha=1 纯热区），复用顶带（caption
        band）的移动引擎 Ctrl.BeginMove/UpdateMove/EndMove，不做新的拖动
        路径。仅 immersive 上巴模式创建（--chrome-top=native 时真系统
        caption 自己管拖动，不建侧带）；下巴模式不影响侧带。"""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # The class exists, derives OverlayWindow, and is a pure ghost hot
        # zone (zero surface, alpha=1 keeps the layered window clickable).
        assert "SideBandWindow : OverlayWindow" in text
        assert "LogicalWidth = 8" in text            # 6-8 DIP hot-zone width
        assert "Color.FromArgb(1, 0, 0, 0)" in text  # ghost fill, hit-testable
        assert "Cursors.SizeAll" in text             # move affordance cursor
        # Move engine reuse: the caption band's BeginMove/UpdateMove/EndMove
        # drive the side drags - no parallel drag path.
        assert "Ctrl.BeginMove()" in text
        assert "Ctrl.UpdateMove()" in text
        assert "Ctrl.EndMove()" in text
        # Immersive-only: the pair is gated at BIRTH by the top mode (never
        # created under a native caption); the bottom/chin mode is no gate.
        assert "_sides = TopNative ? null" in text
        assert "BottomNative ? null" not in text
        # Geometry: inside the ~6 DIP edge-resize strips (the outermost
        # sliver still resizes), strictly below the caption band + capsule
        # berth and above the chin reservation - no overlap with any other
        # affordance, clicks route positionally. The reservation exists
        # only for the IMMERSIVE chin (the one bar that floats over the
        # client bottom); native glues below the window, none never
        # shows - both run the bands the full side (2026-09-09 matrix fix).
        assert "wr.Left + edge, top, w, h" in text
        assert "wr.Right - edge - w, top, w, h" in text
        assert "int reserve = BottomNone || BottomNative ? 0 : _chin.BarHeight;" in text
        assert "client.Bottom - reserve - top" in text
        # Lifecycle: synced with the strips/chin (tick + LOCATIONCHANGE
        # hook), hidden with the other hot zones, disposed with the
        # controller.
        assert "SyncSideBands" in text
        assert "SyncSideBands();" in text             # hook-side live follow
        assert "HideSideBands()" in text
        assert "foreach (SideBandWindow band in _sides) band.Dispose()" in text


def test_overlay_z_sandwich_not_topmost():
        """层级修复（用户报告：overlay 层级与视频窗脱节）：上下巴/顶胶囊/侧带
        不再是 always-on-top 孤儿窗——置顶窗口（用户终端）盖住视频窗时它们仍
        浮在最上层“孤零零”。现在所有 overlay 直插视频窗上方、同 z 层，
        三明治作为一个完整窗口展现；视频窗激活/前置时重断言相对顺序，最小化/
        隐藏时 overlay 随之隐藏（原有 disengage 路径）。

        2026-09-09 真机根因（用户报告：沉浸式上下巴“没有相关的内容”、功能
        不生效；上巴系统+下巴沉浸同样“见不到内容”）：SetWindowPos 的
        hWndInsertAfter 语义是“该窗口位于被定位窗口之上”——旧代码传视频窗
        句柄作插入点，实际把每个 overlay 面插到了视频窗【下方】，整层
        chrome 被视频盖死（真机枚举 video rank=31 / 胶囊 rank=54；直接调
        API 复现 form 33→59）。正确插入点 = 视频窗上面那个窗
        （GetWindow(video, GW_HWNDPREV)），被定位窗口落在它与视频窗之间。
        """
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # No topmost styling anywhere: neither the WinForms property nor
        # the raw ex-style bit survives on any overlay surface.
        assert "TopMost = true" not in text
        assert "WS_EX_TOPMOST" not in text
        # Every surface is inserted DIRECTLY ABOVE the video window via
        # the window that precedes it in z-order (GW_HWNDPREV). The old
        # inverted form (insert-after = the video hwnd itself) put the
        # whole chrome layer BELOW the video - the 2026-09-09 root cause.
        assert "IntPtr above = NativeMethods.GetWindow(_hwnd, 3 /*GW_HWNDPREV*/);" in text
        assert "SetWindowPos(f.Handle, above, 0, 0, 0, 0," in text
        assert "SetWindowPos(f.Handle, _hwnd," not in text
        assert "InsertAbove" in text
        assert "InsertAbove(_top)" in text and "InsertAbove(_chin)" in text
        assert "foreach (SideBandWindow band in _sides) InsertAbove(band);" in text
        assert "foreach (EdgeStrip strip in _strips) InsertAbove(strip);" in text
        # Re-assertion cadence: every engaged tick + immediately on every
        # foreground change (activating the video window raises it past
        # its own overlays - EVENT_SYSTEM_FOREGROUND undoes that).
        assert "RestackOverlays" in text
        assert "SetWinEventHook(0x0003, 0x0003" in text
        # Minimized / hidden / dead video windows still tear the chrome
        # down with them (the pre-existing disengage paths, kept as-is).
        assert "IsIconic" in text
        assert "HideStrips" in text and "HideBars" in text


def test_chin_taskbar_guard():
        """C2 bugfix (user report): the native chin used to cross the work
        area onto the taskbar whenever the video window was fullscreen or
        its bottom edge sat at the work-area bottom (emulated maximize).
        The chin now insets onto the video when there is no room below, and
        the 4th button is clamped into the work area so it can never be
        pushed off-screen."""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # One straddle-proof monitor resolver feeds FakeMaximize, the chin
        # guard and the 4th-button clamp (monitor rect for fullscreen
        # detection, work area for the room checks).
        assert "VideoMonitor" in text and "rcMonitor" in text
        assert "SystemInformation.WorkingArea" in text
        # The guard branch: fullscreen (window ≈ monitor rect) OR no room
        # below (video bottom + chin height > work bottom) insets the bar
        # onto the video instead of gluing it below the window. BarHeight,
        # never the window Height: the corner-ear strip is seam filler on
        # the video side, not bar the taskbar guard must reserve for.
        assert "ChinTop" in text
        assert "fullscreen" in text and "noRoom" in text
        assert "client.Bottom + _chin.BarHeight > work.Bottom" in text
        assert "client.Bottom + _chin.Height > work.Bottom" not in text
        assert "client.Bottom - _chin.BarHeight" in text
        assert "client.Bottom - _chin.Ear" in text
        assert "inset ? client.Bottom - _chin.Height : client.Bottom" not in text
        # State changes reposition: the LOCATIONCHANGE hook covers size and
        # state flips too, and the tick re-runs the guard either way.
        assert "LOCATIONCHANGE" in text
        # 4th-button work-area clamp (not clipped off-screen when the
        # window is fullscreen or dragged past a screen edge).
        assert "Math.Max(work.Left, Math.Min(left, work.Right - _capBtnW))" in text
        assert "Math.Max(work.Top, Math.Min(top, work.Bottom - _capBtnH))" in text


def test_reveal_bands_clamped_inside_window():
        """2026-09-09 真机回归（不背单词 cn.com.langeasy.LangEasyLexis，flex
        竖屏 1248x1340，chrome=immersive/immersive）：上下巴“闪现即消失”。

        根因是半平面露出带 + 窗外光标无法保持 engaged：
          - 下巴触发 ``cursor.Y > client.Bottom - S(TriggerTop)`` 没有下
            界（上巴同理没有上界）：瞄准窗口边缘线时箭头热点常骑在窗外
            1-2px（任务栏/桌面上），照样触发露出；
          - engaged 的全部搭救矩形（overBars 的下巴矩形到 client.Bottom、
            strips 到 wr.Bottom）都在窗内，纯 hover 又从不换前台 → 窗外
            光标下一拍就 disengage，HideBars 把两条巴连带全隐。
        真机日志的成对证据：“chin shown → 61ms → bars hidden”
        （10:13:09.597→09.658）与“top shown ×2 之间无 top hidden”
        （10:12:34.554→34.880，静默 HideBars）。

        修复：两条可见性判定都把露出/保持带钳进窗口矩形——上巴
        [wr.Top, client.Top+band)、下巴 (band, wr.Bottom]。窗外不触发
        （稳定不闪）；窗内一触发 overBars 必然保住 engaged（触发带 ⊂
        下巴自己的矩形）。钳到 wr 而非 client：WS_THICKFRAME 的幻影
        resize 边（窗拥有但不可见）也算窗内，贴边悬停更宽容。"""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # Both visibility computes now take the window rect and the tick
        # passes it - the clamp needs the rect the cursor must stay inside.
        assert "ComputeTopVisibility(Rectangle client, Rectangle wr, Point cursor)" in text
        assert "ComputeChinVisibility(Rectangle client, Rectangle wr, Point cursor)" in text
        assert "ComputeTopVisibility(client, wr, cursor)" in text
        assert "ComputeChinVisibility(client, wr, cursor)" in text
        # Top: reveal AND retain require cursor.Y >= wr.Top - nothing above
        # the window's own pixels may pop or hold the capsule.
        assert "cursor.Y >= wr.Top && cursor.Y < client.Top + S(TriggerTop)" in text
        assert "_top.Visible && cursor.Y >= wr.Top" in text
        assert "cursor.Y < client.Top + S(RetainTop)" in text
        # Chin: reveal AND retain require cursor.Y <= wr.Bottom - a cursor on
        # the taskbar below the window must neither pop the bar nor hold it.
        assert "cursor.Y > client.Bottom - S(TriggerTop) && cursor.Y <= wr.Bottom" in text
        assert "_chin.Visible && cursor.Y > client.Bottom - S(RetainTop)" in text
        assert "cursor.Y <= wr.Bottom" in text
        # The unbounded half-plane variants must be gone.
        assert "if (cursor.Y < client.Top + S(TriggerTop)) return true;" not in text
        assert "if (cursor.Y > client.Bottom - S(TriggerTop)) return true;" not in text
        # HideBars now logs whenever ANY bar was visible - the old chin-only
        # condition made top-only disengages silent (top shown twice with no
        # top hidden between them, the misleading signature in the logs).
        assert 'if (_chin.Visible || _top.Visible) Log.Write("bars hidden")' in text


def test_none_bar_mode_gates_visibility_only():
        """2026-09-09 三态窗口栏（immersive|native|none）：none = 该边永不
        建栏。默认下巴 none（scrcpy 右键已是返回，下巴冗余）——上巴沉浸、
        下巴不显示由面板设置默认注入 argv。源码级合同：
          - 模式归一化三成员，未知值回退 immersive（同旧行为）；
          - TopNone/BottomNone 是纯可见性闸门：胶囊/下巴永不露出，但隐形
            拖动与 resize 操作面（侧带、caption 带、edge strips）全部保留
            ——窗口必须始终可拖可改；
          - 胶囊亚克力采样（SampleTop ~300ms 心跳）在 none 顶也停掉。"""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # Three-member normalization with immersive fallback.
        assert "NormalizeBarMode" in text
        assert 'if ("none".Equals(mode)) return "none";' in text
        assert 'return "immersive";' in text
        assert "_topMode = NormalizeBarMode(topMode);" in text
        assert "_bottomMode = NormalizeBarMode(bottomMode);" in text
        # The none gates exist and sit inside the tick's visibility math.
        assert 'public bool TopNone { get { return "none".Equals(_topMode); } }' in text
        assert 'public bool BottomNone { get { return "none".Equals(_bottomMode); } }' in text
        assert "bool showTop = TopNative ? true" in text
        assert ": (_cursorMoved && ComputeTopVisibility(client, wr, cursor)));" in text
        assert "bool showChin = BottomNative ? true" in text
        assert ": (_cursorMoved && ComputeChinVisibility(client, wr, cursor))" in text
        # Capsule sampling stops under a none top (no capsule to feed).
        assert "if (!TopNative && !TopNone) SampleTop(false)" in text
        # The invisible affordances survive none: side bands are still born
        # (gated on TopNative only, NOT on TopNone) and the strip array is
        # untouched by bar modes.
        assert "_sides = TopNative ? null : new SideBandWindow[]" in text
        # Usage banner accepts the third mode.
        assert "[--chrome-top immersive|native|none]" in text
        assert "[--chrome-bottom immersive|native|none]" in text


def test_startup_immersion_requires_first_mouse_move():
        """2026-09-09 用户报告：按投屏时窗口不够沉浸，动一下才沉浸。

        窗口在静止光标下生成（点面板“投屏”按钮后光标恰停在窗口顶/底边
        附近）时，距离触发的胶囊/下巴会在启动瞬间自己弹出（真机日志：
        启动 3.6s 后 “top shown at …”）。修复：必须先有一次真实位移
        （≥ S(2)）才武装 proximity 露出；首个采样只作锚点，之后永久
        生效（悬停不动的正常露出不受影响）。真机验证：静止 4s 无
        “top shown”；移动入带后 “top shown”，诊断行 moved=True
        showTop=True。
        """
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        assert "private bool _cursorMoved;" in text
        assert "private Point _cursorAnchor = new Point(int.MinValue, int.MinValue);" in text
        assert "+ Math.Abs(cursor.Y - _cursorAnchor.Y) >= S(2))" in text
        assert "_cursorMoved && ComputeTopVisibility(client, wr, cursor)" in text
        assert "_cursorMoved && ComputeChinVisibility(client, wr, cursor)" in text


def test_bar_mode_combination_visibility_matrix():
        """2026-09-09 组合矩阵三处修复（上巴 immersive|native|none × 下巴
        immersive|native|none 的九组合逐一推演后发现的真 bug）：

        1. LOCATIONCHANGE 钩子旧版无条件 SyncChin(liveClient, true)——
           上巴 native 拖真标题栏时连发钩子，下巴 none（永不该怎么露）
           被强制亮起、下一拍 tick 又 HideBars：闪烁 + 违反 none；
           下巴 immersive 未露出时同样被点亮再熄灭。可见性只归 tick
           （模式三态 + 近距露出）；钩子只做几何跟随（传 _chin.Visible，
           已可见则跟随，未可见不点亮）。
        2. overBars 旧版统计隐藏巴的残留 Bounds——上巴 none 时胶囊从未
           Show，Bounds 停在屏幕原点 (0,0)+尺寸：幽灵热区独自保活
           engaged、并强制弹出下巴 immersive（光标明明在窗口顶部）。
           现在两条都加 .Visible 门。
        3. 侧带旧版恒预留 _chin.BarHeight——下巴 none（永不出现）/
           native（贴窗口下方，不占窗内）时白白短一截可拖边；现在
           只在 immersive 下巴预留（见侧带专项测试）。"""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # 1) The hook follows geometry only: never force-shows the chin.
        assert "SyncChin(liveClient, _chin.Visible);" in text
        assert "SyncChin(liveClient, true)" not in text
        # The hook guard arms on ANY visible surface (chin OR capsule/4th
        # button) - the immersive capsule alone (bottom none / not yet
        # revealed) also gets live tracking during drags, not just the
        # native-top 4th button (glm-5.3-flash review finding).
        assert "(_chin.Visible || _top.Visible)" in text
        assert "(TopNative && _top.Visible)" not in text
        # TopNative's 4th-button anchor inside SyncChin runs regardless of
        # the show value (the hook stays useful for native-top drags).
        assert "_top.SyncFourthButton(VisibleBounds(), WorkArea());" in text
        # 2) overBars counts VISIBLE bars only - no phantom rects from
        #    hidden bars' stale Bounds.
        assert "(_chin.Visible && _chin.Bounds.Contains(cursor))" in text
        assert "(_top.Visible && _top.Bounds.Contains(cursor))" in text
        assert (
            "bool overBars = _chin.Bounds.Contains(cursor)"
            " || _top.Bounds.Contains(cursor);" not in text
        )
        # Same .Visible gate on the hot zones: hidden strips' stale rects
        # no longer keep `engaged` alive on their own.
        assert "if (strip.Visible && strip.Bounds.Contains(cursor)) overStrips = true;" in text
        assert "if (band.Visible && band.Bounds.Contains(cursor)) overStrips = true;" in text
        # Chin visibility's !inX branch is symmetrical with the top twin's
        # `_top.Visible &&` prefix (hidden chin's stale rect never retains).
        assert "if (!inX) return _chin.Visible && _chin.Bounds.Contains(cursor);" in text
        # 3) Side bands: capsule berth only exists when a capsule can show
        #    (TopNone parks it), chin reservation only for immersive bottom
        #    (see the side-band test below for the reserve lines).
        assert "int capsuleBerth = TopNone ? 0 : S(TopMargin) + _top.Height;" in text
        # 3) The visibility gates themselves stay three-state correct
        #    (native = always-on while engaged, none = never, immersive =
        #    proximity reveal) - the tick owns every transition.
        assert "bool showTop = TopNative ? true" in text
        assert ": (_cursorMoved && ComputeTopVisibility(client, wr, cursor)));" in text
        assert "bool showChin = BottomNative ? true" in text
        assert ": (_cursorMoved && ComputeChinVisibility(client, wr, cursor))" in text


def test_flex_pin_adopts_external_placements():
        """2026-09-09 Win 快捷键回归（用户报告：Win+左右 snap，窗口过去又
        闪回；真机复现日志 "flex pin restored"——键盘发起的摆放没有左键、
        没有 _moving/_resizing，旧 EnforceFlexPin 把它们全当成 scrcpy 转屏
        自动改窗弹回）。修复后的钉扎语义：

          - 外部摆放（Win+左/右/上/下、Win+Shift+方向键、PowerToys
            FancyZones、第三方窗口管理器）→ 收编为新钉扎（flex pin
            adopted），窗口停在用户摆的位置；
          - 只有【旋转级 Texture】（视频比例 ≠ 客户区比例，HandleLogLine
            的 arm 判定，>5% 相对差）到达后的 2.5s 窗口内才弹回——那才是
            scrcpy 转屏自动改窗，窗口形状是用户财产；
          - 跟随回声 Texture（窗口先动、显示后到，比例≈客户区）不武装
            （真机日志区分 "(rotation-like, pin armed)" vs "(follow echo,
            pin not armed)"）；
          - 旧豁免全保留：启动 4s 宽限、拖拽中、左键按住+1.5s、原生
            最大化、伪最大化。

        真机验证（虚拟宿主窗口 + 外部 SetWindowPos 模拟 snap）：
        T1 无 Texture → adopted（窗口停住）；T2 竖屏 Texture → armed →
        restored（弹回）；T3 武装过期 → adopted；T4 比例匹配 Texture →
        not armed → adopted。"""
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # The bounce is armed-only: _videoChangedAt must be fresh.
        assert "if (_videoChangedAt > 0" in text
        assert "&& Environment.TickCount - _videoChangedAt < 2500)" in text
        # Everything else external is ADOPTED as the new pin (snap sticks).
        assert "_pinnedRect = wr;" in text
        assert 'Log.Write("flex pin adopted " + wr.Width + "x" + wr.Height' in text
        assert ' + " (external placement)");' in text
        # The arm judgment lives in the tailer: rotation-like (aspect
        # mismatch > 5%) arms, follow echo (aspect match) does not.
        assert "private double ClientAspectSafe()" in text
        assert "bool rotationLike = clientAspect <= 0.0" in text
        assert "/ Math.Max(videoAspect, clientAspect) > 0.05;" in text
        assert "if (rotationLike) _videoChangedAt = Environment.TickCount;" in text
        assert '" (rotation-like, pin armed)"' in text
        assert '" (follow echo, pin not armed)"' in text
        # Old exemptions survive verbatim.
        assert "if (_moving || _resizing) return;" in text
        assert "if (NativeMethods.IsZoomed(_hwnd)) return;" in text
        assert "_lbtnAt < 1500" in text
        assert "flex pin restored" in text


def test_compile_command_shape():
        """The csc argv targets a windowed exe with WinForms references."""
        argv = compile_command(
                "/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
                "\\\\wsl.localhost\\archlinux\\home\\duo\\chrome_overlay.cs",
                "\\\\wsl.localhost\\archlinux\\home\\.cache\\DuoChromeOverlay.exe",
        )
        assert argv[0].endswith("csc.exe")
        assert "-nologo" in argv
        assert "-target:winexe" in argv
        assert "-optimize+" in argv
        assert any(a.startswith("-out:") for a in argv)
        refs = [a for a in argv if a.startswith("-r:")]
        assert "-r:System.Windows.Forms.dll" in refs
        assert "-r:System.Drawing.dll" in refs
        assert argv[-1].endswith("chrome_overlay.cs")


def test_overlay_command_plain_argv():
        """The overlay argv uses plain --title/--serial/--adb/--home."""
        argv = overlay_command("/x/DuoChromeOverlay.exe", "不背单词", "4444bd6b", "C:\\a.exe", True)
        assert argv[0] == "/x/DuoChromeOverlay.exe"
        assert argv[argv.index("--title") + 1] == "不背单词"
        assert argv[argv.index("--serial") + 1] == "4444bd6b"
        assert argv[argv.index("--adb") + 1] == "C:\\a.exe"
        assert argv[argv.index("--home") + 1] == "1"
        assert "TitleB64" not in " ".join(argv)


def test_overlay_command_carries_display_mode_and_log():
        """Mirror/fixed windows get the mode + live-size channel; fixed gets
        its known initial video size; flex may carry its launch display box
        (in-place follow seed) and omits video flags by default."""
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                display_mode="mirror",
                session_log=r"C:\logs\\1.log",
        )
        assert argv[argv.index("--display-mode") + 1] == "mirror"
        assert argv[argv.index("--session-log") + 1] == r"C:\logs\\1.log"
        assert "--video-w" not in argv
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                display_mode="fixed",
                video_width=1252,
                video_height=2088,
        )
        assert argv[argv.index("--video-w") + 1] == "1252"
        assert argv[argv.index("--video-h") + 1] == "2088"
        argv = overlay_command("/x.exe", "t", "s", "a", False)
        assert argv[argv.index("--display-mode") + 1] == "flex"
        assert "--session-log" not in argv
        # Flex now also accepts its launch display box (in-place follow seed);
        # the default-argument call above still carries no video flags.
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                display_mode="flex",
                video_width=2560,
                video_height=1440,
        )
        assert argv[argv.index("--video-w") + 1] == "2560"
        assert argv[argv.index("--video-h") + 1] == "1440"


def test_overlay_command_corner_radius():
        """G2 corner radius reaches the overlay; zero stays silent."""
        argv = overlay_command("/x.exe", "t", "s", "a", False, corner_radius_dip=48)
        assert argv[argv.index("--corner-radius") + 1] == "48"
        argv = overlay_command("/x.exe", "t", "s", "a", False, corner_radius_dip=0)
        assert "--corner-radius" not in argv


def test_overlay_command_carries_bar_modes():
        """--chrome-top/--chrome-bottom 恒随 argv 下发（纯透传，枚举在
        duo.core.settings 校验）；函数默认参数仍 immersive（实际值恒由
        设置/每应用覆盖注入）。"""
        argv = overlay_command("/x.exe", "t", "s", "a", False)
        assert argv[argv.index("--chrome-top") + 1] == "immersive"
        assert argv[argv.index("--chrome-bottom") + 1] == "immersive"
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                top_bar_mode="native", bottom_bar_mode="native")
        assert argv[argv.index("--chrome-top") + 1] == "native"
        assert argv[argv.index("--chrome-bottom") + 1] == "native"
        # none（2026-09-09 第三态：该边不建栏）同路透传——overlay 侧的
        # none 处理由 chrome_overlay.cs 集成步骤落地，此处只钉 argv 合同
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                top_bar_mode="none", bottom_bar_mode="none")
        assert argv[argv.index("--chrome-top") + 1] == "none"
        assert argv[argv.index("--chrome-bottom") + 1] == "none"


def test_chrome_overlay_injects_bar_modes(monkeypatch):
        """ChromeOverlay 组装处透传两参数到 overlay argv（含 none）。"""
        monkeypatch.setattr(chrome, "ensure_built", lambda: Path("/x/y.exe"))
        overlay = chrome.ChromeOverlay(
                title="t", serial="s", adb_path="adb",
                top_bar_mode="native", bottom_bar_mode="immersive")
        assert overlay.command[overlay.command.index("--chrome-top") + 1] == "native"
        assert overlay.command[overlay.command.index("--chrome-bottom") + 1] == "immersive"
        overlay = chrome.ChromeOverlay(
                title="t", serial="s", adb_path="adb",
                top_bar_mode="immersive", bottom_bar_mode="none")
        assert overlay.command[overlay.command.index("--chrome-bottom") + 1] == "none"


def test_overlay_command_home_off_for_virtual_displays():
        """App windows (virtual displays) disable the long-press-home ring."""
        argv = overlay_command("/x/DuoChromeOverlay.exe", "t", "s", "a", False)
        assert argv[argv.index("--home") + 1] == "0"


def test_borderless_for_native_top_is_decorated():
        """2026-09-09 真机架构定稿：上巴 native 时 scrcpy 不得无边框。

        SDL 对 --window-borderless 的无边框窗自己接管 WM_NCCALCSIZE 并
        答“客户区=整窗”——事后补 WS_CAPTION 永远占不到标题带（用户真机：
        上巴系统“见不到相关的内容”）。因此 native 顶必须让 scrcpy 建
        自己的带框窗口（真机实测 frameH=57 / SM_CYCAPTION=34，真实
        系统标题栏）；沉浸/无 顶仍走无边框 + overlay。
        """
        assert chrome.borderless_for("native") is False
        assert chrome.borderless_for("immersive") is True
        assert chrome.borderless_for("none") is True


def test_cli_borderless_follows_top_mode():
        """__main__ 把 borderless_for 接到 --chrome 上（native 顶例外）。"""
        from duo import __main__ as cli

        src = __import__("pathlib").Path(cli.__file__).read_text(encoding="utf-8")
        assert "from duo.core.chrome import ChromeError, ChromeOverlay, borderless_for" in src
        assert "borderless=args.chrome and borderless_for(" in src


def test_build_is_fresh_matches_stamp(tmp_path):
        """Freshness compares the cached exe sidecar against the source hash."""
        exe = tmp_path / "DuoChromeOverlay.exe"
        stamp = tmp_path / "DuoChromeOverlay.exe.sha256"
        assert not chrome.build_is_fresh(exe, stamp, "abc")
        exe.write_bytes(b"MZ")
        assert not chrome.build_is_fresh(exe, stamp, "abc")
        stamp.write_text("abc\n", encoding="utf-8")
        assert chrome.build_is_fresh(exe, stamp, "abc")
        assert not chrome.build_is_fresh(exe, stamp, "other")


def test_wsl_path_translation(monkeypatch, tmp_path):
        """Absolute paths go through wslpath; failures raise ChromeError."""

        class FakeResult:
                def __init__(self, returncode: int, stdout: str) -> None:
                        self.returncode = returncode
                        self.stdout = stdout

        def fake_run(cmd, **kwargs):
                assert cmd[:2] == ["wslpath", "-w"]
                return FakeResult(0, "\\\\wsl.localhost\\archlinux" + str(cmd[2]) + "\n")

        monkeypatch.setattr(chrome.subprocess, "run", fake_run)
        assert chrome.wsl_to_windows_path(str(tmp_path)) == (
                "\\\\wsl.localhost\\archlinux" + str(tmp_path)
        )
        assert chrome.wsl_to_windows_path("C:\\bin\\adb.exe") == "C:\\bin\\adb.exe"
        monkeypatch.setattr(
                chrome.subprocess, "run", lambda cmd, **kwargs: FakeResult(1, "")
        )
        with pytest.raises(ChromeError):
                chrome.wsl_to_windows_path(str(tmp_path))


def test_ensure_built_missing_source_raises(monkeypatch, tmp_path):
        """A missing overlay source is a build error."""
        monkeypatch.setattr(chrome, "OVERLAY_SOURCE", tmp_path / "gone.cs")
        with pytest.raises(ChromeError):
                chrome.ensure_built()


def test_start_writes_diagnostic_banner(monkeypatch, tmp_path):
        """2026-09-09 可诊断性：overlay 启动即把源码指纹 + 模式 + argv 写进
        duo 自己的日志目录（chrome-latest.log）——不依赖 overlay 进程的
        %TEMP% 可写性。用户真机测试失败时曾零证据可查（%TEMP% 不可写让
        overlay 自己的诊断日志全部静默丢失）。"""
        import duo.core.chrome as chrome_mod

        monkeypatch.setattr(chrome_mod, "ensure_built", lambda: Path("/x/y.exe"))
        monkeypatch.setattr(chrome_mod, "logs_dir", lambda: tmp_path)
        spawned: dict[str, object] = {}

        class FakeProc:
                def __init__(self, *args: object, **kwargs: object) -> None:
                        spawned["argv"] = kwargs.get("args") or args

                def poll(self) -> int | None:
                        return 0   # already exited: stop() becomes a no-op

        monkeypatch.setattr(chrome_mod.subprocess, "Popen", lambda *a, **k: FakeProc(*a, **k))
        overlay = chrome_mod.ChromeOverlay(
                title="不背单词", serial="s", adb_path="adb",
                top_bar_mode="native", bottom_bar_mode="immersive")
        log = overlay.start()
        text = log.read_text(encoding="utf-8")
        assert "source=" + chrome_mod.source_stamp()[:12] in text
        assert "top=native bottom=immersive" in text
        assert "--title" in text and "不背单词" in text
        overlay.stop()


def test_stop_before_start_is_noop(monkeypatch):
        """Stopping an overlay that never started must not raise."""
        monkeypatch.setattr(chrome, "ensure_built", lambda: Path("/x/y.exe"))
        overlay = chrome.ChromeOverlay(title="t", serial="s", adb_path="adb")
        assert not overlay.running
        overlay.stop()
        assert not overlay.running


def test_chrome_overlay_compiles_with_real_csc():
        """csc 实编译守门（2026-09-08 全串流失败事故：private 字段访问只被
        括号配平检查放过，csc CS0122 让所有 --chrome 会话启动即死）。

        有 /mnt/c .NET csc（WSL 互操作）时真编译；没有则跳过——Windows
        CI/开发机必过。
        """
        import subprocess

        candidates = [
                "/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
                "/mnt/c/Windows/Microsoft.NET/Framework/v4.0.30319/csc.exe",
        ]
        csc = next((p for p in candidates if Path(p).exists()), None)
        if csc is None:
                pytest.skip("no .NET Framework csc reachable from this host")

        # csc.exe 是 Windows 进程，读不了 /home 下的 POSIX 路径——拷到
        # Windows 侧再以 Windows 路径编译（CS2001 = 路径不可达的典型
        # 症状）。工作目录放 duo 自己的 Windows 数据目录
        # （~/.local/share/duo 的 C:\ 倒影，2026-09-09 用户决策“还是
        # 放在 duo 之中”：旧路径 /mnt/c/duo/... 在 C:\ 盘根反复复活
        # C:\duo 文件夹，用户删掉后测试又重建，被当作“乱扔垃圾”）。
        # Windows 用户名与 WSL 用户名无关（此机：WSL luyu / Windows
        # Administrator）——用已有 overlay 缓存的那个用户目录定位数据
        # 目录；找不到可写目录则跳过（CI 无 /mnt/c 时本来就走 skip）。
        workdir = None
        win_scratch = None
        for profile in sorted(Path("/mnt/c/Users").glob("*/.local/share/duo")):
                candidate = profile / "compile-test"
                try:
                        candidate.mkdir(parents=True, exist_ok=True)
                        probe = candidate / ".w"
                        probe.write_bytes(b"")
                        probe.unlink()
                except OSError:
                        continue
                workdir = candidate
                win_scratch = (
                        f"C:\\Users\\{profile.parents[2].name}"
                        "\\.local\\share\\duo\\compile-test")
                break
        if workdir is None:
                pytest.skip("no writable duo data dir on the Windows side")
        src = workdir / "chrome_overlay.cs"
        src.write_bytes(chrome.OVERLAY_SOURCE.read_bytes())
        out = workdir / "duo_chrome_test.exe"
        proc = subprocess.run(
                [csc, "/nologo", "/t:winexe",
                 "/out:" + win_scratch + "\\duo_chrome_test.exe",
                 "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
                 win_scratch + "\\chrome_overlay.cs"],
                capture_output=True, timeout=120,   # csc 输出 GBK，别按 utf-8 解
        )
        assert proc.returncode == 0, (
                "chrome_overlay.cs 编译失败（csc）",
                (proc.stdout + proc.stderr)[-2000:].decode("gbk", "replace"),
        )
        out.unlink(missing_ok=True)
