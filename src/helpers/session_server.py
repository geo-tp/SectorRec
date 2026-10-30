#!/usr/bin/python3 -I
"""Session-scoped root broker for the standard Debian desktop package.

One authenticated pkexec process per GUI lifetime. No user-supplied commands,
no shell, no permanent grants. The existing strict helpers revalidate sources
and destinations. All operations are tied to the requesting user's identity.
"""
from __future__ import annotations
import array
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading

HELPERS = {
    'reader': '/usr/lib/sectorrec/sectorrec-reader',
    'photo': '/usr/lib/sectorrec/sectorrec-photo-reader',
    'image': '/usr/lib/sectorrec/sectorrec-image-reader',
}
_ACTIVE = set()
_LOCK = threading.Lock()


def _pid_start(pid):
    return Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[19]


def validate_context(folder, gui_pid, start, uid):
    """Reject paths outside a private, UID-owned runtime directory."""
    if uid <= 0 or gui_pid <= 1 or _pid_start(gui_pid) != start:
        raise ValueError('Invalid GUI identity')
    if int(Path('/proc/%d' % gui_pid).stat().st_uid) != uid:
        raise ValueError('GUI must belong to Polkit user')
    p = Path(folder)
    if p.name[:10] != 'sectorrec-' or p.is_symlink() or not p.is_absolute():
        raise ValueError('Invalid session path')
    st = p.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
        raise ValueError('Session directory must be private')
    if str(p.resolve()) != str(p):
        raise ValueError('Session directory may not contain symlinks')
    runtime = Path('/run/user/%d' % uid)
    if p.parent != runtime and p.parent != Path('/tmp'):
        raise ValueError('Session directory outside user runtime')
    if len(str(p / 'broker.sock').encode()) > 103:
        raise ValueError('Unix socket path too long')


def _stop_child(proc):
    if proc.poll() is None:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()


def _child_request(conn, uid):
    fds = array.array('i')
    received = []
    proc = None
    try:
        raw, ancillary, _, _ = conn.recvmsg(8193, socket.CMSG_SPACE(3 * fds.itemsize))
        for level, typ, data in ancillary:
            if level == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                a = array.array('i')
                a.frombytes(data[:len(data) - len(data) % a.itemsize])
                received.extend(a)
        if len(received) != 3 or len(raw) > 8192 or not raw.endswith(b'\n'):
            raise ValueError('Invalid privileged request')
        req = json.loads(raw)
        role = req.get('role')
        args = req.get('args')
        if role not in HELPERS or not isinstance(args, list) or len(args) > 12 or any(
                not isinstance(s, str) or len(s) > 4096 or '\x00' in s for s in args):
            raise ValueError('Unsupported privileged operation')
        helper = HELPERS[role]
        if not Path(helper).is_file():
            raise ValueError('Privileged helper is missing')
        # Preserve the UID that pkexec authenticated: PhotoRec and imaging
        # use it to enforce destination permissions and restore file ownership.
        env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
               'PKEXEC_UID': str(uid), 'SUDO_UID': str(uid)}
        import pwd
        env['SUDO_GID'] = str(pwd.getpwuid(uid).pw_gid)
        proc = subprocess.Popen([helper, *args], stdin=received[0], stdout=received[1],
                                stderr=received[2], close_fds=True, start_new_session=True,
                                env=env)
        with _LOCK: _ACTIVE.add(proc)
        # Detect a vanished GUI-side client and stop the entire subprocess
        # tree rather than abandoning a privileged PhotoRec/ddrescue scan.
        conn.settimeout(0.25)
        while proc.poll() is None:
            try:
                b = conn.recv(1, socket.MSG_PEEK)
                if not b:
                    _stop_child(proc)
                    break
                # The protocol has no further client commands.
                _stop_child(proc)
                break
            except socket.timeout:
                pass
        code = proc.wait()
        conn.sendall(('EXIT %d\n' % code).encode())
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        try: conn.sendall(b'EXIT 2\n')
        except OSError: pass
    finally:
        if proc is not None:
            _stop_child(proc)
            with _LOCK: _ACTIVE.discard(proc)
        for fd in received:
            try: os.close(fd)
            except OSError: pass
        conn.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or os.geteuid() != 0:
        return 2
    user = os.environ.get('PKEXEC_UID', '')
    if not user.isdecimal():
        return 2
    uid, folder, pid, start = int(user), argv[0], int(argv[1]), argv[2]
    try:
        validate_context(folder, pid, start, uid)
        # Never bind a root process to a user-writable filesystem path.
        # /run/sectorrec is root-owned, not a writable user runtime dir.
        socket_dir = Path('/run/sectorrec')
        socket_dir.mkdir(mode=0o755, exist_ok=True)
        st = socket_dir.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('Untrusted privileged broker directory')
        # pkexec can inherit a restrictive umask. Users need directory
        # traversal permission to reach their UID-owned 0600 sockets.
        os.chmod(socket_dir, 0o755)
        from src.infrastructure.session_worker import broker_socket_path
        path = broker_socket_path(uid, pid, start)
        if os.path.lexists(path):
            raise ValueError('Refusing existing socket path')
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # 0600 socket mode from creation, before granting the authenticated
        # user's UID access. No chmod() on a pathname that can be swapped.
        old_umask = os.umask(0o177)
        try:
            listener.bind(path)
        finally:
            os.umask(old_umask)
        os.chown(path, uid, -1, follow_symlinks=False)
        listener.listen(16)
        listener.settimeout(0.6)
        # pidfd pinpoints the original GUI process, even if a PID is reused.
        owner_fd = os.pidfd_open(pid)
        try:
            validate_context(folder, pid, start, uid)  # protect PID-reuse race
            while not select.select([owner_fd], [], [], 0)[0]:
                try: conn, _ = listener.accept()
                except socket.timeout: continue
                try:
                    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    _, peer_uid, _ = struct.unpack('3i', creds)
                    if peer_uid != uid:
                        conn.close()
                        continue
                except OSError:
                    conn.close()
                    continue
                threading.Thread(target=_child_request, args=(conn, uid), daemon=True).start()
        finally:
            with _LOCK: children = list(_ACTIVE)
            for child in children: _stop_child(child)
            os.close(owner_fd)
            listener.close()
            if os.path.lexists(path) and stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)
    except (OSError, ValueError) as exc:
        print('SectorRec privileged session: ' + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
