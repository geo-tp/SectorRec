"""Regression: AppImage host processes must not inherit PyInstaller binaries."""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from src.infrastructure.appimage_environment import host_environment
from src.views.desktop_native import DesktopWindow

ROOT = Path(__file__).resolve().parents[1]


def test_appimage_restores_original_host_ld_path():
    env = dict(SECTORREC_APPIMAGE='1',
               SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT='1',
               SECTORREC_HOST_LD_LIBRARY_PATH='/usr/local/mine',
               LD_LIBRARY_PATH='/tmp/AppDir/usr/opt/SectorRec/_internal:/tmp/AppDir/usr/lib',
               LD_LIBRARY_PATH_ORIG='/tmp/AppDir/usr/lib',
               SUDO_ASKPASS='/tmp/AppDir/usr/bin/sectorrec-askpass',
               DISPLAY=':99')
    with patch.dict(os.environ, env, clear=True):
        clean = host_environment()
    assert clean['LD_LIBRARY_PATH'] == '/usr/local/mine'
    assert clean['SUDO_ASKPASS'] == env['SUDO_ASKPASS']
    assert clean['DISPLAY'] == ':99'
    assert clean['PYINSTALLER_RESET_ENVIRONMENT'] == '1'


def test_appimage_removes_loader_when_host_did_not_set_one():
    with patch.dict(os.environ, {
        'SECTORREC_APPIMAGE': '1',
        'SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT': '0',
        'LD_LIBRARY_PATH': '/tmp/AppDir/usr/lib',
        'LD_LIBRARY_PATH_ORIG': '/tmp/AppDir/usr/lib',
    }, clear=True):
        clean = host_environment()
    assert 'LD_LIBRARY_PATH' not in clean


def test_appimage_child_sudo_keeps_askpass_but_not_foreign_libraries(monkeypatch):
    from src.infrastructure import appimage_sudo as auth
    monkeypatch.setattr(auth, '_SUDO', '/usr/bin/sudo')
    observed = {}
    def fake_run(cmd, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stderr=b'')
    with patch.dict(os.environ, {
        'SECTORREC_APPIMAGE': '1',
        'SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT': '0',
        'LD_LIBRARY_PATH': '/tmp/AppDir/usr/lib',
        'SUDO_ASKPASS': '/tmp/AppDir/usr/bin/sectorrec-askpass',
    }), patch('src.infrastructure.appimage_sudo.subprocess.run', side_effect=fake_run):
        auth._run('-A', '-v')
    assert 'LD_LIBRARY_PATH' not in observed['env']
    assert observed['env']['SUDO_ASKPASS'].endswith('/sectorrec-askpass')


def test_appimage_source_picker_passes_clean_environment():
    window = DesktopWindow.__new__(DesktopWindow)
    class Root:
        def after(self, delay, fn):
            pass
    window.root = Root()
    window._native_picker_busy = False
    def fake_run(cmd, **kwargs):
        assert 'zenity' in cmd[0]
        assert 'LD_LIBRARY_PATH' not in kwargs['env']
        return subprocess.CompletedProcess(cmd, 0, '/tmp/source.img\n', '')
    with patch('src.views.desktop_native.sys.platform', 'linux'), \
         patch('src.views.desktop_native.shutil.which', return_value='/usr/bin/zenity'), \
         patch.dict(os.environ, {'SECTORREC_APPIMAGE': '1',
                                  'SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT': '0',
                                  'LD_LIBRARY_PATH': '/tmp/AppDir/usr/lib'}), \
         patch('src.views.desktop_native.subprocess.run', side_effect=fake_run), \
         patch('src.views.desktop_native.filedialog.askopenfilename') as fallback:
        assert window.pick_source_image() == '/tmp/source.img'
        fallback.assert_not_called()


def test_appimage_askpass_has_fresh_bootloader_and_smoke_test():
    build = (ROOT / 'build-appimage.sh').read_text()
    entry = (ROOT / 'launch_desktop.py').read_text()
    assert 'export PYINSTALLER_RESET_ENVIRONMENT=1' in build
    assert 'AppRun" --check-askpass-subprocess' in build
    assert "sys.argv[1] == '--check-askpass-subprocess'" in entry
    assert "askpass(smoke_test='--smoke-test' in sys.argv[2:])" in entry


@pytest.mark.skipif(sys.platform != 'linux' or not __import__('shutil').which('xvfb-run'),
                    reason='requires Xvfb')
def test_real_askpass_dialog_automatically_closes_headlessly():
    cp = subprocess.run(['xvfb-run', '-a', sys.executable,
                         str(ROOT / 'launch_desktop.py'),
                         '--sectorrec-askpass', '--smoke-test'],
                        text=True, capture_output=True, timeout=15,
                        env={**os.environ, 'SECTORREC_APPIMAGE': '1'})
    assert cp.returncode == 0, cp.stderr
    assert not cp.stdout.strip()  # never echo credentials
