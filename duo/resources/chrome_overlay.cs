// Duo chrome overlay - edge controls for a borderless scrcpy window.
//
// Runs on the Windows side, spawned by `duo mirror --chrome`. The scrcpy
// window is borderless; this overlay adds back, on demand:
//
//   top caption band (drag)      -> move the window, native title-bar style:
//                                   the WHOLE band between the corner
//                                   resize zones (below the 6px top
//                                   resize strip) drags the window;
//                                   press-and-hold 250ms enters
//                                   move-follow (drag without
//                                   re-pressing; release ends it)
//   cursor in the top edge band  -> top-right capsule: minimize /
//                                   maximize (taskbar-safe, emulated) /
//                                   close - over a SAMPLED dark-acrylic
//                                   base plate (trio #1), not dry glass
//   always-on (window visible)   -> chin: "<" back (adb keyevent);
//                                   "O" hold: HOME on every display
//                                   (virtual-desktop layer hint; vendors
//                                   may flesh it out - close is on ✕)
//
// Resize policy comes from --display-mode: mirror/fixed windows stay glued
// to the video aspect ratio (live sizes are tailed from the session log,
// where scrcpy emits "INFO: Texture: WxH" on every change, rotation
// included - verified live on scrcpy 4.1); flex windows resize freely and
// the virtual display follows the window.
//
// Native chrome (C2): --chrome-top native gives the video window a REAL
// system caption - WS_CAPTION re-asserted at repair time (same timing and
// technique as the WS_THICKFRAME fix) plus DwmSetWindowAttribute(
// DWMWA_SYSTEMBACKDROP_TYPE, DWMBT_MAINWINDOW): the same Mica that
// PowerShell's and Explorer's title bars are made of. HRESULT failure
// (pre-22H2) falls back to DWMWA_CAPTION_COLOR #F3F3F3, then to the DWM
// default - never a self-drawn bar. The overlay then paints exactly ONE
// thing up there: the 4th caption button (aspect-preserving emulated
// maximize) riding left of the system's own ─ □ ✕ cluster. --chrome-bottom
// native keeps the C1 chin: layered windows cannot host DWM backdrops, so
// its hand-sampled acrylic IS the true acrylic behavior available to them.
//
// None chrome (2026-09-09): --chrome-top/--chrome-bottom none = that edge never grows a visible bar (see docs/window-experience.md §10).
//
// Rendering is per-pixel-alpha layered windows (UpdateLayeredWindow) with
// hand-made acrylic: the content behind each bar is sampled from the target
// window itself (PrintWindow PW_RENDERFULLCONTENT), blurred by down/up
// scaling, then dark-tinted. No OS composition API dependency - the
// SetWindowCompositionAttribute route returns E_FAIL on Win11 24H2.
//
// The window is repaired after discovery: WS_THICKFRAME is re-asserted so
// native edge resize (and Win11 snap) keeps working, and DWMWCP_ROUND is
// declared so the corners follow the Windows 11 rounding convention
// (2026-09-09 组合矩阵定稿：Windows 自带圆角无处不在) - including under
// a native chin, whose corner ears (8 DIP squares lapping over the video
// window's rounded bottom corners) patch the seam so the rounding
// survives the sandwich in EVERY top mode (immersive AND native - the
// real caption's own top corners round exactly like any system window).
// Only the G2 region squares the video window (its own outline + AA
// masks own every corner); no bar-mode combination ever does.
//
// Z-order: NO overlay surface is an always-on-top orphan. Each one is
// inserted DIRECTLY ABOVE the video window (SetWindowPos with the video
// hwnd as hWndInsertAfter, re-asserted on the tick and on every
// foreground change), so the sandwich - video + hot zones + side bands
// + chin + capsule - presents as ONE window: whatever covers the video
// covers the chrome, and the real topmost windows (taskbar) stay above
// the bars where they belong.
//
// Compiled on first use with the .NET Framework csc.exe (C# 5, no Roslyn):
// no string interpolation, no null-conditional operators.
//
// Interop notes (plan.md section 7):
//   - SetProcessDPIAware() before any window: everything is physical pixels.
//   - The window title arrives as real UTF-16 argv (CreateProcessW), so CJK
//     titles need no base64 transport here.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Thread = System.Threading.Thread;
using System.Windows.Forms;

namespace DuoChrome
{
    internal static class NativeMethods
    {
        [StructLayout(LayoutKind.Sequential)]
        public struct RECT { public int Left, Top, Right, Bottom; }

        [StructLayout(LayoutKind.Sequential)]
        public struct POINT { public int X, Y; }

