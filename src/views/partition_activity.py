"""Presentation-only TestDisk activity descriptors, never used for scan control.

Process read I/O can count repeated sectors and cached data. An unchanged log
is not evidence of a hung scan; warnings are deliberately delayed and cautious.
"""
from src.models.files import human_size
from src.views.elapsed import format_elapsed

LOG_PAUSED_SECONDS = 90
POSSIBLE_IDLE_SECONDS = 150


def partition_activity_display(status, elapsed, platform):
    """Small, bounded strings for the desktop TestDisk activity tiles."""
    elapsed = max(0, int(elapsed))
    read_bytes = status.get('testdisk_io_bytes')
    io_idle = max(0, int(status.get('testdisk_io_idle_seconds') or 0))
    read_value = human_size(read_bytes) if read_bytes is not None else 'Waiting…'
    read_detail = ('Cumulative process reads · not scan progress'
                   if read_bytes is not None else 'Reading activity not yet reported')

    log_warn = False
    caution = ''
    if platform == 'win32':
        log_bytes = status.get('testdisk_log_bytes')
        log_value = f'{int(log_bytes):,} bytes' if log_bytes is not None else 'Waiting…'
        phase = str(status.get('testdisk_phase') or '').strip()
        # Avoid echoing arbitrarily long, untrusted external tool output.
        log_detail = (phase[:75] if phase else 'Diagnostic log activity')
        idle = max(0, int(status.get('testdisk_log_idle_seconds') or 0))
        if log_bytes is not None and idle >= LOG_PAUSED_SECONDS and elapsed >= LOG_PAUSED_SECONDS:
            log_detail = f'Log unchanged for {format_elapsed(idle)}'
        # Only mention possible idle when BOTH measurements have stopped, or
        # when process read I/O is unavailable. A quiet log during sustained
        # reading is perfectly normal for TestDisk.
        if (log_bytes is not None and idle >= POSSIBLE_IDLE_SECONDS
                and elapsed >= POSSIBLE_IDLE_SECONDS
                and (read_bytes is None or io_idle >= POSSIBLE_IDLE_SECONDS)):
            log_warn = True
            caution = ('No recent log or read activity detected. The scan may be waiting; '
                       'you can stop early to review partial results.')
        elif log_bytes is not None and idle >= LOG_PAUSED_SECONDS and io_idle < LOG_PAUSED_SECONDS and read_bytes is not None:
            log_detail = 'Log unchanged · process reads still active'
    else:
        log_value = 'Running'
        log_detail = 'Read-only · stop early to review partial results'

    return {'clock': f'{format_elapsed(elapsed)} ELAPSED',
            'read_value': read_value, 'read_detail': read_detail,
            'log_value': log_value, 'log_detail': log_detail,
            'log_warn': log_warn, 'caution': caution}
