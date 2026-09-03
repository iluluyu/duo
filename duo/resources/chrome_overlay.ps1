# Duo chrome overlay - hover-revealed window controls for a borderless scrcpy window.
#
# Runs on the Windows side (spawned by `duo mirror --chrome`). The scrcpy
# window is borderless (no title bar, no decorations), so this overlay adds
# the missing affordances back on demand:
#
#   cursor in the top edge band    -> minimize / maximize / close buttons
#   cursor in the bottom edge band -> back / home buttons (adb keyevents)
#
# While idle both bars are hidden: no pixels on screen, no mouse interception.
#
# Interop hardening (see plan.md section 7):
#   - The window title travels as base64 UTF-8: powershell.exe mangles raw
#     non-ASCII command line arguments (experiment finding).
#   - SetProcessDPIAware() is called before any window exists: scrcpy windows
#     use physical pixels and the overlay must match (150% display here).
#   - This file MUST stay UTF-8 with BOM: PowerShell 5.1 reads BOM-less
#     scripts as ANSI and the CJK button labels would turn to mojibake.
#
# Lifecycle: waits up to 30s for the scrcpy window, exits 15s after it is
# gone (covers engine crash-restarts); the WSL parent also terminates us.

param(
    [Parameter(Mandatory = $true)][string]$TitleB64,
    [Parameter(Mandatory = $true)][string]$Serial,
    [Parameter(Mandatory = $true)][string]$Adb
)

$ErrorActionPreference = 'Stop'

