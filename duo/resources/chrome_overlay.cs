// Duo chrome overlay - edge controls for a borderless scrcpy window.
//
// Runs on the Windows side, spawned by `duo mirror --chrome`. The scrcpy
// window is borderless; this overlay adds back, on demand:
//
//   cursor in the top edge band  -> top-right capsule: minimize / maximize
//                                   (taskbar-safe, emulated) / close
//   always-on (window visible)   -> chin: "<" back  "O" home (adb keyevents)
//
// Resize policy comes from --display-mode: mirror/fixed windows stay glued
// to the video aspect ratio (live sizes are tailed from the session log,
// where scrcpy emits "INFO: Texture: WxH" on every change, rotation
// included - verified live on scrcpy 4.1); flex windows resize freely and
// the virtual display follows the window.
//
// Rendering is per-pixel-alpha layered windows (UpdateLayeredWindow) with
// hand-made acrylic: the content behind each bar is sampled from the target
// window itself (PrintWindow PW_RENDERFULLCONTENT), blurred by down/up
// scaling, then dark-tinted. No OS composition API dependency - the
// SetWindowCompositionAttribute route returns E_FAIL on Win11 24H2.
//
// The window is repaired after discovery: WS_THICKFRAME is re-asserted so
// native edge resize (and Win11 snap) keeps working, and DWMWCP_ROUND is
// declared so the corners follow the Windows 11 rounding convention.
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
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
        [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
        [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
        [DllImport("user32.dll")] public static extern bool SetWindowPos(
            IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
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
        private static readonly string Path_ =
            Path.Combine(Path.GetTempPath(), "duo-chrome-overlay-" +
                Process.GetCurrentProcess().Id + ".log");

        public static void Write(string msg)
        {
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
            }
            if (title == null || serial == null || adb == null)
            {
                Log.Write("usage: --title <t> --serial <s> --adb <path> [--home 0|1] "
                    + "[--display-mode mirror|flex|fixed] [--video-w n] [--video-h n] "
                    + "[--session-log <path>]");
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
                + (sessionLog == null ? "" : " log=" + sessionLog));
            using (Controller c = new Controller(
                title, serial, adb, home, mode, videoW, videoH, sessionLog, cornerDip))
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
        private readonly int _radiusTop, _radiusBottom;
        private Bitmap _behind;               // sampled content, may be null
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
            TopMost = true;
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
                        using (GraphicsPath clip = RoundedPath(
                            Width, Height, _radiusTop, _radiusBottom))
                        using (Region region = new Region(clip))
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

        private void DrawAcrylic(Graphics g)
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

        protected void DrawChevron(Graphics g, float cx, float cy, float opacity)
        {
            float h = 11f * Dpi;
            float dx = (h / 2f) / (float)Math.Tan(50.0 * Math.PI / 180.0);
            using (Pen pen = NavPen(opacity))
            {
                g.DrawLine(pen, cx - dx, cy, cx + dx, cy - h / 2f);
                g.DrawLine(pen, cx - dx, cy, cx + dx, cy + h / 2f);
            }
        }

        protected void DrawRing(Graphics g, float cx, float cy, float opacity)
        {
            float d = 11f * Dpi;
            using (Pen pen = NavPen(opacity))
                g.DrawEllipse(pen, cx - d / 2f, cy - d / 2f, d, d);
        }

        protected Pen NavPen(float opacity)
        {
            Pen pen = new Pen(Color.FromArgb((int)(255 * opacity), 255, 255, 255),
                1.8f * Dpi);
            pen.StartCap = LineCap.Round;
            pen.EndCap = LineCap.Round;
            return pen;
        }

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
    // The chin: persistent bottom bar with "<" back and "O" home.
    // -------------------------------------------------------------------------
    internal sealed class ChinWindow : OverlayWindow
    {
        public const int LogicalHeight = 44;
        private const int LogicalButton = 36;
        private const int HoldMs = 550;

        private readonly Timer _hold;
        private bool _firedHold;

        public ChinWindow(Controller owner, bool home)
            : base(owner, 0, (int)(18 * ScaleOf()))
        {
            GhostBackdrop = true;
            int btn = (int)(LogicalButton * Dpi);
            int h = (int)(LogicalHeight * Dpi);
            Size = new Size(600, h);           // width resynced by the controller
            // mBack homage: one centered ring. Tap = BACK; press-and-hold =
            // HOME (physical mirroring only - a virtual display has no
            // launcher, so holding there does nothing).
            Buttons.Add(new NavButton(
                new Rectangle((600 - btn) / 2, (h - btn) / 2, btn, btn),
                1, delegate { Ctrl.AdbKey(4); }));
            _hold = new Timer { Interval = HoldMs };
            _hold.Tick += delegate
            {
                _hold.Stop();
                _firedHold = true;
                Log.Write("hold fired");
                if (home) Ctrl.AdbKey(3);
            };
            WireInput();
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
                    Render();
                    _hold.Start();
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
            // Invisible resize sliver along the very bottom (alpha=1 is enough
            // to stay hit-testable while visually imperceptible).
            using (SolidBrush band = new SolidBrush(Color.FromArgb(1, 0, 0, 0)))
                g.FillRectangle(band, 0, Height - S6(), Width, S6());
            foreach (NavButton b in Buttons)
            {
                float cx = b.Circle.Left + b.Circle.Width / 2f;
                float cy = b.Circle.Top + b.Circle.Height / 2f;
                // AssistiveTouch-style glass disc: flat smoked glass with a
                // light rim, readable over any content.
                float disc = 38f * Dpi;
                int scrim = b.Pressed ? 200 : (b.Hover ? 175 : 155);
                using (GraphicsPath path = new GraphicsPath())
                {
                    path.AddEllipse(cx - disc / 2f, cy - disc / 2f, disc, disc);
                    using (SolidBrush glass = new SolidBrush(Color.FromArgb(scrim, 10, 10, 12)))
                        g.FillPath(glass, path);
                    // top rim highlight
                    using (Pen rim = new Pen(Color.FromArgb(b.Pressed ? 90 : 55, 255, 255, 255), 1f))
                        g.DrawArc(rim, cx - disc / 2f, cy - disc / 2f, disc, disc, 200f, 100f);
                }
                // hover glow halo
                if (b.Hover || b.Pressed)
                {
                    using (Pen halo = new Pen(Color.FromArgb(60, 255, 255, 255), 1.6f * Dpi))
                    {
                        float d2 = disc + 7f * Dpi;
                        g.DrawEllipse(halo, cx - d2 / 2f, cy - d2 / 2f, d2, d2);
                    }
                }
                float opacity = (b.Hover || b.Pressed) ? 1.0f : 0.72f;
                DrawRing(g, cx, cy, opacity);
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
        }
    }

    // -------------------------------------------------------------------------
    // The top-right capsule: minimize / maximize-restore / close (Fluent glyphs).
    // -------------------------------------------------------------------------
    internal sealed class TopWindow : OverlayWindow
    {
        public const int LogicalButton = 30;
        private const int LogicalPad = 5;
        private const int LogicalGap = 6;

        private static Font _glyphFont;
        private readonly string[] _glyphs;

        public TopWindow(Controller owner, bool fillButton)
            : base(owner, (int)(18 * ScaleOf()), (int)(18 * ScaleOf()))
        {
            GhostBackdrop = true;
            float s = ScaleOf();
            int btn = (int)(LogicalButton * s);
            int pad = (int)(LogicalPad * s);
            int gap = (int)(LogicalGap * s);
            // mirror/fixed windows must never be stretched off-ratio, so the
            // "fill work area" button exists only in flex mode: min / fit / close.
            int n = fillButton ? 4 : 3;
            int w = 2 * pad + n * btn + (n - 1) * gap;
            int h = 2 * pad + btn;
            Size = new Size(w, h);
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
            WireInput();
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
            if (_glyphs.Length > 1)
                _glyphs[1] = ((char)(mode == 1 ? 0xE923 : 0xE740)).ToString();
            if (_glyphs.Length > 3)
                _glyphs[2] = ((char)(mode == 2 ? 0xE923 : 0xE922)).ToString();
        }

        protected override void PaintBar(Graphics g)
        {
            // Minimal capsule: anti-aliased smoked-glass fill (FillPath, not a
            // Region clip - clips have hard edges) with a light rim.
            float rad = Height / 2f;
            using (GraphicsPath path = RoundedPath(Width, Height, (int)rad, (int)rad))
            {
                using (SolidBrush glass = new SolidBrush(Color.FromArgb(180, 10, 10, 12)))
                    g.FillPath(glass, path);
                using (Pen rim = new Pen(Color.FromArgb(70, 255, 255, 255), 1f))
                    g.DrawPath(rim, path);
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
    }

    // -------------------------------------------------------------------------
    // Controller: discovery, window repair, tracking, sampling, visibility.
    // -------------------------------------------------------------------------
    /// <summary>An invisible layered hot-zone hugging one window edge.
    /// Pixel alpha is zero everywhere, but layered windows still receive
    /// mouse input, so this is how the borderless scrcpy window regains
    /// native-feeling resize edges (correct cursors, live feedback).</summary>
    internal sealed class EdgeStrip : Form
    {
        public readonly int Edge;   // HT code: 10 left .. 17 bottomright
        private readonly Controller _owner;

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
            MouseDown += delegate(object s, MouseEventArgs e)
            {
                if (e.Button != MouseButtons.Left) return;
                if (Edge == 0) _owner.BeginMove();
                else _owner.BeginResize(Edge);
                Capture = true;
            };
            MouseMove += delegate(object s, MouseEventArgs e)
            {
                // Resize/move tracking is poll-driven (Tick): window ops
                // under a captured cursor synthesize WM_MOUSEMOVE, so doing
                // work here would form a self-perpetuating feedback storm.
            };
            MouseUp += delegate(object s, MouseEventArgs e)
            {
                if (e.Button != MouseButtons.Left) return;
                if (_owner.Resizing) { _owner.EndResize(); Capture = false; }
                else if (_owner.Moving) { _owner.EndMove(); Capture = false; }
            };
            MouseCaptureChanged += delegate(object s, EventArgs e)
            {
                if (!Capture && (_owner.Resizing || _owner.Moving))
                {
                    _owner.EndResize();
                    _owner.EndMove();
                }
            };
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= 0x00080000      // WS_EX_LAYERED
                           | 0x08000000      // WS_EX_NOACTIVATE
                           | 0x00000080      // WS_EX_TOOLWINDOW
                           | 0x00000008;     // WS_EX_TOPMOST
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

        public CornerMask(int corner)
        {
            _corner = corner;
            StartPosition = FormStartPosition.Manual;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            TopMost = true;
            AutoScaleMode = AutoScaleMode.None;
            Bounds = new Rectangle(-20000, -20000, 1, 1);   // parked until synced
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= 0x00080000      // WS_EX_LAYERED
                           | 0x08000000      // WS_EX_NOACTIVATE
                           | 0x00000080      // WS_EX_TOOLWINDOW
                           | 0x00000020      // WS_EX_TRANSPARENT: never take clicks
                           | 0x00000008;     // WS_EX_TOPMOST
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
            bool moved = Bounds != want;
            if (moved) Bounds = want;
            if (!Visible) Show();
            if (_radius != r || moved)
            {
                _radius = r;
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
                    // Edge treatment (prior art: SO #4425595 - Win32 regions are
                    // aliased; "use a bitmap with alpha transparency to simulate
                    // the anti-aliasing"): a crisp hairline right on the region
                    // cut, then a soft shadow ramp fading outward so the +-1px
                    // staircase band dissolves into what reads as a natural
                    // window shadow. Not copied code - technique reference.
                    StrokeArc(g, cx, cy, sx, sy, r + 0.2f * dpi, 1.1f * dpi, 150);
                    StrokeArc(g, cx, cy, sx, sy, r + 1.2f * dpi, 2.2f * dpi, 80);
                    StrokeArc(g, cx, cy, sx, sy, r + 3.0f * dpi, 3.0f * dpi, 45);
                    StrokeArc(g, cx, cy, sx, sy, r + 5.2f * dpi, 4.5f * dpi, 22);
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
        private const int FirstWaitMs = 12000;
        private const int LostWaitMs = 15000;
        private const int TriggerTop = 6;      // logical px reveal band
        private const int RetainTop = 48;      // logical px hysteresis
        private const int TopMargin = 10;      // logical px from top-right
        private const int MaxGraceMs = 700;

        private readonly string _title, _serial, _adb;
        private readonly string _displayMode;         // mirror | flex | fixed
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
        private Bitmap _sample;                // full-window sample (reused)
        private NativeMethods.WinEventDelegate _hookProc;   // keep delegate alive
        private IntPtr _hook = IntPtr.Zero;
        private int _ticks;
        private EdgeStrip[] _strips;

        private readonly bool _homeEnabled;

        // Live video size: seeded from argv (fixed mode knows its WxH), then
        // updated by the session-log tailer (scrcpy "INFO: Texture: WxH"
        // lines, emitted on every size change including rotation).
        private int _videoW, _videoH;
        private int _videoChangedAt;
        private Thread _logThread;
        private volatile bool _disposed;

        // G2 corners (quartic superellipse, curvature-continuous with the
        // straight edges): pixel-verified live 2026-09-05 that SetWindowRgn
        // DOES visually clip the scrcpy video window (block-diff against the
        // desktop = 1.7 vs 48.8 against the video). The region must be
        // re-applied after every window-rect change - regions do not scale.
        private int _cornerDip;   // 0 = off
        private Rectangle _lastRegionRect;
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

        public Controller(string title, string serial, string adb, bool home,
            string displayMode, int videoW, int videoH, string sessionLog, int cornerDip)
        {
            _title = title; _serial = serial; _adb = adb;
            _homeEnabled = home;
            _displayMode = displayMode == null ? "flex" : displayMode;
            _videoW = videoW; _videoH = videoH;
            _videoChangedAt = 0;
            _cornerDip = cornerDip;
            _chin = new ChinWindow(this, home);
            _top = new TopWindow(this, _displayMode.Equals("flex"));
            // Force handle creation now: the WinEvent callback below may fire
            // for any window move long before the bars are first shown, and
            // BeginInvoke requires an existing handle.
            if (!_chin.IsHandleCreated) { IntPtr h = _chin.Handle; }
            if (!_top.IsHandleCreated) { IntPtr h = _top.Handle; }
            // Invisible resize hot-zones for all four edges AND all four
            // corners (the chin used to be the only bottom affordance, which
            // made bottom resizes undiscoverable). Created after the bars so
            // the visible bars stack above them. The last strip (edge 0) is
            // the top-center move grab zone, created last so it sits above
            // the top resize band.
            _strips = new EdgeStrip[9];
            _strips[0] = new EdgeStrip(this, 10);   // left
            _strips[1] = new EdgeStrip(this, 11);   // right
            _strips[2] = new EdgeStrip(this, 12);   // top
            _strips[3] = new EdgeStrip(this, 13);   // top-left
            _strips[4] = new EdgeStrip(this, 14);   // top-right
            _strips[5] = new EdgeStrip(this, 15);   // bottom
            _strips[6] = new EdgeStrip(this, 16);   // bottom-left
            _strips[7] = new EdgeStrip(this, 17);   // bottom-right
            _strips[8] = new EdgeStrip(this, 0);    // move (top-center)
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
            if (_sample != null) { _sample.Dispose(); _sample = null; }
            foreach (EdgeStrip strip in _strips)
            {
                strip.Dispose();
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
            want = ConstrainToWorkArea(want);
            bool ok = NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                want.Left, want.Top, want.Width, want.Height,
                0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/);
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

        /// <summary>Shrink an oversized drag result to fit the monitor work
        /// area, preserving its proportions and rough center.</summary>
        private Rectangle ConstrainToWorkArea(Rectangle want)
        {
            Rectangle wa = WorkArea();
            if (want.Width <= wa.Width && want.Height <= wa.Height) return want;
            double scale = Math.Min((double)wa.Width / want.Width,
                                    (double)wa.Height / want.Height);
            int w = Math.Max(S(LogicalMinW), (int)(want.Width * scale));
            int h = (int)(want.Height * scale);
            int x = want.Left + (want.Width - w) / 2;
            int y = want.Top + (want.Height - h) / 2;
            return new Rectangle(x, y, w, h);
        }

        public void EndResize()
        {
            if (!_resizing) return;
            _resizing = false;
            Log.Write("resize end moves=" + _resizeMoves);
        }

        // ---- window move (top-center grab zone) ---------------------------

        private bool _moving;
        private Point _moveStart, _moveMouse;

        public bool Moving { get { return _moving; } }

        public void BeginMove()
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            if (_fakedMax)
            {
                _fakedMax = false;
                _top.SetMaximized(0);
            }
            Rectangle wr = WindowRect();
            _moveStart = new Point(wr.Left, wr.Top);
            NativeMethods.POINT pt;
            NativeMethods.GetCursorPos(out pt);
            _moveMouse = new Point(pt.X, pt.Y);
            _moving = true;
            Log.Write("move begin");
        }

        public void UpdateMove()
        {
            if (!_moving) return;
            NativeMethods.POINT pt;
            NativeMethods.GetCursorPos(out pt);
            NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                _moveStart.X + pt.X - _moveMouse.X, _moveStart.Y + pt.Y - _moveMouse.Y,
                0, 0, 0x0001 /*SWP_NOSIZE*/ | 0x0004 /*SWP_NOZORDER*/ | 0x0010 /*SWP_NOACTIVATE*/);
        }

        public void EndMove()
        {
            if (!_moving) return;
            _moving = false;
            Log.Write("move end");
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
                ApplyCornerRegion();     // regions do not scale: refresh on change
            }
            if (_settleSince < 0 || _resizing || _moving || _fakedMax || !RatioLock)
                return;
            if (Environment.TickCount - _settleSince < SettleMs) return;
            _settleSince = -1;                     // one-shot per settle
            ConvergeToVideoAspect(wr);
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
        /// Hard-edged (GDI regions are 1-bit); re-applied on every rect change.</summary>
        private void ApplyCornerRegion()
        {
            if (_cornerDip <= 0) return;
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd)) return;
            if (NativeMethods.IsIconic(_hwnd)) return;
            Rectangle wr = WindowRect();
            if (wr == _lastRegionRect) return;      // dedupe: re-apply only on change
            NativeMethods.RECT e;
            NativeMethods.DwmGetWindowAttribute(_hwnd, 9 /*EXTENDED_FRAME_BOUNDS*/,
                out e, Marshal.SizeOf(typeof(NativeMethods.RECT)));
            int x0 = e.Left - wr.Left, y0 = e.Top - wr.Top;
            int x1 = e.Right - wr.Left, y1 = e.Bottom - wr.Top;
            _visibleRect = new Rectangle(e.Left, e.Top, x1 - x0, y1 - y0);
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
                _lastRegionRect = wr;
                ApplySingleFrameStyle();
            }
            SyncMasks();
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
        /// corner edges (small click-through layered squares).</summary>
        private void SyncMasks()
        {
            if (_cornerDip <= 0 || _hwnd == IntPtr.Zero) return;
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
            _videoChangedAt = Environment.TickCount;
            Log.Write("video size from log: " + w + "x" + h);
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
                Log.Write("alive #" + _ticks);
                // SDL can re-assert its own styles on some events; keep the
                // resize frame alive without user-visible work.
                if (_hwnd != IntPtr.Zero && NativeMethods.IsWindow(_hwnd))
                {
                    int s = NativeMethods.GetWindowLong(_hwnd, -16);
                    if ((s & 0x00040000) == 0) Repair();
                }
            }
        }

        private void TickInner()
        {
            // Poll-driven resize/move tracking (see EdgeStrip.MouseMove note).
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
            bool overBars = _chin.Bounds.Contains(cursor) || _top.Bounds.Contains(cursor);
            bool overStrips = false;
            foreach (EdgeStrip strip in _strips) if (strip.Bounds.Contains(cursor)) overStrips = true;
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
            if (!engaged)
            {
                HideBars();
                HideStrips();
                return;
            }
            Rectangle wr = WindowRect();
            SyncStrips(wr);
            TrackExternalChange(wr);

            // Per-bar proximity rules, symmetric like a native window's own
            // affordances: capsule reveals near the top edge, the mBack dot
            // near the bottom edge. Neither cares about focus.
            bool showTop = ComputeTopVisibility(client, cursor);
            bool showChin = ComputeChinVisibility(client, cursor) || overBars
                || _resizing || _moving;

            SyncChin(client, showChin);
            if (showTop && !_top.Visible)
            {
                _top.Left = client.Right - _top.Width - S(TopMargin);
                _top.Top = client.Top + S(TopMargin);
                _top.Show();
                Log.Write("top shown at " + _top.Left + "," + _top.Top
                    + " " + _top.Width + "x" + _top.Height);
            }
            else if (!showTop && _top.Visible)
            {
                _top.Hide();
                Log.Write("top hidden");
            }

            DropStaleFakeMax();
            // NOTE: PrintWindow content sampling retired - both bars are
            // flat smoked glass now, and dropping the 220ms sample cadence
            // made the bars track window moves noticeably tighter.
        }

        private void HideBars()
        {
            if (_chin.Visible) Log.Write("bars hidden");
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
        }

        /// <summary>Keep the edge hot-zones glued to the window frame. The
        /// strips are always on (invisible, topmost): a normal window is
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
            Place(_strips[2], wr.Left + corner, wr.Top, topLen, edge);
            Place(_strips[3], wr.Left, wr.Top, corner, corner);
            Place(_strips[4], wr.Right - corner, wr.Top, corner, corner);
            Place(_strips[5], wr.Left, wr.Bottom - edge, topLen, edge);
            Place(_strips[6], wr.Left, wr.Bottom - corner, corner, corner);
            Place(_strips[7], wr.Right - corner, wr.Bottom - corner, corner, corner);
            int moveW = Math.Max(0, Math.Min(wr.Width / 3, S(280)));
            int moveH = Math.Max(2, Math.Min(S(14), edge));
            Place(_strips[8], wr.Left + (wr.Width - moveW) / 2, wr.Top, moveW, moveH);
        }

        private static void Place(Form f, int x, int y, int w, int h)
        {
            Rectangle want = new Rectangle(x, y, w, h);
            if (f.Bounds != want) f.Bounds = want;
            if (!f.Visible) f.Show();
        }

        private bool ComputeTopVisibility(Rectangle client, Point cursor)
        {
            bool inX = cursor.X >= client.Left && cursor.X < client.Right;
            if (!inX) return _top.Visible && _top.Bounds.Contains(cursor);
            if (cursor.Y < client.Top + S(TriggerTop)) return true;
            return _top.Visible && cursor.Y < client.Top + S(RetainTop);
        }

        private bool ComputeChinVisibility(Rectangle client, Point cursor)
        {
            // Bottom-edge twin of the capsule rule: reveal when the cursor
            // dips into the bottom band, retain with hysteresis while it
            // stays near.
            bool inX = cursor.X >= client.Left && cursor.X < client.Right;
            if (!inX) return _chin.Bounds.Contains(cursor);
            if (cursor.Y > client.Bottom - S(TriggerTop)) return true;
            return _chin.Visible && cursor.Y > client.Bottom - S(RetainTop);
        }

        private void SyncChin(Rectangle client, bool show)
        {
            _chin.ResyncWidth(client.Width);
            _chin.Left = client.Left;
            _chin.Top = client.Bottom - _chin.Height;
            if (show && !_chin.Visible)
            {
                _chin.Show();
                Log.Write("chin shown");
            }
            else if (!show && _chin.Visible)
            {
                _chin.Hide();
                Log.Write("chin hidden");
            }
            // Hook-driven: keep the capsule glued during moves/resizes too,
            // not just on the 20fps tick.
            if (_top.Visible)
            {
                _top.Left = client.Right - _top.Width - S(TopMargin);
                _top.Top = client.Top + S(TopMargin);
            }
            _chin.Render();
            if (_top.Visible) _top.Render();
        }

        private void MaybeSample()
        {
            if (!_chin.Visible && !_top.Visible) return;
            int now = Environment.TickCount;
            if (now - _lastSample < SampleMs) return;
            _lastSample = now;
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
            Bitmap chinSample = null, topSample = null;
            if (ok) ok = !LooksBlack(_sample);
            if (ok)
            {
                chinSample = Crop(_sample, _chin.Bounds, wr);
                if (_top.Visible) topSample = Crop(_sample, _top.Bounds, wr);
            }
            _chin.SetSample(chinSample);
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
            Log.Write("window found hwnd=0x" + _hwnd.ToString("x"));
            _waitedMs = 0;
            Repair();
            if (_hook == IntPtr.Zero)
            {
                _hookProc = delegate(IntPtr hHook, uint evt, IntPtr hwnd,
                    int idObject, int idChild, uint thread, uint time)
                {
                    if (hwnd == _hwnd && idObject == 0 /*OBJID_WINDOW*/ &&
                        _chin.IsHandleCreated && _chin.Visible)
                    {
                        // The handle can be destroyed between the check and
                        // BeginInvoke (form teardown race); never let that kill
                        // the WinEvent hook thread.
                        try
                        {
                            _chin.BeginInvoke((MethodInvoker)delegate
                            {
                                SyncChin(ClientRect(), true);
                            });
                        }
                        catch { }
                    }
                };
                _hook = NativeMethods.SetWinEventHook(0x800B, 0x800B, IntPtr.Zero,
                    _hookProc, 0, 0, 0 /*WINEVENT_OUTOFCONTEXT*/);
            }
        }

        private void Repair()
        {
            const int GWL_STYLE = -16;
            const int WS_THICKFRAME = 0x00040000;
            int style = NativeMethods.GetWindowLong(_hwnd, GWL_STYLE);
            NativeMethods.SetWindowLong(_hwnd, GWL_STYLE, style | WS_THICKFRAME);
            NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero, 0, 0, 0, 0,
                0x0067 /*NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED|NOOWNERZORDER*/);
            int round = _cornerDip > 0 ? 1 /*DWMWCP_DONOTROUND: region owns it*/
                                       : 2 /*DWMWCP_ROUND*/;
            NativeMethods.DwmSetWindowAttribute(_hwnd, 33 /*CORNER_PREFERENCE*/, ref round, 4);
            if (_cornerDip > 0)
            {
                int none = unchecked((int)0xFFFFFFFE);
                NativeMethods.DwmSetWindowAttribute(_hwnd, 34 /*BORDER_COLOR*/, ref none, 4);
            }
            _repaired = true;
            ApplyCornerRegion();
            Log.Write("window repaired: thickframe+round");
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

        private Rectangle WorkArea()
        {
            NativeMethods.MONITORINFO mi = new NativeMethods.MONITORINFO();
            mi.cbSize = Marshal.SizeOf(typeof(NativeMethods.MONITORINFO));
            IntPtr mon = NativeMethods.MonitorFromWindow(_hwnd, 1 /*NEAREST*/);
            NativeMethods.GetMonitorInfoW(mon, ref mi);
            return Rectangle.FromLTRB(
                mi.rcWork.Left, mi.rcWork.Top, mi.rcWork.Right, mi.rcWork.Bottom);
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

        private int S(int logical)
        {
            return (int)Math.Round(logical * _chin.Height / (double)ChinWindow.LogicalHeight);
        }
    }
}
