"""Cardputer idle display policy: dim at 60 s, switch off at 120 s.

A real backlight shutdown uses a writable Linux brightness node (including
0/1 GPIO nodes). When the kernel exposes no such node, a black framebuffer is
the safe, non-privileged fallback; it is NOT equivalent to power-off.
"""
from pathlib import Path
import subprocess
import os
import time


class IdleDimmer:
    def __init__(self, after_seconds=60, factor=0.10,
                 backlight_dir=Path('/sys/class/backlight'), off_after_seconds=120):
        self.after_seconds = after_seconds
        self.off_after_seconds = max(after_seconds, off_after_seconds)
        self.factor = factor
        self.last_input = time.monotonic()
        self.dimmed = False
        self.off = False
        self.physical_off = False
        self.hardware_dimmed = False
        self.brightness_path = None
        self.original = None
        self.max_brightness = 0
        for candidate in sorted(backlight_dir.glob('*/brightness')):
            try:
                max_value = int((candidate.parent/'max_brightness').read_text().strip())
                current = int(candidate.read_text().strip())
                if max_value < 1 or current <= 0:
                    continue
                self.brightness_path = candidate
                self.original = current
                self.max_brightness = max_value
                break
            except (OSError, ValueError):
                continue

    def _write(self, value):
        if self.brightness_path is None:
            return False
        try:
            self.brightness_path.write_text(str(value))
            return True
        except OSError:
            # A stock Cardputer backlight node may be root-owned. Use only our
            # tightly restricted helper when running against the real sysfs.
            if (self.brightness_path is not None and
                    str(self.brightness_path).startswith('/sys/class/backlight/')):
                try:
                    if os.environ.get('SECTORREC_DEBIAN_ARCHIVE') == '1':
                        from src.infrastructure.privileged import privileged_command
                        command = privileged_command('backlight',
                            self.brightness_path.parent.name, str(value))
                    else:
                        # Legacy unmanaged builds (not the Debian package).
                        command = ['/usr/bin/sudo', '-n',
                            '/usr/lib/sectorrec/sectorrec-backlight',
                            self.brightness_path.parent.name, str(value)]
                    result=subprocess.run(command,
                        timeout=3,capture_output=True,check=False)
                    return result.returncode==0
                except (OSError,subprocess.TimeoutExpired):
                    pass
            return False

    def input(self, now=None):
        self.last_input = time.monotonic() if now is None else now
        was_inactive = self.dimmed or self.off
        if was_inactive:
            self.restore()
        return was_inactive

    def update(self, now=None):
        now = time.monotonic() if now is None else now
        elapsed = now - self.last_input
        if not self.off and elapsed >= self.off_after_seconds:
            self.off = True
            self.dimmed = True
            self.physical_off = self._write(0)
            return True
        if not self.dimmed and elapsed >= self.after_seconds:
            self.dimmed = True
            if self.max_brightness >= 10:
                self.hardware_dimmed = self._write(max(1, round(self.original * self.factor)))
            return True
        return False

    @property
    def software_dim(self):
        return self.dimmed and not self.off and not self.hardware_dimmed

    @property
    def software_off(self):
        return self.off and not self.physical_off

    def restore(self):
        if (self.dimmed or self.off) and self.brightness_path is not None:
            self._write(self.original)
        self.off = False
        self.dimmed = False
        self.physical_off = False
        self.hardware_dimmed = False

    def close(self):
        self.restore()