$title = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TitleB64))

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace DuoWin {
    public struct RECT { public int Left, Top, Right, Bottom; }
    public struct POINT { public int X, Y; }
    public static class U32 {
        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
            public static extern IntPtr FindWindowW(string cls, string title);
        [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
        [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
        [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
        [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint ga);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
        [DllImport("user32.dll")] public static extern bool PostMessageW(
            IntPtr h, uint msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
        [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
    }
}
"@

# Physical pixels from here on - must precede every WinForms type reference.
[DuoWin.U32]::SetProcessDPIAware() | Out-Null

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ---------------------------------------------------------------------------
# Logging (per-process file under Windows %TEMP%, readable from WSL via /mnt/c)
# ---------------------------------------------------------------------------
$logPath = Join-Path $env:TEMP ("duo-chrome-overlay-{0}.log" -f $PID)
function Log([string]$msg) {
    try {
        Add-Content -LiteralPath $logPath `
            -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss.fff'), $msg) -Encoding UTF8
    } catch {}
}

# ---------------------------------------------------------------------------
# Geometry / timing knobs (physical pixels, timer ms)
# ---------------------------------------------------------------------------
$BAND_PX = 48          # hover band height at each window edge
$BAR_H = 48            # control bar height
$BTN_W = 56            # top bar button width (glyph buttons)
$BTN_W_TEXT = 104      # bottom bar button width (label buttons)
$TICK_MS = 100
$FIRST_WAIT_MS = 30000 # initial wait for the scrcpy window
$LOST_WAIT_MS = 15000  # re-wait after the window disappears (engine restart)

$script:hwnd = [IntPtr]::Zero
$script:waitedMs = 0

# ---------------------------------------------------------------------------
# Control bars
# ---------------------------------------------------------------------------
function New-Bar([int]$width) {
    $f = New-Object System.Windows.Forms.Form
    $f.FormBorderStyle = 'None'
    $f.ShowInTaskbar = $false
    $f.TopMost = $true
    $f.StartPosition = 'Manual'
    $f.AutoScaleMode = 'None'
    $f.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 18)
    $f.ClientSize = New-Object System.Drawing.Size($width, $BAR_H)
    # Force handle creation, then make the bar click-through-focus: never
    # steal focus from the scrcpy window and stay out of Alt+Tab.
    $null = $f.Handle
    $GWL_EXSTYLE = -20
    $WS_EX_TOOLWINDOW = 0x00000080
    $WS_EX_NOACTIVATE = 0x08000000
    $ex = [DuoWin.U32]::GetWindowLong($f.Handle, $GWL_EXSTYLE)
    [void][DuoWin.U32]::SetWindowLong(
        $f.Handle, $GWL_EXSTYLE, $ex -bor $WS_EX_NOACTIVATE -bor $WS_EX_TOOLWINDOW)
    return $f
}

function New-GlyphButton([string]$glyph, [int]$x) {
    $b = New-Object System.Windows.Forms.Button
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderSize = 0
    $b.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(64, 64, 64)
    $b.ForeColor = [System.Drawing.Color]::White
    $b.Font = New-Object System.Drawing.Font('Segoe MDL2 Assets', 10)
    $b.Text = $glyph
    $b.Cursor = 'Hand'
    $b.Bounds = New-Object System.Drawing.Rectangle($x, 0, $BTN_W, $BAR_H)
    return $b
}

function New-TextButton([string]$label, [int]$x) {
    $b = New-Object System.Windows.Forms.Button
    $b.FlatStyle = 'Flat'
    $b.FlatAppearance.BorderSize = 0
    $b.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(64, 64, 64)
    $b.ForeColor = [System.Drawing.Color]::White
    $b.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
    $b.Text = $label
    $b.Cursor = 'Hand'
    $b.Bounds = New-Object System.Drawing.Rectangle($x, 0, $BTN_W_TEXT, $BAR_H)
    return $b
}

$script:topForm = New-Bar (3 * $BTN_W)
$btnMin = New-GlyphButton ([char]0xE921) 0
$script:btnMax = New-GlyphButton ([char]0xE922) $BTN_W
$btnClose = New-GlyphButton ([char]0xE8BB) (2 * $BTN_W)
$btnClose.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(232, 17, 35)
$script:topForm.Controls.Add($btnMin)
$script:topForm.Controls.Add($script:btnMax)
$script:topForm.Controls.Add($btnClose)

$script:botForm = New-Bar (2 * $BTN_W_TEXT)
$script:botForm.Controls.Add((New-TextButton ([char]0x8FD4 + [char]0x56DE) 0))
$script:botForm.Controls.Add((New-TextButton ([char]0x684C + [char]0x9762) $BTN_W_TEXT))

function Invoke-AdbKey([int]$code, [string]$name) {
    try {
        $out = & $Adb -s $Serial shell input keyevent $code 2>&1
        Log ("action {0} rc={1} out={2}" -f $name, $LASTEXITCODE, "$out")
    } catch {
        Log ("action {0} failed: {1}" -f $name, $_.Exception.Message)
    }
}

$btnMin.Add_Click({
    Log 'click minimize'
    [void][DuoWin.U32]::ShowWindow($script:hwnd, 6)          # SW_MINIMIZE
})
$script:btnMax.Add_Click({
    if ([DuoWin.U32]::IsZoomed($script:hwnd)) {
        Log 'click restore'
        [void][DuoWin.U32]::ShowWindow($script:hwnd, 9)      # SW_RESTORE
    } else {
        Log 'click maximize'
        [void][DuoWin.U32]::ShowWindow($script:hwnd, 3)      # SW_MAXIMIZE
    }
})
$btnClose.Add_Click({
    Log 'click close'
    [void][DuoWin.U32]::PostMessageW($script:hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
})
$script:botForm.Controls[0].Add_Click({ Invoke-AdbKey 4 'back' })
$script:botForm.Controls[1].Add_Click({ Invoke-AdbKey 3 'home' })

# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = $TICK_MS
$timer.Add_Tick({
    if ($script:hwnd -eq [IntPtr]::Zero) {
        # NullString.Value marshals as a true NULL pointer: a bare $null would become
# an empty string and silently break the "any class" lookup (experiment finding).
$script:hwnd = [DuoWin.U32]::FindWindowW([NullString]::Value, $script:title)
        if ($script:hwnd -ne [IntPtr]::Zero) {
            $script:waitedMs = 0
            Log ("window found title={0} hwnd={1}" -f $script:title, $script:hwnd)
        } else {
            $script:waitedMs += $TICK_MS
            if ($script:waitedMs -ge $FIRST_WAIT_MS) {
                Log 'giving up: scrcpy window never appeared'
                [System.Windows.Forms.Application]::ExitThread()
            }
        }
        return
    }
    if (-not [DuoWin.U32]::IsWindow($script:hwnd)) {
        Log 'window gone - waiting for engine restart'
        $script:hwnd = [IntPtr]::Zero
        $script:waitedMs = $FIRST_WAIT_MS - $LOST_WAIT_MS
        $script:topForm.Visible = $false
        $script:botForm.Visible = $false
        return
    }
    if ([DuoWin.U32]::IsIconic($script:hwnd)) {
        $script:topForm.Visible = $false
        $script:botForm.Visible = $false
        return
    }

    $r = New-Object DuoWin.RECT
    [void][DuoWin.U32]::GetWindowRect($script:hwnd, [ref]$r)
    $p = New-Object DuoWin.POINT
    [void][DuoWin.U32]::GetCursorPos([ref]$p)

    # Only reveal when the cursor truly hovers the scrcpy window itself
    # (GA_ROOT unwraps SDL's child render window; our own bars also qualify).
    $root = [DuoWin.U32]::GetAncestor([DuoWin.U32]::WindowFromPoint($p), 2)
    $overOurs = ($root -eq $script:topForm.Handle) -or ($root -eq $script:botForm.Handle)
    if (-not ($root -eq $script:hwnd -or $overOurs)) {
        $script:topForm.Visible = $false
        $script:botForm.Visible = $false
        return
    }

    $inX = ($p.X -ge $r.Left) -and ($p.X -lt $r.Right)
    $topHit = $inX -and ($p.Y -ge $r.Top) -and ($p.Y -lt ($r.Top + $BAND_PX))
    $botHit = $inX -and ($p.Y -ge ($r.Bottom - $BAND_PX)) -and ($p.Y -lt $r.Bottom)

    if ($topHit) {
        $script:btnMax.Text = if ([DuoWin.U32]::IsZoomed($script:hwnd)) {
            [string][char]0xE923                                   # restore glyph
        } else {
            [string][char]0xE922                                   # maximize glyph
        }
        $script:topForm.Left = $r.Right - (3 * $BTN_W)
        $script:topForm.Top = $r.Top
        $script:topForm.Visible = $true
    } else {
        $script:topForm.Visible = $false
    }
    if ($botHit) {
        $script:botForm.Left = $r.Left + [int](($r.Right - $r.Left - 2 * $BTN_W_TEXT) / 2)
        $script:botForm.Top = $r.Bottom - $BAR_H
        $script:botForm.Visible = $true
    } else {
        $script:botForm.Visible = $false
    }
})

Log ("overlay start title={0} serial={1} adb={2} pid={3}" -f $title, $Serial, $Adb, $PID)
$timer.Start()
[System.Windows.Forms.Application]::Run()
Log 'overlay exit'