        [StructLayout(LayoutKind.Sequential)]
        public struct MINMAXINFO
        {
            public POINT ptReserved, ptMaxSize, ptMaxPosition, ptMinTrackSize, ptMaxTrackSize;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct MONITORINFO
        {
            public int cbSize;
            public RECT rcMonitor, rcWork;
            public uint dwFlags;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct BLENDFUNCTION
        {
            public byte BlendOp, BlendFlags, SourceConstantAlpha, AlphaFormat;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct SIZE { public int cx, cy; }

        [StructLayout(LayoutKind.Sequential)]
        public struct PT { public int X, Y; }

        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
            public static extern IntPtr FindWindowW(string cls, string title);
        [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
        [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint ga);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
        [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
        [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
        [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr h, ref POINT p);
        [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int vKey);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
        [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
        [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
        // GW_HWNDPREV 取“视频窗上面那个窗”，用作 SetWindowPos 插入点
        // （见 InsertAbove：hWndInsertAfter 在新窗之上，直接传视频窗
        // 会把 chrome 插到视频窗下方）。
        [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
        [DllImport("user32.dll")] public static extern bool SetWindowPos(
            IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr MonitorFromPoint(POINT pt, uint flags);
        [DllImport("user32.dll")] public static extern bool GetMonitorInfoW(IntPtr h, ref MONITORINFO mi);
        [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr h);
        [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr h, IntPtr dc);
        [DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleDC(IntPtr dc);
        [DllImport("gdi32.dll")] public static extern bool DeleteDC(IntPtr dc);
        [DllImport("gdi32.dll")] public static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
        [DllImport("gdi32.dll")] public static extern bool DeleteObject(IntPtr obj);
        [DllImport("gdi32.dll")] public static extern IntPtr CreateRoundRectRgn(
            int x1, int y1, int x2, int y2, int w, int h);
        [DllImport("gdi32.dll")]
            public static extern IntPtr CreatePolygonRgn(PT[] pts, int count, int mode);
        [DllImport("user32.dll")] public static extern int SetWindowRgn(IntPtr h, IntPtr rgn, bool redraw);
        [DllImport("user32.dll")] public static extern bool UpdateLayeredWindow(
            IntPtr h, IntPtr dstDc, IntPtr dstPt, ref SIZE size, IntPtr srcDc,
            ref POINT srcPt, uint crKey, ref BLENDFUNCTION blend, uint flags);
        [DllImport("dwmapi.dll")]
            public static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int val, int size);
        [DllImport("dwmapi.dll")]
            public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT pv, int cb);

        public delegate void WinEventDelegate(IntPtr hHook, uint evt, IntPtr hwnd,
            int idObject, int idChild, uint thread, uint time);

        [DllImport("user32.dll")] public static extern IntPtr SetWinEventHook(
            uint min, uint max, IntPtr mod, WinEventDelegate proc,
            uint pid, uint tid, uint flags);
        [DllImport("user32.dll")] public static extern bool UnhookWinEvent(IntPtr h);
    }

    internal static class Log
    {
        // 2026-09-09 可诊断性加固（用户真机测试无任何 overlay 日志留下：
        // %TEMP% 不可写时旧实现静默丢弃全部日志，排查无从下手）：
        // 先探测 %TEMP%，失败则退回 exe 自身目录（数据目录，恒可写），
        // 再失败才放弃。路径在首次写入时锁定。
        private static readonly string Path_ = InitPath();

        private static string InitPath()
        {
            string name = "duo-chrome-overlay-"
                + Process.GetCurrentProcess().Id + ".log";
            try
            {
                string p = Path.Combine(Path.GetTempPath(), name);
                File.AppendAllText(p, "");
                return p;
            }
            catch { }
            try
            {
                string q = Path.Combine(
                    AppDomain.CurrentDomain.BaseDirectory, name);
                File.AppendAllText(q, "");
                return q;
            }
            catch { }
            return null;
        }

        public static void Write(string msg)
        {
            if (Path_ == null) return;
            try
            {
                File.AppendAllText(Path_,
                    DateTime.Now.ToString("HH:mm:ss.fff") + " " + msg + "\r\n");
            }
            catch { }
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] argv)
        {
            string title = null, serial = null, adb = null;
            string mode = "flex", sessionLog = null;
            string topMode = "immersive", bottomMode = "immersive";
            bool home = false;
            int videoW = 0, videoH = 0, cornerDip = 0;
            for (int i = 0; i + 1 < argv.Length; i += 2)
            {
                if (argv[i] == "--title") title = argv[i + 1];
                else if (argv[i] == "--serial") serial = argv[i + 1];
                else if (argv[i] == "--adb") adb = argv[i + 1];
                else if (argv[i] == "--home") home = argv[i + 1] == "1";
                else if (argv[i] == "--display-mode") mode = argv[i + 1];
                else if (argv[i] == "--video-w") int.TryParse(argv[i + 1], out videoW);
                else if (argv[i] == "--video-h") int.TryParse(argv[i + 1], out videoH);
                else if (argv[i] == "--session-log") sessionLog = argv[i + 1];
                else if (argv[i] == "--corner-radius") int.TryParse(argv[i + 1], out cornerDip);
                else if (argv[i] == "--chrome-top") topMode = argv[i + 1];
                else if (argv[i] == "--chrome-bottom") bottomMode = argv[i + 1];
            }
            if (title == null || serial == null || adb == null)
            {
                Log.Write("usage: --title <t> --serial <s> --adb <path> [--home 0|1] "
                    + "[--display-mode mirror|flex|fixed] [--video-w n] [--video-h n] "
                    + "[--session-log <path>] "
                    + "[--chrome-top immersive|native|none] "
                    + "[--chrome-bottom immersive|native|none]");
                return 2;
            }
            NativeMethods.SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += delegate(object s, System.Threading.ThreadExceptionEventArgs e)
            {
                Log.Write("UI thread crash: " + e.Exception);
            };
            AppDomain.CurrentDomain.UnhandledException += delegate(object s, UnhandledExceptionEventArgs e)
            {
                Log.Write("fatal crash: " + e.ExceptionObject);
            };
            Log.Write("overlay start title=" + title + " serial=" + serial
                + " mode=" + mode + " video=" + videoW + "x" + videoH
                + " chrome=" + topMode + "/" + bottomMode
                + (sessionLog == null ? "" : " log=" + sessionLog));
            using (Controller c = new Controller(
                title, serial, adb, home, mode, videoW, videoH, sessionLog, cornerDip,
                topMode, bottomMode))
            {
                Application.Run();
            }
            Log.Write("overlay exit");
            return 0;
        }
    }

    // -------------------------------------------------------------------------
    // One hover button inside a bar: hit circle + what to draw + what to do.
    // -------------------------------------------------------------------------
    internal sealed class NavButton
    {
        public Rectangle Circle;
        public readonly Action Fire;
        public readonly int Kind;          // 0 = chevron, 1 = ring, 2..4 = win glyphs
        public bool Hover;
        public bool Pressed;

        public NavButton(Rectangle circle, int kind, Action fire)
        {
            Circle = circle; Kind = kind; Fire = fire;
        }

        public bool Hit(Point p)
        {
            int dx = p.X - (Circle.Left + Circle.Width / 2);
            int dy = p.Y - (Circle.Top + Circle.Height / 2);
            int r = Circle.Width / 2;
            return dx * dx + dy * dy <= r * r;
        }
    }

    // -------------------------------------------------------------------------
    // Per-pixel-alpha layered bar. Content painted into a 32bpp bitmap pushed
    // through UpdateLayeredWindow; rounded corners come free (anti-aliased),
    // and the acrylic background is whatever the controller sampled.
    // -------------------------------------------------------------------------
    internal class OverlayWindow : Form
    {
        protected readonly float Dpi;
        // protected（2026-09-08）：子类（Chin 补角耳等）需要读窗口圆角——
        // private 导致 csc CS0122，带 --chrome 的会话全部启动即死（真机全串流失败事故）
        protected readonly int _radiusTop, _radiusBottom;
        protected Bitmap _behind;             // sampled content, may be null
        protected readonly List<NavButton> Buttons = new List<NavButton>();
        protected readonly Controller Ctrl;

        protected OverlayWindow(Controller owner, int radiusTop, int radiusBottom)
        {
            Ctrl = owner;
            Bitmap probe = new Bitmap(1, 1);
            using (Graphics g = Graphics.FromImage(probe)) Dpi = g.DpiX / 96f;
            probe.Dispose();
            _radiusTop = radiusTop; _radiusBottom = radiusBottom;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            // NOT a floating always-on-top window: every overlay surface
            // rides DIRECTLY ABOVE the video window instead - Controller.
            // RestackOverlays inserts it right after the video hwnd, so
            // the sandwich (video + bars + hot zones) rises and sinks as
            // ONE window and never floats above whatever covers the video.
            StartPosition = FormStartPosition.Manual;
            AutoScaleMode = AutoScaleMode.None;
            BackColor = Color.Black;
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= 0x00080000      // WS_EX_LAYERED
                           | 0x08000000      // WS_EX_NOACTIVATE
                           | 0x00000080;     // WS_EX_TOOLWINDOW
                return cp;
            }
        }

        protected override void WndProc(ref Message m)
        {
            const int WM_GETMINMAXINFO = 0x0024;
            if (m.Msg == WM_GETMINMAXINFO)
            {
                // Top-level windows clamp to a minimum width otherwise; the
                // capsule must be allowed to be small.
                NativeMethods.MINMAXINFO mmi =
                    (NativeMethods.MINMAXINFO)Marshal.PtrToStructure(
                        m.LParam, typeof(NativeMethods.MINMAXINFO));
                mmi.ptMinTrackSize.X = 1;
                mmi.ptMinTrackSize.Y = 1;
                Marshal.StructureToPtr(mmi, m.LParam, false);
                return;
            }
            base.WndProc(ref m);
        }

        public void SetSample(Bitmap behind)
        {
            Bitmap old = _behind;
            _behind = behind;
            if (old != null) old.Dispose();
        }

        protected bool GhostBackdrop;   // true = no bar surface, only what PaintBar draws

        public void Render()
        {
            if (Width <= 0 || Height <= 0) return;
            using (Bitmap bmp = new Bitmap(Width, Height, PixelFormat.Format32bppArgb))
            {
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    g.PixelOffsetMode = PixelOffsetMode.Half;
                    if (GhostBackdrop)
                    {
                        // Minimal surface: unpainted pixels stay alpha=0 and
                        // pass clicks through to the mirrored app.
                        PaintBar(g);
                    }
                    else
                    {
                        using (Region region = ClipRegion())
                        {
                            g.SetClip(region, CombineMode.Replace);
                            DrawAcrylic(g);
                            PaintBar(g);
                        }
                    }
                }
                PushLayered(bmp);
            }
        }

        /// <summary>Hand-made acrylic: blur the sampled content (downscale
        /// then upscale), then lay a dark smoked tint over it. The native
        /// chin overrides this with its light-acrylic recipe (agy v6); the
        /// native top needs no override - C2 made it a real system caption
        /// with a DWM backdrop, drawn by the OS itself.</summary>
        protected virtual void DrawAcrylic(Graphics g)
        {
            // Hand-made acrylic: blur the sampled content (downscale then
            // upscale), then lay a dark smoked tint over it.
            if (_behind != null && _behind.Width > 0 && _behind.Height > 0)
            {
                int qw = Math.Max(1, Width / 12);
                int qh = Math.Max(1, Height / 4);
                using (Bitmap small = new Bitmap(qw, qh))
                {
                    using (Graphics sg = Graphics.FromImage(small))
                    {
                        sg.InterpolationMode = InterpolationMode.Low;
                        sg.PixelOffsetMode = PixelOffsetMode.Half;
                        sg.DrawImage(_behind, new Rectangle(0, 0, qw, qh));
                    }
                    g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    g.PixelOffsetMode = PixelOffsetMode.Half;
                    g.DrawImage(small, new Rectangle(0, 0, Width, Height));
                }
            }
            using (SolidBrush tint = new SolidBrush(Color.FromArgb(206, 0x21, 0x21, 0x27)))
                g.FillRectangle(tint, 0, 0, Width, Height);
        }

        protected virtual void PaintBar(Graphics g) { }

        private void PushLayered(Bitmap bmp)
        {
            IntPtr screen = NativeMethods.GetDC(IntPtr.Zero);
            IntPtr mem = NativeMethods.CreateCompatibleDC(screen);
            IntPtr hbm = bmp.GetHbitmap(Color.FromArgb(0));
            IntPtr old = NativeMethods.SelectObject(mem, hbm);
            try
            {
                NativeMethods.SIZE size;
                size.cx = Width; size.cy = Height;
                NativeMethods.POINT src;
                src.X = 0; src.Y = 0;
                NativeMethods.BLENDFUNCTION blend;
                blend.BlendOp = 0; blend.BlendFlags = 0;
                blend.SourceConstantAlpha = 255; blend.AlphaFormat = 1; // AC_SRC_ALPHA
                NativeMethods.UpdateLayeredWindow(
                    Handle, screen, IntPtr.Zero, ref size, mem, ref src, 0, ref blend, 2);
            }
            finally
            {
                NativeMethods.SelectObject(mem, old);
                NativeMethods.DeleteObject(hbm);
                NativeMethods.DeleteDC(mem);
                NativeMethods.ReleaseDC(IntPtr.Zero, screen);
            }
        }

        /// <summary>The bar's paint + hit footprint: the union of shapes
        /// the layered bitmap paints alpha into. Unpainted pixels stay
        /// alpha=0 and pass clicks through, so this Region IS the hit
        /// region too. Default: the rounded bar body alone. The native
        /// chin overrides it to add its corner ears (ChinWindow.ClipRegion
        /// - 8 DIP squares lapping over the video window's rounded-corner
        /// notches at the seam).</summary>
        protected virtual Region ClipRegion()
        {
            using (GraphicsPath clip = RoundedPath(
                Width, Height, _radiusTop, _radiusBottom))
                return new Region(clip);
        }

        protected static GraphicsPath RoundedPath(int w, int h, int rt, int rb)
        {
            GraphicsPath p = new GraphicsPath();
            if (rt <= 0 && rb <= 0)
            {
                p.AddRectangle(new Rectangle(0, 0, w, h));
                return p;
            }
            if (rt <= 0) p.AddLine(0, 0, w, 0);
            else
            {
                p.AddArc(0, 0, 2 * rt, 2 * rt, 180, 90);
                p.AddArc(w - 2 * rt, 0, 2 * rt, 2 * rt, 270, 90);
            }
            if (rb <= 0) p.AddLine(w, h, 0, h);
            else
            {
                p.AddArc(w - 2 * rb, h - 2 * rb, 2 * rb, 2 * rb, 0, 90);
                p.AddArc(0, h - 2 * rb, 2 * rb, 2 * rb, 90, 90);
            }
            p.CloseFigure();
            return p;
        }

        // -- shared drawing helpers ------------------------------------------




        protected void DrawHoverFill(Graphics g, NavButton b)
        {
            if (!b.Hover) return;
            Color fill = b.Kind == 5
                ? Color.FromArgb(255, 232, 17, 35)      // close hover: #E81123
                : Color.FromArgb(28, 255, 255, 255);    // rgba(255,255,255,0.11)
            using (SolidBrush brush = new SolidBrush(fill))
                g.FillEllipse(brush, b.Circle);
        }

        // -- input ------------------------------------------------------------

        /// <summary>Which resize edge a drag on empty bar area should start
        /// (0 = none). Evaluated against the local click point.</summary>
        protected virtual int ResizeEdgeAt(Point p)
        {
            return 0;
        }

        protected virtual void WireInput()
        {
            MouseMove += delegate(object s, MouseEventArgs e)
            {
                if (Ctrl.Resizing || Ctrl.Moving) return;   // polled from Tick
                int hit = HitIndex(e.Location);
                Cursor = hit >= 0 ? Cursors.Hand
                    : (ResizeEdgeAt(e.Location) != 0 ? Cursors.SizeNS : Cursors.Default);
                for (int i = 0; i < Buttons.Count; i++)
                    Buttons[i].Hover = i == hit;
                Render();
            };
            MouseLeave += delegate
            {
                foreach (NavButton b in Buttons) b.Hover = false;
                Render();
            };
            MouseClick += delegate(object s, MouseEventArgs e)
            {
                int hit = HitIndex(e.Location);
                if (hit >= 0) Buttons[hit].Fire();
            };
            // The target window is borderless: SDL swallows native edge
            // hit-testing, so drags on empty bar area resize the target via
            // our own mouse capture + live SetWindowPos (normal feel).
            MouseDown += delegate(object s, MouseEventArgs e)
            {
                if (e.Button != MouseButtons.Left) return;
                if (HitIndex(e.Location) >= 0) return;
                int edge = ResizeEdgeAt(e.Location);
                if (edge != 0)
                {
                    Ctrl.BeginResize(edge);
                    Capture = true;
                }
            };
            MouseUp += delegate(object s, MouseEventArgs e)
            {
                if (e.Button == MouseButtons.Left && Ctrl.Resizing)
                {
                    Ctrl.EndResize();
                    Capture = false;
                }
            };
            // A drag that ends by capture loss (alt-tab, modal steal) never
            // delivers MouseUp - end the gesture here instead of leaving the
            // controller stuck in resizing state.
            MouseCaptureChanged += delegate(object s, EventArgs e)
            {
                if (!Capture)
                {
                    if (Ctrl.Resizing) Ctrl.EndResize();
                    if (Ctrl.Moving) Ctrl.EndMove();
                }
            };
        }

        protected int HitIndex(Point p)
        {
            for (int i = 0; i < Buttons.Count; i++)
                if (Buttons[i].Hit(p)) return i;
            return -1;
        }
    }

    // -------------------------------------------------------------------------
    // The chin: persistent bottom bar with one centered control. Physical
    // mirroring shows the mBack ring (tap = back, long-press = home on the
    // phone's real launcher). Virtual displays (flex/fixed) show the same
    // ring - one glyph, one gesture everywhere: long-press = HOME (the
    // virtual-desktop layer; vendors may flesh it out later). Closing a
    // session lives on the capsule's ✕ only.
    // -------------------------------------------------------------------------
    internal sealed class ChinWindow : OverlayWindow
    {
        public const int LogicalHeight = 44;
        public const int LogicalHeightNative = 32;   // agy v6: native acrylic bar
        public const int LogicalEar = 8;   // corner ear: DWM round radius, DIP
        private const int LogicalButton = 36;   // hit zone (paint is a 4px pill)
        private const int HoldMs = 350;        // agy 2026-09-08: pill fully grown = HOME
        // agy v6 adaptive pill colors (native bar): dark on a bright bar,
        // white on a dark bar - picked from the tinted-bar luminance (0.52 cut).
        private static readonly Color PillDark = Color.FromArgb(102, 29, 29, 31);
        private static readonly Color PillLight = Color.FromArgb(155, 255, 255, 255);

        private readonly bool _native;
        // Video window keeps DWMWCP_ROUND under Repair whenever there is
        // no G2 region (2026-09-09: true under BOTH top modes - the ears
        // patch the bottom-corner seam in every native-chin sandwich):
        // its bottom corners notch at the seam -> ears on.
        private readonly bool _videoRounded;
        private int _ear;                    // corner-ear height, physical px (0 = flush bar)
        private bool _nativeDark = true;       // tinted bar bright -> dark pill
        private readonly Timer _hold;
        private readonly Timer _anim;          // ~60fps re-render while holding
        private readonly Timer _flash;         // 120ms white flash after HOME fires
        private DateTime _pressStart;
        private bool _firedHold;
        private bool _flashing;

        public ChinWindow(Controller owner, bool home, string displayMode, string mode,
            bool videoRounded)
            : base(owner, 0, (int)((NativeMode(mode) ? 8f : 18f) * ScaleOf()))
        {
            // Native mode (agy v6 sandwich): a real, always-visible 32px
            // acrylic bar glued BELOW the video window; the bottom corners
            // carry the sandwich's outer 8px rounding. Immersive keeps the
            // ghost hot-zone - zero change from the status quo.
            _native = NativeMode(mode);
            _videoRounded = videoRounded;
            GhostBackdrop = !_native;
            int btn = (int)(LogicalButton * Dpi);
            int h = BarHeight;
            Size = new Size(600, h);           // width resynced by the controller
            // Glyph follows the DISPLAY TYPE, not the home flag: a flex
            // session without --app runs with home=1 but is still a virtual
            // display with no launcher to go home to (see ChinHold). The
            // single control stays centered either way - only the glyph
            // changes, so no layout or width bookkeeping is needed.
            // mBack homage: one centered ring for every mode. Tap = BACK
            // (AdbKey 4); press-and-hold = Ctrl.ChinHold() (HOME on physical
            // mirroring, session close on virtual displays). The glyph never
            // changes by mode - users learn one shape (2026-09-06: a mode-
            // switching glyph read as a regression; reverted).
            Buttons.Add(new NavButton(
                new Rectangle((600 - btn) / 2, (h - btn) / 2, btn, btn),
                1, delegate { Ctrl.AdbKey(4); }));
            _hold = new Timer { Interval = HoldMs };
            _hold.Tick += delegate
            {
                _hold.Stop();
                _anim.Stop();
                _firedHold = true;
                Log.Write("hold fired home=" + home);
                Ctrl.ChinHold();
                // HOME confirmation: 120ms flash, then settle back to rest
                _flashing = true;
                Render();
                _flash.Start();
            };
            _anim = new Timer { Interval = 16 };
            _anim.Tick += delegate { Render(); };   // width grows while held
            _flash = new Timer { Interval = 120 };
            _flash.Tick += delegate { _flash.Stop(); _flashing = false; Render(); };
            WireInput();
        }

        /// <summary>The BAR's own height in physical px, ears excluded.
        /// Geometry that anchors around the bar (taskbar guard, side
        /// bands) wants this, never the window Height - the ear strip
        /// above the bar is seam filler, not bar.</summary>
        public int BarHeight
        {
            get { return (int)((_native ? LogicalHeightNative : LogicalHeight) * Dpi); }
        }

        /// <summary>Current corner-ear height in physical px (0 = off).</summary>
        public int Ear { get { return _ear; } }

        /// <summary>Corner ears (DWM round seam patch): without a G2
        /// region Repair keeps DWMWCP_ROUND on the video window, so its
        /// corners round - all four, top and bottom. The bottom rounding
        /// notches exactly at the seam against this bar. Ears ON grow the
        /// window 8 DIP upward: two square ears at the top corners lap
        /// OVER the notches (the chin rides above the video window in z
        /// already - RestackOverlays), the bar body stays flush below the
        /// video bottom, and the seam reads continuous. Off in inset mode
        /// (the bar then rides mid-video: no seam to patch) and whenever
        /// the G2 region owns the outline (DWM does not round then).</summary>
        public void SetEars(bool on)
        {
            int want = on && _native && _videoRounded
                ? (int)(LogicalEar * Dpi) : 0;
            if (want == _ear) return;
            _ear = want;
            int btn = (int)(LogicalButton * Dpi);
            int h = BarHeight;
            Size = new Size(Width, _ear + h);
            Rectangle was = Buttons[0].Circle;
            Buttons[0].Circle = new Rectangle(was.X,
                _ear + (h - btn) / 2, was.Width, was.Height);
            Render();
        }

        /// <summary>Bar footprint + the two corner ears: body = rounded
        /// bar translated down by the ear strip; ears = 8x8 DIP squares
        /// at the top-left / top-right, lapping exactly over the video
        /// window's rounded-corner notches behind the seam. The Region is
        /// the paint clip AND the hit surface (alpha=0 elsewhere stays
        /// click-through).</summary>
        protected override Region ClipRegion()
        {
            if (_ear <= 0) return base.ClipRegion();
            Region region;
            using (GraphicsPath body = RoundedPath(
                Width, Height - _ear, _radiusTop, _radiusBottom))
            using (Matrix shift = new Matrix(1, 0, 0, 1, 0, _ear))
            {
                body.Transform(shift);
                region = new Region(body);
            }
            region.Union(new Rectangle(0, 0, _ear, _ear));
            region.Union(new Rectangle(Width - _ear, 0, _ear, _ear));
            return region;
        }

        protected override void WireInput()
        {
            MouseMove += delegate(object s, MouseEventArgs e)
            {
                if (Ctrl.Resizing || Ctrl.Moving) return;
                int hit = HitIndex(e.Location);
                Cursor = hit >= 0 ? Cursors.Hand
                    : (ResizeEdgeAt(e.Location) != 0 ? Cursors.SizeNS : Cursors.Default);
                for (int i = 0; i < Buttons.Count; i++) Buttons[i].Hover = i == hit;
                Render();
            };
            MouseLeave += delegate
            {
                foreach (NavButton b in Buttons) { b.Hover = false; b.Pressed = false; }
                _hold.Stop();
                _anim.Stop();
                Render();
            };
            MouseClick += delegate(object s, MouseEventArgs e)
            {
                if (_firedHold) { _firedHold = false; return; }   // long-press already acted
                if (HitIndex(e.Location) >= 0) Buttons[0].Fire();
            };
            MouseDown += delegate(object s, MouseEventArgs e)
            {
                if (e.Button == MouseButtons.Left && HitIndex(e.Location) >= 0)
                {
                    _firedHold = false;
                    Buttons[0].Pressed = true;
                    Capture = true;   // ensure the matching MouseUp comes home
                    _pressStart = DateTime.UtcNow;
                    _hold.Start();
                    _anim.Start();   // pill grows 28 -> 48 over HoldMs
                    Render();
                    return;
                }
                int edge = ResizeEdgeAt(e.Location);
                if (edge != 0)
                {
                    Ctrl.BeginResize(edge);
                    Capture = true;
                }
            };
            MouseUp += delegate(object s, MouseEventArgs e)
            {
                _hold.Stop();
                _anim.Stop();
                if (Buttons[0].Pressed)
                {
                    Buttons[0].Pressed = false;
                    Capture = false;
                    Render();
                }
                if (e.Button == MouseButtons.Left && (Ctrl.Resizing || Ctrl.Moving))
                {
                    Ctrl.EndResize();
                    Ctrl.EndMove();
                    Capture = false;
                }
            };
            MouseCaptureChanged += delegate(object s, EventArgs e)
            {
                if (!Capture)
                {
                    _hold.Stop();
                    _anim.Stop();
                    if (Ctrl.Resizing) Ctrl.EndResize();
                    if (Ctrl.Moving) Ctrl.EndMove();
                }
            };
        }

        protected override int ResizeEdgeAt(Point p)
        {
            // Dragging the chin resizes the device from its bottom edge; the
            // outer fifths pick the diagonal corners (like a native frame).
            if (p.X < Width * 0.2) return 16;      // HTBOTTOMLEFT
            if (p.X > Width * 0.8) return 17;      // HTBOTTOMRIGHT
            return 15;                            // HTBOTTOM
        }

        private static float ScaleOf()
        {
            Bitmap probe = new Bitmap(1, 1);
            float s;
            using (Graphics g = Graphics.FromImage(probe)) s = g.DpiX / 96f;
            probe.Dispose();
            return s;
        }

        protected override void PaintBar(Graphics g)
        {
            if (_native)
            {
                PaintNativePill(g);
                return;
            }
            // Invisible resize sliver along the very bottom (alpha=1 is enough
            // to stay hit-testable while visually imperceptible).
            using (SolidBrush band = new SolidBrush(Color.FromArgb(1, 0, 0, 0)))
                g.FillRectangle(band, 0, Height - S6(), Width, S6());
            foreach (NavButton b in Buttons)
            {
                float cx = b.Circle.Left + b.Circle.Width / 2f;
                // 2026-09-08: pill hugs the very bottom edge (center 9px up,
                // iOS Home Indicator seating); the invisible 36px hit zone
                // stays centered in the 44px band - it still covers the
                // pill (zone spans y 4..40, pill spans y 7..11).
                float cy = Height - 9f * Dpi;
                // 2026-09-08 agy Opus verdict: AssistiveTouch glass disc is
                // the wrong language for fixed chrome (suspension-ball
                // cheapness); the back affordance is a horizontal PILL
                // (iOS Home Indicator language): 4px tall, white, almost
                // gone until touched. Tap = BACK, hold = the pill grows
                // 28 -> 48 over HoldMs then flashes = HOME.
                float alpha;
                float widthL;
                if (_flashing)
                {
                    alpha = 250f;
                    widthL = 48f;
                }
                else if (b.Pressed)
                {
                    float t = Math.Min(1f, (float)
                        (DateTime.UtcNow - _pressStart).TotalMilliseconds / HoldMs);
                    alpha = 210f - 35f * t;          // 210 -> 175 while growing
                    widthL = 28f + 20f * t;           // 28 -> 48
                }
                else
                {
                    alpha = b.Hover ? 155f : 95f;
                    widthL = 36f;
                }
                float h = 4f * Dpi, w = widthL * Dpi, r = h / 2f;
                using (GraphicsPath pill = new GraphicsPath())
                {
                    float x = cx - w / 2f, y = cy - h / 2f;
                    // pill: two cap circles (diameter = pill height) +
                    // auto-connecting straight edges; each box is h×h
                    pill.AddArc(x, y, h, h, 180, 90);           // left cap, top
                    pill.AddArc(x + w - h, y, h, h, 270, 90);   // right cap, top
                    pill.AddArc(x + w - h, y, h, h, 0, 90);     // right cap, bottom
                    pill.AddArc(x, y, h, h, 90, 90);            // left cap, bottom
                    pill.CloseFigure();
                    using (SolidBrush white = new SolidBrush(Color.FromArgb(
                        (int)alpha, 255, 255, 255)))
                        g.FillPath(white, pill);
                }
            }
        }


        private static bool NativeMode(string m)
        {
            return m != null && m.Equals("native");
        }

        /// <summary>Store a native-mode screen sample and derive the pill
        /// color from it: average luminance over the bar's center 50%
        /// region, tinted with the same rgba(248,248,248,184) wash the paint
        /// applies, compared against the 0.52 cut (agy v6: brighter bar -&gt;
        /// dark pill, darker bar -&gt; white pill). The capture carries an 8px
        /// margin around the bar; only the bar's own rows are measured.</summary>
        public void SetNativeSample(Bitmap behind)
        {
            SetSample(behind);
            if (behind == null || behind.Width < 4 || behind.Height < 4)
            {
                Render();
                return;
            }
            try
            {
                using (Bitmap probe = new Bitmap(32, 4))
                using (Graphics pg = Graphics.FromImage(probe))
                {
                    pg.InterpolationMode = InterpolationMode.Low;
                    int margin = Math.Min(8, behind.Height / 2);
                    int x0 = behind.Width / 4;
                    int w = Math.Max(1, behind.Width / 2);
                    pg.DrawImage(behind, new Rectangle(0, 0, 32, 4),
                        new Rectangle(x0, margin, w,
                            Math.Max(1, behind.Height - 2 * margin)),
                        GraphicsUnit.Pixel);
                    double sum = 0;
                    for (int y = 0; y < 4; y++)
                        for (int x = 0; x < 32; x++)
                        {
                            Color c = probe.GetPixel(x, y);
                            sum += 0.2126 * c.R + 0.7152 * c.G + 0.0722 * c.B;
                        }
                    double lum = sum / (32.0 * 4.0 * 255.0);
                    double a = 184.0 / 255.0;
                    double tinted = a * (248.0 / 255.0) + (1.0 - a) * lum;
                    _nativeDark = tinted > 0.52;
                }
            }
            catch { }
            Render();
        }

        /// <summary>agy v6 native acrylic material: the sampled screen
        /// backdrop (Controller.SampleNativeChin captures the bar rect +
        /// 8px) is blurred by down/up scaling to 1/8 (~20px), saturated
        /// x1.15 via ColorMatrix, then washed with rgba(248,248,248,184);
        /// top hairline rgba(0,0,0,0.08) + inner light edge
        /// rgba(255,255,255,0.45), 1px each.</summary>
        protected override void DrawAcrylic(Graphics g)
        {
            if (!_native)
            {
                base.DrawAcrylic(g);
                return;
            }
            if (_behind != null && _behind.Width > 10 && _behind.Height > 10)
            {
                int qw = Math.Max(1, Width / 8);
                int qh = Math.Max(1, Height / 8);
                using (Bitmap small = new Bitmap(qw, qh))
                {
                    using (Graphics sg = Graphics.FromImage(small))
                    {
                        sg.InterpolationMode = InterpolationMode.Low;
                        sg.PixelOffsetMode = PixelOffsetMode.Half;
                        // the capture carries an 8px margin around the bar:
                        // map only the bar's own footprint onto the blur
                        // source (Render's rounded-rect clip trims the rest).
                        int sx = Math.Min(8, Math.Max(0, _behind.Width - 1));
                        int sy = Math.Min(8, Math.Max(0, _behind.Height - 1));
                        int sw = Math.Min(Width, _behind.Width - sx);
                        int sh = Math.Min(Height, _behind.Height - sy);
                        if (sw > 0 && sh > 0)
                            sg.DrawImage(_behind, new Rectangle(0, 0, qw, qh),
                                new Rectangle(sx, sy, sw, sh), GraphicsUnit.Pixel);
                    }
                    using (ImageAttributes ia = new ImageAttributes())
                    {
                        float sat = 1.15f;
                        ColorMatrix cm = new ColorMatrix();
                        cm.Matrix00 = sat;
                        cm.Matrix11 = sat;
                        cm.Matrix22 = sat;
                        ia.SetColorMatrix(cm);
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.PixelOffsetMode = PixelOffsetMode.Half;
                        g.DrawImage(small, new Rectangle(0, 0, Width, Height),
                            0, 0, qw, qh, GraphicsUnit.Pixel, ia);
                    }
                }
            }
            using (SolidBrush tint = new SolidBrush(Color.FromArgb(184, 248, 248, 248)))
                g.FillRectangle(tint, 0, 0, Width, Height);
            // Hairlines ride the BAR's top edge (= the seam), not the
            // window's: with corner ears the window top is 8 DIP above
            // the seam, inside the ear squares.
            using (SolidBrush hair = new SolidBrush(Color.FromArgb(20, 0, 0, 0)))
                g.FillRectangle(hair, 0, _ear, Width, 1);
            using (SolidBrush lum = new SolidBrush(Color.FromArgb(115, 255, 255, 255)))
                g.FillRectangle(lum, 0, _ear + 1, Width, 1);
        }

        /// <summary>agy v6 native chin pill. The acrylic surface comes from
        /// DrawAcrylic; this paints the ADAPTIVE pill: centered, bottom
        /// margin 14px, dark-on-light / white-on-dark by tinted-bar
        /// luminance. The press/hold/flash state machine is the immersive
        /// one - only the color source is dual-mode (rest 0.40/0.61, hover
        /// 0.65/0.80, pressed 28px, hold grows to 48px, flash white).</summary>
        private void PaintNativePill(Graphics g)
        {
            foreach (NavButton b in Buttons)
            {
                float cx = b.Circle.Left + b.Circle.Width / 2f;
                // pill floats 14px above the bar bottom; the bar is 32px, so
                // the pill center lands on the bar's vertical center.
                float cy = Height - 14f * Dpi - 2f * Dpi;
                Color rgb = _nativeDark ? PillDark : PillLight;
                float alpha;
                float widthL;
                if (_flashing)
                {
                    alpha = 250f;
                    widthL = 48f;
                }
                else if (b.Pressed)
                {
                    float t = Math.Min(1f, (float)
                        (DateTime.UtcNow - _pressStart).TotalMilliseconds / HoldMs);
                    alpha = _nativeDark ? 170f - 30f * t : 215f - 30f * t;
                    widthL = 28f + 20f * t;           // 28 -> 48 (HOME countdown)
                }
                else
                {
                    alpha = _nativeDark ? (b.Hover ? 166f : 102f)
                                        : (b.Hover ? 204f : 155f);
                    widthL = 36f;
                }
                float h = 4f * Dpi, w = widthL * Dpi, r = h / 2f;
                Color fill = _flashing
                    ? Color.FromArgb(250, 255, 255, 255)
                    : Color.FromArgb((int)alpha, rgb.R, rgb.G, rgb.B);
                using (GraphicsPath pill = new GraphicsPath())
                {
                    float x = cx - w / 2f, y = cy - h / 2f;
                    pill.AddArc(x, y, h, h, 180, 90);           // left cap, top
                    pill.AddArc(x + w - h, y, h, h, 270, 90);   // right cap, top
                    pill.AddArc(x + w - h, y, h, h, 0, 90);     // right cap, bottom
                    pill.AddArc(x, y, h, h, 90, 90);            // left cap, bottom
                    pill.CloseFigure();
                    using (SolidBrush brush = new SolidBrush(fill))
                        g.FillPath(brush, pill);
                }
            }
        }

        private int S6()
        {
            return (int)(6 * Dpi);
        }

        public void ResyncWidth(int width)
        {
            if (width == Width) return;
            int btn = (int)(LogicalButton * Dpi);
            Size = new Size(width, Height);
            // keep the single mBack ring centered
            Rectangle was = Buttons[0].Circle;
            Buttons[0].Circle = new Rectangle(
                (width - btn) / 2, was.Y, btn, btn);
            // On-demand render: the bitmap is re-pushed only when the width
            // truly changed (moves alone just reposition the layered surface).
            Render();
        }
    }

    // -------------------------------------------------------------------------
    // The top-right capsule (immersive): minimize / maximize-restore / close
    // (Fluent glyphs). Native top mode paints NO bar - the video window gets
    // a real system caption (WS_CAPTION + DWM Mica, applied in Repair) and
    // this window shrinks to the single 4th caption button riding left of
    // the system ─ □ ✕ cluster: aspect-preserving (emulated) maximize.
    // -------------------------------------------------------------------------
    internal sealed class TopWindow : OverlayWindow
    {
        public const int LogicalButton = 30;
        private const int LogicalPad = 5;
        private const int LogicalGap = 6;
        // C2 4th-button geometry: system caption metrics floor + the
        // caption band's vertical center, all logical (x Dpi).
        private const int LogicalCapButtonW = 46;
        private const int LogicalCapButtonH = 32;
        private const int LogicalCapCenterY = 16;

        private static Font _glyphFont;
        private readonly string[] _glyphs;
        private readonly bool _native;
        private readonly char _maxBase;             // slot-1 base glyph
        private readonly int _maxAction;            // TopAction id of slot 1
        private int _capBtnW, _capBtnH;             // caption metrics, physical

        public TopWindow(Controller owner, bool fillButton, string mode)
            : base(owner, 0, 0)   // ghost surfaces: no rounded-bar clip
        {
            _native = NativeMode(mode);
            float s = ScaleOf();
            int btn = (int)(LogicalButton * s);
            int gap = (int)(LogicalGap * s);
            if (_native)
            {
                // C2: the fake 32px mica bar is DEAD - Repair() gives the
                // video window a genuine WS_CAPTION (a real system
                // title bar, titled with the window title scrcpy already
                // set) plus the DWM Mica backdrop, so the system itself
                // draws caption, title, ─ □ ✕ and the rounded corners.
                // This overlay window shrinks to the ONE thing the system
                // cannot offer: the 4th caption button (aspect-preserving
                // emulated maximize). Alpha is 0 outside the button, so the
                // real caption underneath keeps every pixel of its own
                // drag / button hit-testing.
                GhostBackdrop = true;
                InitCaptionMetrics();
                Size = new Size(_capBtnW, _capBtnH);
                _maxBase = (char)0xE740;            // aspect-fit arrows (⤢)
                _maxAction = 1;
                _glyphs = new string[1];
                _glyphs[0] = _maxBase.ToString();
                Buttons.Add(new NavButton(
                    new Rectangle(0, 0, _capBtnW, _capBtnH), 2,
                    delegate { owner.TopAction(1); }));   // emulated maximize
            }
            else
            {
                GhostBackdrop = true;
                int pad = (int)(LogicalPad * s);
                // mirror/fixed windows must never be stretched off-ratio, so the
                // "fill work area" button exists only in flex mode: min / fit / close.
                int n = fillButton ? 4 : 3;
                int w = 2 * pad + n * btn + (n - 1) * gap;
                int h = 2 * pad + btn;
                Size = new Size(w, h);
                _maxBase = (char)0xE740;            // ChromeFullScreen -> aspect fit
                _maxAction = 1;
                _glyphs = new string[n];
                _glyphs[0] = ((char)0xE921).ToString();   // ChromeMinimize
                _glyphs[1] = ((char)0xE740).ToString();   // ChromeFullScreen -> aspect fit
                if (fillButton)
                    _glyphs[2] = ((char)0xE922).ToString();   // ChromeMaximize -> fill work area
                _glyphs[n - 1] = ((char)0xE8BB).ToString();   // ChromeClose
                for (int i = 0; i < n; i++)
                {
                    int slot = i;
                    Buttons.Add(new NavButton(
                        new Rectangle(pad + i * (btn + gap), pad, btn, btn), 2 + i,
                        delegate { owner.TopAction(ActionFor(fillButton, slot)); }));
                }
            }
            WireInput();
        }

        private static bool NativeMode(string m)
        {
            return m != null && m.Equals("native");
        }

        /// <summary>System caption-button metrics, physical px. The 4th
        /// button anchors against "visible right edge - 3 x width", so the
        /// estimate must never UNDERSHOOT the real DWM buttons: an overlap
        /// would block the system ✕ hit area (this layered dot sits
        /// directly above the video window's own z slot and wins the
        /// z-order). GetSystemMetrics(SM_CXSIZE / SM_CYSIZE) is
        /// the legacy caption size; the 46x32 logical floor covers the
        /// DWM-drawn Win10/11 buttons at every scale, and max() keeps us
        /// conservative whichever source lies.</summary>
        private void InitCaptionMetrics()
        {
            int mw = NativeMethods.GetSystemMetrics(32 /*SM_CXSIZE*/);
            int mh = NativeMethods.GetSystemMetrics(33 /*SM_CYSIZE*/);
            _capBtnW = Math.Max(mw, (int)Math.Round(LogicalCapButtonW * Dpi));
            _capBtnH = Math.Max(mh, (int)Math.Round(LogicalCapButtonH * Dpi));
        }

        /// <summary>Map a visual slot to a TopAction id. With the fill
        /// button the layout is 1:1 (0 min, 1 fit, 2 fill, 3 close); without
        /// it the third slot becomes close.</summary>
        private static int ActionFor(bool fill, int slot)
        {
            if (fill) return slot;
            return slot < 2 ? slot : 3;
        }

        protected override int ResizeEdgeAt(Point p)
        {
            // Native mode: this window is a single button over the real
            // caption - never a resize edge (the system frame resizes).
            if (_native) return 0;
            // Dragging the top capsule resizes from the top edge.
            if (p.X < Width * 0.2) return 13;      // HTTOPLEFT
            if (p.X > Width * 0.8) return 14;      // HTTOPRIGHT
            return 12;                            // HTTOP
        }

        private static float ScaleOf()
        {
            Bitmap probe = new Bitmap(1, 1);
            float s;
            using (Graphics g = Graphics.FromImage(probe)) s = g.DpiX / 96f;
            probe.Dispose();
            return s;
        }

        internal static Font GlyphFont(float scale)
        {
            if (_glyphFont != null) return _glyphFont;
            string family = "Segoe Fluent Icons";
            try
            {
                using (Font probe = new Font(family, 9f))
                    if (probe.Name != family) family = "Segoe MDL2 Assets";
            }
            catch { family = "Segoe MDL2 Assets"; }
            _glyphFont = new Font(family, 12f * scale, FontStyle.Regular, GraphicsUnit.Pixel);
            return _glyphFont;
        }

        /// <summary>Swap the glyph of the active maximize button to the
        /// restore glyph (two overlapping squares) so the user sees which
        /// mode is on; the other button keeps its base glyph. mode: 0 none,
        /// 1 aspect fit, 2 full fill.</summary>
        public void SetMaximized(int mode)
        {
            if (_native)
            {
                // 4th button swaps to the restore glyph while the emulated
                // aspect maximize is active.
                _glyphs[0] = ((char)(mode == 1 ? 0xE923 : _maxBase)).ToString();
                return;
            }
            if (_glyphs.Length > 1)
                _glyphs[1] = ((char)(mode == _maxAction ? 0xE923 : _maxBase)).ToString();
            if (_glyphs.Length > 3)
                _glyphs[2] = ((char)(mode == 2 ? 0xE923 : 0xE922)).ToString();
        }

        protected override void PaintBar(Graphics g)
        {
            if (_native)
            {
                PaintFourthButton(g);
                return;
            }
            // Capsule base plate (user-feedback trio #1): the rounded pill
            // GraphicsPath is filled with REAL sampled acrylic - the video
            // content behind the capsule, blurred by 1/8 down/up scaling
            // (~20px), saturated x1.15 and smoked with the Win11 dark-menu
            // tint. The three glass dots (glyphs / hover / red close) paint
            // on top, unchanged.
            float rad = Height / 2f;
            using (GraphicsPath capsule = RoundedPath(Width, Height, (int)rad, (int)rad))
            {
                DrawCapsuleAcrylic(g, capsule);
                using (Pen rim = new Pen(Color.FromArgb(70, 255, 255, 255), 1f))
                    g.DrawPath(rim, capsule);
            }
            Font font = GlyphFont(Dpi);
            foreach (NavButton b in Buttons)
            {
                DrawHoverFill(g, b);
                float opacity = b.Hover ? 1.0f : 0.78f;
                Color color = Color.FromArgb((int)(255 * opacity), 255, 255, 255);
                TextRenderer.DrawText(g, _glyphs[b.Kind - 2], font, b.Circle, color,
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.NoPrefix);
            }
        }

        /// <summary>User-feedback trio #1: real acrylic for the hover
        /// capsule. The sampled video content behind the capsule (_behind,
        /// fed by Controller.SampleTop on reveal + a ~300ms refresh) is
        /// blurred by 1/8 down/up scaling (~20px), saturated x1.15 via
        /// ColorMatrix, then smoked with the dark acrylic tint
        /// rgba(28,28,30,~0.55) - the Win11 dark-menu material; a 1px
        /// inner light edge rgba(255,255,255,0.10) sits along the top.
        /// Everything is clipped to the capsule GraphicsPath (the base
        /// plate keeps the pill shape; alpha stays 0 outside it, so the
        /// ghost hit-testing language is untouched). Without a sample yet
        /// (the single frame before the reveal-time capture lands) a dry
        /// dark base of the same hue shows - never an empty capsule.</summary>
        private void DrawCapsuleAcrylic(Graphics g, GraphicsPath capsule)
        {
            GraphicsState state = g.Save();
            g.SetClip(capsule);
            if (_behind != null && _behind.Width > 0 && _behind.Height > 0)
            {
                int qw = Math.Max(1, Width / 8);
                int qh = Math.Max(1, Height / 8);
                using (Bitmap small = new Bitmap(qw, qh))
                {
                    using (Graphics sg = Graphics.FromImage(small))
                    {
                        sg.InterpolationMode = InterpolationMode.Low;
                        sg.PixelOffsetMode = PixelOffsetMode.Half;
                        sg.DrawImage(_behind, new Rectangle(0, 0, qw, qh));
                    }
                    using (ImageAttributes ia = new ImageAttributes())
                    {
                        float sat = 1.15f;
                        ColorMatrix cm = new ColorMatrix();
                        cm.Matrix00 = sat;
                        cm.Matrix11 = sat;
                        cm.Matrix22 = sat;
                        ia.SetColorMatrix(cm);
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.PixelOffsetMode = PixelOffsetMode.Half;
                        g.DrawImage(small, new Rectangle(0, 0, Width, Height),
                            0, 0, qw, qh, GraphicsUnit.Pixel, ia);
                    }
                }
                using (SolidBrush tint = new SolidBrush(Color.FromArgb(140, 28, 28, 30)))
                    g.FillRectangle(tint, 0, 0, Width, Height);
            }
            else
            {
                using (SolidBrush dry = new SolidBrush(Color.FromArgb(180, 28, 28, 30)))
                    g.FillRectangle(dry, 0, 0, Width, Height);
            }
            using (SolidBrush edge = new SolidBrush(Color.FromArgb(26, 255, 255, 255)))
                g.FillRectangle(edge, 0, 1, Width, 1);
            g.Restore(state);
        }

        /// <summary>C2 4th caption button paint: one glass dot over the
        /// real (system-drawn) caption - rgba(0,0,0,0.06) hover wash + dark
        /// #1D1D1F glyph, quiet at rest (0.55) and fully present on hover
        /// (hover 现形) - the capsule's drawing language on a light system
        /// bar. The glyph is the aspect-fit arrows; SetMaximized swaps it
        /// to the restore glyph while the maximize is active.</summary>
        private void PaintFourthButton(Graphics g)
        {
            NavButton b = Buttons[0];
            if (b.Hover)
                using (SolidBrush wash = new SolidBrush(Color.FromArgb(15, 0, 0, 0)))
                    g.FillEllipse(wash, b.Circle);
            int alpha = b.Hover ? 235 : 140;
            TextRenderer.DrawText(g, _glyphs[0], GlyphFont(Dpi), b.Circle,
                Color.FromArgb(alpha, 0x1D, 0x1D, 0x1F),
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter |
                TextFormatFlags.NoPrefix);
        }

        /// <summary>Caption button width (physical px) - the unit the
        /// system's three-button cluster is measured in; SyncStrips keeps
        /// its top strip clear of the cluster using it.</summary>
        public int CapButtonWidth { get { return _capBtnW; } }

        /// <summary>C2 4th-button anchor, called every tick and from the
        /// WinEvent move/size hook (via SyncChin): the system ─ □ ✕
        /// cluster starts at visibleRight - 3 caption-button widths, so the
        /// dot takes the slot just LEFT of ─; vertically it centers on the
        /// caption band (~16 logical px below the visible top). ``visible``
        /// is the video window's DWM EXTENDED_FRAME_BOUNDS - where the
        /// caption buttons actually end (the raw window rect carries ~7px
        /// invisible resize borders past them). ``work`` is the monitor's
        /// work area: the anchor is clamped into it so a window dragged
        /// partly off-screen (or a fullscreen top) never pushes the dot
        /// out of reach (C2 taskbar-adjacent guard).</summary>
        public void SyncFourthButton(Rectangle visible, Rectangle work)
        {
            int left = visible.Right - 4 * _capBtnW;
            int top = visible.Top + (int)Math.Round(LogicalCapCenterY * Dpi)
                    - _capBtnH / 2;
            left = Math.Max(work.Left, Math.Min(left, work.Right - _capBtnW));
            top = Math.Max(work.Top, Math.Min(top, work.Bottom - _capBtnH));
            Rectangle want = new Rectangle(left, top, _capBtnW, _capBtnH);
            if (Bounds != want) Bounds = want;
            if (Buttons.Count == 1)
                Buttons[0].Circle = new Rectangle(0, 0, _capBtnW, _capBtnH);
        }
    }

    // -------------------------------------------------------------------------
    // Side bands (immersive top mode only): invisible drag-to-move strips
    // glued to the video window's left and right edges - the top band's
    // move engine (Ctrl.BeginMove/UpdateMove/EndMove, the very same one
    // the caption band drives) reached from the sides, so an immersive
    // window can be dragged by ANY of its three bands. Pure hot zone: a
    // GhostBackdrop OverlayWindow that paints nothing but the alpha=1
    // ghost fill (layered windows pass clicks through where alpha=0, so
    // the tiniest non-zero opacity keeps the band hit-testable while
    // staying visually imperceptible - EdgeStrip's PushGhost contract).
    // The bands sit INSIDE the ~6 DIP edge-resize strips: the outermost
    // sliver keeps resizing, the band inside it moves, so no rect ever
    // overlaps another affordance and clicks route positionally - the
    // same no-z-order-bookkeeping contract as SyncStrips.
    // --chrome-top=native never creates them (Controller gates the pair
    // at birth): the real system caption owns dragging there.
    // -------------------------------------------------------------------------
    internal sealed class SideBandWindow : OverlayWindow
    {
        /// <summary>Band width, logical px. 8 DIP: wide enough to grab
        /// without looking, narrow enough that the video keeps all but a
        /// sliver of its edge pixels (6-8 was the agreed hot-zone range).
        public const int LogicalWidth = 8;

        public SideBandWindow(Controller owner)
            : base(owner, 0, 0)   // ghost surface: no rounded-bar clip
        {
            GhostBackdrop = true;         // zero bar surface: pure hot zone
            int w0 = Math.Max(1, (int)(LogicalWidth * Dpi));
            Size = new Size(w0, w0);      // real bounds come from SyncTo
            Cursor = Cursors.SizeAll;     // the move affordance cursor
            WireInput();
        }

        /// <summary>Ghost hot zone: nothing to see, everything to hit -
        /// alpha=1 everywhere keeps the layered surface clickable while
        /// visually imperceptible (alpha=0 would pass clicks through; see
        /// EdgeStrip.PushGhost).</summary>
        protected override void PaintBar(Graphics g)
        {
            using (SolidBrush ghost = new SolidBrush(Color.FromArgb(1, 0, 0, 0)))
                g.FillRectangle(ghost, 0, 0, Width, Height);
        }

        protected override void WireInput()
        {
            // Plain move, no direction disambiguation: the caption band
            // needs its horizontal/vertical judgment because a top drag
            // may be an Android shade pull, but a side-edge press is a
            // window move in every direction. Press = grab at the cursor
            // (BeginMove, the caption band's engine reused verbatim),
            // motion = event-driven follow (the held capture keeps the
            // WM_MOUSEMOVEs flowing; the tick's UpdateMove poll is the
            // fallback), release or capture loss = drop. Fallback if the
            // mirrored app ever needs its edge swipes back: judge
            // direction like EdgeStrip and replay inward swipes with
            // ShadeCaption's posting technique - every change would live
            // in this handler, mirroring the Edge == 0 confinement.
            MouseDown += delegate(object s, MouseEventArgs e)
            {
                if (e.Button != MouseButtons.Left) return;
                Ctrl.BeginMove();      // the top band's move engine, reused
                Capture = true;        // the whole drag stays on this band
            };
            MouseMove += delegate(object s, MouseEventArgs e)
            {
                if (Ctrl.Moving) Ctrl.UpdateMove();
            };
            MouseUp += delegate(object s, MouseEventArgs e)
            {
                if (e.Button == MouseButtons.Left && Ctrl.Moving)
                {
                    Ctrl.EndMove();
                    Capture = false;
                }
            };
            // A drag that ends by capture loss (alt-tab, modal steal) never
            // delivers MouseUp - same recovery as every other drag surface.
            MouseCaptureChanged += delegate(object s, EventArgs e)
            {
                if (!Capture && Ctrl.Moving) Ctrl.EndMove();
            };
        }

        /// <summary>Glue to one side edge. Moves only reposition the
        /// layered surface; the ghost bitmap is re-pushed solely when the
        /// size changed - the same on-demand render contract as the chin
        /// and the corner masks.</summary>
        public void SyncTo(Rectangle want)
        {
            if (want.Width <= 0 || want.Height <= 0) return;
            bool sizeChanged = Width != want.Width || Height != want.Height;
            if (Bounds != want) Bounds = want;
            if (!Visible)
            {
                Show();
                Render();
            }
            else if (sizeChanged) Render();
        }
    }

    // -------------------------------------------------------------------------
    // Controller: discovery, window repair, tracking, sampling, visibility.
    // ------------------------------------------------------------------------
    /// <summary>An invisible layered hot-zone hugging one window edge.
    /// Pixel alpha is zero everywhere, but layered windows still receive
    /// mouse input, so this is how the borderless scrcpy window regains
    /// native-feeling resize edges (correct cursors, live feedback).
    /// Edge 0 is the caption twin: a FULL-WIDTH band directly below the
    /// top resize strip that MOVES the window - the native title-bar
    /// layout (top sliver = resize, band = drag, corners = resize) with
    /// a plain arrow cursor, so drag-anywhere needs zero learning
    /// (user-feedback trio #2). Press-and-hold ~250ms without moving
    /// enters move-follow mode (trio #3): the window then follows the
    /// cursor without a re-press, release ends it - native feel.
    /// The band also DISAMBIGUATES drag direction (see the MouseMove
    /// hook): horizontal = window move, vertical = the Android
    /// notification shade pull handed to the mirrored video.</summary>
    internal sealed class EdgeStrip : Form
    {
        public readonly int Edge;   // HT code: 10 left .. 17 bottomright
        private readonly Controller _owner;
        // Edge-0 caption disambiguation state. While _pendingDown is set
        // the drag direction is undecided and the window MUST NOT move:
        // a vertical drag from the band belongs to the mirrored Android
        // status bar (shade pull), not to the desktop window manager.
        private bool _pendingDown;
        private Point _downScreen;          // press point, screen coords
        /// <summary>User-feedback trio #3: hold-to-move timer. Pressing
        /// the caption band (strictly non-button area - the capsule floats
        /// ABOVE the band and its painted pill wins those hits, so button
        /// presses never reach this strip) and holding still for
        /// HoldMoveMs enters move-follow mode. Reuses the plain move
        /// engine (BeginMoveAt + UpdateMove) - no parallel drag path.</summary>
        private const int HoldMoveMs = 250;
        private readonly Timer _holdMove;
        /// <summary>Set when a vertical caption drag was handed to the
        /// video as a shade pull: the hidden strip cannot see the MouseUp
        /// (hiding drops its capture), so SyncStrips polls the physical
        /// button and must not re-show this strip until it is released.</summary>
        public bool ShadeHold;

        public EdgeStrip(Controller owner, int edge)
        {
            _owner = owner;
            Edge = edge;
            StartPosition = FormStartPosition.Manual;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            AutoScaleMode = AutoScaleMode.None;
            Bounds = new Rectangle(-20000, -20000, 1, 1);   // parked until synced
            bool vertical = edge == 10 || edge == 11;
            bool horizontal = edge == 12 || edge == 15;
            if (vertical) Cursor = Cursors.SizeWE;
            else if (horizontal) Cursor = Cursors.SizeNS;
            else if (edge == 13 || edge == 17) Cursor = Cursors.SizeNWSE;
            else if (edge == 14 || edge == 16) Cursor = Cursors.SizeNESW;
            else Cursor = Cursors.Default;       // move zone: plain arrow
            // ---- caption direction disambiguation (Edge 0 only) ------
            // The central band sits over mirrored video, so a drag that
            // starts on it is ambiguous: Windows wants "move the window",
            // Android wants "pull the notification shade". Both intents
            // begin identically (press, then motion), so the decision
            // cannot be made at MouseDown. Instead the band enters the
            // pending state above and commits once, when the drag first
            // crosses a small threshold (S(8) DIP, controller-side):
            //   |dx| >= |dy|  -> window move, grabbed at the PRESS point
            //                    so the pre-decision displacement is applied
            //                    in one UpdateMove step - the grab point
            //                    stays pinned under the cursor, no jump.
            //   |dy| >  |dx|  -> Android shade pull: hide this strip (its
            //                    capture drops, real events reach the video)
            //                    and replay the consumed press as posted
            //                    WM_LBUTTONDOWN + WM_MOUSEMOVE to the scrcpy
            //                    window, which injects touch WITHOUT being
            //                    activated (no focus steal). If the cursor
            //                    already left the window, the coordinates
            //                    are posted anyway.
            // Until the threshold the window never moves; the decision is
            // made exactly once per drag; a release below the threshold is
            // a plain click (or a long press in place - trio #3: the
            // HoldMoveMs timer below turns a held press into move-follow
            // mode) and resets cleanly.
            // Fallback path (user plan): if the shade replay ever fails on
            // a target device, revert these Edge == 0 branches to the old
            // plain-geometry behavior (MouseDown -> BeginMove) - every
            // caption change is confined here to keep that revert tiny.
            if (edge == 0)
            {
                // Trio #3 hold-to-move: armed at MouseDown, stopped by any
                // commitment (slip, decision, release, capture loss). On
                // fire: still pressed and still undecided -> grab the
                // window at the PRESS point and follow the cursor (the
                // pre-hold displacement, if any, applies in one step -
                // same catch-up contract as the disambiguation decision).
                _holdMove = new Timer { Interval = HoldMoveMs };
                _holdMove.Tick += delegate
                {
                    _holdMove.Stop();
                    if (_pendingDown &&
                        (NativeMethods.GetAsyncKeyState(0x01 /*VK_LBUTTON*/) & 0x8000) != 0)
                    {
                        _pendingDown = false;
                        _owner.BeginMoveAt(_downScreen);
                        _owner.UpdateMove();
                        Log.Write("caption hold -> move follow");
                    }
                };
            }
            MouseDown += delegate(object s, MouseEventArgs e)
            {
                if (e.Button != MouseButtons.Left) return;
                if (Edge == 0)
                {
                    NativeMethods.POINT pt;
                    NativeMethods.GetCursorPos(out pt);
                    _pendingDown = true;          // judge later, not now
                    _downScreen = new Point(pt.X, pt.Y);
                    ShadeHold = false;
                    Capture = true;   // the whole drag stays on this strip
                    _holdMove.Start();   // trio #3: hold still -> follow mode
                }
                else _owner.BeginResize(Edge);
                Capture = true;   // the whole drag stays on this strip
            };
            MouseMove += delegate(object s, MouseEventArgs e)
            {
                // Event-driven gesture tracking: with the capture held this
                // strip keeps receiving WM_MOUSEMOVE through the whole drag
                // (even when the moving target re-synthesizes them under a
                // stationary cursor). Both UpdateMove and UpdateResize
                // dedupe on the cursor position, so a synthesized no-op
                // message cannot self-perpetuate a feedback storm. The Tick
                // poll remains as a fallback, not the driver.
                if (Edge == 0)
                {
                    if (_pendingDown)
                    {
                        NativeMethods.POINT pt;
                        NativeMethods.GetCursorPos(out pt);
                        int dx = pt.X - _downScreen.X;
                        int dy = pt.Y - _downScreen.Y;
                        int t = _owner.CaptionDisambiguationPx();
                        // Trio #3: while the 250ms hold timer is armed, a
                        // HORIZONTAL drag commits to MOVE at the smaller
                        // slip threshold (4 DIP) - quick drags keep moving
                        // instantly instead of waiting out the full
                        // disambiguation width. Vertical drags keep the
                        // jitter-proof threshold: they disambiguate to the
                        // Android shade pull and must not fire early.
                        bool armed = _holdMove != null && _holdMove.Enabled;
                        if (armed && Math.Abs(dx) >= _owner.HoldMoveSlipPx()
                            && Math.Abs(dx) >= Math.Abs(dy))
                        {
                            _pendingDown = false;   // committed: move, early
                            StopHoldMove();
                            _owner.BeginMoveAt(_downScreen);
                            _owner.UpdateMove();   // catch up in one step
                            Log.Write("caption slip " + dx + "/" + dy + " -> move");
                            return;
                        }
                        if (Math.Abs(dx) < t && Math.Abs(dy) < t) return;
                        _pendingDown = false;   // decide exactly once
                        StopHoldMove();
                        if (Math.Abs(dx) >= Math.Abs(dy))
                        {
                            _owner.BeginMoveAt(_downScreen);
                            _owner.UpdateMove();   // catch up in one step
                            Log.Write("caption " + dx + "/" + dy + " -> move");
                        }
                        else
                        {
                            ShadeHold = true;  // SyncStrips holds the hide
                            Hide();            // real events now hit the video
                            Capture = false;
                            _owner.ShadeCaption(_downScreen, new Point(pt.X, pt.Y));
                            Log.Write("caption " + dx + "/" + dy + " -> shade");
                        }
                        return;
                    }
                    _owner.UpdateMove();
                }
                else _owner.UpdateResize();
            };
            MouseUp += delegate(object s, MouseEventArgs e)
            {
                if (e.Button != MouseButtons.Left) return;
                StopHoldMove();
                if (_pendingDown)
                {
                    // Released below the threshold: a tap. The mirrored
                    // phone's top-center hosts its "smart island" UI, so
                    // forward the tap to the video instead of eating it
                    // (user request): press+release replayed as a pair.
                    _pendingDown = false;
                    Capture = false;
                    Point tapScreen = PointToScreen(new Point(e.X, e.Y));
                    _owner.TapCaption(tapScreen);
                    return;
                }
                if (_owner.Resizing) { _owner.EndResize(); Capture = false; }
                else if (_owner.Moving) { _owner.EndMove(); Capture = false; }
            };
            MouseCaptureChanged += delegate(object s, EventArgs e)
            {
                if (!Capture)
                {
                    if (Edge == 0) _pendingDown = false;   // drag died mid-judgement
                    StopHoldMove();                        // ...and mid-hold
                    if (_owner.Resizing || _owner.Moving)
                    {
                        _owner.EndResize();
                        _owner.EndMove();
                    }
                }
            };
        }

        private void StopHoldMove()
        {
            if (_holdMove != null) _holdMove.Stop();
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                // Same sandwich contract as the bars: no topmost style -
                // the controller inserts the strip right above the video
                // window (RestackOverlays), so a window covering the video
                // covers these hot zones too.
                cp.ExStyle |= 0x00080000      // WS_EX_LAYERED
                           | 0x08000000      // WS_EX_NOACTIVATE
                           | 0x00000080;     // WS_EX_TOOLWINDOW
                return cp;
            }
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            PushGhost();
        }

        protected override void OnSizeChanged(EventArgs e)
        {
            base.OnSizeChanged(e);
            if (IsHandleCreated) PushGhost();
        }

        /// <summary>Make every pixel fully transparent (alpha 0) yet keep the
        /// window hit-testable - only WS_EX_TRANSPARENT would pass clicks
        /// through, and we deliberately do not set it.</summary>
        private void PushGhost()
        {
            if (Width <= 0 || Height <= 0) return;
            using (Bitmap bmp = new Bitmap(Width, Height, PixelFormat.Format32bppArgb))
            {
                // alpha=1: layered windows pass mouse through where alpha=0,
                // so the hot-zone needs the tiniest non-zero opacity to be
                // clickable while staying visually imperceptible.
                using (Graphics g = Graphics.FromImage(bmp))
                using (SolidBrush ghost = new SolidBrush(Color.FromArgb(1, 0, 0, 0)))
                {
                    g.FillRectangle(ghost, 0, 0, Width, Height);
                }
                IntPtr screen = NativeMethods.GetDC(IntPtr.Zero);
                IntPtr mem = NativeMethods.CreateCompatibleDC(screen);
                IntPtr hbm = bmp.GetHbitmap(Color.FromArgb(0));
                IntPtr old = NativeMethods.SelectObject(mem, hbm);
                try
                {
                    NativeMethods.SIZE size;
                    size.cx = Width; size.cy = Height;
                    NativeMethods.POINT src;
                    src.X = 0; src.Y = 0;
                    NativeMethods.BLENDFUNCTION blend;
                    blend.BlendOp = 0; blend.BlendFlags = 0;
                    blend.SourceConstantAlpha = 255; blend.AlphaFormat = 1;
                    NativeMethods.UpdateLayeredWindow(
                        Handle, screen, IntPtr.Zero, ref size, mem, ref src, 0, ref blend, 2);
                }
                finally
                {
                    NativeMethods.SelectObject(mem, old);
                    NativeMethods.DeleteObject(hbm);
                    NativeMethods.DeleteDC(mem);
                    NativeMethods.ReleaseDC(IntPtr.Zero, screen);
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Corner mask: a tiny click-through layered square per window corner that
    // strokes the SAME superellipse the region clips along, anti-aliased.
    // GDI regions are 1-bit (hard staircase); this 2px per-pixel-alpha stroke
    // covers the +-1px stair band and reads as a designed hairline edge.
    // -------------------------------------------------------------------------
    internal sealed class CornerMask : Form
    {
        private readonly int _corner;        // 0=TL 1=TR 2=BR 3=BL (clockwise)
        private int _radius;                 // physical px, 0 = hidden
        private float _dpi;                  // last rendered scale

        public CornerMask(int corner)
        {
            _corner = corner;
            StartPosition = FormStartPosition.Manual;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            AutoScaleMode = AutoScaleMode.None;
            Bounds = new Rectangle(-20000, -20000, 1, 1);   // parked until synced
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                // Click-through and sandwich-z (inserted above the video
                // window by the controller, never a floating topmost).
                cp.ExStyle |= 0x00080000      // WS_EX_LAYERED
                           | 0x08000000      // WS_EX_NOACTIVATE
                           | 0x00000080      // WS_EX_TOOLWINDOW
                           | 0x00000020;     // WS_EX_TRANSPARENT: never take clicks
                return cp;
            }
        }

        /// <summary>Place the square over one corner of the visible window
        /// bounds and stroke the matching superellipse quadrant. The square
        /// extends ``o`` px past the corner so the outer shadow arcs stay
        /// inside the bitmap instead of being hard-cut at the window edge.
        /// </summary>
        public void SyncTo(Rectangle visible, int radiusPhysical, float dpi)
        {
            int r = Math.Min(radiusPhysical, Math.Min(visible.Width, visible.Height) / 2);
            if (r <= 1)
            {
                if (Visible) Hide();
                return;
            }
            int o = (int)Math.Ceiling(12f * dpi);   // outward margin past corner
            int q = (int)Math.Ceiling(8f * dpi);    // inward margin along edges
            int size = r + o + q;
            bool left = (_corner == 0 || _corner == 3);
            bool top = (_corner == 0 || _corner == 1);
            int x = left ? visible.Left - o : visible.Right - (size - o);
            int y = top ? visible.Top - o : visible.Bottom - (size - o);
            Rectangle want = new Rectangle(x, y, size, size);
            // Moves only reposition the layered surface (no re-push); the
            // bitmap is re-rendered solely on size / radius / DPI change.
            Rectangle old = Bounds;
            bool sizeChanged = old.Size != want.Size;
            if (old != want) Bounds = want;
            if (!Visible) Show();
            if (_radius != r || _dpi != dpi || sizeChanged)
            {
                _radius = r;
                _dpi = dpi;
                Render(r, dpi, left, top);
            }
        }

        public void HideMask()
        {
            if (Visible) Hide();
            _radius = 0;
        }

        private void Render(int r, float dpi, bool left, bool top)
        {
            if (Width <= 0 || Height <= 0) return;
            using (Bitmap bmp = new Bitmap(Width, Height, PixelFormat.Format32bppArgb))
            {
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    int wx = left ? (int)Math.Ceiling(12f * dpi) : Width - (int)Math.Ceiling(12f * dpi);
                    int wy = top ? (int)Math.Ceiling(12f * dpi) : Height - (int)Math.Ceiling(12f * dpi);
                    int cx = wx + (left ? r : -r);
                    int cy = wy + (top ? r : -r);
                    int sx = left ? -1 : 1;
                    int sy = top ? -1 : 1;
                    // The region underneath is binary, so broad shadow ramps make
                    // the edge look like a dirty halo and become visibly detached
                    // during resize. Keep only a narrow, low-alpha anti-alias pass;
                    // the mask must hide the staircase, not paint a fake shadow.
                    StrokeArc(g, cx, cy, sx, sy, r + 0.15f * dpi, 1.35f * dpi, 105);
                    StrokeArc(g, cx, cy, sx, sy, r + 1.0f * dpi, 1.6f * dpi, 45);
                }
                PushGhostBitmap(bmp);
            }
        }

        private static void StrokeArc(Graphics g, int cx, int cy, int sx, int sy,
            float r, float width, int alpha)
        {
            using (GraphicsPath path = new GraphicsPath())
            {
                const int steps = 24;
                PointF prev = PointAt(cx, cy, sx, sy, r, 0f);
                for (int i = 1; i <= steps; i++)
                {
                    PointF p = PointAt(cx, cy, sx, sy, r,
                        (float)(Math.PI / 2 * i / steps));
                    path.AddLine(prev, p);
                    prev = p;
                }
                using (Pen pen = new Pen(Color.FromArgb(alpha, 12, 12, 14), width))
                {
                    pen.StartCap = LineCap.Round;
                    pen.EndCap = LineCap.Round;
                    pen.Alignment = PenAlignment.Center;
                    g.DrawPath(pen, path);
                }
            }
        }

        private static PointF PointAt(int cx, int cy, int sx, int sy, float r, float t)
        {
            float u = (float)Math.Sqrt(Math.Cos(t));
            float v = (float)Math.Sqrt(Math.Sin(t));
            return new PointF(cx + sx * r * u, cy + sy * r * v);
        }

        private void PushGhostBitmap(Bitmap bmp)
        {
            IntPtr screen = NativeMethods.GetDC(IntPtr.Zero);
            IntPtr mem = NativeMethods.CreateCompatibleDC(screen);
            IntPtr hbm = bmp.GetHbitmap(Color.FromArgb(0));
            IntPtr old = NativeMethods.SelectObject(mem, hbm);
            try
            {
                NativeMethods.SIZE size;
                size.cx = Width; size.cy = Height;
                NativeMethods.POINT src;
                src.X = 0; src.Y = 0;
                NativeMethods.BLENDFUNCTION blend;
                blend.BlendOp = 0; blend.BlendFlags = 0;
                blend.SourceConstantAlpha = 255; blend.AlphaFormat = 1;
                NativeMethods.UpdateLayeredWindow(
                    Handle, screen, IntPtr.Zero, ref size, mem, ref src, 0, ref blend, 2);
            }
            finally
            {
                NativeMethods.SelectObject(mem, old);
                NativeMethods.DeleteObject(hbm);
                NativeMethods.DeleteDC(mem);
                NativeMethods.ReleaseDC(IntPtr.Zero, screen);
            }
        }
    }

    internal sealed class Controller : IDisposable
    {
        private const int TickMs = 50;
        private const int SampleMs = 220;
        private const int CapsuleSampleMs = 300;   // trio #1 acrylic refresh
        private const int FirstWaitMs = 12000;
        private const int LostWaitMs = 15000;
        private const int TriggerTop = 6;      // logical px reveal band
        private const int RetainTop = 48;      // logical px hysteresis
        private const int TopMargin = 10;      // logical px from top-right
        private const int MaxGraceMs = 700;

        private readonly string _title, _serial, _adb;
        private readonly string _displayMode;         // mirror | flex | fixed
        private readonly string _topMode;             // immersive | native | none
        private readonly string _bottomMode;          // immersive | native | none
        private readonly float _dpi;                  // probe-based scale (S())
        private readonly Timer _tick = new Timer();
        private readonly ChinWindow _chin;
        private readonly TopWindow _top;
        private IntPtr _hwnd = IntPtr.Zero;
        private int _waitedMs;
        private bool _repaired;
        private bool _fakedMax;
        private int _fakedMode;                    // 1 aspect fit, 2 full fill
        private Rectangle _savedRect, _maxRect;
        private int _maxGraceUntil;
        private int _lastSample;
        private int _topSampleAt;               // capsule acrylic cadence
        private Bitmap _sample;                // full-window sample (reused)
        private NativeMethods.WinEventDelegate _hookProc;   // keep delegate alive
        private IntPtr _hook = IntPtr.Zero;
        // EVENT_SYSTEM_FOREGROUND hook: z-order re-assertion for the
        // sandwich (activating the video window raises it past its own
        // overlays - undo that the instant it happens, not next tick).
        private NativeMethods.WinEventDelegate _fgHookProc;
        private IntPtr _fgHook = IntPtr.Zero;
        private int _ticks;
        // 2026-09-09 启动沉浸（用户报告：按投屏时窗口不够沉浸，动一下才沉浸）：
        // 窗口在静止光标下生成（点面板“投屏”按钮后光标停在窗口顶/底边附近）
        // 时，距离触发的胶囊/下巴会在启动瞬间自己弹出。_cursorMoved = 首次
        // 真实位移（≥ S(2)）之后才允许 proximity 露出；首个采样只作锚点。
        private bool _cursorMoved;
        private Point _cursorAnchor = new Point(int.MinValue, int.MinValue);
        private EdgeStrip[] _strips;
        // Side move bands (left + right edges), IMMERSIVE top mode only:
        // null under --chrome-top=native - the real system caption owns
        // dragging there, so the pair is gated at birth, not hidden per tick.
        private readonly SideBandWindow[] _sides;

        private readonly bool _homeEnabled;

        /// <summary>Native (C2) bar modes: top = real system caption
        /// (WS_CAPTION + DWM Mica applied in Repair, one overlay 4th
        /// button); bottom = the C1 acrylic chin below the window. Either
        /// mode makes its bar always-visible while engaged. Corner policy
        /// lives in Repair: without a G2 region the video window keeps
        /// DWM's own rounding in EVERY combination (the real caption's top
        /// corners round like any system window's; the native chin's
        /// corner ears patch the bottom-corner seam) - only the G2 region
        /// squares the video window.</summary>
        public bool TopNative { get { return "native".Equals(_topMode); } }

        public bool BottomNative { get { return "native".Equals(_bottomMode); } }

        /// <summary>none = 该边永不建可见栏；隐形拖动/resize 操作面保留（见 docs/window-experience.md §10）。</summary>
        public bool TopNone { get { return "none".Equals(_topMode); } }

        public bool BottomNone { get { return "none".Equals(_bottomMode); } }

        /// <summary>Normalize a bar-mode argv value to immersive|native|none (unknown -> immersive).</summary>
        private static string NormalizeBarMode(string mode)
        {
            if ("native".Equals(mode)) return "native";
            if ("none".Equals(mode)) return "none";
            return "immersive";
        }

        /// <summary>Repair's DWM corner policy for the VIDEO window, at a
        /// glance: no G2 region -> DWMWCP_ROUND (all four corners follow
        /// Windows 11's own rounding; the native chin ears patch the
        /// bottom seam under BOTH immersive and native tops). This is the
        /// ChinWindow's ear eligibility.</summary>
        public bool VideoRounded
        {
            get { return _cornerDip <= 0; }
        }

        // Live video size: seeded from argv (fixed mode knows its WxH), then
        // updated by the session-log tailer (scrcpy "INFO: Texture: WxH"
        // lines, emitted on every size change including rotation).
        private int _videoW, _videoH;
        // 2026-09-09 起这只足“武装态”时间戳：只在旋转级 Texture（视频比
        // 例 ≠ 客户区比例，见 HandleLogLine）时设置——EnforceFlexPin 的弹
        // 回窗口和 ConvergeToVideoAspect 的 500ms 节流部读它。跟随回声
        // Texture（窗口先动、显示后到）不再设置（Win+左右 snap 被弹回的
        // 根因修复）。
        private int _videoChangedAt;
        private Thread _logThread;
        private volatile bool _disposed;

        // G2 corners (quartic superellipse, curvature-continuous with the
        // straight edges): pixel-verified live 2026-09-05 that SetWindowRgn
        // DOES visually clip the scrcpy video window (block-diff against the
        // desktop = 1.7 vs 48.8 against the video). The region must be
        // re-applied after every window-rect change - regions do not scale.
        private int _cornerDip;   // 0 = off
        private int _vdDisplayId = -1;   // virtual display from session log (-1 unknown)
        private Size _lastRegionSize;    // last size seen (defers through churn)
        private Size _lastAppliedSize;   // size the region currently matches
        private bool _regionOff;         // region cleared while resizing
        private int _regionSettleAt;     // tick deadline before re-applying
        private Rectangle _visibleRect;          // DWM visible bounds (screen)
        private readonly CornerMask[] _masks = new CornerMask[4];

        // Aspect convergence: window rect changes we did not cause (external
        // window managers, scrcpy's own rotation re-layout) settle for
        // SettleMs, then mirror/fixed windows are reshaped to the video
        // aspect inside their current bounds - one-shot, never a loop.
        private Rectangle _lastRect;
        private bool _haveLastRect;
        private int _settleSince = -1;
        private const int SettleMs = 350;
        private bool _chinInset;                    // taskbar-guard state (log once per flip)

        public Controller(string title, string serial, string adb, bool home,
            string displayMode, int videoW, int videoH, string sessionLog, int cornerDip,
            string topMode, string bottomMode)
        {
            _title = title; _serial = serial; _adb = adb;
            _homeEnabled = home;
            _displayMode = displayMode == null ? "flex" : displayMode;
            _topMode = NormalizeBarMode(topMode);
            _bottomMode = NormalizeBarMode(bottomMode);
            _dpi = ProbeDpi();
            _videoW = videoW; _videoH = videoH;
            _videoChangedAt = 0;
            _cornerDip = cornerDip;
            _chin = new ChinWindow(this, home, _displayMode, _bottomMode, VideoRounded);
            _top = new TopWindow(this, _displayMode.Equals("flex"), _topMode);
            // Force handle creation now: the WinEvent callback below may fire
            // for any window move long before the bars are first shown, and
            // BeginInvoke requires an existing handle.
            if (!_chin.IsHandleCreated) { IntPtr h = _chin.Handle; }
            if (!_top.IsHandleCreated) { IntPtr h = _top.Handle; }
            // Side move bands: immersive windows drag from the left and
            // right edges too, not just the top band. IMMERSIVE TOP ONLY:
            // --chrome-top=native gives the video window a real system
            // caption and the system already owns edge dragging - never
            // create the pair there. Their rects stay disjoint from every
            // other affordance (see SyncSideBands); stacking comes from
            // RestackOverlays' insertion order (capsule above the bands).
            _sides = TopNative ? null : new SideBandWindow[]
            {
                new SideBandWindow(this),   // left edge
                new SideBandWindow(this)    // right edge
            };
            // Invisible resize hot-zones for all four edges AND all four
            // corners (the chin used to be the only bottom affordance, which
            // made bottom resizes undiscoverable). The last strip (edge 0) is
            // the full-width caption move band; it sits strictly below the
            // top resize strip and between the corner zones, so no strip
            // rects overlap and no z-order assertion is ever needed.
            _strips = new EdgeStrip[9];
            _strips[0] = new EdgeStrip(this, 10);   // left
            _strips[1] = new EdgeStrip(this, 11);   // right
            _strips[2] = new EdgeStrip(this, 12);   // top
            _strips[3] = new EdgeStrip(this, 13);   // top-left
            _strips[4] = new EdgeStrip(this, 14);   // top-right
            _strips[5] = new EdgeStrip(this, 15);   // bottom
            _strips[6] = new EdgeStrip(this, 16);   // bottom-left
            _strips[7] = new EdgeStrip(this, 17);   // bottom-right
            _strips[8] = new EdgeStrip(this, 0);    // move (caption band)
            for (int i = 0; i < 4; i++) _masks[i] = new CornerMask(i);
            StartLogTailer(sessionLog);
            _tick.Interval = TickMs;
            _tick.Tick += Tick;
            _tick.Start();
        }

        public void Dispose()
        {
            _disposed = true;
            _tick.Stop();
            if (_hook != IntPtr.Zero)
            {
                NativeMethods.UnhookWinEvent(_hook);
                _hook = IntPtr.Zero;
            }
            if (_fgHook != IntPtr.Zero)
            {
                NativeMethods.UnhookWinEvent(_fgHook);
                _fgHook = IntPtr.Zero;
            }
            if (_sample != null) { _sample.Dispose(); _sample = null; }
            foreach (EdgeStrip strip in _strips)
            {
                strip.Dispose();
            }
            if (_sides != null)
            {
                foreach (SideBandWindow band in _sides) band.Dispose();
            }
            foreach (CornerMask mask in _masks)
            {
                mask.Dispose();
            }
        }

        // -- actions used by the bars ----------------------------------------

        // ---- live resize engine (replaces SC_SIZE: our overlay holds the
        // mouse capture, so the target's own size-move loop would starve) ----

        private bool _resizing;
        private Rectangle _resizeStart;          // outer window rect at drag start
        private Rectangle _resizeStartClient;    // client rect at drag start (screen)
        private int _chromeL, _chromeT, _chromeR, _chromeB;   // window-client insets
        private double _startClientW, _startClientH;
        private Point _resizeMouse;
        private int _resizeEdge;
        private int _resizeMoves;
        private int _lastDx = int.MinValue, _lastDy = int.MinValue;
        private const int LogicalMinW = 320, LogicalMinH = 240;   // DIP, DPI-scaled

        public bool Resizing { get { return _resizing; } }

        /// <summary>Whether the window must stay glued to the video aspect
        /// (mirror and fixed modes with a known size; flex follows freely).
        private bool RatioLock
        {
            get { return _videoW > 0 && _videoH > 0 && !_displayMode.Equals("flex"); }
        }

        private double VideoAspect()
        {
            if (_videoW <= 0 || _videoH <= 0) return 0.0;
            return (double)_videoW / _videoH;
        }

        public void BeginResize(int edge)
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            if (_fakedMax)
            {
                _fakedMax = false;
                _top.SetMaximized(0);
            }
            _resizeEdge = edge;
            _resizeStart = WindowRect();
            Rectangle client = ClientRect();
            _resizeStartClient = client;
            _chromeL = client.Left - _resizeStart.Left;
            _chromeT = client.Top - _resizeStart.Top;
            _chromeR = _resizeStart.Right - client.Right;
            _chromeB = _resizeStart.Bottom - client.Bottom;
            _startClientW = client.Width;
            _startClientH = client.Height;
            NativeMethods.POINT pt;
            NativeMethods.GetCursorPos(out pt);
            _resizeMouse = new Point(pt.X, pt.Y);
            _lastDx = int.MinValue; _lastDy = int.MinValue;
            _resizing = true;
            Log.Write("resize begin edge=" + edge + " ratio=" + (RatioLock ? "on" : "off"));
        }

        public void UpdateResize()
        {
            if (!_resizing) return;
            NativeMethods.POINT pt;
            NativeMethods.GetCursorPos(out pt);
            int dx = pt.X - _resizeMouse.X, dy = pt.Y - _resizeMouse.Y;
            if (dx == _lastDx && dy == _lastDy) return;   // dedupe: no-op drags
            _lastDx = dx; _lastDy = dy;
            _resizeMoves++;
            int L = _resizeStart.Left, T = _resizeStart.Top;
            int R = _resizeStart.Right, B = _resizeStart.Bottom;
            bool left = _resizeEdge == 10 || _resizeEdge == 13 || _resizeEdge == 16;
            bool top = _resizeEdge == 12 || _resizeEdge == 13 || _resizeEdge == 14;
            bool right = _resizeEdge == 11 || _resizeEdge == 14 || _resizeEdge == 17;
            bool bottom = _resizeEdge == 15 || _resizeEdge == 16 || _resizeEdge == 17;
            int minW = S(LogicalMinW), minH = S(LogicalMinH);
            if (left) L = Math.Min(_resizeStart.Left + dx, R - minW);
            if (top) T = Math.Min(_resizeStart.Top + dy, B - minH);
            if (right) R = Math.Max(_resizeStart.Right + dx, L + minW);
            if (bottom) B = Math.Max(_resizeStart.Bottom + dy, T + minH);
            Rectangle want = Rectangle.FromLTRB(L, T, R, B);
            if (RatioLock) want = ConstrainToVideo(want, _resizeEdge);
            want = ConstrainToWorkArea(want, _resizeEdge);
            // SWP_ASYNCWINDOWPOS: 跨进程 SetWindowPos 默认同步等待目标窗口
            // 处理 WM_WINDOWPOSCHANGING（SDL 视频窗口重排很慢，曾致拖拽
            // 粘滞不跟手）；异步下发后本线程永不阻塞，鼠标事件不再堆积。
            bool ok = NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                want.Left, want.Top, want.Width, want.Height,
                0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/
                | 0x4000 /*SWP_ASYNCWINDOWPOS*/);
            if (!ok && _resizeMoves == 1) Log.Write("swp failed");
        }

        /// <summary>Reshape a raw drag rect so the CLIENT area (where the
        /// video lives) matches the video aspect. Side drags anchor the
        /// opposite edge and re-center vertically; corner drags keep the
        /// opposite corner fixed and let the dominant axis drive.</summary>
        private Rectangle ConstrainToVideo(Rectangle outer, int edge)
        {
            double a = VideoAspect();
            if (a <= 0) return outer;
            int cw = outer.Width - _chromeL - _chromeR;
            int ch = outer.Height - _chromeT - _chromeB;
            if (cw <= 0 || ch <= 0 || _startClientW <= 0 || _startClientH <= 0)
                return outer;
            bool left = edge == 10 || edge == 13 || edge == 16;
            bool top = edge == 12 || edge == 13 || edge == 14;
            bool side = (edge == 10 || edge == 11);
            bool vert = (edge == 12 || edge == 15);
            int nw, nh;
            if (side)
            {
                nw = cw;
                nh = (int)Math.Round(nw / a);
            }
            else if (vert)
            {
                nh = ch;
                nw = (int)Math.Round(nh * a);
            }
            else
            {
                double sw = cw / _startClientW;
                double sh = ch / _startClientH;
                double s = Math.Abs(sw - 1) >= Math.Abs(sh - 1) ? sw : sh;
                nw = (int)Math.Round(_startClientW * s);
                nh = (int)Math.Round(nw / a);
            }
            // DPI-scaled minimums without breaking the ratio.
            int minW = S(LogicalMinW), minH = S(LogicalMinH);
            if (nw < minW) { nw = minW; nh = (int)Math.Round(nw / a); }
            if (nh < minH) { nh = minH; nw = (int)Math.Round(nh * a); }
            int ow = nw + _chromeL + _chromeR;
            int oh = nh + _chromeT + _chromeB;
            int x = left ? outer.Right - _chromeR - ow : outer.Left + _chromeL;
            int y = top ? outer.Bottom - _chromeB - oh : outer.Top + _chromeT;
            if (side)
            {
                double cy = _resizeStart.Top + _chromeT + _startClientH / 2.0;
                y = (int)Math.Round(cy - oh / 2.0);
            }
            if (vert)
            {
                double cx = _resizeStart.Left + _chromeL + _startClientW / 2.0;
                x = (int)Math.Round(cx - ow / 2.0);
            }
            return new Rectangle(x, y, ow, oh);
        }

        /// <summary>Shrink an oversized drag result to fit the work area of
        /// the monitor under the rect's CENTER.
        ///
        /// Multi-monitor contract (mixed-DPI bug fix): MonitorFromWindow is
        /// straddle-sensitive - for a window spanning two screens it flips
        /// to the OTHER monitor mid-drag, which used to clamp the window
        /// into the wrong screen's work area and re-center it, snapping the
        /// window across the boundary (the reported "card switch at the
        /// screen edge"). Picking by the center point follows the user's
        /// drag target instead. Point flag 2 = MONITOR_DEFAULTTONEAREST
        /// (1 would be MONITOR_DEFAULTTOPRIMARY; see WorkArea). And because
        /// a clamp may legitimately fire
        /// while dragging across onto a smaller monitor, the shrunk rect
        /// stays anchored at the edges NOT being dragged - the dragged
        /// corner keeps chasing the cursor, nothing teleports.
        /// Physical pixels throughout: work areas are per-monitor physical,
        /// and a ratio already mixes with them DPI-free.</summary>
        private Rectangle ConstrainToWorkArea(Rectangle want, int edge)
        {
            NativeMethods.POINT center;
            center.X = want.Left + want.Width / 2;
            center.Y = want.Top + want.Height / 2;
            IntPtr mon = NativeMethods.MonitorFromPoint(
                center, 2 /*MONITOR_DEFAULTTONEAREST*/);
            NativeMethods.MONITORINFO mi = new NativeMethods.MONITORINFO();
            mi.cbSize = Marshal.SizeOf(typeof(NativeMethods.MONITORINFO));
            if (mon == IntPtr.Zero || !NativeMethods.GetMonitorInfoW(mon, ref mi))
                return want;
            Rectangle wa = Rectangle.FromLTRB(
                mi.rcWork.Left, mi.rcWork.Top, mi.rcWork.Right, mi.rcWork.Bottom);
            if (want.Width <= wa.Width && want.Height <= wa.Height) return want;
            double scale = Math.Min((double)wa.Width / want.Width,
                                    (double)wa.Height / want.Height);
            int w = Math.Max(S(LogicalMinW), (int)(want.Width * scale));
            int h = Math.Max(S(LogicalMinH), (int)(want.Height * scale));
            bool dragLeft = edge == 10 || edge == 13 || edge == 16;
            bool dragTop = edge == 12 || edge == 13 || edge == 14;
            int x = dragLeft ? want.Right - w : want.Left;
            int y = dragTop ? want.Bottom - h : want.Top;
            return new Rectangle(x, y, w, h);
        }

        public void EndResize()
        {
            if (!_resizing) return;
            _resizing = false;
            PinCurrentRect();
            Log.Write("resize end moves=" + _resizeMoves);
        }

        // ---- window pin (app sessions) ---------------------------------
        // 恢复于 2026-09-06（曾误删）：窗口形状是用户财产，任何外部变化——
        // 尤其 scrcpy 在 APP 转屏后的自动改窗（live-verified：app 方向翻转
        // 曾把窗口旋转成 1336x1986）——都弹回用户钉住的矩形。Drag guards
        // 必须：_moving/_resizing 期间矩形合法变化，那时的弹回就是第一次
        // 钉扎的 teleport-back bug。
        private Rectangle _pinnedRect = new Rectangle(0, 0, 0, 0);
        private int _lbtnAt;                            // last tick LBUTTON was held
        private int _discoveredAt;                      // tick the window was found

        private void PinCurrentRect()
        {
            if (!_displayMode.Equals("flex")) return;
            Rectangle wr = WindowRect();
            if (wr.Width < 8 || wr.Height < 8) return;
            _pinnedRect = wr;
        }

        private void EnforceFlexPin(Rectangle wr)
        {
            if (!_displayMode.Equals("flex")) return;
            // 启动宽限期（flex 跟随时代，2026-09-06 晚）：scrcpy 建窗为
            // 256x256，首帧后才程序化调到初始尺寸；竞态下钉扎先收养了
            // 256x256，再把首帧调整弹回 → 显示跟随窗口卡死在 256x256
            // （真机：piliplus 横屏会话全型“flex pin restored 256x256
            // (was 2560x1440)”）。发现后 4s 内只跟踪不弹回，让初始布局
            // 完成；也永不收养 256x256 本身。
            if (_discoveredAt > 0 && Environment.TickCount - _discoveredAt < 4000)
            {
                if (!(wr.Width == 256 && wr.Height == 256)) _pinnedRect = wr;
                return;
            }
            if (_moving || _resizing) return;         // user is driving: never bounce
            if (NativeMethods.IsZoomed(_hwnd)) return; // native maximize is the user's act
            // Native-border drags never set _moving/_resizing (they run in
            // the scrcpy window's own modal loop). ANY left-button-down window
            // change is user action: skip while held, and adopt the result for
            // a grace period after release instead of bouncing it back (the
            // "cannot resize the window" bug - pin fought native drags).
            if ((NativeMethods.GetAsyncKeyState(0x01 /*VK_LBUTTON*/) & 0x8000) != 0)
            {
                _lbtnAt = Environment.TickCount;
                return;
            }
            if (_lbtnAt > 0 && Environment.TickCount - _lbtnAt < 1500)
            {
                _pinnedRect = wr;                     // adopt the user's new rect
                return;
            }
            if (_fakedMax) { _pinnedRect = wr; return; }
            if (_pinnedRect.Width < 8) { _pinnedRect = wr; return; }
            if (wr == _pinnedRect) return;
            // 2026-09-09 Win 快捷键回归修复（用户报告：Win+左右 snap，
            // 窗口过去又闪回，真机复现日志“flex pin restored”） ：外部改窗
            // 不再一律弹回。键盘发起的摆放（Win+左/右/上/下 snap、
            // Win+Shift+方向键跨屏、PowerToys FancyZones、第三方窗口
            // 管理器）没有左键、没有 _moving/_resizing——旧逻辑把它们全当
            // 成 scrcpy 转屏自动改窗弹回。现在只有【旋转级 Texture 刚到】
            // （视频比例 ≠ 窗口客户区比例，见 HandleLogLine 的 arm 判定）
            // 后的短窗口内才弹回——那才是 scrcpy 转屏自动改窗，窗口形状是
            // 用户财产；跟随回声 Texture（窗口先动、显示后到）不武装。
            // 其余一切外部变化收编为新的钉扎（snap 后的半屏矩形就是用户
            // 的新形状）。
            if (_videoChangedAt > 0
                && Environment.TickCount - _videoChangedAt < 2500)
            {
                NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                    _pinnedRect.Left, _pinnedRect.Top,
                    _pinnedRect.Width, _pinnedRect.Height,
                    0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/);
                Log.Write("flex pin restored " + _pinnedRect.Width + "x"
                    + _pinnedRect.Height + " (was " + wr.Width + "x" + wr.Height + ")");
                return;
            }
            _pinnedRect = wr;
            Log.Write("flex pin adopted " + wr.Width + "x" + wr.Height
                + " (external placement)");
        }

        // ---- window move (caption band) -----------------------------------

        private bool _moving;
        private Point _moveStart, _moveMouse;
        private int _lastMoveX = int.MinValue, _lastMoveY = int.MinValue;
        private int _moveMoves;

        public bool Moving { get { return _moving; } }

        public void BeginMove()
        {
            NativeMethods.POINT pt;
            NativeMethods.GetCursorPos(out pt);
            BeginMoveAt(new Point(pt.X, pt.Y));
        }

        /// <summary>BeginMove with an explicit grab point. The caption
        /// disambiguation (EdgeStrip, Edge 0) passes the original PRESS
        /// point, so the displacement that accumulated while the drag
        /// direction was still undecided is applied in one UpdateMove step:
        /// the window simply catches up to where a plain move would have
        /// been all along - the grab point stays pinned under the cursor,
        /// no visible jump in either direction.</summary>
        public void BeginMoveAt(Point grab)
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            if (_fakedMax)
            {
                _fakedMax = false;
                _top.SetMaximized(0);
            }
            Rectangle wr = WindowRect();
            _moveStart = new Point(wr.Left, wr.Top);
            _moveMouse = grab;
            _moving = true;
            _lastMoveX = int.MinValue; _lastMoveY = int.MinValue;
            _moveMoves = 0;
            Log.Write("move begin");
        }

        public void UpdateMove()
        {
            if (!_moving) return;
            NativeMethods.POINT pt;
            NativeMethods.GetCursorPos(out pt);
            int x = _moveStart.X + pt.X - _moveMouse.X;
            int y = _moveStart.Y + pt.Y - _moveMouse.Y;
            // Dedupe: under a held capture, WM_MOUSEMOVE is re-synthesized
            // whenever the target (or our own strips) shifts under a
            // stationary cursor. Skipping the no-op SetWindowPos breaks that
            // feedback loop - same contract as UpdateResize's dx/dy guard.
            if (x == _lastMoveX && y == _lastMoveY) return;
            _lastMoveX = x; _lastMoveY = y;
            _moveMoves++;
            NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero, x, y,
                0, 0, 0x0001 /*SWP_NOSIZE*/ | 0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/
                | 0x4000 /*SWP_ASYNCWINDOWPOS*/);
        }

        public void EndMove()
        {
            if (!_moving) return;
            _moving = false;
            PinCurrentRect();
            Log.Write("move end moves=" + _moveMoves);
        }

        /// <summary>DPI-scaled drag distance that turns an undecided
        /// caption press into a committed move-or-shade decision. Large
        /// enough to swallow hand jitter, small enough to feel instant.</summary>
        public int CaptionDisambiguationPx()
        {
            return S(8);
        }

        /// <summary>User-feedback trio #3: DPI-scaled horizontal slip
        /// (4 DIP) at which an armed hold-to-move press commits to a
        /// window move EARLY, instead of waiting out the disambiguation
        /// width. Deliberately horizontal-only (see EdgeStrip's MouseMove)
        /// so hand jitter on a still press cannot fire the shade pull.</summary>
        public int HoldMoveSlipPx()
        {
            return S(4);
        }

        /// <summary>Vertical caption drag = Android status-bar shade pull.
        /// The caption band consumed the press while judging direction, so
        /// replay it to the scrcpy window as posted messages: one
        /// WM_LBUTTONDOWN at the press point plus one WM_MOUSEMOVE at the
        /// current point (the messages bypass hit-testing, so the screen-
        /// to-client mapping is ours to do; MK_LBUTTON in wParam keeps the
        /// button state consistent for the motion that follows). Posted,
        /// not sent, and deliberately NO activation: scrcpy injects touch
        /// without focus, and stealing focus would disturb the device.
        /// If the cursor has already left the window the coordinates are
        /// posted regardless - scrcpy clamps what it cannot reach.
        /// Fallback if a device ignores the replay: make this a no-op
        /// (the pure-geometry revert of the EdgeStrip Edge == 0 branch).</summary>
        public void ShadeCaption(Point pressScreen, Point nowScreen)
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            NativeMethods.POINT press = new NativeMethods.POINT();
            press.X = pressScreen.X; press.Y = pressScreen.Y;
            NativeMethods.POINT now = new NativeMethods.POINT();
            now.X = nowScreen.X; now.Y = nowScreen.Y;
            NativeMethods.ScreenToClient(_hwnd, ref press);
            NativeMethods.ScreenToClient(_hwnd, ref now);
            IntPtr mk = (IntPtr)0x0001;   // MK_LBUTTON
            NativeMethods.PostMessageW(_hwnd, 0x0201 /*WM_LBUTTONDOWN*/, mk,
                (IntPtr)((press.X & 0xFFFF) | ((press.Y & 0xFFFF) << 16)));
            NativeMethods.PostMessageW(_hwnd, 0x0200 /*WM_MOUSEMOVE*/, mk,
                (IntPtr)((now.X & 0xFFFF) | ((now.Y & 0xFFFF) << 16)));
            Log.Write("shade replay down=" + press.X + "," + press.Y +
                " move=" + now.X + "," + now.Y);
        }

