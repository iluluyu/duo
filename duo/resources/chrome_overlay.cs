// Duo chrome overlay - edge controls for a borderless scrcpy window.
//
// Runs on the Windows side, spawned by `duo mirror --chrome`. The scrcpy
// window is borderless; this overlay adds back, on demand:
//
//   cursor in the top edge band  -> top-right capsule: minimize / maximize
//                                   (taskbar-safe, emulated) / close
//   always-on (window visible)   -> chin: "<" back  "O" home (adb keyevents)
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

        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
            public static extern IntPtr FindWindowW(string cls, string title);
        [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
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
            for (int i = 0; i + 1 < argv.Length; i += 2)
            {
                if (argv[i] == "--title") title = argv[i + 1];
                else if (argv[i] == "--serial") serial = argv[i + 1];
                else if (argv[i] == "--adb") adb = argv[i + 1];
            }
            if (title == null || serial == null || adb == null)
            {
                Log.Write("usage: --title <t> --serial <s> --adb <path>");
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
            Log.Write("overlay start title=" + title + " serial=" + serial);
            using (Controller c = new Controller(title, serial, adb))
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
        public readonly Rectangle Circle;
        public readonly Action Fire;
        public readonly int Kind;          // 0 = chevron, 1 = ring, 2..4 = win glyphs
        public bool Hover;

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

        protected OverlayWindow(int radiusTop, int radiusBottom)
        {
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

        public void Render()
        {
            if (Width <= 0 || Height <= 0) return;
            using (Bitmap bmp = new Bitmap(Width, Height, PixelFormat.Format32bppArgb))
            {
                using (Graphics g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    g.PixelOffsetMode = PixelOffsetMode.Half;
                    using (GraphicsPath clip = RoundedPath(
                        Width, Height, _radiusTop, _radiusBottom))
                    using (Region region = new Region(clip))
                    {
                        g.SetClip(region, CombineMode.Replace);
                        DrawAcrylic(g);
                        PaintBar(g);
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
            Color fill = b.Kind == 4
                ? Color.FromArgb(255, 232, 17, 35)      // close hover: #E81123
                : Color.FromArgb(28, 255, 255, 255);    // rgba(255,255,255,0.11)
            using (SolidBrush brush = new SolidBrush(fill))
                g.FillEllipse(brush, b.Circle);
        }

        // -- input ------------------------------------------------------------

        protected void WireInput()
        {
            MouseMove += delegate(object s, MouseEventArgs e)
            {
                int hit = HitIndex(e.Location);
                Cursor = hit >= 0 ? Cursors.Hand : Cursors.Default;
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
        }

        private int HitIndex(Point p)
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
        private const int LogicalGap = 112;

        public ChinWindow(Controller owner)
            : base(0, (int)(10 * ScaleOf()))
        {
            int btn = (int)(LogicalButton * Dpi);
            int gap = (int)(LogicalGap * Dpi);
            int h = (int)(LogicalHeight * Dpi);
            Size = new Size(600, h);           // width resynced by the controller
            int total = 2 * btn + gap;
            int x0 = (600 - total) / 2;
            for (int i = 0; i < 2; i++)
            {
                int kind = i;                  // 0 chevron, 1 ring
                int code = i == 0 ? 4 : 3;     // keyevent BACK / HOME
                Buttons.Add(new NavButton(
                    new Rectangle(x0 + i * (btn + gap), (h - btn) / 2, btn, btn),
                    kind, delegate { owner.AdbKey(code); }));
            }
            WireInput();
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
            // Hairline along the top edge: rgba(255,255,255,0.10).
            using (Pen pen = new Pen(Color.FromArgb(26, 255, 255, 255), 1f))
                g.DrawLine(pen, 0, 0.5f, Width, 0.5f);
            foreach (NavButton b in Buttons)
            {
                DrawHoverFill(g, b);
                float cx = b.Circle.Left + b.Circle.Width / 2f;
                float cy = b.Circle.Top + b.Circle.Height / 2f;
                float opacity = b.Hover ? 1.0f : 0.72f;
                if (b.Kind == 0) DrawChevron(g, cx, cy, opacity);
                else DrawRing(g, cx, cy, opacity);
            }
        }

        public void ResyncWidth(int width)
        {
            if (width == Width) return;
            int btn = (int)(LogicalButton * Dpi);
            int gap = (int)(LogicalGap * Dpi);
            Size = new Size(width, Height);
            int total = 2 * btn + gap;
            int x0 = (width - total) / 2;
            for (int i = 0; i < Buttons.Count; i++)
                Buttons[i] = new NavButton(
                    new Rectangle(x0 + i * (btn + gap), (Height - btn) / 2, btn, btn),
                    Buttons[i].Kind, Buttons[i].Fire);
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

        public TopWindow(Controller owner)
            : base((int)(14 * ScaleOf()), (int)(14 * ScaleOf()))
        {
            float s = ScaleOf();
            int btn = (int)(LogicalButton * s);
            int pad = (int)(LogicalPad * s);
            int gap = (int)(LogicalGap * s);
            int w = 2 * pad + 3 * btn + 2 * gap;
            int h = 2 * pad + btn;
            Size = new Size(w, h);
            _glyphs = new string[3];
            _glyphs[0] = ((char)0xE921).ToString();   // ChromeMinimize
            _glyphs[1] = ((char)0xE922).ToString();   // ChromeMaximize
            _glyphs[2] = ((char)0xE8BB).ToString();   // ChromeClose
            for (int i = 0; i < 3; i++)
            {
                int index = i;
                Buttons.Add(new NavButton(
                    new Rectangle(pad + i * (btn + gap), pad, btn, btn), 2 + index,
                    delegate { owner.TopAction(index); }));
            }
            WireInput();
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

        public void SetMaximized(bool maximized)
        {
            _glyphs[1] = ((char)(maximized ? 0xE923 : 0xE922)).ToString();
        }

        protected override void PaintBar(Graphics g)
        {
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
    internal sealed class Controller : IDisposable
    {
        private const int TickMs = 50;
        private const int SampleMs = 220;
        private const int FirstWaitMs = 30000;
        private const int LostWaitMs = 15000;
        private const int TriggerTop = 6;      // logical px reveal band
        private const int RetainTop = 48;      // logical px hysteresis
        private const int TopMargin = 10;      // logical px from top-right
        private const int MaxGraceMs = 700;

        private readonly string _title, _serial, _adb;
        private readonly Timer _tick = new Timer();
        private readonly ChinWindow _chin;
        private readonly TopWindow _top;
        private IntPtr _hwnd = IntPtr.Zero;
        private int _waitedMs;
        private bool _repaired;
        private bool _fakedMax;
        private Rectangle _savedRect, _maxRect;
        private int _maxGraceUntil;
        private int _lastSample;
        private Bitmap _sample;                // full-window sample (reused)
        private NativeMethods.WinEventDelegate _hookProc;   // keep delegate alive
        private IntPtr _hook = IntPtr.Zero;
        private int _ticks;

        public Controller(string title, string serial, string adb)
        {
            _title = title; _serial = serial; _adb = adb;
            _chin = new ChinWindow(this);
            _top = new TopWindow(this);
            // Force handle creation now: the WinEvent callback below may fire
            // for any window move long before the bars are first shown, and
            // BeginInvoke requires an existing handle.
            if (!_chin.IsHandleCreated) { IntPtr h = _chin.Handle; }
            if (!_top.IsHandleCreated) { IntPtr h = _top.Handle; }
            _tick.Interval = TickMs;
            _tick.Tick += Tick;
            _tick.Start();
        }

        public void Dispose()
        {
            _tick.Stop();
            if (_hook != IntPtr.Zero)
            {
                NativeMethods.UnhookWinEvent(_hook);
                _hook = IntPtr.Zero;
            }
            if (_sample != null) { _sample.Dispose(); _sample = null; }
        }

        // -- actions used by the bars ----------------------------------------

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
            else if (index == 1) FakeMaximize(!_fakedMax);
            else if (index == 2)
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
            if (++_ticks % 100 == 0) Log.Write("alive #" + _ticks);
        }

        private void TickInner()
        {
            if (_hwnd == IntPtr.Zero || !NativeMethods.IsWindow(_hwnd))
            {
                Discover();
                return;
            }
            if (NativeMethods.IsIconic(_hwnd) || !NativeMethods.IsWindowVisible(_hwnd))
            {
                HideBars();
                return;
            }

            Rectangle client = ClientRect();
            Point cursor = CursorPosition();
            bool overWindow = client.Contains(cursor);
            bool overBars = _chin.Bounds.Contains(cursor) || _top.Bounds.Contains(cursor);
            bool foreground = NativeMethods.GetAncestor(
                NativeMethods.GetForegroundWindow(), 2 /*GA_ROOT*/) == _hwnd;
            // The chin belongs to the visible window; it must not float over
            // other apps, so it needs window activity or cursor proximity.
            bool barsAllowed = foreground || overWindow || overBars;
            if (!barsAllowed) { HideBars(); return; }

            SyncChin(client);
            bool showTop = ComputeTopVisibility(client, cursor);
            if (showTop && !_top.Visible)
            {
                _top.Left = client.Right - _top.Width - S(TopMargin);
                _top.Top = client.Top + S(TopMargin);
                _top.Show();
                Log.Write("top shown");
            }
            else if (!showTop && _top.Visible)
            {
                _top.Hide();
                Log.Write("top hidden");
            }

            DropStaleFakeMax();
            MaybeSample();
        }

        private void HideBars()
        {
            if (_chin.Visible) Log.Write("bars hidden");
            _chin.Hide();
            _top.Hide();
        }

        private bool ComputeTopVisibility(Rectangle client, Point cursor)
        {
            bool inX = cursor.X >= client.Left && cursor.X < client.Right;
            if (!inX) return _top.Visible && _top.Bounds.Contains(cursor);
            if (cursor.Y < client.Top + S(TriggerTop)) return true;
            return _top.Visible && cursor.Y < client.Top + S(RetainTop);
        }

        private void SyncChin(Rectangle client)
        {
            _chin.ResyncWidth(client.Width);
            _chin.Left = client.Left;
            _chin.Top = client.Bottom - _chin.Height;
            if (!_chin.Visible)
            {
                _chin.Show();
                Log.Write("chin shown");
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
                                SyncChin(ClientRect());
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
            int round = 2 /*DWMWCP_ROUND*/;
            NativeMethods.DwmSetWindowAttribute(_hwnd, 33 /*CORNER_PREFERENCE*/, ref round, 4);
            _repaired = true;
            Log.Write("window repaired: thickframe+round");
        }

        // -- fake maximize (taskbar-safe) ---------------------------------------

        private void DropStaleFakeMax()
        {
            if (!_fakedMax || Environment.TickCount < _maxGraceUntil) return;
            Rectangle r = WindowRect();
            if (Math.Abs(r.X - _maxRect.X) > 2 || Math.Abs(r.Y - _maxRect.Y) > 2 ||
                Math.Abs(r.Width - _maxRect.Width) > 2 || Math.Abs(r.Height - _maxRect.Height) > 2)
            {
                _fakedMax = false;
                Log.Write("fake maximize dropped");
            }
        }

        private void FakeMaximize(bool on)
        {
            if (on)
            {
                _savedRect = WindowRect();
                Rectangle wa = WorkArea();
                NativeMethods.RECT insets = FrameInsets();
                int x = wa.X - insets.Left;
                int y = wa.Y - insets.Top;
                int w = wa.Width + insets.Left + insets.Right;
                int h = wa.Height + insets.Top + insets.Bottom;
                NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero, x, y, w, h, 0x0014);
                _maxRect = new Rectangle(x, y, w, h);
                _fakedMax = true;
                _maxGraceUntil = Environment.TickCount + MaxGraceMs;
                Log.Write("fake maximize on " + _maxRect);
            }
            else
            {
                NativeMethods.SetWindowPos(_hwnd, IntPtr.Zero,
                    _savedRect.X, _savedRect.Y, _savedRect.Width, _savedRect.Height, 0x0014);
                _fakedMax = false;
                Log.Write("fake maximize off");
            }
            _top.SetMaximized(_fakedMax);
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
