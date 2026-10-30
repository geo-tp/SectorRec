"""PC presentation regression: fail-closed destination choice must never look successful.

All tests are simulated: never open physical disks or start any recovery engines.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_INITIAL_DISPLAY = os.environ.get('DISPLAY')

from src.app import UI
from src.views.desktop_native import DesktopWindow, DANGER


class WarningTextTests(unittest.TestCase):
    def test_same_disk_message_is_explicit(self):
        text = DesktopWindow._explain_destination_error(
            ValueError('Destination belongs to the source physical disk. Choose another disk.'))
        self.assertIn('SAME PHYSICAL DISK', text)
        self.assertIn('another partition', text)
        self.assertIn('overwrite', text)

    def test_unrelated_error_is_preserved(self):
        self.assertEqual(DesktopWindow._explain_destination_error('Permission denied'),
                         'Permission denied')


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Tk needs display')
class ThemedDestinationTests(unittest.TestCase):
    def setUp(self):
        self._display_patch = patch.dict(os.environ, {'DISPLAY': _INITIAL_DISPLAY} if _INITIAL_DISPLAY else {})
        self._display_patch.start()
        self.addCleanup(self._display_patch.stop)
        self.ui = UI(demo=True, desktop=True)
        self.window = DesktopWindow(self.ui)
        self.window.root.update()

    def tearDown(self):
        self.window.close()

    def _text_widgets(self, widget):
        result = []
        for item in widget.winfo_children():
            if 'text' in item.keys():
                result.append(item)
            result.extend(self._text_widgets(item))
        return result

    def test_image_same_disk_shows_red_x_and_prevents_launch(self):
        source = dict(path='/dev/sdb', label='Test USB', size=8192,
                      partition_disk=True)
        with tempfile.TemporaryDirectory() as temp:
            self.window.show_image_setup(source)
            with patch.object(self.window, 'pick_destination_directory', return_value=temp), \
                 patch('src.services.image_rescue.plan_image',
                       side_effect=ValueError('Destination belongs to the source physical disk.')):
                browse = next(item for item in self._text_widgets(self.window.body)
                              if item.cget('text') == 'Browse…')
                browse.invoke()
                self.window.root.update_idletasks()
            texts = self._text_widgets(self.window.body)
            chosen = next(item for item in texts
                          if item.cget('text').startswith('✕  ' + temp))
            self.assertEqual(chosen.cget('fg'), DANGER)
            self.assertFalse(any(item.cget('text').startswith('✓  ' + temp)
                                 for item in texts))
            launch = next(item for item in self._text_widgets(self.window.footer)
                          if item.cget('text') == 'Start imaging  →')
            self.assertEqual(launch.cget('state'), 'disabled')

    def test_image_valid_destination_shows_green_and_enables_launch(self):
        source = dict(path='/dev/sdb', label='Test USB', size=8192,
                      partition_disk=True)
        with tempfile.TemporaryDirectory() as temp:
            self.window.show_image_setup(source)
            with patch.object(self.window, 'pick_destination_directory', return_value=temp), \
                 patch('src.services.image_rescue.plan_image',
                       return_value=Path(temp)/'copy.img'):
                browse = next(item for item in self._text_widgets(self.window.body)
                              if item.cget('text') == 'Browse…')
                browse.invoke()
                self.window.root.update_idletasks()
            texts = self._text_widgets(self.window.body)
            chosen = next(item for item in texts
                          if item.cget('text').startswith('✓  ' + temp))
            self.assertNotEqual(chosen.cget('fg'), DANGER)
            launch = next(item for item in self._text_widgets(self.window.footer)
                          if item.cget('text') == 'Start imaging  →')
            self.assertEqual(launch.cget('state'), 'normal')

    def test_bad_folder_notice_is_themed(self):
        with patch.object(self.window, 'show_modal_notice') as themed:
            self.window._open_folder('/path/this/folder/does/not/exist')
        themed.assert_called_once()
        self.assertEqual(themed.call_args.args[0], 'Destination unavailable')
        self.assertTrue(themed.call_args.kwargs['warning'])

class FastNativeWindowsCheckTests(unittest.TestCase):
    def test_native_only_check_is_tristate_and_never_calls_powershell(self):
        from src.infrastructure import windows_raw
        disk = r'\\.\PhysicalDrive1'
        with patch.object(windows_raw.sys, 'platform', 'win32'), \
             patch.object(windows_raw, '_native_volume_disk_number', return_value=1) as native:
            self.assertTrue(windows_raw.quick_same_windows_disk(disk, r'E:\\Recover'))
            native.assert_called_once_with(r'E:\\Recover')
        with patch.object(windows_raw.sys, 'platform', 'win32'), \
             patch.object(windows_raw, '_native_volume_disk_number', return_value=2):
            self.assertFalse(windows_raw.quick_same_windows_disk(disk, r'E:\\Recover'))
        with patch.object(windows_raw.sys, 'platform', 'win32'), \
             patch.object(windows_raw, '_native_volume_disk_number', return_value=None):
            self.assertIsNone(windows_raw.quick_same_windows_disk(disk, r'E:\\Recover'))
