"""Release smoke guardrails: Tk/BLT runtime and curated public downloads."""
from pathlib import Path
import os
import subprocess
import sys
import textwrap
import re
import shutil
from zipfile import ZipFile

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


def test_release_publishes_only_seven_user_downloads_plus_sources_and_sha(tmp_path):
    flow = (ROOT / '.github/workflows/packages.yml').read_text()
    assert 'gh release create "$RELEASE_TAG" public-assets/*' in flow
    assert 'gh release upload "$RELEASE_TAG" public-assets/* --clobber' in flow
    assert '(cd public-assets && sha256sum ./* > SHA256SUMS.txt)' in flow
    assert 'gh release create "$RELEASE_TAG" release-assets/*' not in flow
    assert 'gh release upload "$RELEASE_TAG" release-assets/*' not in flow
    script = flow.split("python3 - <<'PYCODE'\n", 1)[1].split('\n          PYCODE', 1)[0]
    script = textwrap.dedent(script)
    src = tmp_path / 'release-assets'
    src.mkdir()
    __version__ = re.search(r"__version__\s*=\s*['\"]([^'\"]+)",
                            (ROOT / 'src/__init__.py').read_text()).group(1)
    names = [
        f'SectorRec-{__version__}-Windows-x64-Setup.exe',
        f'SectorRec-{__version__}-Windows-x64.zip',
        f'SectorRec-{__version__}-Linux-amd64.deb',
        f'SectorRec-{__version__}-Linux-arm64.deb',
        f'SectorRec-{__version__}-Cardputer-Zero-arm64.deb',
        f'SectorRec_{__version__}_x86_64.AppImage',
        f'SectorRec_{__version__}_aarch64.AppImage',
    ]
    legal = ['SectorRec-Cygwin-GPL-sources.zip',
             'SectorRec-Windows-thirdparty-sources.zip',
             'THIRD_PARTY_NOTICES.txt',
             f'sectorrec_{__version__}.orig.tar.gz',
             f'sectorrec_{__version__}-9.dsc',
             f'sectorrec_{__version__}-9.debian.tar.xz']
    for name in names + legal + ['SectorRec.exe']:
        (src / name).write_bytes(b'dummy-binary')
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'LICENSE').write_text('MIT')
    for name in ['distribution-licenses.md', 'windows-ddrescue-licenses.md']:
        (tmp_path / 'docs' / name).write_text('Notices')
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    published = {p.name for p in (tmp_path / 'public-assets').iterdir()}
    assert len(published) == 8  # seven editions + one source/notice archive
    assert set(names).issubset(published)
    assert 'SectorRec.exe' not in published
    source_bundle = tmp_path / 'public-assets' / f'SectorRec-{__version__}-Sources-and-Licenses.zip'
    with ZipFile(source_bundle) as zip_file:
        inside = set(zip_file.namelist())
    assert set(legal).issubset(inside)
    assert 'LICENSE' in inside
    assert 'docs/distribution-licenses.md' in inside