        /// <summary>Replay a plain tap (press+release, no motion) onto the
        /// video window. Caption-band taps below the drag threshold belong
        /// to the phone's top-center UI ("smart island"), not to the window.
        /// Fallback: make this a no-op to revert to tap-swallowing.</summary>
        public void TapCaption(Point tapScreen)
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            NativeMethods.POINT p = new NativeMethods.POINT();
            p.X = tapScreen.X; p.Y = tapScreen.Y;
            NativeMethods.ScreenToClient(_hwnd, ref p);
            IntPtr mk = (IntPtr)0x0001;   // MK_LBUTTON
            IntPtr lp = (IntPtr)((p.X & 0xFFFF) | ((p.Y & 0xFFFF) << 16));
            NativeMethods.PostMessageW(_hwnd, 0x0201 /*WM_LBUTTONDOWN*/, mk, lp);
            NativeMethods.PostMessageW(_hwnd, 0x0202 /*WM_LBUTTONUP*/, IntPtr.Zero, lp);
            Log.Write("caption tap replay " + p.X + "," + p.Y);
        }

        // ---- aspect convergence (external changes, rotation, maximize) ----

        /// <summary>Watch for window-rect changes we did not cause (window
        /// managers, scrcpy's own rotation re-layout, native maximize). Once
        /// the rect has been stable for SettleMs, ratio-locked windows are
        /// reshaped once so the client matches the video aspect inside their
        /// current bounds. Uncovered screen area stays desktop - the
        /// fullscreen-fit look with no letterbox bars.</summary>
        private void TrackExternalChange(Rectangle wr)
        {
            if (!_haveLastRect || wr != _lastRect)
            {
                _lastRect = wr;
                _haveLastRect = true;
                _settleSince = Environment.TickCount;
            }
            if (_settleSince < 0 || _resizing || _moving || _fakedMax) return;
            if (Environment.TickCount - _settleSince < SettleMs) return;
            _settleSince = -1;                     // one-shot per settle
            if (RatioLock) ConvergeToVideoAspect(wr);
            // Flex: NOTHING. The window is a plain Windows window - exactly
            // the size the user dragged, no snapping, no aspect chasing, no
            // fit to whatever the app reports (2026-09-06 user decision:
            // "我不想要这种跳跃的设计"). Whatever the app does inside its
            // display bounds is its own business; the window never moves
            // itself.
        }

