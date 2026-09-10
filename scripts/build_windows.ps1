# Build the Duo panel and deploy the FIXED artifact C:\Tools\Duo.exe.
# Prereq: 64-bit Windows Python with the py launcher; repo at C:\duo
# (or pass the repo path as the first argument).
$ErrorActionPreference = 'Stop'

$repo = if ($args[0]) { $args[0] } else { 'C:\duo' }
Set-Location $repo

# Stale-copy guard (the "rebuilt but still old icon" trap): C:\duo is a
# robocopy of the WSL tree; if the icon-critical files drifted, refuse to
# build and demand a fresh robocopy first. Skipped when the share is not
# reachable (e.g. building from a checkout elsewhere).
$wslRepo = '\\wsl.localhost\archlinux\home\luyu\duo'
if (Test-Path $wslRepo -ErrorAction SilentlyContinue) {
        foreach ($f in @('duo.spec', 'duo\ui\app.py', 'assets\duo.ico', 'gui_entry.py')) {
                $here = Get-FileHash -Algorithm SHA256 (Join-Path $repo $f)
                $there = Get-FileHash -Algorithm SHA256 (Join-Path $wslRepo $f)
                if ($here.Hash -ne $there.Hash) {
                        throw "stale copy: $f differs from $wslRepo - re-run robocopy first"
                }
        }
}

# Reuse the dev venv when present so repeat builds stay incremental;
# the build extra (pyproject.toml) is what pulls in pyinstaller.
if (-not (Test-Path '.venv\Scripts\python.exe')) {
        py -m venv .venv
}
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -e ".[dev,gui,build]"

# win64 bundle needs 64-bit Python (PyInstaller targets the running
# interpreter's architecture).
$bits = .venv\Scripts\python -c "import struct; print(struct.calcsize('P') * 8)"
if ($bits -ne '64') { throw "need 64-bit Windows Python for the win64 bundle (got ${bits}-bit)" }

# Onefile via the committed spec: dist\Duo.exe (single portable file,
# self-extracts to temp on launch). The spec is the single source of
# truth for datas/hiddenimports - no flag soup duplicated here.
.venv\Scripts\pyinstaller duo.spec --noconfirm
if (-not (Test-Path 'dist\Duo.exe')) { throw "missing artifact: $repo\dist\Duo.exe" }

# Hard evidence the exe embeds the current ico (all RT_ICON frames).
.venv\Scripts\python scripts\verify_exe_icon.py dist\Duo.exe assets\duo.ico
if ($LASTEXITCODE -ne 0) { throw "dist\Duo.exe does not embed assets\duo.ico" }

# Deploy the fixed artifact. A running panel locks the file, so close it
# first (stateless launcher - restartable any time). NOTE: no `2>$null`
# here - under $ErrorActionPreference='Stop' PS 5.1 turns a native command's
# stderr redirect into a terminating NativeCommandError when Duo.exe is not
# running ("process not found" on stderr), which silently killed the deploy
# step after a successful build. | Out-Null + reading $LASTEXITCODE is safe
# both ways (1/128 = not running, which is fine).
taskkill /IM Duo.exe /F | Out-Null
$kill = $LASTEXITCODE
Write-Output "taskkill exit: $kill (1/128 = not running, fine)"
New-Item -ItemType Directory -Force -Path C:\Tools | Out-Null
Move-Item -Force dist\Duo.exe C:\Tools\Duo.exe

Write-Output "deployed: C:\Tools\Duo.exe"
Write-Output "smoke test: C:\Tools\Duo.exe --check; `$LASTEXITCODE (0 = tools found)"
Write-Output 'if Explorer still shows the OLD icon: ie4uinit.exe -show + restart explorer (icon cache); pinned taskbar entries need unpin + repin'
