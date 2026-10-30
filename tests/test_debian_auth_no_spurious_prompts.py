"""Polkit permission flow regressions. No privileged hardware is accessed."""
import os
import sys
import subprocess
from unittest.mock import patch

import pytest

from src.infrastructure.privileged import privileged_command, isolated_process_group, stop_proc
from src.infrastructure.source_guard import SourceMountGuard
from src.services import device_diagnostics as diagnostics


def test_direct_device_read_does_not_require_pkexec_to_be_installed():
    with patch.dict(os.environ, {'SECTORREC_DEBIAN_ARCHIVE':'1', 'SECTORREC_CARDPUTER':'0'}, clear=False), \
         patch('src.infrastructure.privileged.shutil.which', return_value=None), \
         patch('src.infrastructure.privileged.os.access', return_value=True):
        assert privileged_command('reader', 'scan', '/dev/sdz1') == [
            '/usr/lib/sectorrec/sectorrec-reader', 'scan', '/dev/sdz1']


def test_debian_worker_keeps_gui_sid_and_own_process_group(monkeypatch):
    if os.name != 'posix':
        pytest.skip('Linux session semantics')
    monkeypatch.setenv('SECTORREC_DEBIAN_ARCHIVE','1')
    monkeypatch.delenv('SECTORREC_APPIMAGE', raising=False)
    proc = subprocess.Popen([sys.executable, '-c',
        'import os,time; print(os.getsid(0),os.getpgrp(),flush=True);time.sleep(30)'],
        stdout=subprocess.PIPE, text=True, **isolated_process_group())
    try:
        sid, pgid = map(int, proc.stdout.readline().split())
        assert sid == os.getsid(0)
        assert pgid == proc.pid
    finally:
        stop_proc(proc)
        proc.stdout.close()


def test_guard_gives_user_time_to_authorize(monkeypatch):
    monkeypatch.setenv('SECTORREC_DEBIAN_ARCHIVE','1')
    monkeypatch.delenv('SECTORREC_APPIMAGE', raising=False)
    selectors = []
    script = 'import sys;print("READY",flush=True);sys.stdin.buffer.read()'
    class FakeSelector:
        def __enter__(self):return self
        def __exit__(self,*_):return None
        def register(self,*_):pass
        def select(self, timeout):
            selectors.append(timeout)
            return [1]
    with patch('src.infrastructure.source_guard.helper_command',
               return_value=['/usr/bin/pkexec', sys.executable, '-u', '-c', script]), \
         patch('src.infrastructure.source_guard.selectors.DefaultSelector', FakeSelector), \
         patch('src.infrastructure.source_guard.subprocess.Popen') as popen:
        popen.return_value.stdout.readline.return_value = b'READY\n'
        guard = SourceMountGuard.acquire('/dev/sdz1')
        assert selectors == [300]
        assert guard.process == popen.return_value


def test_background_smart_never_calls_polkit():
    device = {'path':'/dev/sdz1', 'disk_path':'/dev/sdz'}
    fake = subprocess.CompletedProcess(['smartctl'], 2, '', 'Permission denied')
    with patch.object(diagnostics.shutil, 'which', return_value='/usr/sbin/smartctl'), \
         patch.object(diagnostics.subprocess, 'run', return_value=fake) as run, \
         patch.object(diagnostics.os,'geteuid',return_value=1000), \
         patch('src.infrastructure.privileged.helper_command') as privileged:
        data,error = diagnostics._read_smart(device, extended=False)
    assert data is None
    assert 'SMART' in error
    assert run.call_count == 1
    assert run.call_args.args[0][0] == '/usr/sbin/smartctl'
    privileged.assert_not_called()


def test_full_smart_uses_direct_access_when_available():
    fake = subprocess.CompletedProcess(['smartctl'], 0,
                '{"device":{"name":"/dev/sdz"},"smart_status":{"passed":true}}', '')
    with patch.object(diagnostics.shutil, 'which', return_value='/usr/sbin/smartctl'), \
         patch.object(diagnostics.subprocess, 'run', return_value=fake) as run, \
         patch.object(diagnostics.os,'geteuid',return_value=1000), \
         patch('src.infrastructure.privileged.helper_command') as privileged:
        data,error = diagnostics._read_smart({'disk_path':'/dev/sdz', 'path':'/dev/sdz1'}, extended=True)
    assert error == '' and data['smart_status']['passed']
    assert run.call_count == 1
    privileged.assert_not_called()


