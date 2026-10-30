"""Best-effort *cumulative* read I/O for a TestDisk subprocess.

Not bytes uniquely scanned, not a percentage, and never used to decide whether
an operation is complete. Absence of a counter must not affect recovery.
"""
from __future__ import annotations

import sys


def linux_read_counter(pid: int) -> int | None:
    """Linux rchar is total bytes returned by read-like syscalls (cached too)."""
    try:
        with open(f'/proc/{int(pid)}/io', encoding='ascii') as report:
            for line in report:
                if line.startswith('rchar:'):
                    return max(0, int(line.partition(':')[2].strip()))
    except (OSError, ValueError, PermissionError):
        pass
    return None


def process_read_bytes(proc) -> int | None:
    """Return a cumulative counter or None without spawning subprocesses."""
    if proc is None or getattr(proc, 'pid', None) is None:
        return None
    if sys.platform == 'win32':
        from src.infrastructure.windows_process import process_io_bytes
        return process_io_bytes(proc)
    if sys.platform.startswith('linux'):
        return linux_read_counter(proc.pid)
    return None


def record_read_activity(status: dict, value: int | None, now: float) -> None:
    """Update UI diagnostics; counter decreases imply reset, not completion."""
    if value is None:
        return
    previous = status.get('testdisk_io_bytes')
    status['testdisk_io_bytes'] = max(0, int(value))
    if previous is None or value != previous:
        status['testdisk_io_last_change'] = now
    status['testdisk_io_idle_seconds'] = int(max(
        0, now - status.get('testdisk_io_last_change', now)))
