"""Debian desktop: one Polkit authorization for a GUI lifetime.

A separate client process connects each existing Popen operation to one root
broker.  Three passed file descriptors preserve stdout/stderr/stdin streaming
and existing cancellation semantics; helpers still validate all arguments.

This module is NOT used by the portable AppImage or the Cardputer edition.
"""
from __future__ import annotations

import atexit
import array
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

SERVER = '/usr/lib/sectorrec/sectorrec-session-server'
ROLES = frozenset(('reader', 'photo', 'image'))
ROOT_SOCKET_DIR = '/run/sectorrec'


def broker_socket_path(uid, pid, start):
    if not str(uid).isdecimal() or not str(pid).isdecimal() or not str(start).isdecimal():
        raise ValueError('Invalid broker identity')
    return '%s/sectorrec-%s-%s-%s.sock' % (ROOT_SOCKET_DIR, uid, pid, start)
_RUNTIME = None
_RUNTIME_LOCK = threading.Lock()


def _pid_start(pid):
    # Field 22 (starttime); comm may contain parentheses or whitespace.
    return Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[19]


def session_environment():
    """Called by the GUI process; don't create a persistent system grant."""
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            base = '/run/user/%d' % os.getuid()
            if not Path(base).is_dir() or not os.access(base, os.W_OK | os.X_OK):
                base = '/tmp'
            folder = tempfile.mkdtemp(prefix='sectorrec-', dir=base)
            _RUNTIME = folder
            atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return (_RUNTIME, str(os.getpid()), _pid_start(os.getpid()))


def proxy_command(role, *arguments):
    if role not in ROLES:
        raise ValueError('Unknown root helper role')
    folder, pid, start = session_environment()
    # Absolute Python path; no user-supplied executable or shell.
    return ['/usr/bin/python3', '-I', '-m', 'src.infrastructure.session_worker',
            '--proxy', folder, pid, start, role, *arguments]


def _request(sock, role, arguments):
    payload = json.dumps({'role': role, 'args': arguments}, separators=(',', ':')).encode()
    if len(payload) > 8192:
        raise ValueError('Privileged operation arguments too long')
    descriptors = array.array('i', [0, 1, 2])
    sock.sendmsg([payload + b'\n'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors.tobytes())])
    answer = bytearray()
    while len(answer) < 4096:
        data = sock.recv(128)
        if not data:
            raise RuntimeError('Privileged worker disconnected')
        answer.extend(data)
        if b'\n' in data:
            break
    if not answer.startswith(b'EXIT '):
        raise RuntimeError('Privileged worker rejected the operation')
    return int(answer.split(b'\n', 1)[0][5:])


def _connect(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(2)
        sock.connect(path)
        sock.settimeout(None)
        return sock
    except (OSError, TimeoutError):
        sock.close()
        return None


def _launch_server(folder, pid, start):
    pkexec = shutil.which('pkexec')
    if not pkexec:
        raise RuntimeError('Polkit is required for protected disk access')
    args = [pkexec, SERVER, folder, pid, start]
    kwargs = ({'process_group': 0} if sys.version_info >= (3, 11)
              else {'preexec_fn': os.setpgrp})
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kwargs)
    return proc


def _proxy(folder, pid, start, role, arguments):
    if role not in ROLES:
        raise ValueError('Unknown root helper')
    directory = Path(folder)
    if not directory.is_dir() or directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise RuntimeError('Invalid SectorRec session directory')
    path = broker_socket_path(os.getuid(), pid, start)
    lockfile = directory / 'startup.lock'
    with open(lockfile, 'a+b') as lock:
        # Only one subprocess may request Polkit, even if several scans begin
        # simultaneously. Never prompt twice for concurrent operations.
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        sock = _connect(path)
        if sock is None:
            proc = _launch_server(folder, pid, start)
            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                sock = _connect(path)
                if sock is not None:
                    break
                if proc.poll() is not None:
                    raise RuntimeError('Disk access authorization was cancelled or denied')
                time.sleep(0.1)
            if sock is None:
                raise RuntimeError('Timed out waiting for disk access authorization')
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    with sock:
        return _request(sock, role, list(arguments))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 5 or argv[0] != '--proxy':
        return 2
    try:
        return _proxy(argv[1], argv[2], argv[3], argv[4], argv[5:])
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('SectorRec: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
