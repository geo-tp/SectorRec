"""One interactive sudo authorization for the lifetime of the AppImage GUI.

All sudo subprocesses must inherit the GUI's POSIX session. Sudoers normally
keys its timestamp to the terminal/session; a new session during root-helper
staging previously caused an immediate second password prompt.

The password is handled only by the separate SUDO_ASKPASS process and sudo.
SectorRec neither receives nor persists it. While the GUI is open, a small
noninteractive sudo timestamp refresh prevents ordinary sudoers timeouts.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import threading

_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD = None
_AUTHORIZED = False
_REGISTERED = False
_SUDO = None
_REFRESH_SECONDS = 45


def _sudo():
    """Use the sudo executable, not an AppImage-bundled recovery tool."""
    return shutil.which('sudo')


def _run(*args):
    return subprocess.run([_SUDO, *args], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          timeout=180 if '-A' in args else 8, check=False)


def _valid():
    """Refresh an existing timestamp without ever opening a password dialog."""
    try:
        return _run('-n', '-v').returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _refresh():
    global _AUTHORIZED
    # Daemon stops automatically when the main process is killed and explicitly
    # on clean GUI exit. It must NEVER display a second password dialog.
    while not _STOP.wait(_REFRESH_SECONDS):
        with _LOCK:
            if _AUTHORIZED and not _valid():
                # Sudoers can revoke authorization (e.g. session policy change).
                # Reauthorize only on the next physical-disk operation; the
                # refresh thread stays available if authorization resumes.
                _AUTHORIZED = False


def authorize():
    """Prompt only on the first privileged operation (sudo path only).

    A single lock protects concurrent worker requests. Polkit-only systems
    keep their normal Polkit agent policy; no password is collected here.
    """
    global _SUDO, _AUTHORIZED, _THREAD, _REGISTERED
    with _LOCK:
        if _SUDO is None:
            _SUDO = _sudo()
        if not _SUDO or not os.environ.get('SECTORREC_ASKPASS'):
            raise RuntimeError('sudo and the bundled SectorRec password dialog are required')
        if _AUTHORIZED and _valid():
            return _SUDO
        # -A: only the separate askpass process receives the password. The
        # whole app, including its scanning subprocesses, never runs as root.
        try:
            result = _run('-A', '-v')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PermissionError('Administrator authorization failed') from exc
        if result.returncode:
            details = result.stderr.decode('utf-8', 'replace').strip()[-240:]
            raise PermissionError('Administrator authorization cancelled or rejected' +
                                  (': ' + details if details else ''))
        _AUTHORIZED = True
        # A refresh thread may have stopped after a policy reset. Restart it.
        if _THREAD is None or not _THREAD.is_alive():
            _STOP.clear()
            _THREAD = threading.Thread(target=_refresh, name='sectorrec-sudo-refresh',
                                       daemon=True)
            _THREAD.start()
        if not _REGISTERED:
            atexit.register(close)
            _REGISTERED = True
        return _SUDO


def close():
    """End the keepalive and invalidate this app's cached sudo timestamp."""
    global _AUTHORIZED
    _STOP.set()
    with _LOCK:
        if _AUTHORIZED and _SUDO:
            try:
                # No prompt; ordinary sudoers ties this to the GUI session.
                subprocess.run([_SUDO, '-k'], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=4, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        _AUTHORIZED = False
