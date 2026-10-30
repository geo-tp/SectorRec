"""End-to-end IPC/streaming/cancellation tests without requiring root or a disk."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from src.helpers import session_server as broker
from src.infrastructure import session_worker as client
from src.infrastructure.privileged import privileged_command


def test_session_broker_rejects_arbitrary_commands_and_paths(tmp_path):
    with pytest.raises(ValueError):
        client.proxy_command('shell', '/bin/bash')
    with pytest.raises(ValueError):
        broker.validate_context(str(tmp_path), os.getpid(), broker._pid_start(os.getpid()), max(1, os.getuid()))
    with pytest.raises(ValueError):
        broker.validate_context('/tmp/sectorrec-nonexistent', os.getpid(), 'WRONG', max(1, os.getuid()))


def test_broker_keeps_three_streams_and_reuses_same_listener(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('Requires Linux SCM_RIGHTS')
    folder = tmp_path / 'sectorrec-session'
    folder.mkdir(mode=0o700)
    path = str(folder / 'broker.sock')
    helper = tmp_path / 'stub-helper'
    helper.write_text('#!/usr/bin/python3\nimport sys\nprint("OUT:"+sys.argv[1], flush=True)\nprint("ERR:"+sys.argv[1], file=sys.stderr, flush=True)\n')
    helper.chmod(0o755)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    listener.listen(5)
    errors = []

    def accept_two():
        try:
            for _ in range(2):
                conn, _ = listener.accept()
                broker._child_request(conn, os.getuid())
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()

    t = threading.Thread(target=accept_two, daemon=True)
    t.start()
    with patch.dict(broker.HELPERS, {'reader': str(helper)}):
        for tag in ('one', 'two'):
            proc = subprocess.run([sys.executable, '-c',
                                   'import sys; import src.infrastructure.session_worker as c; '
                                   'c.broker_socket_path=lambda *_:sys.argv[1]; '
                                   'sys.exit(c.main(sys.argv[2:]))', path,
                                   '--proxy', str(folder), str(os.getpid()),
                                   broker._pid_start(os.getpid()), 'reader', tag],
                                  capture_output=True, text=True, timeout=8)
            assert proc.returncode == 0, proc.stderr
            assert 'OUT:' + tag in proc.stdout
            assert 'ERR:' + tag in proc.stderr
    t.join(4)
    assert not t.is_alive() and not errors


def test_proxy_uses_one_session_owner_and_scoped_role(monkeypatch, tmp_path):
    monkeypatch.setenv('SECTORREC_DEBIAN_ARCHIVE', '1')
    monkeypatch.setenv('SECTORREC_CARDPUTER', '0')
    monkeypatch.delenv('SECTORREC_APPIMAGE', raising=False)
    with patch('src.infrastructure.privileged.shutil.which', return_value='/usr/bin/pkexec'), \
         patch('src.infrastructure.privileged.os.access', return_value=False), \
         patch('src.infrastructure.session_worker.session_environment',
               return_value=(str(tmp_path), '123', '456')):
        reader = privileged_command('reader', 'scan', '/dev/sdb')
        photo = privileged_command('photo', '/dev/sdb', '/tmp/SectorRec_261029', 'all')
        assert reader[4:8] == photo[4:8] == ['--proxy', str(tmp_path), '123', '456']
        assert reader[8] == 'reader'
        assert photo[8] == 'photo'


def test_user_owned_session_path_disallows_other_users_and_symlinks(tmp_path):
    folder = tmp_path / 'sectorrec-test'
    folder.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        broker.validate_context(str(folder), os.getpid(), broker._pid_start(os.getpid()), 2**31 - 1)
    link = tmp_path / 'sectorrec-link'
    link.symlink_to(folder)
    with pytest.raises(ValueError):
        broker.validate_context(str(link), os.getpid(), broker._pid_start(os.getpid()), max(1, os.getuid()))


def test_session_holder_timeout_includes_proxy(monkeypatch):
    from src.infrastructure.source_guard import SourceMountGuard
    with patch('src.infrastructure.source_guard.helper_command',
               return_value=['/usr/bin/python3','-I','-m','src.infrastructure.session_worker',
                             '--proxy','/tmp/sectorrec-demo','100','200','reader','hold_source','/dev/sdb']), \
         patch('src.infrastructure.source_guard.subprocess.Popen') as popen, \
         patch('src.infrastructure.source_guard.selectors.DefaultSelector') as selector:
        selector.return_value.__enter__.return_value.select.return_value = [1]
        popen.return_value.stdout.readline.return_value = b'READY\n'
        guard = SourceMountGuard.acquire('/dev/sdb')
        assert selector.return_value.__enter__.return_value.select.call_args.args[0] >= 300
        assert guard.process == popen.return_value


def test_broker_terminates_privileged_scan_when_proxy_disappears(tmp_path):
    """A cancelled scan must not leave a root child running in the broker."""
    if sys.platform != 'linux':
        pytest.skip('Linux process groups only')
    marker = tmp_path / 'pid'
    helper = tmp_path / 'long-running-helper'
    helper.write_text('''#!/usr/bin/python3
import os,sys,time
with open(sys.argv[1], 'w') as f: f.write(str(os.getpid()))
while True: time.sleep(0.2)
''')
    helper.chmod(0o755)
    server, peer = socket.socketpair(socket.AF_UNIX)
    with patch.dict(broker.HELPERS, {'reader': str(helper)}):
        worker = threading.Thread(target=broker._child_request, args=(server, os.getuid()), daemon=True)
        worker.start()
        import array
        with open(os.devnull, 'rb') as src, open(os.devnull, 'wb') as dst:
            fds = array.array('i', [src.fileno(), dst.fileno(), dst.fileno()])
            peer.sendmsg([b'{"role":"reader","args":["' + str(marker).encode() + b'"]}\n'],
                         [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds.tobytes())])
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.03)
            assert marker.exists(), 'Privileged subprocess never started'
            child_pid = int(marker.read_text())
            peer.close()  # simulate killed / cancelled GUI-side subprocess
            worker.join(4)
            assert not worker.is_alive(), 'The privileged child did not stop'
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
