# SectorRec Windows x64 standalone release build.
# Run in PowerShell on Windows 10/11 with Python 3.12/3.13 and internet access.
# All third-party binaries are downloaded from their official projects and SHA256 checked.
param(
    [string]$PythonExe = 'python',
    [switch]$SkipTests,
    [switch]$KeepDownloadedArchives
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot
Write-Host 'SectorRec 1.0 Windows x64 build'
if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) { throw 'This build must run on native Windows.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'SectorRec Windows requires 64-bit Windows.' }
$root = $PSScriptRoot
$downloads = Join-Path $root '.windows-build'
New-Item -ItemType Directory -Force $downloads | Out-Null

function DownloadVerified([string] $url, [string] $filename, [string] $sha256) {
    $file = Join-Path $downloads $filename
    if (-not (Test-Path $file)) {
        Write-Host "Downloading $filename from official upstream..."
        Invoke-WebRequest -Uri $url -OutFile $file -MaximumRedirection 5
    }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $sha256) { throw "Checksum mismatch for $filename : $actual" }
    return $file
}

$tskZipVerified = DownloadVerified 'https://github.com/sleuthkit/sleuthkit/releases/download/sleuthkit-4.15.0/sleuthkit-4.15.0-win32.zip' 'sleuthkit-4.15.0-win32.zip' 'c2ebab8105b893d97bd8ce35b88e01985e2a106efc97f03adf95840a631b20ce'
$photoZipVerified = DownloadVerified 'https://www.cgsecurity.org/testdisk-7.2.win64.zip' 'testdisk-7.2.win64.zip' 'e97e203ce77b6b1a3a37d01beccf069dc6c4632b579ffbb82ae739cdda229f38'
$tskDir = Join-Path $root 'tsk-bin'
if (-not (Test-Path (Join-Path $tskDir 'fls.exe')) -or -not (Test-Path (Join-Path $tskDir 'icat.exe'))) {
    $tskZip = $tskZipVerified
    $tskUnpack = Join-Path $downloads 'unpacked-tsk'
    Expand-Archive -Path $tskZip -DestinationPath $tskUnpack -Force
    $fls = Get-ChildItem -Path $tskUnpack -Recurse -File -Filter 'fls.exe' | Select-Object -First 1
    if ($null -eq $fls) { throw 'Official Sleuth Kit zip did not include fls.exe' }
    New-Item -ItemType Directory -Force $tskDir | Out-Null
    Copy-Item -Path (Join-Path $fls.Directory.FullName '*') -Destination $tskDir -Recurse -Force
}
if (-not (Test-Path (Join-Path $tskDir 'icat.exe'))) { throw 'Missing icat.exe' }

$photoDir = Join-Path $root 'photorec-bin'
if (-not (Test-Path (Join-Path $photoDir 'photorec_win.exe')) -or -not (Test-Path (Join-Path $photoDir 'testdisk_win.exe'))) {
    $photoZip = $photoZipVerified
    $photoUnpack = Join-Path $downloads 'unpacked-photorec'
    Expand-Archive -Path $photoZip -DestinationPath $photoUnpack -Force
    $photo = Get-ChildItem -Path $photoUnpack -Recurse -File -Filter 'photorec_win.exe' | Select-Object -First 1
    if ($null -eq $photo) { throw 'Official CGSecurity zip did not include photorec_win.exe' }
    New-Item -ItemType Directory -Force $photoDir | Out-Null
    Copy-Item -Path (Join-Path $photo.Directory.FullName '*') -Destination $photoDir -Recurse -Force
}
if (-not (Test-Path (Join-Path $photoDir 'testdisk_win.exe'))) { throw 'Missing testdisk_win.exe' }

