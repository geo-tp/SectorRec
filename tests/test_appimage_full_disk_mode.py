"""Portable AppImage must match Debian disk operations without root GUI."""
import os
import subprocess
import sys
import zipapp
from pathlib import Path
from unittest.mock import patch
import pytest

from src.infrastructure.privileged import helper_command, privileged_command
from src.infrastructure.platforms import current_platform
from src.helpers.bundled_tools import binary, isolated_tool_env

ROOT = Path(__file__).resolve().parents[1]


def _image_env(tmp_path):
    bundle = tmp_path / 'sectorrec-root.pyz'
    bundle.touch()
    askpass = tmp_path / 'sectorrec-askpass'
    askpass.touch()
    return {
        'SECTORREC_APPIMAGE': '1',
        'SECTORREC_ROOT_BUNDLE': str(bundle),
        'SECTORREC_ASKPASS': str(askpass),
        'SECTORREC_IMAGE_ONLY': '',
        'SECTORREC_ROOT_STAGED': '1',
    }


def test_appimage_exposes_linux_disks_and_uses_sudo_askpass(tmp_path):
    env = _image_env(tmp_path)
    with patch.dict(os.environ, env), patch('shutil.which', side_effect=lambda name: '/usr/bin/sudo' if name == 'sudo' else None), patch('src.infrastructure.appimage_sudo.authorize', return_value='/usr/bin/sudo'):
        assert current_platform().raw_drives
        assert helper_command('scan', '/dev/sdb') == [
            '/usr/bin/sudo', '-A', '/usr/bin/python3', '-I',
            env['SECTORREC_ROOT_BUNDLE'], 'reader', 'scan', '/dev/sdb']
        assert privileged_command('image', '/dev/sdb', '/tmp/SectorRec_sdb.img')[-3:] == [
            'image', '/dev/sdb', '/tmp/SectorRec_sdb.img']
        assert privileged_command('photo', '/dev/sdb', '/tmp/SectorRec_261029', 'all')[-4:] == [
            'photo', '/dev/sdb', '/tmp/SectorRec_261029', 'all']


def test_appimage_can_fall_back_to_polkit(tmp_path):
    env = _image_env(tmp_path)
    with patch.dict(os.environ, env), patch('shutil.which', side_effect=lambda name: '/usr/bin/pkexec' if name == 'pkexec' else None):
        assert helper_command('scan', '/dev/sdb')[:4] == ['/usr/bin/pkexec', '/usr/bin/python3', '-I', env['SECTORREC_ROOT_BUNDLE']]


def test_image_only_explicit_override_remains_available(tmp_path):
    with patch.dict(os.environ, {**_image_env(tmp_path), 'SECTORREC_IMAGE_ONLY': '1'}):
        assert not current_platform().raw_drives


def test_invalid_device_injection_is_rejected_before_elevation(tmp_path):
    with patch.dict(os.environ, _image_env(tmp_path)):
        for bad in ('/tmp/file', '/dev/sda;touch /tmp/pwn', '/dev/disk/by-id/thing'):
            with pytest.raises(ValueError):
                helper_command('scan', bad)


