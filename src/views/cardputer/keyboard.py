"""Cardputer Zero evdev keys; synthetic hold-to-repeat is driver-independent.

Use physical F/X/Z/C directly (no Fn). Linux EV_KEY repeat events differ
between kernels, so one repeat policy is implemented here for navigation.
"""
import glob
import os
import select
import struct
import time

# Shared Windows tests import the event logic, without using Linux evdev I/O.
try:
    import fcntl
except ImportError:
    fcntl = None

EV_KEY = 1
ESC = 1
ENTER = {28, 96, 57}  # Enter / numpad Enter / Space
# Cardputer Zero physical keys. Kernel arrow keys remain secondary aliases.
UP = {33, 103}        # F / Up
DOWN = {45, 108}      # X / Down
LEFT = {44, 105}      # Z / Left
RIGHT = {46, 106}     # C / Right
KEY_TAB = 15
KEY_A = 30
KEY_R = 19
KEY_E = 18  # Post-scan extension filter
INPUT_EVENT = struct.Struct('llHHi')  # Linux arm64 evdev 24 bytes

REPEAT_DELAY = 0.16
REPEAT_INTERVAL = 0.047
FAST_REPEAT_AFTER = 0.85
FAST_REPEAT_INTERVAL = 0.028
TURBO_REPEAT_AFTER = 3.0
TURBO_REPEAT_INTERVAL = 0.012


def _evdev_ioctl(nr, size):
    return (2 << 30) | (size << 16) | (ord('E') << 8) | nr


KEY_BITS_LEN = 96
INTERESTING_KEYS = {ESC, *ENTER, *UP, *DOWN, *LEFT, *RIGHT, KEY_TAB, KEY_A, KEY_R, KEY_E}


def _has_key(bits, key):
    index = key // 8
    return index < len(bits) and bool(bits[index] & (1 << (key % 8)))


class RepeatTracker:
    """Independent repeat for directions only; never repeat destructive actions."""
    _dirs = {**{code: 'UP' for code in UP}, **{code: 'DOWN' for code in DOWN},
             **{code: 'LEFT' for code in LEFT}, **{code: 'RIGHT' for code in RIGHT}}

    def __init__(self):
        self.held = {}  # (fd, keycode) -> (key_name, down_at, next_at)

    def feed(self, fd, code, value, now):
        direction = self._dirs.get(code)
        if not direction:
            return []
        token = fd, code
        if value == 0:
            self.held.pop(token, None)
            return []
        if value == 1:
            if token not in self.held:
                self.held[token] = (direction, now, now + REPEAT_DELAY)
                return [direction]
        # value 2 is driver's repeat; ignore to prevent double acceleration
        return []

    def poll(self, now):
        output = []
        for token, (direction, started, deadline) in list(self.held.items()):
            if now < deadline:
                continue
            held_for = now - started
            if held_for >= TURBO_REPEAT_AFTER:
                interval = TURBO_REPEAT_INTERVAL
            elif held_for >= FAST_REPEAT_AFTER:
                interval = FAST_REPEAT_INTERVAL
            else:
                interval = REPEAT_INTERVAL
            # Skip backlogged repeats when rendering slows; no burst of 100 keys.
            self.held[token] = (direction, started, now + interval)
            output.append(direction)
        return output


class Keyboard:
    def __init__(self):
        if fcntl is None:
            raise RuntimeError("Cardputer keyboard input requires Linux evdev/fcntl")
        self.fd = []
        self.esc_pressed = None
        self.buffers = {}
        self.repeat = RepeatTracker()
        prefer = os.environ.get('SECTORREC_INPUT', '')
        candidates = ([prefer] if prefer else []) + sorted(glob.glob('/dev/input/event*'))
        accessible = 0
        for path in dict.fromkeys(candidates):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError:
                continue
            accessible += 1
            try:
                bits = bytearray(KEY_BITS_LEN)
                try:
                    fcntl.ioctl(fd, _evdev_ioctl(0x20 + EV_KEY, len(bits)), bits, True)
                    is_keyboard = any(_has_key(bits, key) for key in INTERESTING_KEYS)
                except OSError:
                    name = bytearray(256)
                    try:
                        fcntl.ioctl(fd, _evdev_ioctl(0x06, len(name)), name, True)
                        desc = name.partition(b'\0')[0].decode('utf-8', 'replace').lower()
                    except OSError:
                        desc = ''
                    is_keyboard = any(word in desc for word in ('keyboard', 'cardputer', 'keypad', 'kbd'))
                if path == prefer or is_keyboard:
                    self.fd.append(fd)
                    self.buffers[fd] = bytearray()
                else:
                    os.close(fd)
            except BaseException:
                os.close(fd)
                self.close()
                raise
        if not self.fd:
            if accessible:
                raise RuntimeError('Input accessible, but keyboard keys not detected. Set SECTORREC_INPUT=/dev/input/eventN.')
            raise RuntimeError('No readable /dev/input/event* device. Check input permissions.')

    def esc_hold_fraction(self):
        if self.esc_pressed is None:
            return 0.0
        return min(1.0, (time.monotonic() - self.esc_pressed) / 3.0)

    def poll(self, timeout=0.003):
        ready, _, _ = select.select(self.fd, [], [], timeout)
        events = []
        for fd in ready:
            try:
                chunk = os.read(fd, INPUT_EVENT.size * 32)
            except (OSError, BlockingIOError):
                continue
            if not chunk:
                continue
            self.buffers[fd].extend(chunk)
            buf = self.buffers[fd]
            while len(buf) >= INPUT_EVENT.size:
                record = bytes(buf[:INPUT_EVENT.size])
                del buf[:INPUT_EVENT.size]
                _, _, etype, code, value = INPUT_EVENT.unpack(record)
                if etype != EV_KEY:
                    continue
                now = time.monotonic()
                events.extend(self.repeat.feed(fd, code, value, now))
                if code in self.repeat._dirs:
                    continue
                if code == ESC:
                    if value == 1:
                        self.esc_pressed = now
                        events.append('ESC_DOWN')
                    elif value == 0 and self.esc_pressed is not None:
                        duration = now - self.esc_pressed
                        events.append('EXIT' if duration >= 3.0 else 'BACK')
                        self.esc_pressed = None
                elif value == 1:
                    if code in ENTER:
                        events.append('OK')
                    elif code == KEY_TAB:
                        events.append('FILTER')
                    elif code == KEY_E:
                        events.append('EXTENSION')
                    elif code == KEY_A:
                        events.append('ALL')
                    elif code == KEY_R:
                        events.append('REFRESH')
        now = time.monotonic()
        events.extend(self.repeat.poll(now))
        if self.esc_pressed is not None and now - self.esc_pressed >= 3.0:
            self.esc_pressed = None
            events.append('EXIT')
        return events

    def discard_wake_input(self):
        """Consume all state of the key used to wake the blanked LCD.

        In particular ESC_DOWN has already armed esc_pressed during poll().
        Without clearing it, the *release* still sends BACK and stops a scan;
        holding the key may otherwise emit EXIT after three seconds. Held
        arrow keys must not keep scrolling from synthetic auto-repeat either.
        A fresh press after release continues to work as usual.
        """
        self.esc_pressed = None
        self.repeat.held.clear()

    def close(self):
        for fd in self.fd:
            os.close(fd)
        self.fd.clear()