# GNU ddrescue for Windows: use the existing Cygwin runtime when available.
# This avoids restarting setup-x86_64.exe after a completed installation.
$ddDir = Join-Path $root 'ddrescue-bin'
$cygRoot = Join-Path $downloads 'cygwin-root'
$cygCache = Join-Path $downloads 'cygwin-cache'
$cygBin = Join-Path $cygRoot 'bin'
$requiredRuntime = @('ddrescue.exe', 'cygpath.exe', 'cygwin1.dll')
$bundledRuntimeReady = $true
foreach ($fileName in $requiredRuntime) {
    $path = Join-Path $ddDir $fileName
    if (-not (Test-Path -LiteralPath $path)) {
        $bundledRuntimeReady = $false
    }
}
if (-not $bundledRuntimeReady) {
    $missingCygwin = @()
    foreach ($fileName in $requiredRuntime) {
        $path = Join-Path $cygBin $fileName
        if (-not (Test-Path -LiteralPath $path)) {
            $missingCygwin += $fileName
        }
    }
    $ddInstalled = Test-Path -LiteralPath (Join-Path $cygBin 'ddrescue.exe')
    if ($ddInstalled -and $missingCygwin.Count -gt 0) {
        throw "Cygwin installation appears incomplete. Missing in $cygBin : $($missingCygwin -join ', '). Setup will not be restarted automatically."
    }
    if ($missingCygwin.Count -gt 0) {
        $setup = Join-Path $downloads 'setup-x86_64.exe'
        if (-not (Test-Path -LiteralPath $setup)) {
            Write-Host 'Downloading official Cygwin installer...'
            Invoke-WebRequest -Uri 'https://cygwin.com/setup-x86_64.exe' -OutFile $setup -MaximumRedirection 5
        }
        New-Item -ItemType Directory -Force $cygRoot,$cygCache | Out-Null
        $mirror = 'https://mirrors.kernel.org/sourceware/cygwin/'
        if ($env:SECTORREC_CYGWIN_MIRROR) {
            $mirror = $env:SECTORREC_CYGWIN_MIRROR
        }
        Write-Host 'Installing official Cygwin ddrescue into isolated build directory...'
        & $setup --quiet-mode --no-admin --no-shortcuts --no-desktop --no-startmenu `
            --only-site --site $mirror --root $cygRoot --local-package-dir $cygCache `
            --include-source --packages ddrescue,cygwin,libgcc1,libstdc++6
        if ($LASTEXITCODE -ne 0) {
            throw "Cygwin setup exited with code $LASTEXITCODE"
        }
    } else {
        Write-Host 'Reusing existing Cygwin installation; skipping setup...'
    }
    foreach ($fileName in $requiredRuntime) {
        if (-not (Test-Path -LiteralPath (Join-Path $cygBin $fileName))) {
            throw "Required Cygwin runtime missing: $fileName"
        }
    }
    New-Item -ItemType Directory -Force $ddDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $cygBin 'ddrescue.exe') -Destination $ddDir -Force
    Copy-Item -LiteralPath (Join-Path $cygBin 'cygpath.exe') -Destination $ddDir -Force
    # Copy all DLLs in this isolated Cygwin bin directory. This also covers
    # transitive dependencies without relying on cygcheck.exe output formatting.
    $cygDlls = @(Get-ChildItem -LiteralPath $cygBin -File -Filter '*.dll')
    if ($cygDlls.Count -eq 0) {
        throw 'No Cygwin runtime DLLs were found.'
    }
    foreach ($dll in $cygDlls) {
        Copy-Item -LiteralPath $dll.FullName -Destination $ddDir -Force
    }
    Write-Host "Prepared ddrescue runtime with $($cygDlls.Count) Cygwin DLL(s)."
} else {
    Write-Host 'Reusing existing packaged ddrescue runtime...'
}
# Safe self-check: --version does not access a disk.
$oldPath = $env:PATH
try {
    $env:PATH = "$ddDir;$oldPath"
    & (Join-Path $ddDir 'ddrescue.exe') --version
    if ($LASTEXITCODE -ne 0) {
        throw "ddrescue --version failed (exit code $LASTEXITCODE)"
    }
} finally {
    $env:PATH = $oldPath
}