def test_portable_root_archive_is_isolated_and_validates_device(tmp_path):
    source = tmp_path / 'archive'
    for module in (ROOT / 'src').rglob('*.py'):
        dest = source / module.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(module.read_bytes())
    (source / '__main__.py').write_bytes((ROOT / 'appimage-root-main.py').read_bytes())
    bundle = tmp_path / 'sectorrec-root.pyz'
    zipapp.create_archive(source, bundle)
    result = subprocess.run(['/usr/bin/python3', '-I', str(bundle), 'reader', 'scan', '/not-a-disk'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Invalid device path' in result.stderr
    assert 'Traceback' not in result.stderr
    result = subprocess.run(['/usr/bin/python3', '-I', str(bundle), 'unknown', 'scan', '/dev/sda'],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Unsupported privileged operation' in result.stderr


def test_reader_rejects_malformed_device_before_requesting_root(monkeypatch, capsys):
    """Package tests run unprivileged; bad input must be rejected first."""
    from src.helpers import reader
    monkeypatch.setattr(reader.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(reader.sys, 'argv', ['sectorrec-reader', 'scan', '/not-a-disk'])
    with pytest.raises(SystemExit) as exit_status:
        reader.main()
    assert exit_status.value.code == 2
    assert 'Invalid device path' in capsys.readouterr().err


def test_debian_paths_not_redirected_by_portable_helper():
    with patch.dict(os.environ, {}, clear=True):
        assert binary('fls') == '/usr/bin/fls'
        assert binary('smartctl') == '/usr/sbin/smartctl'
        assert 'LD_LIBRARY_PATH' not in isolated_tool_env()


def test_staging_copies_and_locks_bundle_under_root(tmp_path):
    """Smoke-test the real privileged staging protocol without opening disks.

    Skip on non-root test runners; production CI still checks the fixed archive.
    """
    if os.geteuid() != 0:
        pytest.skip('Requires root access for staging smoke test')
    import shutil
    from src.infrastructure.appimage_privilege import _create, _ENGINES
    appdir = tmp_path / 'AppDir' / 'usr'
    for directory in ('libexec', 'lib', 'bin'):
        (appdir / directory).mkdir(parents=True)
    bundle = appdir / 'libexec' / 'sectorrec-root.pyz'
    bundle.write_bytes(b'PK' + b'x' * 200)
    for engine in _ENGINES:
        (appdir / 'bin' / engine).write_bytes(b'not-a-real-binary')
    with patch('src.infrastructure.appimage_privilege._authority', return_value=['/usr/bin/sudo', '-A']):
        staged = Path(_create(bundle))
    try:
        assert staged.exists()
        assert staged.read_bytes() == bundle.read_bytes()
        assert staged.stat().st_uid == 0
        assert staged.stat().st_mode & 0o222 == 0
        assert (staged.parent.parent / 'bin' / 'fls').stat().st_mode & 0o222 == 0
    finally:
        shutil.rmtree(staged.parents[2])


def test_session_sudo_prompts_once_and_keeps_timestamp(tmp_path, monkeypatch):
    """Every privileged role uses the same sudo authorization, not its own."""
    from src.infrastructure import appimage_sudo as auth
    env = _image_env(tmp_path)
    calls = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.started = False
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started

    def fake_run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stderr=b'')

    monkeypatch.setattr(auth, '_SUDO', None)
    monkeypatch.setattr(auth, '_AUTHORIZED', False)
    monkeypatch.setattr(auth, '_REGISTERED', True)
    monkeypatch.setattr(auth, '_THREAD', None)
    monkeypatch.setattr(auth, '_sudo', lambda: '/usr/bin/sudo')
    monkeypatch.setattr(auth, '_run', fake_run)
    monkeypatch.setattr(auth.threading, 'Thread', FakeThread)
    with patch.dict(os.environ, env):
        assert helper_command('scan', '/dev/sdb')[:2] == ['/usr/bin/sudo', '-A']
        assert privileged_command('photo', '/dev/sdb', '/tmp/out', 'all')[:2] == ['/usr/bin/sudo', '-A']
        assert privileged_command('image', '/dev/sdb', '/tmp/out.img')[:2] == ['/usr/bin/sudo', '-A']
        assert calls.count(('-A', '-v')) == 1
        assert calls.count(('-n', '-v')) == 2
    # Cleanup is registered for actual GUI exits, not test imports.


def test_session_sudo_cancelled_without_caching(tmp_path, monkeypatch):
    from src.infrastructure import appimage_sudo as auth
    monkeypatch.setattr(auth, '_SUDO', None)
    monkeypatch.setattr(auth, '_AUTHORIZED', False)
    monkeypatch.setattr(auth, '_sudo', lambda: '/usr/bin/sudo')
    monkeypatch.setattr(auth, '_run', lambda *args: subprocess.CompletedProcess(
        args=args, returncode=1, stderr=b'authorization cancelled'))
    with patch.dict(os.environ, _image_env(tmp_path)):
        with pytest.raises(PermissionError, match='cancelled'):
            auth.authorize()
        assert auth._AUTHORIZED is False


def test_stage_bootstrap_shares_main_gui_session():
    from src.infrastructure import appimage_privilege
    source = (Path(appimage_privilege.__file__)).read_text()
    assert 'start_new_session=False' in source
    assert "return [authorize(), '-A']" in source


def test_close_session_invalidates_sudo_without_prompt(monkeypatch):
    from src.infrastructure import appimage_sudo as auth
    commands = []
    monkeypatch.setattr(auth, '_SUDO', '/usr/bin/sudo')
    monkeypatch.setattr(auth, '_AUTHORIZED', True)
    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0)
    with patch('src.infrastructure.appimage_sudo.subprocess.run', side_effect=fake_run):
        auth.close()
    assert commands == [['/usr/bin/sudo', '-k']]
    assert auth._AUTHORIZED is False


def test_appimage_unmount_uses_interactive_sudo_for_actual_operation(tmp_path):
    """Regression: sudo -v succeeded, but sudo -n unmount then rejected auth."""
    from src.infrastructure.linux_devices import unmount_volume
    env = _image_env(tmp_path)
    fake_disk = {'path': '/dev/sdz1', 'mounted': True,
                 'is_system': False, 'partition_disk': False}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == '/usr/bin/udisksctl':
            return subprocess.CompletedProcess(command, 1, '', 'Permission denied')
        if command[:2] == ['/usr/bin/sudo', '-n']:
            return subprocess.CompletedProcess(command, 1, '',
                                               'sudo: a password is required')
        if command[:2] == ['/usr/bin/sudo', '-A']:
            return subprocess.CompletedProcess(command, 0, '', '')
        raise AssertionError(f'Unexpected operation: {command!r}')

    with patch.dict(os.environ, env), \
         patch('shutil.which', side_effect=lambda name: '/usr/bin/' + name if name in ('sudo', 'udisksctl') else None), \
         patch('src.infrastructure.appimage_sudo.authorize', return_value='/usr/bin/sudo'), \
         patch('src.infrastructure.linux_devices.subprocess.run', side_effect=run):
        unmount_volume('/dev/sdz1', lambda: [fake_disk], helper_command)

    assert calls[-1][:2] == ['/usr/bin/sudo', '-A']
    assert calls[-1][-3:] == ['reader', 'unmount', '/dev/sdz1']


def test_appimage_workers_preserve_unix_session_and_isolate_process_group(monkeypatch):
    """sudoers timestamp follows SID; killpg must still target only the child."""
    if os.name != 'posix':
        pytest.skip('POSIX sessions only')
    from src.infrastructure.privileged import isolated_process_group, stop_proc
    monkeypatch.setenv('SECTORREC_APPIMAGE', '1')
    process = subprocess.Popen(
        [sys.executable, '-c', 'import os, time; print(os.getsid(0), os.getpgrp(), flush=True); time.sleep(30)'],
        stdout=subprocess.PIPE, text=True, **isolated_process_group())
    try:
        sid, pgid = (int(value) for value in process.stdout.readline().split())
        assert sid == os.getsid(0), 'Worker must not lose GUI sudo timestamp session'
        assert pgid == process.pid, 'Worker must remain independently cancellable'
    finally:
        stop_proc(process)
        process.stdout.close()
    assert process.returncode is not None


def test_other_processes_keep_existing_session_launch_mode(monkeypatch):
    from src.infrastructure.privileged import isolated_process_group
    monkeypatch.delenv('SECTORREC_APPIMAGE', raising=False)
    monkeypatch.delenv('SECTORREC_DEBIAN_ARCHIVE', raising=False)
    assert isolated_process_group() == {'start_new_session': True}
