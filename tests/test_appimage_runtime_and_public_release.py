"""Release smoke guardrails: Tk/BLT runtime and curated public downloads."""
from pathlib import Path
import subprocess
import sys
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_appimage_explicitly_bundles_blt_and_verifies_real_tk():
    script = (ROOT / 'build-appimage.sh').read_text()
    entrypoint = (ROOT / 'launch_desktop.py').read_text()
    assert 'BLT_SOURCES=()' in script
    assert "'libBLT.2.5.so.*' 'libBLTlite.2.5.so.*'" in script
    assert 'Missing $pattern: install the tk8.6-blt2.5 package' in script
    assert 'bundled Tk library' in script
    assert 'find_library_deb_owner "$library"' in script
    assert 'xvfb-run -a "$smoke_dir/squashfs-root/AppRun" --check-tk-runtime' in script
    assert 'xvfb-run -a "$smoke_dir/squashfs-root/AppRun" --check-askpass-subprocess' in script
    assert "sys.argv[1] == '--check-tk-runtime'" in entrypoint
    assert 'root = tk.Tk()' in entrypoint
    # Still enforce the existing license / source manifest verification.
    assert 'sort -u "$manifest" -o "$manifest"' in script


@pytest.mark.skipif(sys.platform != 'linux' or shutil.which('xvfb-run') is None,
                    reason='Requires xvfb-run on Linux')
def test_packaging_tk_probe_initializes_a_real_window():
    # This is a *source* smoke test. GitHub Actions additionally invokes the
    # frozen AppRun probe during AppImage packaging.
    result = subprocess.run(['xvfb-run', '-a', sys.executable, str(ROOT / 'launch_desktop.py'),
                             '--check-tk-runtime'], cwd=ROOT, timeout=12,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_release_publishes_named_user_downloads_and_preserves_legal_sources():
    """Keep the current release layout in sync with the actual build outputs.

    The release publishes the seven platform downloads directly, together with
    corresponding-source archives and notices.  No intermediate public-assets
    repackaging step is used by the current workflow.
    """
    flow = (ROOT / '.github/workflows/packages.yml').read_text()
    appimage = (ROOT / 'build-appimage.sh').read_text()
    windows = (ROOT / 'build-windows.ps1').read_text()

    assert 'gh release create "$RELEASE_TAG" release-assets/*' in flow
    assert 'gh release upload "$RELEASE_TAG" release-assets/* --clobber' in flow
    assert '(cd release-assets && sha256sum ./* > SHA256SUMS.txt)' in flow
    assert 'public-assets/*' not in flow

    # AppImages are built with public Debian-style architecture names.
    assert 'SectorRec-${VERSION}-Linux-${PUBLIC_ARCH}.AppImage' in appimage
    assert 'x86_64) PUBLIC_ARCH=amd64' in appimage
    assert 'aarch64) PUBLIC_ARCH=arm64' in appimage
    assert 'SectorRec-${version}-Linux-${{ matrix.arch }}.AppImage' in flow
    assert 'SectorRec-${version_app}-Linux-${image_arch}.AppImage' in flow

    # Both Windows downloads have an unambiguous role. The raw executable
    # remains a CI artifact, not a misleading standalone release download.
    assert 'SectorRec-$version-Windows-x64-Portable.zip' in windows
    assert 'SectorRec-${version_app}-Windows-x64-Portable.zip' in flow
    assert 'SectorRec-${version_app}-Windows-x64-Setup.exe' in flow
    assert 'rm -f release-assets/SectorRec.exe' in flow
    assert flow.index('rm -f release-assets/SectorRec.exe') < flow.index(
        '(cd release-assets && sha256sum ./* > SHA256SUMS.txt)')

    # These three .deb files complete the seven end-user downloads.
    for name in (
        'SectorRec-${version_app}-Linux-amd64.deb',
        'SectorRec-${version_app}-Linux-arm64.deb',
        'SectorRec-${version_app}-Cardputer-Zero-arm64.deb',
    ):
        assert name in flow

    # Preserve redistributable third-party source archives and notices,
    # even though they are not counted as end-user application downloads.
    for name in (
        'SectorRec-Cygwin-GPL-sources.zip',
        'SectorRec-Windows-thirdparty-sources.zip',
        'THIRD_PARTY_NOTICES.txt',
        'sectorrec_${version%-*}.orig.tar.gz',
        'sectorrec_${version}.dsc',
        'sectorrec_${version}.debian.tar.xz',
    ):
        assert name in flow
