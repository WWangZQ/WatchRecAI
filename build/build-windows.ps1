param([string]$Python = "$PSScriptRoot\..\.build-env\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
$projectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not (Test-Path -LiteralPath $Python)) { throw 'Create .build-env and install build/requirements-desktop.txt first; see docs/build.md.' }
Push-Location $projectDir
try {
    & $Python -m PyInstaller --noconfirm --distpath dist --workpath .build-output build/desktop.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    Copy-Item -LiteralPath README.md -Destination dist/WatchRec/README.md
    Copy-Item -LiteralPath THIRD_PARTY_NOTICES.md -Destination dist/WatchRec/THIRD_PARTY_NOTICES.md
    & $Python build/collect-notices.py
    if ($LASTEXITCODE -ne 0) { throw 'Dependency notices collection failed.' }
} finally { Pop-Location }