$venvDir = Join-Path $root '.venv-windows'
if (-not (Test-Path (Join-Path $venvDir 'Scripts/python.exe'))) {
    & $PythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment' }
}
$python = Join-Path $venvDir 'Scripts/python.exe'
& $python -c "import struct, tkinter; assert struct.calcsize('P') == 8, '64-bit Python required'"
if ($LASTEXITCODE -ne 0) { throw 'Windows build requires 64-bit Python with Tkinter' }
$version = & $python -c 'from src import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Unable to read package version' }
& $python -m pip install --disable-pip-version-check 'pyinstaller>=6,<7' 'Pillow>=9.4' 'pytest>=8' 'pytest-timeout>=2'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (-not $SkipTests) {
    # Linux runners exercise the complete suite, including fcntl, sysfs and
    # Debian build tests. Windows runs its native regressions and shared
    # packaging checks without importing Linux-only test modules.
    $testFiles = @(Get-ChildItem -LiteralPath (Join-Path $root 'tests') -File -Filter 'test_win*.py' |
        Sort-Object Name | ForEach-Object { 'tests/' + $_.Name })
    $testFiles += @('tests/test_packaging.py', 'tests/test_sectorrec_branding.py',
                   'tests/test_v1_windows_installer_workflow.py',
                   'tests/test_v1_resume_loading_and_windows_testdisk_activity.py',
                   'tests/test_source_assessment.py', 'tests/test_alpha15_full_catalog.py')
    if ($testFiles.Count -lt 7) { throw 'Windows regression tests are missing' }
    # Capture full pytest diagnostics, including the failing traceback.
    # Start-Process gives a dependable exit code in Windows PowerShell 5.1.
    $testOut = Join-Path $downloads 'windows-portability-stdout.log'
    $testErr = Join-Path $downloads 'windows-portability-stderr.log'
    Write-Host 'Running Windows portability tests; full logs will be saved in .windows-build ...'
    $testProcess = Start-Process -FilePath $python -WorkingDirectory $root `
        -ArgumentList (@('-m','pytest','-q','-ra') + $testFiles) `
        -RedirectStandardOutput $testOut -RedirectStandardError $testErr -Wait -PassThru -NoNewWindow
    if ($testProcess.ExitCode -ne 0) {
        Write-Host 'Windows tests FAILED. Last 140 lines of diagnostics:' -ForegroundColor Red
        if (Test-Path $testErr) { Get-Content -LiteralPath $testErr -Tail 140 }
        if (Test-Path $testOut) { Get-Content -LiteralPath $testOut -Tail 140 }
        throw "Windows portability tests failed (exit $($testProcess.ExitCode)). See $testErr"
    }
    if (Test-Path $testErr) { Get-Content -LiteralPath $testErr -Tail 6 }
}
& $python build-desktop-bundle.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$exe = Join-Path $root 'dist/SectorRec.exe'
if (-not (Test-Path $exe)) { throw 'PyInstaller did not produce SectorRec.exe' }
$smoke = Join-Path $downloads 'windows-smoke.png'
if (Test-Path $smoke) { Remove-Item -LiteralPath $smoke }
# --windowed applications are not always waited for by PowerShell's call operator.
# Start-Process -Wait ensures the screenshot was produced by the real frozen EXE.
$smokeProcess = Start-Process -FilePath $exe -ArgumentList "--screenshot results --output `"$smoke`"" -Wait -PassThru
if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path $smoke)) { throw 'Windows screenshot smoke test failed' }
& $python -c 'import sys; from PIL import Image; Image.open(sys.argv[1]).verify()' $smoke
if ($LASTEXITCODE -ne 0) { throw 'Windows screenshot is not a valid image' }
$ddSmoke = Start-Process -FilePath $exe -ArgumentList '--selftest-ddrescue' -Wait -PassThru
if ($ddSmoke.ExitCode -ne 0) { throw 'Frozen Windows ddrescue runtime self-test failed' }
$bundle = Join-Path $root "dist/SectorRec-$version-Windows-x64.zip"
if (Test-Path $bundle) { Remove-Item $bundle }
Compress-Archive -LiteralPath $exe,(Join-Path $root 'LICENSE'),(Join-Path $root 'docs/windows.md'),(Join-Path $root 'docs/windows-ddrescue-licenses.md') -DestinationPath $bundle
$sourceTarballs = @(Get-ChildItem -Path (Join-Path $downloads 'cygwin-cache') -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '-src\.tar\.(xz|bz2|gz|zst)$' })
# Check both ddrescue and the Cygwin runtime itself have corresponding source.
foreach ($name in @('ddrescue', 'cygwin')) {
    if (-not @($sourceTarballs | Where-Object { $_.Name -match "^$name-[0-9].*-src\.tar\." }).Count) {
        throw "Missing corresponding Cygwin source package: $name"
    }
}
if ($sourceTarballs.Count -gt 0) {
    $sourceArchive = Join-Path $root 'dist/SectorRec-Cygwin-GPL-sources.zip'
    if (Test-Path $sourceArchive) { Remove-Item -LiteralPath $sourceArchive }
    Compress-Archive -LiteralPath @($sourceTarballs | ForEach-Object FullName) -DestinationPath $sourceArchive
    Write-Host "GPL runtime source archives: $sourceArchive"
} else {
    throw 'Cygwin source archives missing: refusing redistribution build. Run with a clean .windows-build/Cygwin installation to download corresponding sources.'
}
# The standalone Windows binary redistributes upstream recovery programs.
# Cygwin ddrescue sources above are necessary but not sufficient: ship
# corresponding versioned sources for Sleuth Kit and TestDisk/PhotoRec too.
$thirdPartySources = Join-Path $downloads 'upstream-sources'
New-Item -ItemType Directory -Force $thirdPartySources | Out-Null
$sourceItems = @(
    @{ Name='sleuthkit-4.15.0-source.tar.gz'; Url='https://github.com/sleuthkit/sleuthkit/archive/refs/tags/sleuthkit-4.15.0.tar.gz' },
    @{ Name='testdisk-7.2-source.tar.bz2'; Url='https://www.cgsecurity.org/testdisk-7.2.tar.bz2' }
)
$sourceFileList = @()
$manifestLines = @('# Source archives accompanying the bundled Windows third-party binaries')
foreach ($item in $sourceItems) {
    $target = Join-Path $thirdPartySources $item.Name
    if (-not (Test-Path -LiteralPath $target)) {
        Invoke-WebRequest -Uri $item.Url -OutFile $target -MaximumRedirection 5
    }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Get-Item $target).Length -eq 0) {
        throw "Third-party corresponding source missing: $($item.Name)"
    }
    $sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestLines += "$($item.Name)  $sha256  $($item.Url)"
    $sourceFileList += $target
}
$manifestFile = Join-Path $thirdPartySources 'SOURCES.txt'
$manifestLines | Set-Content -LiteralPath $manifestFile -Encoding UTF8
$thirdPartyArchive = Join-Path $root 'dist/SectorRec-Windows-thirdparty-sources.zip'
if (Test-Path $thirdPartyArchive) { Remove-Item -LiteralPath $thirdPartyArchive }
Compress-Archive -LiteralPath @($sourceFileList + @($manifestFile)) -DestinationPath $thirdPartyArchive
if (-not (Test-Path $thirdPartyArchive) -or (Get-Item $thirdPartyArchive).Length -eq 0) {
    throw 'No Windows upstream source archive: refusing publication'
}
Write-Host "Windows upstream source archive: $thirdPartyArchive"
# Explicitly inventory every redistributed executable/DLL, and point users to
# source archives which MUST ship alongside any public Windows binary release.
$legalFile = Join-Path $root 'dist/THIRD_PARTY_NOTICES.txt'
$legal = @(
    'SectorRec 1.0 Windows: bundled upstream components and source availability',
    'SectorRec application: MIT (see LICENSE)',
    'Sleuth Kit 4.15.0: bundled upstream release; source: SectorRec-Windows-thirdparty-sources.zip',
    'TestDisk and PhotoRec 7.2: bundled upstream release; source: SectorRec-Windows-thirdparty-sources.zip',
    'GNU ddrescue and Cygwin: upstream packages; source: SectorRec-Cygwin-GPL-sources.zip',
    'Cygwin license information: https://cygwin.com/licensing.html',
    'Full licensing guidance: docs/windows-ddrescue-licenses.md',
    'The source bundles are separate required GitHub release attachments.',
    '',
    'SHA256 of binaries distributed inside the portable executable:'
)
foreach ($dir in @($tskDir, $photoDir, $ddDir)) {
    Get-ChildItem -LiteralPath $dir -File -Recurse | Where-Object { $_.Extension -in @('.exe','.dll') } | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart([char[]]@('\','/'))
        $legal += "$relative  $((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
}
$legal | Set-Content -LiteralPath $legalFile -Encoding UTF8
if (-not (Test-Path -LiteralPath $legalFile) -or (Get-Item $legalFile).Length -eq 0) {
    throw 'Missing third-party runtime notice; refusing Windows release build'
}
# Add license and provenance notice to the portable archive. Source archives
# remain separately attached to the GitHub release to avoid duplication.
Compress-Archive -LiteralPath $legalFile -DestinationPath $bundle -Update
if (-not $KeepDownloadedArchives) {
    Remove-Item -Path (Join-Path $downloads 'unpacked-tsk'),(Join-Path $downloads 'unpacked-photorec') -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Completed: $bundle"
Write-Host 'Windows raw disk recovery + ddrescue: UAC requested when a physical disk method is selected.'