def test_full_smart_allows_long_polkit_authorization(monkeypatch):
    monkeypatch.setenv('SECTORREC_DEBIAN_ARCHIVE', '1')
    user = subprocess.CompletedProcess(['smartctl'], 2, '', 'Permission denied')
    root = subprocess.CompletedProcess(['pkexec'], 0, '{"smart_status":{"passed":true}}', '')
    with patch.object(diagnostics.shutil, 'which', return_value='/usr/sbin/smartctl'), \
         patch.object(diagnostics.subprocess, 'run', side_effect=[user, root]) as run, \
         patch.object(diagnostics.os,'geteuid',return_value=1000), \
         patch('src.services.device_diagnostics.Path.is_file', return_value=True), \
         patch('src.infrastructure.privileged.helper_command',return_value=['/usr/bin/pkexec', '/usr/lib/sectorrec/sectorrec-reader','smart_full','/dev/sdz']):
        data,error = diagnostics._read_smart({'disk_path':'/dev/sdz', 'path':'/dev/sdz1'}, extended=True)
    assert error == '' and data['smart_status']['passed']
    assert run.call_count == 2
    assert run.call_args.kwargs['timeout'] >= 300


def test_udisks_unmount_uses_user_permission_before_root_helper():
    from src.infrastructure.linux_devices import unmount_volume
    drive = {'path':'/dev/sdz1', 'mounted':True,
             'partition_disk':False, 'is_system':False}
    normal = subprocess.CompletedProcess(['udisksctl'], 0, 'Unmounted', '')
    with patch('src.infrastructure.linux_devices.shutil.which', return_value='/usr/bin/udisksctl'), \
         patch('src.infrastructure.linux_devices.subprocess.run', return_value=normal) as run:
        def should_not_auth(*args):
            pytest.fail('Polkit should not be used for user-authorized UDisks unmount')
        unmount_volume('/dev/sdz1', lambda: [drive], should_not_auth)
    assert run.call_count == 1
    assert '--no-user-interaction' in run.call_args.args[0]


def test_unmount_falls_back_to_protected_helper_when_udisks_denies():
    from src.infrastructure.linux_devices import unmount_volume
    drive = {'path':'/dev/sdz1', 'mounted':True,
             'partition_disk':False, 'is_system':False}
    deny = subprocess.CompletedProcess(['udisksctl'], 1, '', 'Not authorized')
    done = subprocess.CompletedProcess(['pkexec'], 0, '', '')
    called = []
    def build(action,device):
        called.append((action,device))
        return ['/usr/bin/pkexec','/usr/lib/sectorrec/sectorrec-reader',action,device]
    with patch.dict(os.environ, {'SECTORREC_DEBIAN_ARCHIVE':'1'}), \
         patch('src.infrastructure.linux_devices.shutil.which', return_value='/usr/bin/udisksctl'), \
         patch('src.infrastructure.linux_devices.subprocess.run', side_effect=[deny,done]) as run:
        unmount_volume('/dev/sdz1',lambda: [drive],build)
    assert called == [('unmount','/dev/sdz1')]
    assert run.call_args.kwargs['timeout'] >= 120


def test_guard_keeps_short_timeout_without_auth(monkeypatch):
    monkeypatch.setenv('SECTORREC_DEBIAN_ARCHIVE','1')
    durations = []
    script = 'import sys;print("READY",flush=True);sys.stdin.buffer.read()'
    class FakeSelector:
        def __enter__(self):return self
        def __exit__(self,*_):return None
        def register(self,*_):pass
        def select(self,timeout):durations.append(timeout);return [1]
    with patch('src.infrastructure.source_guard.helper_command',
               return_value=['/usr/lib/sectorrec/sectorrec-reader','hold_source','/dev/sdz1']), \
         patch('src.infrastructure.source_guard.selectors.DefaultSelector',FakeSelector), \
         patch('src.infrastructure.source_guard.subprocess.Popen') as popen:
        popen.return_value.stdout.readline.return_value=b'READY\n'
        SourceMountGuard.acquire('/dev/sdz1')
    assert durations == [5.0]
