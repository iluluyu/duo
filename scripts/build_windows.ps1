# Build the Duo panel and deploy the FIXED artifact C:\Tools\Duo.exe.
# Prereq: 64-bit Windows Python with the py launcher; repo at C:\duo
# (or pass the repo path as the first argument).
$ErrorActionPreference = 'Stop'

$repo = if ($args[0]) { $args[0] } else { 'C:\duo' }
Set-Location $repo

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

# Deploy the fixed artifact. A running panel locks the file, so close it
# first (stateless launcher - restartable any time).
taskkill /IM Duo.exe /F 2>$null
New-Item -ItemType Directory -Force -Path C:\Tools | Out-Null
Move-Item -Force dist\Duo.exe C:\Tools\Duo.exe

Write-Output "deployed: C:\Tools\Duo.exe"
Write-Output "smoke test: C:\Tools\Duo.exe --check; `$LASTEXITCODE (0 = tools found)"