        /// <summary>Reshape the window so its client area exactly matches
        /// the video aspect, fitted inside the current rect and centered
        /// there. Tolerates a couple of pixels so SDL size-snapping does not
        /// trigger endless corrections; skips the window right after a video
        /// size change while scrcpy may still be re-laying out itself.</summary>
        private void ConvergeToVideoAspect(Rectangle wr)
        {
            double a = VideoAspect();
            if (a <= 0) return;
            if (Environment.TickCount - _videoChangedAt < 500) return;
            Rectangle client = ClientRect();
            int cxL = client.Left - wr.Left;
            int cxT = client.Top - wr.Top;
            int cxR = wr.Right - client.Right;
            int cxB = wr.Bottom - client.Bottom;
            int cw = client.Width, ch = client.Height;
            if (cw <= 0 || ch <= 0) return;
            double tol = Math.Max(2.0, 0.015 * Math.Min(cw, ch));
            if (Math.Abs(cw - a * ch) <= tol) return;
            int nw = (int)Math.Round(Math.Min((double)cw, ch * a));
            int nh = (int)Math.Round(nw / a);
            double ccx = client.Left + cw / 2.0;
            double ccy = client.Top + ch / 2.0;
            int x = (int)Math.Round(ccx - nw / 2.0) - cxL;
            int y = (int)Math.Round(ccy - nh / 2.0) - cxT;
            // A native maximize (Win+Up / snap) letterboxes with black bars;
            // leave the maximized state, then apply the aspect-fit rect so
            // the uncovered bands return to visible desktop.
            if (NativeMethods.IsZoomed(_hwnd))
                NativeMethods.ShowWindow(_hwnd, 9 /*SW_RESTORE*/);
            NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                x, y, nw + cxL + cxR, nh + cxT + cxB,
                0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/);
            Log.Write("converged to video aspect " + nw + "x" + nh);
        }

