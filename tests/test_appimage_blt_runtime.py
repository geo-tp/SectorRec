"""Regression tests for the missing BLT library in portable AppImages."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_appimage_stages_both_blt_libraries_inside_frozen_runtime():
    script = (ROOT / 'build-appimage.sh').read_text()
    assert 'PYI_LIBDIR="$APPDIR/usr/opt/SectorRec/_internal"' in script
    assert "'libBLT.2.5.so.*' 'libBLTlite.2.5.so.*'" in script
    assert 'cp -L -- "$library" "$PYI_LIBDIR/$name"' in script
    assert 'BLT_SOURCES+=("$library")' in script
    assert 'Missing $pattern: install the tk8.6-blt2.5 package' in script
    assert 'xvfb-run -a "$smoke_dir/squashfs-root/AppRun" --check-tk-runtime' in script
    assert 'find_library_deb_owner "$library"' in script


def test_frozen_tk_probe_loads_internal_blt_before_creating_window():
    entrypoint = (ROOT / 'launch_desktop.py').read_text()
    assert "sys.argv[1] == '--check-tk-runtime'" in entrypoint
    assert "Path(sys.executable).parent / '_internal'" in entrypoint
    assert 'ctypes.CDLL(str(matches[0]))' in entrypoint
    assert 'root = tk.Tk()' in entrypoint


@pytest.mark.skipif(sys.platform != 'linux' or shutil.which('xvfb-run') is None,
                    reason='Needs Xvfb on Linux')
def test_source_tk_probe_runs_headlessly():
    proc = subprocess.run(
        ['xvfb-run', '-a', sys.executable, str(ROOT / 'launch_desktop.py'), '--check-tk-runtime'],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
        env={**os.environ, 'SECTORREC_APPIMAGE': '1'},
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(sys.platform != 'linux' or not Path('/usr/lib/libBLT.2.5.so.8.6').exists(),
                    reason='Needs installed BLT to exercise the staging step')
def test_real_blt_libraries_are_copied_and_loadable(tmp_path):
    """Test the actual build-script BLT staging block, not just its source."""
    import ctypes
    script = (ROOT / 'build-appimage.sh').read_text()
    snippet = script.split('PYI_LIBDIR="$APPDIR/usr/opt/SectorRec/_internal"', 1)[1].split(
        'install -m 0755 "$(command -v fls)"', 1)[0]
    snippet = 'PYI_LIBDIR="$APPDIR/usr/opt/SectorRec/_internal"\n' + snippet
    appdir = tmp_path / 'app'
    runtime = appdir / 'usr/opt/SectorRec/_internal'
    runtime.mkdir(parents=True)
    (appdir / 'usr/lib').mkdir(parents=True)
    (runtime / '_tkinter.fake.so').write_bytes(b'probe')
    env = {**os.environ, 'APPDIR': str(appdir)}
    proc = subprocess.run(['bash', '-euo', 'pipefail', '-c', snippet],
                          env=env, text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    for library in ('libBLT.2.5.so.8.6', 'libBLTlite.2.5.so.8.6'):
        frozen_copy = runtime / library
        assert frozen_copy.is_file()
        assert (appdir / 'usr/lib' / library).is_file()
        ctypes.CDLL(str(frozen_copy))
