"""Resolve trusted subprocess executables in the portable privileged archive.

The archive is stored at AppDir/usr/libexec/sectorrec-root.pyz. Do not use
caller-controlled PATH/LD_LIBRARY_PATH or accept executable paths as arguments.
"""
from pathlib import Path
import sys


def bundled_dir():
    archive = Path(sys.argv[0])
    if archive.name == 'sectorrec-root.pyz' and archive.is_file():
        return archive.resolve().parent.parent
    return None


def binary(name):
    if name not in ('fls', 'icat', 'testdisk', 'photorec', 'ddrescue', 'smartctl'):
        raise ValueError('Unsupported tool name')
    root = bundled_dir()
    return str(root / 'bin' / name) if root else ('/usr/sbin/smartctl' if name == 'smartctl' else '/usr/bin/' + name)


def isolated_tool_env(extra=None):
    clean = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
    root = bundled_dir()
    if root:
        clean['LD_LIBRARY_PATH'] = str(root / 'lib')
    if extra:
        clean.update(extra)
    return clean

