"""Compact, readable elapsed-time labels shared by PC and Cardputer views.

Durations are observations, not estimates of completion time. Keep the
Cardputer variant short enough for its 320-pixel display.
"""


def format_elapsed(seconds, compact=False):
    """Show seconds, minutes + seconds, or hours + minutes + seconds."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return (f'{hours}h{minutes:02}m{secs:02}s' if compact
                else f'{hours}h {minutes:02}m {secs:02}s')
    if minutes:
        return f'{minutes}m{secs:02}s' if compact else f'{minutes}m {secs:02}s'
    return f'{secs}s'
