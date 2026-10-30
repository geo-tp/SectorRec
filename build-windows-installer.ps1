# Build the optional per-user Windows installer, reusing the working portable EXE.
# Does not rebuild SectorRec, fetch dependencies, or require Administrator rights.
param([string]$IsccPath = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = $PSScriptRoot
Set-Location $root
$exe = Join-Path $root 'dist\SectorRec.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw 'Missing dist\SectorRec.exe. Build and smoke-test the portable EXE with build-windows.ps1 first.'
}
if (-not $IsccPath) {
    $knownPaths = @(
        (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Inno Setup 6\ISCC.exe'),
        (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Inno Setup 6\ISCC.exe')
    )
    foreach ($candidate in $knownPaths) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $IsccPath = $candidate
            break
        }
    }
}
if (-not $IsccPath) {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $IsccPath = $found.Source }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Inno Setup 6 (ISCC.exe) not found. Install Inno Setup 6, or pass -IsccPath. The portable EXE remains usable.'
}
$python = Join-Path $root '.venv-windows\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Missing .venv-windows Python. Build the portable EXE with build-windows.ps1 first.'
}
$version = & $python -c 'from src import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0 -or -not $version -or $version -notmatch '^\d+\.\d+(\.\d+)?([.-][A-Za-z0-9.]+)?$') {
    throw 'Unable to read a valid SectorRec version from the Windows Python environment.'
}
$iss = Join-Path $root 'SectorRec.iss'
& $IsccPath "/DAppVersion=$version" $iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed (exit $LASTEXITCODE)" }
$setup = Join-Path $root "dist\SectorRec-$version-Windows-x64-Setup.exe"
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw "Installer missing: $setup" }
Write-Host "Created: $setup"
Write-Host 'Portable EXE kept unchanged: dist\SectorRec.exe'
