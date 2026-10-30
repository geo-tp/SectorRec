"""Regression checks for Windows GUI responsiveness, hidden tools and optional UAC.

Pure mocks: these tests never read a real drive or invoke Windows UAC.
"""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from src.controllers.controller import Controller
from src.infrastructure import windows_raw, windows_elevation
from src.models.core import ScanWorker


class WindowsRuntimeFixTests(unittest.TestCase):
    def test_destination_picker_does_not_query_powershell(self):
        class Win:
            name='win32'
            def devices(self): return []
        ui = Controller(demo=True, desktop=True, platform=Win())
        ui.selection = 0
        ui.devices[0]['path'] = r'\\.\F:'
        try:
            with tempfile.TemporaryDirectory() as folder:
                with patch('src.services.signature.validate_destination', side_effect=AssertionError('premature disk guard')):
                    self.assertEqual(str(ui.set_metadata_destination(folder)), str(Path(folder).resolve()))
                with patch('src.services.signature.validate_destination', return_value=(Path(folder), 200_000_000)) as guard:
                    self.assertEqual(ui.metadata_preflight()[1], 200_000_000)
                    guard.assert_called_once()
        finally:
            ui.close()

    def test_fast_destination_mapping_skips_powershell_when_proven(self):
        with patch.object(windows_raw, '_native_volume_disk_number', return_value=2):
            self.assertEqual(windows_raw._disk_for_destination('C:/Recovered'), 2)

    def test_multiple_disks_fail_closed(self):
        with patch.object(windows_raw, '_native_volume_disk_number', side_effect=ValueError('multiple physical disks')):
            with self.assertRaisesRegex(ValueError, 'multiple physical disks'):
                windows_raw._disk_for_destination('C:/Recovered')

    def test_three_second_windows_refresh(self):
        class Win:
            name = 'win32'
            def devices(self): return []
        ui = Controller(demo=True, platform=Win(), desktop=True)
        ui.demo = False
        try:
            with patch('src.controllers.controller.threading.Thread'):
                ui.request_device_refresh(now=42)
                self.assertEqual(ui.next_device_refresh, 45.0)
        finally:
            ui.close()

    def test_elevation_only_via_user_authorization(self):
        with patch.object(windows_elevation.sys, 'platform', 'win32'), \
             patch.object(windows_elevation.sys, 'frozen', True, create=True), \
             patch.object(windows_elevation.ctypes, 'windll', create=True) as dll:
            dll.shell32.ShellExecuteW.return_value = 42
            self.assertTrue(windows_elevation.restart_elevated_for_source(r'\\.\PhysicalDrive7'))
            args = dll.shell32.ShellExecuteW.call_args.args
            self.assertEqual(args[1], 'runas')
            self.assertIn('--resume-device', args[3])
            self.assertIn('PhysicalDrive7', args[3])
            dll.shell32.ShellExecuteW.return_value = 5
            with self.assertRaises(PermissionError):
                windows_elevation.restart_elevated_for_source(r'\\.\PhysicalDrive7')

    def test_invalid_uac_device_never_invoked(self):
        with patch.object(windows_elevation.sys, 'platform', 'win32'), \
             patch.object(windows_elevation.sys, 'frozen', True, create=True), \
             patch.object(windows_elevation.ctypes, 'windll', create=True) as dll:
            with self.assertRaises(ValueError):
                windows_elevation.restart_elevated_for_source(r'\\.\GLOBALROOT\Device')
            dll.shell32.ShellExecuteW.assert_not_called()

    def test_fls_hidden_without_affecting_stream(self):
        scanner = ScanWorker.__new__(ScanWorker)
        scanner.cancel_event = __import__('threading').Event()
        scanner.command_builder = lambda action, device, inode: ['fls.exe','-r','image.img']
        scanner.device = 'image.img'
        scanner.proc = None
        fake = SimpleNamespace(stdout=io.StringIO('d/r 17: file.jpg\n'), wait=lambda:0)
        with patch('src.models.core.subprocess.Popen', return_value=fake) as launch, \
             patch('src.infrastructure.windows_process.sys.platform','win32'):
            self.assertEqual(list(scanner.stream('scan')), ['d/r 17: file.jpg\n'])
            self.assertEqual(launch.call_args.kwargs['creationflags'], 0x08000000)


if __name__ == '__main__': unittest.main()
