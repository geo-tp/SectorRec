"""Keyboard probes work even with device names not containing 'keyboard'."""
import unittest
from unittest import mock

from src.views.cardputer import keyboard


class KeyboardTests(unittest.TestCase):
    def test_linux_ioctl_lengths(self):
        self.assertEqual(keyboard._evdev_ioctl(0x06, 256), 0x81004506)
        self.assertEqual(keyboard._evdev_ioctl(0x21, 96), 0x80604521)

    def test_detects_unknown_named_keyboard_by_capabilities(self):
        paths=['/dev/input/event0','/dev/input/event1']
        def fake_open(path,flags):
            return 10 + paths.index(path)
        def fake_ioctl(fd,code,buf,mutate=True):
            self.assertEqual(code,keyboard._evdev_ioctl(0x21,96))
            keys = [116] if fd==10 else [1,28,57,103,108]  # Power button vs real keyboard
            for key in keys:
                buf[key//8] |= 1 << (key%8)
            return 0
        with mock.patch.object(keyboard.glob,'glob',return_value=paths), \
             mock.patch.dict(keyboard.os.environ,{'SECTORREC_INPUT':''}), \
             mock.patch.object(keyboard.os,'open',side_effect=fake_open), \
             mock.patch.object(keyboard.os,'close') as close, \
             mock.patch.object(keyboard.fcntl,'ioctl',side_effect=fake_ioctl):
            k=keyboard.Keyboard()
            self.assertEqual(k.fd,[11])
            close.assert_called_once_with(10)
            k.close()
            self.assertEqual(close.call_count,2)

    def test_explicit_override(self):
        with mock.patch.object(keyboard.glob,'glob',return_value=['/dev/input/event1']), \
             mock.patch.dict(keyboard.os.environ,{'SECTORREC_INPUT':'/dev/input/event2'}), \
             mock.patch.object(keyboard.os,'open',side_effect=lambda p,f:{'/dev/input/event2':12,'/dev/input/event1':11}[p]), \
             mock.patch.object(keyboard.os,'close'), \
             mock.patch.object(keyboard.fcntl,'ioctl',return_value=0):
            k=keyboard.Keyboard()
            self.assertEqual(k.fd,[12])
            k.close()

    def test_fallback_device_name_uses_correct_sized_ioctl(self):
        def fake_ioctl(fd,code,buf,mutate=True):
            if code==keyboard._evdev_ioctl(0x21,96):
                raise OSError('unsupported')
            self.assertEqual(code,keyboard._evdev_ioctl(0x06,256))
            buf[:17] = b'Generic Keyboard\0'
            return 0
        with mock.patch.object(keyboard.glob,'glob',return_value=['/dev/input/event7']), \
             mock.patch.dict(keyboard.os.environ,{'SECTORREC_INPUT':''}), \
             mock.patch.object(keyboard.os,'open',return_value=17), \
             mock.patch.object(keyboard.os,'close'), \
             mock.patch.object(keyboard.fcntl,'ioctl',side_effect=fake_ioctl):
            k=keyboard.Keyboard()
            self.assertEqual(k.fd,[17])
            k.close()


if __name__=='__main__':
    unittest.main()
