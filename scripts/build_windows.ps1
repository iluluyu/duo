# Build the Duo panel bundle on Windows: venv + PyInstaller (onedir, win64).
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

# --noconfirm overwrites dist\ and build\ left over from earlier runs.
.venv\Scripts\pyinstaller duo.spec --noconfirm

# Artifact sanity (PyInstaller >= 6 onedir puts data under _internal\):
# the windowed exe plus the sidecar files it loads at runtime - QML
# sources for the panel, the C# overlay source for --chrome windows.
$artifacts = @(
        'dist\Duo\Duo.exe',
        'dist\Duo\_internal\duo\ui\qml\Main.qml',
        'dist\Duo\_internal\duo\ui\qml\qmldir',
        'dist\Duo\_internal\duo\resources\chrome_overlay.cs'
)
foreach ($f in $artifacts) {
        if (-not (Test-Path $f)) { throw "missing artifact: $repo\$f" }
}

Write-Output "bundle ready: $repo\dist\Duo\Duo.exe"
Write-Output "smoke test  : dist\Duo\Duo.exe --check; `$LASTEXITCODE (0 = tools found)"
