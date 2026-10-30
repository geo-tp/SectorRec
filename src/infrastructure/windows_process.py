"""Hide console windows spawned by a frozen Windows GUI application.

Do not apply this to interactive commands that require a visible terminal.
The Windows flag is set only on Windows; Unix subprocess semantics are unchanged.
"""
import subprocess
import sys


def no_console_window():
    """Return kwargs for subprocess.run/Popen that suppress console flashes."""
    if sys.platform == 'win32':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)}
    return {}


def process_io_bytes(proc):
    """Read native Windows per-process I/O counters (no subprocess or disk reads).

    None means counters unavailable. This is *diagnostic*, not scan progress:
    some device drivers do not account raw reads as ordinary file I/O.
    """
    if sys.platform != 'win32' or proc is None:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name,ctypes.c_ulonglong) for name in (
                'ReadOperationCount','WriteOperationCount','OtherOperationCount',
                'ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        fn=ctypes.WinDLL('kernel32',use_last_error=True).GetProcessIoCounters
        fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(IO_COUNTERS)]
        fn.restype=wintypes.BOOL
        counters=IO_COUNTERS()
        handle=getattr(proc,'_handle',None)
        if handle is None or not fn(handle,ctypes.byref(counters)):
            return None
        return int(counters.ReadTransferCount)
    except (AttributeError, OSError, ValueError, TypeError):
        return None
