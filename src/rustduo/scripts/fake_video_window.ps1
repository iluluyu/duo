$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
}
"@
[Win]::SetProcessDPIAware() | Out-Null
$dir = '<duo-chin-test 目录>'
function Log([string]$m) { [System.IO.File]::AppendAllText("$dir\harness.log", "$m`r`n") }
Set-Variable -Name dir -Scope Script
Set-Variable -Name Log -Scope Script

$f = New-Object System.Windows.Forms.Form
$f.Text = 'DUOFAKEVIDEO'
$f.StartPosition = 'Manual'
$f.FormBorderStyle = 'None'
$f.Location = New-Object System.Drawing.Point(140, 140)
$f.Size = New-Object System.Drawing.Size(960, 600)

$f.Add_Paint({
  param($s, $e)
  $g = $e.Graphics
  $g.Clear([System.Drawing.Color]::FromArgb(245,245,247))
  $stripes = @(
    @(192,57,43), @(41,128,185), @(39,174,96), @(142,68,173), @(243,156,18),
    @(192,57,43), @(41,128,185), @(39,174,96), @(142,68,173), @(243,156,18))
  for ($i = 0; $i -lt 10; $i++) {
    $b = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb($stripes[$i][0],$stripes[$i][1],$stripes[$i][2]))
    $g.FillRectangle($b, 20 + $i * 36, 40, 30, 560)
    $b.Dispose()
  }
  $ink = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(29,29,31))
  $font = New-Object System.Drawing.Font('Segoe UI', 22, [System.Drawing.FontStyle]::Regular)
  $g.DrawString('DUO fake video 0123 ABCabc', $font, $ink, 400, 80)
  $g.DrawString('sharp text to blur 98765', $font, $ink, 400, 140)
  $small = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Regular)
  $g.DrawString('chin floats over this content; corners must be clean', $small, $ink, 400, 210)
  $bb = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::Black)
  $g.FillRectangle($bb, 400, 260, 200, 90)
  $bb.Dispose()
  $ww = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
  $g.FillRectangle($ww, 620, 260, 200, 90)
  $ww.Dispose()
  # 判别实验：岛正后方一块纯红（亚克力是否采样窗口内容）
  $rr = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(220,30,30))
  $g.FillRectangle($rr, 100, 516, 760, 70)
  $rr.Dispose()
  $ink.Dispose(); $font.Dispose(); $small.Dispose()
})

$script:combos = @(
  @{ glass = '1'; theme = 'dark';  name = 'glass-dark'  },
  @{ glass = '0'; theme = 'dark';  name = 'plain-dark'  },
  @{ glass = '1'; theme = 'light'; name = 'glass-light' },
  @{ glass = '0'; theme = 'light'; name = 'plain-light' }
)
$script:state = 0
$script:idx = 0
$script:ov = $null

$t = New-Object System.Windows.Forms.Timer
$t.Interval = 3000
$t.Add_Tick({
  try {
    if ($script:state % 2 -eq 0) {
      if ($script:ov -ne $null) {
        Stop-Process -Id $script:ov.Id -Force -ErrorAction SilentlyContinue
        $script:ov = $null
      }
      if ($script:idx -ge $script:combos.Count) {
        $t.Stop()
        [System.IO.File]::WriteAllText("$script:dir\harness-done.txt", "ok")
        $f.Close()
        return
      }
      $c = $script:combos[$script:idx]
      Log ("launch " + $c.name)
      $script:ov = Start-Process -FilePath "$script:dir\DuoChromeOverlay.exe" -ArgumentList @(
        '--title','DUOFAKEVIDEO','--serial','TEST','--adb','C:\Windows\System32\cmd.exe',
        '--home','1','--display-mode','flex','--chrome-top','none','--chrome-bottom','native',
        '--glass',$c.glass,'--bar-theme',$c.theme) -PassThru
      [Win]::SetForegroundWindow($f.Handle) | Out-Null
      $f.Invalidate(); $f.Refresh()
      $script:state++
    } else {
      $c = $script:combos[$script:idx]
      [Win]::SetForegroundWindow($f.Handle) | Out-Null
      Start-Sleep -Milliseconds 400
      $r = New-Object Win+RECT
      [Win]::GetWindowRect($f.Handle, [ref]$r) | Out-Null
      $w = $r.R - $r.L; $h = $r.B - $r.T
      $bmp = New-Object System.Drawing.Bitmap($w, $h)
      $g2 = [System.Drawing.Graphics]::FromImage($bmp)
      $g2.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size($w, $h)))
      $bmp.Save("$script:dir\chin-$($c.name).png", [System.Drawing.Imaging.ImageFormat]::Png)
      $g2.Dispose(); $bmp.Dispose()
      Log ("saved " + $c.name)
      $script:idx++
      $script:state++
    }
  } catch {
    Log ("ERR " + $_.Exception.Message)
    $t.Stop()
    [System.IO.File]::WriteAllText("$script:dir\harness-done.txt", "error")
    $f.Close()
  }
})

$f.Add_Shown({ $t.Start() })
[void]$f.ShowDialog()
Log "closed"