        // ---- G2 corner region (quartic superellipse) ----------------------

        /// <summary>Clip the target window with a G2-continuous rounded
        /// outline: one quadrant of |x/a|^4 + |y/a|^4 = 1 per corner, joined
        /// tangentially to the straight edges (curvature 0 at the joins).
        /// Hard-edged (GDI regions are 1-bit).
        ///
        /// Perf contract: regions are window-relative, so MOVES never need a
        /// re-apply (position-only changes are deduped away). During size
        /// churn the region is removed outright (square corners, zero stale-
        /// clip flicker) and re-applied once 300ms after the size settles -
        /// SetWindowRgn storms while dragging were the stutter source.</summary>
        private void ApplyCornerRegion()
        {
            if (_cornerDip <= 0) return;
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            if (NativeMethods.IsIconic(_hwnd)) return;
            Rectangle wr = WindowRect();
            NativeMethods.RECT e;
            NativeMethods.DwmGetWindowAttribute(_hwnd, 9 /*EXTENDED_FRAME_BOUNDS*/,
                out e, Marshal.SizeOf(typeof(NativeMethods.RECT)));
            int x0 = e.Left - wr.Left, y0 = e.Top - wr.Top;
            int x1 = e.Right - wr.Left, y1 = e.Bottom - wr.Top;
            _visibleRect = new Rectangle(e.Left, e.Top, x1 - x0, y1 - y0);
            Size sz = wr.Size;
            if (sz != _lastRegionSize)
            {
                _lastRegionSize = sz;
                _regionSettleAt = Environment.TickCount + 300;
            }
            if (Environment.TickCount < _regionSettleAt)
            {
                if (!_regionOff)
                {
                    _regionOff = true;
                    NativeMethods.SetWindowRgn(_hwnd, IntPtr.Zero, false);
                    Log.Write("region defer sz=" + sz.Width + "x" + sz.Height
                        + " dip=" + _cornerDip);
                }
                return;
            }
            if (!_regionOff && _lastAppliedSize == sz) return;   // applied already
            if (x1 - x0 < 8 || y1 - y0 < 8) return;
            int r = Math.Min(S(_cornerDip), Math.Min(x1 - x0, y1 - y0) / 2);
            if (r <= 1) return;
            List<NativeMethods.PT> pts = new List<NativeMethods.PT>(80);
            AddCornerArc(pts, x0 + r, y0 + r, -1, -1, r, true);    // TL: top -> left
            AddCornerArc(pts, x0 + r, y1 - r, -1, 1, r, false);    // BL: left -> bottom
            AddCornerArc(pts, x1 - r, y1 - r, 1, 1, r, true);      // BR: bottom -> right
            AddCornerArc(pts, x1 - r, y0 + r, 1, -1, r, false);    // TR: right -> top
            NativeMethods.PT[] array = pts.ToArray();
            IntPtr rgn = NativeMethods.CreatePolygonRgn(array, array.Length, 1 /*ALTERNATE*/);
            if (rgn != IntPtr.Zero)
            {
                NativeMethods.SetWindowRgn(_hwnd, rgn, false);
                _regionOff = false;
                _lastAppliedSize = sz;
                ApplySingleFrameStyle();
                Log.Write("region applied " + sz.Width + "x" + sz.Height
                    + " r=" + r);
            }
            else
            {
                Log.Write("region: CreatePolygonRgn failed");
            }
        }

