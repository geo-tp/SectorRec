"""Low-level privileged command validation and child process teardown.

Kept independent of the domain model so infrastructure need not import it.
The historic src.models.core API re-exports these functions for compatibility.
"""
from __future__ import annotations
import os
import re
import signal
import sys
import subprocess
from pathlib import Path
import shutil

ROOT_HELPER = '/usr/lib/sectorrec/sectorrec-reader'
_INODE = re.compile(r'^\d+(?:-\d+){0,2}$')

def isolated_process_group():
    """Create a cancellable process group WITHOUT changing the POSIX session.

    sudoers' default credentials are scoped to the terminal/session (and often
    the parent PID when no terminal exists). ``start_new_session=True`` breaks
    that scope, so a sudo -v in the GUI does not authorize the scan process.
    A new process *group* still lets stop_proc() terminate the entire helper
    tree safely with os.killpg(), while retaining the shared session ID.
    On Python >= 3.11 use subprocess's native process_group (no preexec_fn).
    For Python 3.10, the isolated process group uses the POSIX setpgrp
    function.  Debian Polkit and portable sudo both retain their GUI session.
    """
    if os.name == 'posix' and (os.environ.get('SECTORREC_APPIMAGE') == '1' or
                              os.environ.get('SECTORREC_DEBIAN_ARCHIVE') == '1'):
        if sys.version_info >= (3, 11):
            return {'process_group': 0}
        # Compatibility for distro builds whose PyInstaller bundles Python
        # 3.10. Use only the C-level os.setpgrp; new-session sudo tickets
        # are not reusable between worker processes.
        return {'preexec_fn': os.setpgrp}
    return {'start_new_session': True}


def privileged_command(role, *arguments):
    """Call ONLY a validated root helper, using interactive elevation in AppImage.

    Both Debian editions use Polkit for fixed, audited helpers; the
    desktop reuses one authenticated broker for the current GUI session. AppImage
    invokes the same audited Python helper code using its isolated zipapp;
    no permanent installation, unrestricted sudoers rule or root GUI.
    """
    if role not in ('reader', 'photo', 'image', 'backlight'):
        raise ValueError('Unknown privileged helper role')
    bundle = os.environ.get('SECTORREC_ROOT_BUNDLE')
    if os.environ.get('SECTORREC_APPIMAGE') == '1':
        if role == 'backlight':
            raise ValueError('Backlight control is available only on Cardputer Debian')
        if not bundle or not Path(bundle).is_file():
            raise RuntimeError('AppImage privileged helper is unavailable')
        interpreter = '/usr/bin/python3'
        if not Path(interpreter).is_file():
            raise RuntimeError('Python 3 is required for privileged disk access')
        if os.environ.get('SECTORREC_ROOT_STAGED') != '1':
            from src.infrastructure.appimage_privilege import stage_runtime
            bundle = stage_runtime(bundle)
            os.environ['SECTORREC_ROOT_BUNDLE'] = bundle
            os.environ['SECTORREC_ROOT_STAGED'] = '1'
        args = [interpreter, '-I', bundle, role, *arguments]
        askpass = os.environ.get('SECTORREC_ASKPASS')
        if askpass and Path(askpass).is_file() and shutil.which('sudo'):
            # Refresh/reuse the app-scoped sudo timestamp. The *actual*
            # operation must allow askpass too: a passwordless sudo -n can
            # reject the operation even after sudo -v succeeds if a local
            # sudoers rule scopes credentials differently. No root GUI.
            from src.infrastructure.appimage_sudo import authorize
            return [authorize(), '-A', *args]
        if shutil.which('pkexec'):
            # Polkit fallback; PKEXEC_UID is available to restricted PhotoRec.
            return [shutil.which('pkexec'), *args]
        raise RuntimeError('Install sudo or Polkit to authorize physical disk operations')
    helper = {'reader': ROOT_HELPER,
              'photo': '/usr/lib/sectorrec/sectorrec-photo-reader',
              'image': '/usr/lib/sectorrec/sectorrec-image-reader',
              'backlight': '/usr/lib/sectorrec/sectorrec-backlight'}[role]
    if os.environ.get('SECTORREC_DEBIAN_ARCHIVE') == '1':
        # With native read access, bypass Polkit entirely. For protected
        # desktop operations a single root broker lives for the GUI lifetime.
        if (role == 'reader' and len(arguments) >= 2 and
                arguments[0] not in ('unmount', 'smart', 'smart_full') and
                isinstance(arguments[1], str) and
                re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', arguments[1]) and
                os.access(arguments[1], os.R_OK)):
            return [helper, *arguments]
        pkexec = shutil.which('pkexec')
        if not pkexec:
            raise RuntimeError('Polkit is required for protected disk operations')
        if os.environ.get('SECTORREC_CARDPUTER') == '1':
            # Cardputer's existing narrowly scoped passwordless policy.
            return [pkexec, helper, *arguments]
        from src.infrastructure.session_worker import proxy_command
        return proxy_command(role, *arguments)
    if role == 'photo' or role == 'backlight':
        return [shutil.which('pkexec') or '/usr/bin/pkexec', helper, *arguments]
    return ['/usr/bin/sudo', '-n', helper, *arguments]

def helper_command(action, device, inode=None):
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.\-]+', device):
        raise ValueError('Invalid device')
    command = privileged_command('reader', action, device)
    if inode is not None:
        if action.startswith('partition_'):
            valid = re.fullmatch(r'\d{1,16}(?::\d+(?:-\d+){0,2})?', str(inode))
        else:
            valid = _INODE.fullmatch(str(inode))
        if not valid:
            raise ValueError('Invalid inode or partition offset')
        command.append(inode)
    return command


def stop_proc(proc):
    """Stop and reap a subprocess/group before its session is released."""
    if not proc:
        return
    try:
        if proc.poll() is None:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                else:
                    proc.kill()
                proc.wait()  # critical: do not abandon an unreaped child
        else:
            proc.wait()
    except ProcessLookupError:
        try: proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError): pass
    except PermissionError:
        raise RuntimeError('Cannot stop privileged recovery subprocess')

