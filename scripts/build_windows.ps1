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

# --onefile: a single portable Duo.exe (user-facing default; slower to
# start than onedir because it self-extracts to temp on launch).
# The onedir duo.spec remains available for faster dev iteration:
#   .venv\Scripts\pyinstaller duo.spec --noconfirm
.venv\Scripts\pyinstaller --onefile --noconsole --name Duo `
        --icon assets/duo.ico `
        --add-data "duo/ui/qml;duo/ui/qml" `
        --add-data "duo/resources/chrome_overlay.cs;duo/resources" `
        --hidden-import PyQt6.QtQml --hidden-import PyQt6.QtQuick `
        gui_entry.py --noconfirm

# Artifact sanity for the single-file bundle.
$artifacts = @(
        'dist\Duo.exe'
)
foreach ($f in $artifacts) {
        if (-not (Test-Path $f)) { throw "missing artifact: $repo\$f" }
}

Write-Output "bundle ready: $repo\dist\Duo.exe (single file, portable)"
Write-Output "smoke test  : dist\Duo.exe --check; `$LASTEXITCODE (0 = tools found)"