        /// <summary>While the G2 region owns the outline, kill the two extra
        /// frame layers DWM would draw: the 1px border color and the 8px
        /// system corner rounding (they stack as visible double borders
        /// around the region cut). The AA corner masks provide the edge.</summary>
        private void ApplySingleFrameStyle()
        {
            if (_hwnd == IntPtr.Zero) return;
            int none = unchecked((int)0xFFFFFFFE);   // DWMWA_COLOR_NONE
            NativeMethods.DwmSetWindowAttribute(_hwnd,
                34 /*DWMWA_BORDER_COLOR*/, ref none, 4);
            int dontRound = 1;                       // DWMWCP_DONOTROUND
            NativeMethods.DwmSetWindowAttribute(_hwnd,
                33 /*DWMWA_CORNER_PREFERENCE*/, ref dontRound, 4);
        }

        /// <summary>Stroke the AA hairline over the region's stair-stepped
        /// corner edges (small click-through layered squares). Called every
        /// tick: moves only reposition the squares (no re-render); the masks
        /// stay hidden while the region is temporarily off (resize).</summary>
        private void SyncMasks()
        {
            if (_cornerDip <= 0 || _hwnd == IntPtr.Zero || _regionOff)
            {
                foreach (CornerMask mask in _masks)
                {
                    if (mask.Visible) mask.HideMask();
                }
                return;
            }
            if (_visibleRect.Width <= 0 || _visibleRect.Height <= 0) return;
            float dpi = _masks[0].DeviceDpi / 96f;
            int r = Math.Min(S(_cornerDip),
                Math.Min(_visibleRect.Width, _visibleRect.Height) / 2);
            foreach (CornerMask mask in _masks)
                mask.SyncTo(_visibleRect, r, dpi);
        }

        /// <summary>Append one superellipse quadrant (16 samples). The point
        /// at parameter t is (cx + sx*r*cos(t)^0.5, cy + sy*r*sin(t)^0.5);
        /// |cos t|^4-style check: (cos^0.5 t)^4 + (sin^0.5 t)^4 = 1 on the
        /// curve. ``reverse`` only fixes the traversal direction so the
        /// polygon stays simple (clockwise around the window).</summary>
        private static void AddCornerArc(
            List<NativeMethods.PT> pts, int cx, int cy, int sx, int sy, int r, bool reverse)
        {
            const int steps = 16;
            for (int i = 0; i <= steps; i++)
            {
                int k = reverse ? steps - i : i;
                double t = (Math.PI / 2) * k / steps;
                double u = Math.Sqrt(Math.Cos(t));
                double v = Math.Sqrt(Math.Sin(t));
                NativeMethods.PT p;
                p.X = cx + (int)Math.Round(sx * r * u);
                p.Y = cy + (int)Math.Round(sy * r * v);
                if (pts.Count > 0)
                {
                    NativeMethods.PT last = pts[pts.Count - 1];
                    if (last.X == p.X && last.Y == p.Y) continue;
                }
                pts.Add(p);
            }
        }

        // ---- session log tailer: live video size --------------------------

        private void StartLogTailer(string path)
        {
            if (path == null || path.Length == 0) return;
            _logThread = new Thread(delegate() { TailLoop(path); });
            _logThread.IsBackground = true;
            _logThread.Start();
        }

        private void TailLoop(string path)
        {
            FileStream fs = null;
            for (int waited = 0; fs == null && waited < 60000 && !_disposed;
                 waited += 500)
            {
                try
                {
                    fs = new FileStream(path, FileMode.Open, FileAccess.Read,
                        FileShare.ReadWrite);
                }
                catch (IOException) { Thread.Sleep(500); }
                catch (UnauthorizedAccessException) { Thread.Sleep(500); }
            }
            if (fs == null)
            {
                Log.Write("log tailer gave up: " + path);
                return;
            }
            Log.Write("log tailer attached: " + path);
            byte[] buf = new byte[4096];
            Decoder dec = Encoding.UTF8.GetDecoder();
            StringBuilder line = new StringBuilder();
            while (!_disposed)
            {
                int n;
                try { n = fs.Read(buf, 0, buf.Length); }
                catch (IOException) { break; }
                if (n > 0)
                {
                    char[] chars = new char[dec.GetCharCount(buf, 0, n)];
                    dec.GetChars(buf, 0, n, chars, 0);
                    for (int i = 0; i < chars.Length; i++)
                    {
                        if (chars[i] == '\n')
                        {
                            HandleLogLine(line.ToString());
                            line.Length = 0;
                        }
                        else line.Append(chars[i]);
                    }
                }
                else Thread.Sleep(200);
            }
        }

        /// <summary>scrcpy emits "INFO: Texture: 2400x3392" on stderr at
        /// default verbosity on every video size change, rotation included
        /// (verified live, scrcpy 4.1). The session log captures stderr.</summary>
        private void HandleLogLine(string s)
        {
            // scrcpy logs the virtual display id once, e.g.
            // "[server] INFO: New display: virtual display id 3 (...)",
            // so the chin can target the in-session virtual desktop.
            int nd = s.IndexOf("New display:");
            if (nd >= 0)
            {
                int idAt = s.IndexOf("id=", nd, StringComparison.Ordinal);
                if (idAt >= 0)
                {
                    int digits = idAt + 3;
                    while (digits < s.Length && char.IsDigit(s[digits])) digits++;
                    int id;
                    if (int.TryParse(s.Substring(idAt + 3, digits - idAt - 3), out id)
                        && id != _vdDisplayId)
                    {
                        _vdDisplayId = id;
                        Log.Write("virtual display id=" + id);
                        // 一次性方向忽略锁（2026-09-06 晚定稿）：--flex-display
                        // 的虚拟屏建屏自带 ROTATES_WITH_CONTENT，APP 方向请求
                        // 会旋转显示→scrcpy 重申窗口形状→乒乓风暴（上午全天
                        // A/B 根因）。锁死后方向请求被 WM 层忽略：显示尺寸
                        // 只随窗口（原生填满），APP 自己适配或自挔黑边。
                        // "1" 而非 "true"：wm 命令按 int 解析（真机实测）。
                        AdbShell("wm set-ignore-orientation-request -d "
                            + id + " 1");
                    }
                }
                return;
            }
            int at = s.IndexOf("Texture:");
            if (at < 0) return;
            string rest = s.Substring(at + 8).Trim();
            int x = rest.IndexOf('x');
            if (x <= 0) return;
            int w, h;
            if (!int.TryParse(rest.Substring(0, x).Trim(), out w)) return;
            if (!int.TryParse(rest.Substring(x + 1).Trim(), out h)) return;
            if (w <= 0 || h <= 0) return;
            if (w == _videoW && h == _videoH) return;
            _videoW = w;
            _videoH = h;
            // 2026-09-09 钉扎武装判定（Win+左右 snap 被弹回的根因修复，
            // 见 EnforceFlexPin）：flex 的 Texture 大多是“显示跟随窗口”的
            // 回声（窗口先动、显示后到，视频比例 ≈ 当前客户区比例），它
            // 不该武装弹回；只有【视频比例 ≠ 客户区比例】的 Texture 才是
            // 真旋转/真重排（scrcpy 即将自动改窗），才设 _videoChangedAt
            // 打开弹回窗口。比例相对差 >5% 判旋转（客户区含 caption/
            // 边框开销，跟随回声也有小幅比例差）；窗口还不可用时保守
            // 武装（维持旧保护语义）。ConvergeToVideoAspect 的 500ms 节流
            // 只吃武装行，语义不变。
            double videoAspect = (double)w / h;
            double clientAspect = ClientAspectSafe();
            bool rotationLike = clientAspect <= 0.0
                || Math.Abs(videoAspect - clientAspect)
                   / Math.Max(videoAspect, clientAspect) > 0.05;
            if (rotationLike) _videoChangedAt = Environment.TickCount;
            Log.Write("video size from log: " + w + "x" + h
                + (rotationLike ? " (rotation-like, pin armed)"
                                : " (follow echo, pin not armed)"));
        }

