"""On-demand UAC for Windows raw-device operations; disk images need no UAC."""
import ctypes
import subprocess
import sys
from pathlib import Path


def is_elevated():
    if sys.platform != 'win32':
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def restart_elevated_for_source(source):
    """Request UAC and open SectorRec on the same verified physical source.

    Returns only after Windows accepted the request; the caller closes the
    unprivileged window, leaving it open if the UAC dialog was cancelled.
    """
    if sys.platform != 'win32':
        raise RuntimeError('UAC is Windows-only')
    from src.infrastructure.windows_raw import require_windows_device
    require_windows_device(source)
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Automatic UAC restart is available in the packaged SectorRec.exe. '
                           'When running from source, start the Python terminal as Administrator.')
    arguments = subprocess.list2cmdline(['--ui', 'desktop', '--resume-device', source])
    shell = ctypes.windll.shell32
    shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    code = shell.ShellExecuteW(None, 'runas', str(Path(sys.executable)), arguments, None, 1)
    if not code or int(code) <= 32:
        raise PermissionError('Administrator authorization was cancelled or denied.')
    return True
