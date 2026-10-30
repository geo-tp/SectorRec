"""First-use, authenticated AppImage tool staging.

FUSE normally denies root access to mounts created by another user. Therefore
sudo/Polkit cannot directly execute the AppImage's root helper or bundled TSK
binaries from /tmp/.mount_*. On first raw-device action, an explicitly
authorized, stdlib-only bootstrap copies ONLY the restricted helper archive
and required engines to a root-owned temporary directory under /run.

No system package, sudoers file, user program, or privileged GUI is installed.
The bootstrap cannot extract arbitrary paths, links or devices from its input.
The staged directory disappears after reboot (and is never user-writable).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path

from src.infrastructure.appimage_environment import host_environment

_LOCK = threading.Lock()
_STAGED = None
_ENGINES = ('fls', 'icat', 'testdisk', 'photorec', 'ddrescue', 'smartctl')

# This code is passed literally to the trusted system Python interpreter. It
# must not import any code from an untrusted FUSE mount. The archive may only
# populate a freshly-created root-owned /run directory and only known paths.
_BOOTSTRAP = r'''
import os, re, shutil, sys, tarfile, tempfile
from pathlib import Path
root = tempfile.mkdtemp(prefix='sectorrec-', dir='/run')
expected = {'usr/libexec/sectorrec-root.pyz',
            'usr/bin/fls', 'usr/bin/icat', 'usr/bin/testdisk',
            'usr/bin/photorec', 'usr/bin/ddrescue', 'usr/bin/smartctl'}
seen = set()
size = 0
try:
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
        for entry in archive:
            name = entry.name
            if (not entry.isfile() or name in seen or
                not (name in expected or re.fullmatch(r'usr/lib/lib[A-Za-z0-9._+\-]{1,120}', name))):
                raise ValueError('Unexpected file in privileged runtime')
            if entry.size < 1 or entry.size > 128 * 1024 * 1024:
                raise ValueError('Invalid engine size')
            size += entry.size
            if size > 320 * 1024 * 1024 or len(seen) > 250:
                raise ValueError('Privileged bundle is too large')
            target = Path(root) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as source:
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o400)
                with os.fdopen(fd, 'wb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            os.chmod(target, 0o500 if name.startswith('usr/bin/') else 0o400)
            seen.add(name)
    if not expected.issubset(seen):
        raise ValueError('Incomplete privileged runtime')
    # The user can inspect but never modify the staged source or binaries.
    for current in [Path(root), Path(root)/'usr', Path(root)/'usr'/'bin',
                    Path(root)/'usr'/'lib', Path(root)/'usr'/'libexec']:
        if current.is_dir():
            os.chmod(current, 0o755)
    print(str(Path(root)/'usr'/'libexec'/'sectorrec-root.pyz'), flush=True)
except BaseException:
    shutil.rmtree(root, ignore_errors=True)
    raise
'''


def _files(bundle: Path):
    """Yield regular files only, from the read-only mounted AppDir as user."""
    usr = bundle.parent.parent
    if usr.name != 'usr' or bundle.name != 'sectorrec-root.pyz':
        raise RuntimeError('Unrecognized AppImage helper layout')
    yield 'usr/libexec/sectorrec-root.pyz', bundle
    for name in _ENGINES:
        candidate = usr / 'bin' / name
        if not candidate.is_file() or candidate.is_symlink():
            raise RuntimeError('Bundled recovery engine is missing: ' + name)
        yield 'usr/bin/' + name, candidate
    folder = usr / 'lib'
    for candidate in sorted(folder.iterdir()):
        if (candidate.is_file() and not candidate.is_symlink() and
                re.fullmatch(r'lib[A-Za-z0-9._+\-]{1,120}', candidate.name)):
            yield 'usr/lib/' + candidate.name, candidate


def _authority():
    if shutil.which('sudo') and os.environ.get('SECTORREC_ASKPASS'):
        from src.infrastructure.appimage_sudo import authorize
        return [authorize(), '-A']
    if shutil.which('pkexec'):
        return [shutil.which('pkexec')]
    raise RuntimeError('Install sudo or Polkit to authorize raw disk access')


def _create(bundle: Path):
    command = [*_authority(), '/usr/bin/python3', '-I', '-c', _BOOTSTRAP]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            # Staging stays in the GUI's POSIX session so sudo's
                            # timestamp is shared with later reader operations.
                            start_new_session=False, env=host_environment())
    try:
        try:
            with tarfile.open(fileobj=proc.stdin, mode='w|') as archive:
                for name, path in _files(bundle):
                    archive.add(path, arcname=name, recursive=False)
        except (BrokenPipeError, OSError):
            pass  # sudo/Polkit may have rejected the authorization.
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        result = proc.wait(timeout=180)
        output = proc.stdout.read(8192).decode('utf-8', 'replace').strip()
        errors = proc.stderr.read(8192).decode('utf-8', 'replace').strip()
        if result != 0:
            raise PermissionError('Administrator authorization or helper staging failed: ' + errors[-350:])
        stage = Path(output)
        # /run is root-owned, the content is copied and changed to read-only by
        # the privileged bootstrap. The unprivileged process can only inspect.
        if (not re.fullmatch(r'/run/sectorrec-[a-z0-9_]{4,}/usr/libexec/sectorrec-root\.pyz', output)
                or not stage.is_file()):
            raise RuntimeError('The privileged helper was not staged correctly')
        return str(stage)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    finally:
        if proc.stdout: proc.stdout.close()
        if proc.stderr: proc.stderr.close()


def stage_runtime(bundle):
    """Stage once per UI process. A single authenticated operation owns setup."""
    global _STAGED
    with _LOCK:
        if _STAGED and Path(_STAGED).is_file():
            return _STAGED
        _STAGED = _create(Path(bundle))
        return _STAGED