        /// <summary>Current client aspect of the video window (0.0 when
        /// unavailable). Tailer-thread side of the pin-arm judgment in
        /// HandleLogLine: Win32 rect queries are safe cross-thread.</summary>
        private double ClientAspectSafe()
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return 0.0;
            try
            {
                NativeMethods.RECT c;
                NativeMethods.GetClientRect(_hwnd, out c);
                if (c.Right <= 0 || c.Bottom <= 0) return 0.0;
                return (double)c.Right / c.Bottom;
            }
            catch { return 0.0; }
        }

        public void AdbKey(int code)
        {
            try
            {
                Process p = new Process();
                p.StartInfo.FileName = _adb;
                p.StartInfo.Arguments = "-s " + _serial + " shell input keyevent " + code;
                p.StartInfo.CreateNoWindow = true;
                p.StartInfo.UseShellExecute = false;
                p.Start();
                Log.Write("keyevent " + code + " sent");
            }
            catch (Exception ex) { Log.Write("keyevent failed: " + ex.Message); }
        }

        /// <summary>Run one ``adb shell`` command line (fire and forget);
        /// used for the display-targeted HOME that opens the virtual
        /// desktop. Mirrors AdbKey's lifecycle: never throws into the UI.</summary>
        private void AdbShell(string args)
        {
            try
            {
                Process p = new Process();
                p.StartInfo.FileName = _adb;
                p.StartInfo.Arguments = "-s " + _serial + " shell " + args;
                p.StartInfo.CreateNoWindow = true;
                p.StartInfo.UseShellExecute = false;
                p.Start();
                Log.Write("shell: " + args);
            }
            catch (Exception ex) { Log.Write("shell failed: " + ex.Message); }
        }

        /// <summary>The chin ring's long-press action. Physical mirroring
        /// (home enabled + display-mode mirror) sends HOME to the phone's
        /// launcher. A virtual display (flex/fixed) has NO launcher to go
        /// home to: keyevent 3 there makes Android raise the system
        /// launcher's all-apps picker on the mirrored display (the reported
        /// "confusing app selector"), and HOME on the physical display
        /// instead would wake/alter the phone behind --turn-screen-off.
        /// Closing the session window is the honest equivalent of "back to
        /// desktop" on the PC side: scrcpy exits cleanly through WM_CLOSE
        /// and the CLI tears the session down. (KISS tradeoff over
        /// display-targeted HOME: `input keyevent --display` needs the
        /// virtual display id, which scrcpy 4.1 does not surface to us.)
        /// Note the gate is the DISPLAY TYPE, not the home flag alone:
        /// `duo mirror --chrome` without --app runs a flex display with
        /// home=1, and must also close rather than send keyevent 3.</summary>
        public void ChinHold()
        {
            // Long-press = HOME everywhere. On a virtual display the bare
            // keyevent lands on the physical screen, so instead the session's
            // virtual desktop (secondary-display launcher) is opened with a
            // display-targeted HOME intent - that page IS the feature the
            // user asked to keep (2026-09-06). Mirror mode keeps keyevent 3.
            if (!_displayMode.Equals("mirror") && _vdDisplayId >= 0)
            {
                AdbShell("am start --display " + _vdDisplayId
                    + " -a android.intent.action.MAIN"
                    + " -c android.intent.category.HOME");
                return;
            }
            AdbKey(3);
        }

        /// <summary>Close the mirrored window (WM_CLOSE): scrcpy exits
        /// cleanly and the CLI's finally block stops this overlay and
        /// releases the audio lock. Same path as the capsule's close
        /// button.</summary>
        public void CloseSessionWindow()
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            Log.Write("session close requested (virtual-display home)");
            NativeMethods.PostMessageW(_hwnd, 0x0010 /*WM_CLOSE*/, IntPtr.Zero, IntPtr.Zero);
        }

        public void TopAction(int index)
        {
            if (index == 0) NativeMethods.ShowWindow(_hwnd, 6 /*SW_MINIMIZE*/);
            else if (index == 1) FakeMaximize(!_fakedMax || _fakedMode != 1, true);
            else if (index == 2) FakeMaximize(!_fakedMax || _fakedMode != 2, false);
            else if (index == 3)
                NativeMethods.PostMessageW(_hwnd, 0x0010 /*WM_CLOSE*/, IntPtr.Zero, IntPtr.Zero);
        }

        // -- main tick ---------------------------------------------------------

        private void Tick(object sender, EventArgs e)
        {
            try
            {
                TickInner();
            }
            catch (Exception ex)
            {
                Log.Write("tick error (kept alive): " + ex.Message);
            }
            if (++_ticks % 100 == 0)
            {
                Log.Write("alive #" + _ticks + " cornerDip=" + _cornerDip
                    + " regionOff=" + _regionOff
                    + " appliedSz=" + _lastAppliedSize);
                // SDL can re-assert its own styles on some events; keep the
                // resize frame alive without user-visible work.
                if (_hwnd != IntPtr.Zero && NativeMethods.IsWindow(_hwnd))
                {
                    int s = NativeMethods.GetWindowLong(_hwnd, -16);
                    // C2: SDL can re-assert its own borderless style on some
                    // events; keep BOTH the resize frame and the native-mode
                    // caption alive. 2026-09-09: the caption check watches the
                    // FULL style family (CAPTION|SYSMENU|MIN|MAXBOX) plus
                    // WS_POPUP re-growth - a bare WS_CAPTION strip is the
                    // "见不到相关的内容" regression, so partial styles
                    // must re-repair too.
                    const int WS_THICKFRAME = 0x00040000;
                    const int WS_POPUP = unchecked((int)0x80000000);
                    const int WS_CAPTION = 0x00C00000;
                    const int WS_SYSMENU = 0x00080000;
                    const int WS_MINIMIZEBOX = 0x00020000;
                    const int WS_MAXIMIZEBOX = 0x00010000;
                    int captionMask = WS_CAPTION | WS_SYSMENU
                        | WS_MINIMIZEBOX | WS_MAXIMIZEBOX;
                    bool frameLost = (s & WS_THICKFRAME) == 0;
                    bool captionIncomplete = TopNative
                        && ((s & captionMask) != captionMask
                            || (s & WS_POPUP) != 0);
                    if (frameLost || captionIncomplete) Repair();
                }
            }
        }

        private void TickInner()
        {
            // Gestures are event-driven from MouseMove (see EdgeStrip);
            // this poll is only a fallback for stalled events, and both
            // updates dedupe so the fallback is a no-op when the event
            // path is already current.
            if (_resizing) UpdateResize();
            else if (_moving) UpdateMove();
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd))
            {
                HideBars();          // never linger bars over a dead window
                HideStrips();        // ...and never leave hot zones behind
                Discover();
                return;
            }
            if (NativeMethods.IsIconic(_hwnd) || !NativeMethods.IsWindowVisible(_hwnd))
            {
                HideBars();
                HideStrips();
                return;
            }

            Rectangle client = ClientRect();
            Point cursor = CursorPosition();
            // 启动沉浸门（见字段注释）：静止光标不武装距离露出；真实移动
            // 一次后永久生效（含悬停不动的正常露出）。
            if (!_cursorMoved)
            {
                if (_cursorAnchor.X != int.MinValue
                    && Math.Abs(cursor.X - _cursorAnchor.X)
                       + Math.Abs(cursor.Y - _cursorAnchor.Y) >= S(2))
                    _cursorMoved = true;
                _cursorAnchor = cursor;
            }
            // 2026-09-09 组合修复：overBars 只统计【可见】巴的矩形。
            // 旧代码用隐藏巴的残留 Bounds——上巴 none 时胶囊从未 Show，
            // Bounds 停在屏幕原点 (0,0)+尺寸，成为一块幽灵热区：光标
            // 停在原点附近窗口内时 engaged 被它独自保活，且下巴
            // immersive 被 overBars 强制弹出（光标明明在窗口顶部）。
            // 加 .Visible 门后：触发带 ⊂ 巴自身矩形的保活论证不变
            // （一旦露出即可见，包含光标即维持）；隐藏巴不再参与
            // engaged/露出判定。
            bool overBars = (_chin.Visible && _chin.Bounds.Contains(cursor))
                         || (_top.Visible && _top.Bounds.Contains(cursor));
            bool overStrips = false;
            // 同款 .Visible 门：隐藏热区（HideStrips 后 Bounds 残留）
            // 不再独自保活 engaged——与 overBars 同一幽灵矩形 bug 类。
            foreach (EdgeStrip strip in _strips)
                if (strip.Visible && strip.Bounds.Contains(cursor)) overStrips = true;
            if (_sides != null)
            {
                foreach (SideBandWindow band in _sides)
                    if (band.Visible && band.Bounds.Contains(cursor)) overStrips = true;
            }
            // Deep binding: affordances exist only while the scrcpy window
            // truly owns the screen under the cursor (or is foreground, or
            // the cursor is over our own bars/strips). When another app
            // covers the window, everything must vanish - no floating chrome
            // above someone else's fullscreen app.
            NativeMethods.POINT probe;
            probe.X = cursor.X; probe.Y = cursor.Y;
            IntPtr rootAtCursor = NativeMethods.GetAncestor(
                NativeMethods.WindowFromPoint(probe), 2 /*GA_ROOT*/);
            bool foreground = NativeMethods.GetAncestor(
                NativeMethods.GetForegroundWindow(), 2 /*GA_ROOT*/) == _hwnd;
            bool engaged = foreground || rootAtCursor == _hwnd
                || overBars || overStrips || _resizing || _moving;
            Rectangle wr = WindowRect();
            EnforceFlexPin(wr);   // window never follows display rotation
            // Window-state duties run regardless of engagement: the corner
            // region must settle even when the cursor is away, and the
            // aspect convergence must see external changes while idle.
            TrackExternalChange(wr);
            ApplyCornerRegion();   // per-tick: settles the deferred region
            if (!engaged)
            {
                HideBars();
                HideStrips();      // strips AND corner masks
                return;
            }
            SyncStrips(wr);
            // Strip rects are pairwise disjoint (see SyncStrips), so click
            // routing needs no z-order assertion. The top-right capsule is
            // the one window that intentionally floats INSIDE the band's
            // rect (trio #2 made the band full-width): its painted pill is
            // hit-testable and stacked above the strips, so its buttons win
            // every click while the band keeps serving the rest of the
            // title area - the same stacking as a native caption under its
            // own buttons.
            SyncMasks();
            // Side move bands: the strips' own tracking contract - synced
            // on the tick here, and live through the LOCATIONCHANGE hook
            // below while the window itself is being dragged around.
            SyncSideBands(wr, client);

            // Per-bar proximity rules, symmetric like a native window's own
            // affordances: capsule reveals near the top edge, the mBack dot
            // near the bottom edge. Neither cares about focus.
            // Native bars are always-on while engaged (agy v6 常驻可见);
            bool showTop = TopNative ? true
                : (TopNone ? false
                : (_cursorMoved && ComputeTopVisibility(client, wr, cursor)));
            bool showChin = BottomNative ? true
                : (BottomNone ? false
                : (_cursorMoved && ComputeChinVisibility(client, wr, cursor))
                || overBars || _resizing || _moving);

            SyncChin(client, showChin);
            if (TopNative) SyncTop(client, showTop);
            else if (showTop && !_top.Visible)
            {
                _top.Left = client.Right - _top.Width - S(TopMargin);
                _top.Top = client.Top + S(TopMargin);
                SampleTop(true);   // trio #1: acrylic lands in the first frame
                _top.Show();
                _top.Render();
                Log.Write("top shown at " + _top.Left + "," + _top.Top
                    + " " + _top.Width + "x" + _top.Height);
            }
            else if (!showTop && _top.Visible)
            {
                _top.Hide();
                Log.Write("top hidden");
            }
            if (BottomNative) SampleNativeChin();
            if (!TopNative && !TopNone) SampleTop(false);   // capsule acrylic, ~300ms cadence

            // Sandwich z-order, re-asserted while engaged: whatever covers
            // the video window must cover the chrome too (bug report:
            // always-on-top bars kept floating above a covering terminal);
            // and the video window being RAISED on activation jumps it past
            // its own overlays - undone here within one tick, instantly by
            // the foreground hook.
            RestackOverlays();

            DropStaleFakeMax();
            // PrintWindow sampling now serves exactly one surface: the
            // immersive capsule's acrylic base plate (SampleTop above -
            // reveal-time + ~300ms cadence, never per frame). The bars
            // themselves stay flat glass / OS-drawn, so nothing else
            // samples and the tick stays cheap between refreshes.
        }

        private void HideBars()
        {
            if (_chin.Visible || _top.Visible) Log.Write("bars hidden");
            _chin.Hide();
            _top.Hide();
        }

        private void HideStrips()
        {
            foreach (EdgeStrip strip in _strips)
            {
                if (strip.Visible) strip.Hide();
            }
            foreach (CornerMask mask in _masks) mask.HideMask();
            // Side move bands hide with the other hot zones (dead window,
            // minimized, disengaged) - never a floating drag strip over
            // someone else's screen.
            HideSideBands();
        }

        /// <summary>Keep the edge hot-zones glued to the window frame. The
        /// strips are always on (invisible, riding directly above the video
        /// window - see RestackOverlays): a normal window is
        /// resizable regardless of focus. Sizes adapt to small windows
        /// instead of bailing out below a fixed pixel floor (the old 800px
        /// cutoff silently disabled resize on small windows).</summary>
        private void SyncStrips(Rectangle wr)
        {
            int span = Math.Min(wr.Width, wr.Height);
            if (span < 12) return;
            int edge = Math.Max(2, Math.Min(S(6), span / 6));
            int corner = Math.Max(edge + 2, Math.Min(S(18), span / 3));
            int sideLen = Math.Max(0, wr.Height - 2 * corner);
            int topLen = Math.Max(0, wr.Width - 2 * corner);
            Place(_strips[0], wr.Left, wr.Top + corner, edge, sideLen);
            Place(_strips[1], wr.Right - edge, wr.Top + corner, edge, sideLen);
            // C2: with the real caption the system's caption-button cluster
            // (plus our 4th button left of it) owns the top-right corner -
            // the top strip stops short of the cluster and the top-right
            // corner zone is parked, so no overlay pixel steals a click or
            // drag meant for a system caption button.
            int cluster = TopNative ? 4 * _top.CapButtonWidth : 0;
            Place(_strips[2], wr.Left + corner, wr.Top,
                Math.Max(0, Math.Min(topLen, wr.Width - 2 * corner - cluster)), edge);
            Place(_strips[3], wr.Left, wr.Top, corner, corner);
            if (TopNative)
            {
                if (_strips[4].Visible) _strips[4].Hide();
            }
            else Place(_strips[4], wr.Right - corner, wr.Top, corner, corner);
            Place(_strips[5], wr.Left, wr.Bottom - edge, topLen, edge);
            Place(_strips[6], wr.Left, wr.Bottom - corner, corner, corner);
            Place(_strips[7], wr.Right - corner, wr.Bottom - corner, corner, corner);
            // Caption move band (edge 0): the native title-bar layout -
            //   top sliver (edge px)   -> top edge resize
            //   band below it (24 DIP) -> window move, plain arrow cursor
            //   four corners           -> corner resize
            // User-feedback trio #2: the band spans the ENTIRE width
            // between the corner zones (the old central-half split is
            // retired) - press and drag ANYWHERE in the top band moves the
            // window, exactly like a native title bar. Priorities stay
            // positional and disjoint, no z-order bookkeeping: the 6px
            // sliver ABOVE the band resizes, the corner zones resize, and
            // the hover capsule floats ABOVE the band so its three buttons
            // keep their clicks (a button hit wins before the band ever
            // sees the press). The span clamp only bites on degenerate
            // tiny windows.
            // Direction disambiguation tie-in: a vertical drag that started
            // on this band is replayed to the video as an Android shade
            // pull (EdgeStrip/ShadeCaption), which needs the band to stay
            // HIDDEN for the whole gesture - the periodic Place below
            // would otherwise re-show it under the cursor mid-drag and
            // steal the real WM_MOUSEMOVEs the video window must see.
            // The hidden strip cannot see the MouseUp, so hold the hide
            // while the left button is still physically down and let the
            // first tick after release restore the band.
            int bandH = Math.Min(S(24), span / 2);
            int bandW = Math.Max(0, wr.Width - 2 * corner);
            EdgeStrip caption = _strips[8];
            if (TopNative)
            {
                // C2: with a real WS_CAPTION the system title bar owns the
                // band's duties (drag-to-move, double-click maximize); a
                // floating band above it would eat caption drags AND replay
                // plain taps as Android touches into a real title bar.
                // Park it (hidden) - the band is an immersive-mode tool.
                if (caption.Visible) caption.Hide();
                return;
            }
            if (caption.ShadeHold)
            {
                if ((NativeMethods.GetAsyncKeyState(0x01 /*VK_LBUTTON*/) & 0x8000) != 0)
                    return;   // mid-shade: leave the band parked (hidden)
                caption.ShadeHold = false;
            }
            Place(caption, wr.Left + corner, wr.Top + edge,
                bandW, bandH);
        }

        private static void Place(Form f, int x, int y, int w, int h)
        {
            Rectangle want = new Rectangle(x, y, w, h);
            if (f.Bounds != want) f.Bounds = want;
            if (!f.Visible) f.Show();
        }

        /// <summary>Glue the side move bands to the video window's left and
        /// right edges. Geometry, all deliberate overlaps avoided:
        ///   vertical   - strictly BELOW the top caption band (edge sliver
        ///                + band) and below the hover capsule's berth
        ///                (which pokes under the band), and strictly ABOVE
        ///                the chin reservation at the client bottom
        ///                (immersive bottom mode only - native glues
        ///                below the window, none never shows);
        ///   horizontal - INSIDE the ~6 DIP edge-resize strips, so the
        ///                outermost sliver keeps resizing and the band
        ///                inside it moves.
        /// Net effect: no side-band rect overlaps any other affordance, so
        /// clicks route positionally with zero z-order bookkeeping - the
        /// SyncStrips contract. The edge/bandH formulas mirror SyncStrips
        /// by hand; keep them in step. Immersive top mode only: _sides is
        /// null under a native top (mode gate at birth).</summary>
        private void SyncSideBands(Rectangle wr, Rectangle client)
        {
            if (_sides == null) return;                     // native top
            int span = Math.Min(wr.Width, wr.Height);
            if (span < 12) return;                         // degenerate window
            int edge = Math.Max(2, Math.Min(S(6), span / 6));   // as SyncStrips
            int bandH = Math.Min(S(24), span / 2);              // as SyncStrips
            int w = S(SideBandWindow.LogicalWidth);
            // 上巴 none 时胶囊永不出现，不留胶囊泊位（多 ~10 DIP 可拖
            // 边）；immersive 顶照旧让出胶囊 berth。TopNative 时 _sides
            // 为 null，不会走到这。
            int capsuleBerth = TopNone ? 0 : S(TopMargin) + _top.Height;
            int top = Math.Max(wr.Top + edge + bandH,       // below 顶带
                client.Top + capsuleBerth);                 // below capsule
            // 2026-09-09 组合修复：只有下巴 immersive 才浮在窗内底缘，
            // 需要预留；下巴 native 贴在窗口下方（不占窗内侧带）、
            // none 永不出现——两者侧带直下到 client.Bottom，不再白
            // 白短一截可拖边。
            int reserve = BottomNone || BottomNative ? 0 : _chin.BarHeight;
            int h = client.Bottom - reserve - top;           // above 下巴
            if (h <= 0) { HideSideBands(); return; }
            _sides[0].SyncTo(new Rectangle(wr.Left + edge, top, w, h));
            _sides[1].SyncTo(new Rectangle(wr.Right - edge - w, top, w, h));
        }

        /// <summary>Hook-side twin of SyncSideBands: re-resolves the rects
        /// itself (the LOCATIONCHANGE callback only carries the client)
        /// so the bands track live drags at hook cadence, like the chin.</summary>
        private void SyncSideBands()
        {
            if (_sides == null) return;
            SyncSideBands(WindowRect(), ClientRect());
        }

        private void HideSideBands()
        {
            if (_sides == null) return;
            foreach (SideBandWindow band in _sides)
                if (band.Visible) band.Hide();
        }

        /// <summary>Insert one overlay surface directly above the video
        /// window, same z band - the surface rises and sinks WITH the
        /// video window instead of floating above whatever covers it.
        /// Z-only (NOSIZE | NOMOVE | NOACTIVATE): geometry stays with the
        /// sync passes.
        ///
        /// 2026-09-09 真机根因（用户报告：沉浸式上下巴“没有相关的内容”、
        /// 功能不生效；上巴系统+下巴沉浸同样“见不到内容”）：
        /// SetWindowPos 的 hWndInsertAfter 语义是“该窗口位于被定位窗口
        /// 之上”——旧代码传 _hwnd（视频窗）当插入点，实际把每个 overlay
        /// 面插到了视频窗【下方】，整层 chrome 被视频盖死（真机枚举：
        /// video rank=31、胶囊 rank=54；直接调 API 复现：form 33→59）。
        /// 正确做法：插入点取视频窗【上面那个窗】（GW_HWNDPREV），被定位
        /// 窗口就落在它与视频窗之间 = 紧贴视频之上；视频窗已在带顶时
        /// GW_HWNDPREV 返回 NULL（= HWND_TOP）仍正确。逐次调用的堆叠
        /// 语义与 RestackOverlays 注释一致：先调者最终最高。</summary>
        private void InsertAbove(Form f)
        {
            if (!f.IsHandleCreated || !f.Visible) return;
            IntPtr above = NativeMethods.GetWindow(_hwnd, 3 /*GW_HWNDPREV*/);
            NativeMethods.SetWindowPos(f.Handle, above, 0, 0, 0, 0,
                0x0001 /*SWP_NOSIZE*/ | 0x0002 /*SWP_NOMOVE*/
                | 0x0010 /*SWP_NOACTIVATE*/);
        }

        /// <summary>Sandwich z-order (bug report: 层级与视频窗脱节 - the
        /// bars were always-on-top orphans: a covering window hid the video
        /// but NOT the chrome, which kept floating above it, 孤零零).
        /// Every visible surface is re-inserted directly above the video
        /// window, so the whole sandwich - video + hot zones + side bands
        /// + chin + capsule - presents as ONE complete window: whatever
        /// covers the video covers the chrome, and the taskbar (a genuine
        /// topmost window) can never be covered by our bars either.
        /// Insertion order IS the stacking: each call lands its window
        /// directly above the video and pushes the earlier ones one slot
        /// up, so the FIRST call ends up highest - capsule above the
        /// caption band / side bands, chin above the bottom strip, masks
        /// hugging the video itself. Called every engaged tick (raising
        /// the video on activation is undone within 50ms) and instantly
        /// from the EVENT_SYSTEM_FOREGROUND hook; when the video window
        /// is minimized or hidden the bars are hidden with it (TickInner
        /// disengage paths) so nothing ever floats alone.</summary>
        private void RestackOverlays()
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            InsertAbove(_top);
            InsertAbove(_chin);
            if (_sides != null)
            {
                foreach (SideBandWindow band in _sides) InsertAbove(band);
            }
            foreach (EdgeStrip strip in _strips) InsertAbove(strip);
            foreach (CornerMask mask in _masks) InsertAbove(mask);
        }

        // 露出/保持带钳进窗口矩形（根因与日志证据见 docs/window-experience.md §10）
        private bool ComputeTopVisibility(Rectangle client, Rectangle wr, Point cursor)
        {
            bool inX = cursor.X >= client.Left && cursor.X < client.Right;
            if (!inX) return _top.Visible && _top.Bounds.Contains(cursor);
            if (cursor.Y >= wr.Top && cursor.Y < client.Top + S(TriggerTop)) return true;
            return _top.Visible && cursor.Y >= wr.Top
                && cursor.Y < client.Top + S(RetainTop);
        }

        private bool ComputeChinVisibility(Rectangle client, Rectangle wr, Point cursor)
        {
            // Bottom-edge twin of the capsule rule (bands clamped into wr).
            bool inX = cursor.X >= client.Left && cursor.X < client.Right;
            // 与上巴判定对称：隐藏下巴的残留矩形不参与保活。
            if (!inX) return _chin.Visible && _chin.Bounds.Contains(cursor);
            if (cursor.Y > client.Bottom - S(TriggerTop) && cursor.Y <= wr.Bottom) return true;
            return _chin.Visible && cursor.Y > client.Bottom - S(RetainTop)
                && cursor.Y <= wr.Bottom;
        }

        private void SyncChin(Rectangle client, bool show)
        {
            _chin.ResyncWidth(client.Width);
            _chin.Left = client.Left;
            _chin.Top = ChinTop(client);
            if (show && !_chin.Visible)
            {
                _chin.Show();
                _chin.Render();
                Log.Write("chin shown");
            }
            else if (!show && _chin.Visible)
            {
                _chin.Hide();
                Log.Write("chin hidden");
            }
            // Hook-driven: keep the capsule glued during moves/resizes too,
            // not just on the 20fps tick.
            if (_top.Visible && !TopNative)
            {
                _top.Left = client.Right - _top.Width - S(TopMargin);
                _top.Top = client.Top + S(TopMargin);
            }
            if (TopNative)
            {
                // C2: the 4th button rides the REAL caption band, which
                // lives ABOVE the client origin (WS_CAPTION) - so it anchors
                // to the DWM-visible bounds, not the client rect, clamped
                // into the monitor work area. Runs here so WinEvent-driven
                // moves (the hook calls SyncChin) track it live; SyncTop
                // handles visibility.
                _top.SyncFourthButton(VisibleBounds(), WorkArea());
            }
            // On-demand rendering: bars re-push only on show / hover /
            // width change; per-tick repaints (full-width bitmap alloc +
            // UpdateLayeredWindow) fought the UI thread during drags.
        }

        /// <summary>Chin Y with taskbar protection (C2 bugfix, user report:
        /// the native chin covered the taskbar at fullscreen / emulated
        /// maximize). The bar normally glues BELOW the video window bottom
        /// (sandwich), but when the video window is fullscreen (window rect
        /// ≈ monitor rect) or its bottom edge leaves no room above the
        /// work-area bottom (视频底缘 + 下巴高 &gt; 工作区底), the bar INSETS
        /// onto the video content instead - y = video bottom − chin height,
        /// pill stays usable, and the bar never leaves the work area. The
        /// immersive chin already rides inside the window: untouched.
        /// Corner ears ride ONLY the below-video glue: there the video
        /// window's DWM-rounded bottom corners notch at the seam; inset
        /// mode sits mid-video (no seam) and turns them off.</summary>
        private int ChinTop(Rectangle client)
        {
            if (!BottomNative) return client.Bottom - _chin.Height;
            Rectangle monitor, work;
            VideoMonitor(out monitor, out work);
            Rectangle wr = WindowRect();
            // fullscreen = the window fills the monitor itself (covers the
            // taskbar); tolerance absorbs the DWM frame insets.
            bool fullscreen = wr.Left <= monitor.Left + S(8)
                && wr.Top <= monitor.Top + S(8)
                && wr.Right >= monitor.Right - S(8)
                && wr.Bottom >= monitor.Bottom - S(8);
            bool noRoom = client.Bottom + _chin.BarHeight > work.Bottom;
            bool inset = fullscreen || noRoom;
            if (inset != _chinInset)
            {
                _chinInset = inset;
                Log.Write(inset
                    ? "chin inset onto video (" + (fullscreen ? "fullscreen" : "no room below")
                      + ": taskbar guard)"
                    : "chin restored below video");
            }
            // Ears are part of the geometry, so they settle here: on only
            // when the video window is DWM-rounded (no G2 region - true
            // under BOTH top modes since the 2026-09-09 corner matrix)
            // AND the bar glues below it - the seam case.
            _chin.SetEars(VideoRounded && !inset);
            // Below-video glue: the 8 DIP ear strip laps UP over the
            // video window's rounded corner notches behind the seam;
            // inset rides mid-video with no ear, flush at the video bottom.
            return inset ? client.Bottom - _chin.BarHeight
                         : client.Bottom - _chin.Ear;
        }

        /// <summary>C2: with the real system caption the overlay top window
        /// is just the 4th button; its position is maintained by SyncChin
        /// (VisibleBounds anchor, shared with the WinEvent move/size hook),
        /// so here we only show/hide on engagement.</summary>
        private void SyncTop(Rectangle client, bool show)
        {
            if (!TopNative) return;
            if (show && !_top.Visible)
            {
                _top.Show();
                _top.Render();
                Log.Write("top native shown " + _top.Width + "x" + _top.Height);
            }
            else if (!show && _top.Visible)
            {
                _top.Hide();
                Log.Write("top native hidden");
            }
        }

        /// <summary>Feed the native chin a live backdrop sample (agy v6):
        /// CopyFromScreen over the chin rect + 8px outward margin, on the
        /// SampleMs cadence while the bar is visible. The capture also
        /// grabs the bar's own layered pixels, so the bar's footprint rows
        /// are replaced with the desktop band below the bar (or the video
        /// band above, at the screen edge) before handoff - after the 1/8
        /// blur the composite is indistinguishable from the true backdrop,
        /// and the self-feedback loop (bar -&gt; capture of bar -&gt; flat
        /// tint) is broken without any hide/blank flicker.</summary>
        private void SampleNativeChin()
        {
            if (!BottomNative || !_chin.Visible) return;
            int now = Environment.TickCount;
            if (now - _lastSample < SampleMs) return;
            _lastSample = now;
            Rectangle r = _chin.Bounds;
            const int Margin = 8;
            Rectangle full = Rectangle.Inflate(r, Margin, Margin);
            full.Intersect(SystemInformation.VirtualScreen);
            if (full.Width < 4 || full.Height < 4) return;
            Bitmap capture;
            try
            {
                capture = new Bitmap(full.Width, full.Height,
                    PixelFormat.Format32bppArgb);
                using (Graphics cg = Graphics.FromImage(capture))
                {
                    cg.CopyFromScreen(full.Left, full.Top, 0, 0,
                        new Size(full.Width, full.Height),
                        CopyPixelOperation.SourceCopy);
                }
            }
            catch (Exception ex)
            {
                Log.Write("native chin capture failed: " + ex.Message);
                return;
            }
            // rows the bar itself covers inside the capture
            int bandTop = Math.Max(0, r.Top - full.Top);
            int bandBottom = Math.Min(full.Height, bandTop + r.Height);
            int srcY = bandBottom < full.Height
                ? bandBottom            // desktop band below the bar
                : (bandTop > 0 ? 0 : -1);   // screen edge: video band above
            if (bandBottom > bandTop && srcY >= 0)
            {
                int srcH = srcY == 0 ? bandTop : full.Height - bandBottom;
                using (Bitmap band = capture.Clone(
                    new Rectangle(0, srcY, capture.Width, srcH), capture.PixelFormat))
                using (Graphics cg = Graphics.FromImage(capture))
                {
                    cg.InterpolationMode = InterpolationMode.Low;
                    cg.PixelOffsetMode = PixelOffsetMode.Half;
                    cg.DrawImage(band, new Rectangle(0, bandTop,
                        capture.Width, bandBottom - bandTop));
                }
            }
            _chin.SetNativeSample(capture);
        }

        /// <summary>User-feedback trio #1: feed the immersive top capsule
        /// its acrylic backdrop sample. PrintWindow grabs the video window
        /// (into the reused full-size bitmap), the capsule's own bounds
        /// are cropped out of it and handed to the bar, whose
        /// DrawCapsuleAcrylic blurs / saturates / tints them into the base
        /// plate. Sampled once when the capsule reveals (force, so the
        /// very first frame is already acrylic) and refreshed on the
        /// CapsuleSampleMs cadence (~300ms) while it stays visible -
        /// never per frame. PrintWindow failures and all-black captures
        /// (D3D quirks) keep the last good sample instead of flashing a
        /// dry capsule.</summary>
        private void SampleTop(bool force)
        {
            if (TopNative) return;
            if (!_top.Visible && !force) return;
            int now = Environment.TickCount;
            if (!force && now - _topSampleAt < CapsuleSampleMs) return;
            _topSampleAt = now;
            Rectangle wr = WindowRect();
            if (wr.Width <= 0 || wr.Height <= 0) return;
            if (_sample == null || _sample.Width != wr.Width || _sample.Height != wr.Height)
            {
                if (_sample != null) _sample.Dispose();
                _sample = new Bitmap(wr.Width, wr.Height, PixelFormat.Format32bppArgb);
            }
            bool ok = false;
            using (Graphics g = Graphics.FromImage(_sample))
            {
                IntPtr hdc = g.GetHdc();
                try { ok = NativeMethods.PrintWindow(_hwnd, hdc, 2 /*FULLCONTENT*/); }
                finally { g.ReleaseHdc(hdc); }
            }
            if (ok) ok = !LooksBlack(_sample);
            if (!ok) return;                     // keep the last good sample
            Bitmap topSample = Crop(_sample, _top.Bounds, wr);
            if (topSample != null) _top.SetSample(topSample);
        }

        private static bool LooksBlack(Bitmap bmp)
        {
            // PrintWindow on D3D surfaces can silently yield black; four
            // probes far from the edges are enough to notice.
            int[] xs = { bmp.Width / 4, 3 * bmp.Width / 4, bmp.Width / 2, bmp.Width / 3 };
            int[] ys = { bmp.Height / 4, 3 * bmp.Height / 4, bmp.Height / 3, bmp.Height / 2 };
            for (int i = 0; i < xs.Length; i++)
            {
                Color c = bmp.GetPixel(xs[i], ys[i]);
                if (c.R + c.G + c.B > 12) return false;
            }
            return true;
        }

        private static Bitmap Crop(Bitmap source, Rectangle screenRect, Rectangle windowRect)
        {
            int sx = Math.Max(0, Math.Min(source.Width - 1, screenRect.X - windowRect.X));
            int sy = Math.Max(0, Math.Min(source.Height - 1, screenRect.Y - windowRect.Y));
            int cw = Math.Min(screenRect.Width, source.Width - sx);
            int ch = Math.Min(screenRect.Height, source.Height - sy);
            if (cw <= 0 || ch <= 0) return null;
            return source.Clone(new Rectangle(sx, sy, cw, ch), PixelFormat.Format32bppArgb);
        }

        // -- discovery + repair -------------------------------------------------

        private void Discover()
        {
            _hwnd = NativeMethods.FindWindowW(null, _title);
            if (_hwnd == IntPtr.Zero)
            {
                _waitedMs += TickMs;
                if (_waitedMs >= FirstWaitMs)
                {
                    Log.Write("giving up: window never appeared");
                    Application.ExitThread();
                }
                return;
            }
            _discoveredAt = Environment.TickCount;
            Log.Write("window found hwnd=0x" + _hwnd.ToString("x"));
            _waitedMs = 0;
            Repair();
            if (_hook == IntPtr.Zero)
            {
                // EVENT_OBJECT_LOCATIONCHANGE (0x800B) fires on location,
                // shape AND size changes - so the bars track moves AND
                // maximize/restore/emulated-maximize live through this hook;
                // the 50ms tick is the fallback poll (SyncChin re-runs the
                // chin's taskbar guard and the 4th-button anchor either way).
                _hookProc = delegate(IntPtr hHook, uint evt, IntPtr hwnd,
                    int idObject, int idChild, uint thread, uint time)
                {
                    // 2026-09-09 组合补全：任意可见面（下巴 OR 胶囊/4键）
                    // 都要活跟踪——旧守卫只认下巴和 native 顶的 4 键，
                    // 沉浸胶囊单独可见时（下巴 none/未露出）钩子不响应，
                    // 拖窗时胶囊只能跟 50ms tick 档位。钩子内部永不点亮
                    // （SyncChin 传 _chin.Visible），SyncTop 仅 TopNative
                    // 有效，放宽容门安全。
                    if (hwnd == _hwnd && idObject == 0 /*OBJID_WINDOW*/ &&
                        _chin.IsHandleCreated &&
                        (_chin.Visible || _top.Visible))
                    {
                        // The handle can be destroyed between the check and
                        // BeginInvoke (form teardown race); never let that kill
                        // the WinEvent hook thread.
                        try
                        {
                            _chin.BeginInvoke((MethodInvoker)delegate
                            {
                                Rectangle liveClient = ClientRect();
                                // 2026-09-09 组合修复：钩子绝不强制点亮任何巴。
                                // 旧代码传 show=true——上巴 native 时拖标题栏连发
                                // LOCATIONCHANGE，下巴 none（永不该露）被这里
                                // 亮起、下一拍 tick 又 HideBars（闪烁）；下巴
                                // immersive 未露出时同样被点亮再熄灭。可见性
                                // 只归 tick 管（模式三态 + 近距露出）；钩子的
                                // 职责只是让已可见的面跟着窗口动（几何同步）。
                                // TopNative 的第 4 键锚定在 SyncChin 内部无条件
                                // 运行，不受 show 值影响。
                                SyncChin(liveClient, _chin.Visible);
                                SyncTop(liveClient, true);
                                SyncSideBands();   // bands follow live drags too
                                RestackOverlays(); // z follows the sandwich too
                            });
                        }
                        catch { }
                    }
                };
                _hook = NativeMethods.SetWinEventHook(0x800B, 0x800B, IntPtr.Zero,
                    _hookProc, 0, 0, 0 /*WINEVENT_OUTOFCONTEXT*/);
            }
            if (_fgHook == IntPtr.Zero)
            {
                // EVENT_SYSTEM_FOREGROUND (0x0003): raising the video window
                // (click, alt-tab, taskbar) moves ONE window - the video -
                // past its own overlays; the sandwich must be re-stacked
                // immediately or the bars blink behind the video for a tick.
                // Fires for every foreground change system-wide; the cheap
                // RestackOverlays no-ops when the order is already right.
                _fgHookProc = delegate(IntPtr hHook, uint evt, IntPtr hwnd,
                    int idObject, int idChild, uint thread, uint time)
                {
                    if (_chin.IsHandleCreated)
                    {
                        try
                        {
                            _chin.BeginInvoke((MethodInvoker)delegate
                            {
                                RestackOverlays();
                            });
                        }
                        catch { }
                    }
                };
                _fgHook = NativeMethods.SetWinEventHook(0x0003, 0x0003, IntPtr.Zero,
                    _fgHookProc, 0, 0, 0 /*WINEVENT_OUTOFCONTEXT*/);
            }
        }

        private void Repair()
        {
            const int GWL_STYLE = -16;
            const int WS_THICKFRAME = 0x00040000;
            const int WS_POPUP = unchecked((int)0x80000000);
            const int WS_CAPTION = 0x00C00000;
            const int WS_SYSMENU = 0x00080000;
            const int WS_MINIMIZEBOX = 0x00020000;
            const int WS_MAXIMIZEBOX = 0x00010000;
            int style = NativeMethods.GetWindowLong(_hwnd, GWL_STYLE);
            // C2: native top bar = a REAL system caption on the video
            // window. 2026-09-09 架构定稿（真机三轮实测）：上巴 native
            // 时 duo 根本不给 scrcpy 传 --window-borderless——scrcpy
            // 自己建带框窗口（SDL 非无边框），系统标题栏从一开始就在：
            // 标题文字、─□✕、双击最大化、拖动、吸附、圆角全原生。
            //
            // 为什么不能再“给无边框窗补 caption 样式”：SDL 对
            // --window-borderless 建的无边框窗自己接管 WM_NCCALCSIZE
            // 并答“客户区=整窗”，WS_CAPTION 永远占不到标题带（真机
            // 表现：上巴系统“见不到相关的内容”；WinForms 测试宿主
            // 走 DefWindowProc 复现不出）。跨进程子类化拦截
            // WM_NCCALCSIZE 已被 Windows 从 Vista 起禁用（真机实测
            // SetWindowLongPtr(GWL_WNDPROC) 返回 ERROR_ACCESS_DENIED）。
            //
            // 下面的样式手术保留作防御：万一窗口带着不完整的 caption
            // 样式出现（旧构建/外部工具），补齐家族并清 WS_POPUP；
            // 已完整则一律不动（避免 FRAMECHANGED 闪帧）。
            if (TopNative)
            {
                int caption = WS_THICKFRAME | WS_CAPTION | WS_SYSMENU
                    | WS_MINIMIZEBOX | WS_MAXIMIZEBOX;
                int captionMask2 = WS_CAPTION | WS_SYSMENU
                    | WS_MINIMIZEBOX | WS_MAXIMIZEBOX;
                bool complete = (style & captionMask2) == captionMask2
                    && (style & WS_POPUP) == 0
                    && (style & WS_THICKFRAME) != 0;
                if (!complete)
                {
                    NativeMethods.SetWindowLong(_hwnd, GWL_STYLE,
                        (style & ~WS_POPUP) | caption);
                    NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero, 0, 0, 0, 0,
                        0x0067 /*NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED|NOOWNERZORDER*/);
                    Log.Write("caption styles completed (was incomplete)");
                }
            }
            else
            {
                NativeMethods.SetWindowLong(_hwnd, GWL_STYLE,
                    style | WS_THICKFRAME);
                NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero, 0, 0, 0, 0,
                    0x0067 /*NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED|NOOWNERZORDER*/);
            }
            // Corner policy (2026-09-09 组合矩阵定稿): Windows 自带圆角
            // 无处不在——只要没有 G2 区域，视频窗一律 DWMWCP_ROUND：DWM
            // 自己圆四个角，真 caption 的顶角和任何系统窗口一样圆。
            // 下巴 native 时拼缝处的底角缺口由 ChinWindow 的补角耳
            // （8 DIP 方块盖在缺口正后方，ClipRegion 含耳）修补——
            // 沉浸顶与原生顶同欸（VideoRounded 只看 G2 区域，不看
            // 上巴模式）。只有 G2 区域（自带轮廓 + AA masks）才平方
            // 视频窗。
            int round = (_cornerDip > 0)
                ? 1 /*DWMWCP_DONOTROUND: the G2 region + masks own the outline*/
                : 2 /*DWMWCP_ROUND: Windows 11 native rounding in every combo*/;
            NativeMethods.DwmSetWindowAttribute(_hwnd, 33 /*CORNER_PREFERENCE*/, ref round, 4);
            if (_cornerDip > 0)
            {
                int none = unchecked((int)0xFFFFFFFE);
                NativeMethods.DwmSetWindowAttribute(_hwnd, 34 /*BORDER_COLOR*/, ref none, 4);
            }
            if (TopNative) ApplySystemBackdrop();
            _repaired = true;
            ApplyCornerRegion();
            Log.Write("window repaired: thickframe"
                + (TopNative ? "+caption" : "") + " round=" + round);
            // Mark user-size once at repair: scrcpy auto-resizes its window
            // on video rotation while it believes the user never resized.
            Rectangle wr0 = WindowRect();
            NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                wr0.Left, wr0.Top, wr0.Width, wr0.Height,
                0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/);
        }

        /// <summary>C2 real-caption material: Win11 22H2+ Mica via
        /// DWMWA_SYSTEMBACKDROP_TYPE / DWMBT_MAINWINDOW - the same backdrop
        /// PowerShell's and Explorer's title bars are made of. HRESULT
        /// failure (older builds) falls back to DWMWA_CAPTION_COLOR
        /// #F3F3F3; if that fails too, the DWM default caption color
        /// stays. Never a self-drawn bar.</summary>
        private void ApplySystemBackdrop()
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            int mica = 2;   // DWMBT_MAINWINDOW
            int hr = NativeMethods.DwmSetWindowAttribute(_hwnd,
                38 /*DWMWA_SYSTEMBACKDROP_TYPE*/, ref mica, 4);
            if (hr == 0)
            {
                Log.Write("caption backdrop: DWMBT_MAINWINDOW (mica)");
                return;
            }
            int tint = unchecked((int)0xFFF3F3F3);
            NativeMethods.DwmSetWindowAttribute(_hwnd,
                35 /*DWMWA_CAPTION_COLOR*/, ref tint, 4);
            Log.Write("caption backdrop fallback hr=0x" + hr.ToString("x")
                + " -> caption color F3F3F3");
        }

        // -- fake maximize (taskbar-safe) ---------------------------------------

        private void DropStaleFakeMax()
        {
            if (!_fakedMax || Environment.TickCount < _maxGraceUntil) return;
            Rectangle r = WindowRect();
            // Tolerance covers SDL size-snapping (observed 1-3px drift);
            // real user drags clear the state explicitly in BeginResize/BeginMove.
            if (Math.Abs(r.X - _maxRect.X) > 12 || Math.Abs(r.Y - _maxRect.Y) > 12 ||
                Math.Abs(r.Width - _maxRect.Width) > 12 || Math.Abs(r.Height - _maxRect.Height) > 12)
            {
                _fakedMax = false;
                _top.SetMaximized(0);
                _top.Render();
                Log.Write("fake maximize dropped");
            }
        }

        private void FakeMaximize(bool on, bool fit)
        {
            // A natively maximized window (Win+Up, snap) must leave the
            // WS_MAXIMIZE state before any custom geometry sticks.
            if (NativeMethods.IsZoomed(_hwnd))
                NativeMethods.ShowWindow(_hwnd, 9 /*SW_RESTORE*/);
            if (on)
            {
                if (!_fakedMax) _savedRect = WindowRect();
                Rectangle wa = WorkArea();
                NativeMethods.RECT insets = FrameInsets();
                int x, y, w, h;
                if (fit)
                {
                    // Aspect-preserving fit against the VIDEO ratio (not the
                    // old window shape): the window grows to the largest
                    // video-ratio rect that fits the work area and centers
                    // there. The uncovered screen stays pure desktop - no
                    // window, no letterbox bars: the video reads as a
                    // floating panel, which is the intended "fullscreen"
                    // look for ratio-locked modes.
                    double ratio = VideoAspect();
                    if (ratio <= 0)
                    {
                        int cw = Math.Max(1, _savedRect.Width - insets.Left - insets.Right);
                        int ch = Math.Max(1, _savedRect.Height - insets.Top - insets.Bottom);
                        ratio = (double)cw / ch;
                    }
                    double waAr = (double)wa.Width / wa.Height;
                    if (ratio > waAr)
                    {
                        w = wa.Width - insets.Left - insets.Right;
                        h = (int)Math.Round(w / ratio);
                    }
                    else
                    {
                        h = wa.Height - insets.Top - insets.Bottom;
                        w = (int)Math.Round(h * ratio);
                    }
                    w += insets.Left + insets.Right;
                    h += insets.Top + insets.Bottom;
                    x = wa.X + (wa.Width - w) / 2;
                    y = wa.Y + (wa.Height - h) / 2;
                }
                else
                {
                    // True maximize semantics (flex only): fill the work area.
                    x = wa.X - insets.Left;
                    y = wa.Y - insets.Top;
                    w = wa.Width + insets.Left + insets.Right;
                    h = wa.Height + insets.Top + insets.Bottom;
                }
                NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero, x, y, w, h, 0x0014);
                _maxRect = new Rectangle(x, y, w, h);
                _fakedMax = true;
                _fakedMode = fit ? 1 : 2;
                _maxGraceUntil = Environment.TickCount + MaxGraceMs;
                Log.Write("fake maximize on mode=" + _fakedMode + " " + _maxRect);
            }
            else
            {
                NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                    _savedRect.X, _savedRect.Y, _savedRect.Width, _savedRect.Height, 0x0014);
                _fakedMax = false;
                Log.Write("fake maximize off");
            }
            _top.SetMaximized(on ? (fit ? 1 : 2) : 0);
            _top.Render();
        }

        // -- geometry helpers ----------------------------------------------------

        private Rectangle WindowRect()
        {
            NativeMethods.RECT r;
            NativeMethods.GetWindowRect(_hwnd, out r);
            return Rectangle.FromLTRB(r.Left, r.Top, r.Right, r.Bottom);
        }

        /// <summary>DWM-visible bounds (EXTENDED_FRAME_BOUNDS) of the video
        /// window. With the real C2 caption the system's caption buttons
        /// end at this RIGHT edge and the caption band starts at this TOP
        /// edge; the raw window rect carries ~7px invisible resize borders
        /// past both, which would shove the 4th button over the system's
        /// buttons. Falls back to the window rect if DWM declines.</summary>
        private Rectangle VisibleBounds()
        {
            NativeMethods.RECT e;
            if (_hwnd != IntPtr.Zero && NativeMethods.DwmGetWindowAttribute(
                _hwnd, 9 /*EXTENDED_FRAME_BOUNDS*/, out e,
                Marshal.SizeOf(typeof(NativeMethods.RECT))) == 0)
                return Rectangle.FromLTRB(e.Left, e.Top, e.Right, e.Bottom);
            return WindowRect();
        }

        private Rectangle ClientRect()
        {
            NativeMethods.RECT c;
            NativeMethods.GetClientRect(_hwnd, out c);
            NativeMethods.POINT org;
            org.X = 0; org.Y = 0;
            NativeMethods.ClientToScreen(_hwnd, ref org);
            return new Rectangle(org.X, org.Y, c.Right, c.Bottom);
        }

        private static Point CursorPosition()
        {
            NativeMethods.POINT p;
            NativeMethods.GetCursorPos(out p);
            return new Point(p.X, p.Y);
        }

        /// <summary>Work area of the monitor the WINDOW is on, resolved
        /// from the window rect's CENTER - the same straddle-proof contract
        /// as ConstrainToWorkArea. MonitorFromWindow is straddle-sensitive
        /// (a window overhanging a screen boundary can flip to the other
        /// monitor), which handed FakeMaximize the wrong screen's work area
        /// and made "maximize" fit/center on the other screen. Every
        /// FakeMaximize entry re-resolves from the current rect, so after a
        /// cross-screen drag the next maximize fits the screen the window
        /// is actually on. The point flag must be 2 (MONITOR_DEFAULTTONEAREST):
        /// for an off-desktop center (window dragged past the screen edge)
        /// 1 would be MONITOR_DEFAULTTOPRIMARY - maximize would jump the
        /// window to the primary screen, the very wrong-screen bug this
        /// center resolution exists to kill. NEAREST never returns null.
        /// </summary>
        private Rectangle WorkArea()
        {
            Rectangle monitor, work;
            VideoMonitor(out monitor, out work);
            return work;
        }

        /// <summary>Resolve the monitor under the video window's CENTER
        /// into its full bounds + work area (taskbar-excluded) - one
        /// straddle-proof resolver for FakeMaximize's work area, the chin's
        /// taskbar guard (needs the monitor rect for fullscreen detection)
        /// and the 4th button's work-area clamp. Failure falls back to the
        /// virtual screen / SPI working area instead of empty rects.</summary>
        private bool VideoMonitor(out Rectangle monitor, out Rectangle work)
        {
            Rectangle wr = WindowRect();
            NativeMethods.POINT center;
            center.X = wr.Left + wr.Width / 2;
            center.Y = wr.Top + wr.Height / 2;
            IntPtr mon = NativeMethods.MonitorFromPoint(
                center, 2 /*MONITOR_DEFAULTTONEAREST*/);
            NativeMethods.MONITORINFO mi = new NativeMethods.MONITORINFO();
            mi.cbSize = Marshal.SizeOf(typeof(NativeMethods.MONITORINFO));
            if (mon == IntPtr.Zero || !NativeMethods.GetMonitorInfoW(mon, ref mi))
            {
                monitor = SystemInformation.VirtualScreen;
                work = SystemInformation.WorkingArea;
                return false;
            }
            monitor = Rectangle.FromLTRB(
                mi.rcMonitor.Left, mi.rcMonitor.Top,
                mi.rcMonitor.Right, mi.rcMonitor.Bottom);
            work = Rectangle.FromLTRB(
                mi.rcWork.Left, mi.rcWork.Top,
                mi.rcWork.Right, mi.rcWork.Bottom);
            return true;
        }

        private NativeMethods.RECT FrameInsets()
        {
            NativeMethods.RECT wr;
            NativeMethods.GetWindowRect(_hwnd, out wr);
            NativeMethods.RECT e;
            NativeMethods.DwmGetWindowAttribute(_hwnd, 9 /*EXTENDED_FRAME_BOUNDS*/,
                out e, Marshal.SizeOf(typeof(NativeMethods.RECT)));
            NativeMethods.RECT insets;
            insets.Left = e.Left - wr.Left; insets.Top = e.Top - wr.Top;
            insets.Right = wr.Right - e.Right; insets.Bottom = wr.Bottom - e.Bottom;
            return insets;
        }

        private static float ProbeDpi()
        {
            Bitmap probe = new Bitmap(1, 1);
            float s;
            using (Graphics g = Graphics.FromImage(probe)) s = g.DpiX / 96f;
            probe.Dispose();
            return s;
        }

        private int S(int logical)
        {
            // Probe-based scale: the old chin-derived formula silently broke
            // in native mode (the bar is 32px, not the 44px ghost band it
            // assumed) - S() serves strips, margins and minimums alike.
            return (int)Math.Round(logical * _dpi);
        }
    }
}
