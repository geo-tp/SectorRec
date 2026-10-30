"""Regression checks for invisible Windows background shell calls."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure import platform_disks, windows_raw, windows_ddrescue
from src.infrastructure.windows_process import no_console_window
from src.controllers.controller import Controller


class WindowsBackgroundConsoleTests(unittest.TestCase):
    def test_windows_only_flag(self):
        with patch('src.infrastructure.windows_process.sys.platform', 'win32'):
            self.assertEqual(no_console_window()['creationflags'], 0x08000000)
        with patch('src.infrastructure.windows_process.sys.platform', 'linux'):
            self.assertEqual(no_console_window(), {})

    def test_inventory_uses_invisible_powershell(self):
        calls=[]
        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return SimpleNamespace(returncode=0, stdout='[]')
        with patch('src.infrastructure.windows_process.sys.platform', 'win32'):
            self.assertEqual(platform_disks.windows_inventory(run=run), [])
        self.assertEqual(calls[0][0][0], 'powershell.exe')
        self.assertEqual(calls[0][1]['creationflags'], 0x08000000)

    def test_guard_uses_invisible_powershell(self):
        calls=[]
        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return SimpleNamespace(returncode=0, stdout='2\n')
        with patch('src.infrastructure.windows_process.sys.platform','win32'):
            self.assertEqual(windows_raw._disk_for_destination('C:/Backup',run=run), 2)
        self.assertEqual(calls[0][1]['creationflags'],0x08000000)

    def test_ddrescue_identity_uses_invisible_powershell(self):
        calls=[]
        def run(cmd, **kwargs):
            calls.append((cmd,kwargs))
            return SimpleNamespace(returncode=0,stdout=json.dumps({'Serial':'GOOD','Unique':'','Size':'4096'}))
        with patch('src.infrastructure.windows_process.sys.platform','win32'):
            self.assertTrue(windows_ddrescue.source_identity(r'\\.\PhysicalDrive1',run=run))
        self.assertEqual(calls[0][1]['creationflags'],0x08000000)

    def test_windows_refresh_is_less_frequent(self):
        class Win:
            name='win32'
            def devices(self): return []
        ui=Controller(demo=True,platform=Win(),desktop=True)
        ui.demo=False
        try:
            with patch('src.controllers.controller.threading.Thread') as thread:
                thread.return_value.is_alive.return_value=False
                ui.request_device_refresh(now=100.0)
                self.assertEqual(ui.next_device_refresh,103.0)
                thread.assert_called_once()
        finally:
            ui.close()

if __name__=='__main__': unittest.main()
