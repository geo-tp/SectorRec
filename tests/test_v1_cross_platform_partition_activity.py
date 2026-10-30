"""Regression tests for TestDisk activity UI and initial device discovery."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure import process_activity
from src.controllers.controller import Controller


class ProcessReadTests(unittest.TestCase):
    def test_linux_counter_parses_read_syscall_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'io'
            report.write_text('rchar: 895000000\nread_bytes: 524288000\n')
            real_open = open
            def redirect(path, *args, **kwargs):
                if str(path) == '/proc/4711/io':
                    return real_open(report, *args, **kwargs)
                return real_open(path, *args, **kwargs)
            with patch('builtins.open', side_effect=redirect):
                self.assertEqual(process_activity.linux_read_counter(4711), 895000000)

    def test_read_counter_can_exceed_source_capacity(self):
        status = {}
        process_activity.record_read_activity(status, 895000000, 10.0)
        self.assertEqual(status['testdisk_io_bytes'], 895000000)
        process_activity.record_read_activity(status, 895000000, 15.0)
        self.assertEqual(status['testdisk_io_idle_seconds'], 5)
        process_activity.record_read_activity(status, 895000001, 16.0)
        self.assertEqual(status['testdisk_io_idle_seconds'], 0)

    def test_counter_unavailable_is_noop(self):
        status = {}
        process_activity.record_read_activity(status, None, 10.0)
        self.assertNotIn('testdisk_io_bytes',status)
        with patch.object(process_activity.sys, 'platform', 'unsupported'):
            self.assertIsNone(process_activity.process_read_bytes(SimpleNamespace(pid=22)))

    def test_platform_routing(self):
        proc = SimpleNamespace(pid=99)
        with patch.object(process_activity.sys,'platform','linux'), \
             patch.object(process_activity,'linux_read_counter',return_value=1024) as linux:
            self.assertEqual(process_activity.process_read_bytes(proc),1024)
            linux.assert_called_once_with(99)
        with patch.object(process_activity.sys,'platform','unsupported'):
            self.assertIsNone(process_activity.process_read_bytes(proc))
        with patch.object(process_activity.sys,'platform','win32'), \
             patch('src.infrastructure.windows_process.process_io_bytes',return_value=4096):
            self.assertEqual(process_activity.process_read_bytes(proc),4096)


class DiscoveryInitialStateTests(unittest.TestCase):
    def test_empty_initial_list_has_separate_flag_from_no_devices(self):
        ui=Controller(demo=True,desktop=True)
        try:
            ui.devices=[]
            ui.device_refresh_completed=False
            self.assertFalse(ui.device_refresh_completed)
            ui.device_refresh_ready=('ok',[])
            ui.apply_device_refresh()
            self.assertTrue(ui.device_refresh_completed)
            self.assertEqual(ui.devices,[])
            ui.device_refresh_completed=False
            ui.device_refresh_ready=('error','a safe error')
            ui.apply_device_refresh()
            self.assertTrue(ui.device_refresh_completed)
            self.assertEqual(ui.device_refresh_error,'a safe error')
        finally:
            ui.close()

    def test_desktop_initial_scan_to_empty_repaints(self):
        from src.views.desktop_native import DesktopWindow
        ui=Controller(demo=True,desktop=True)
        ui.devices=[]
        ui.device_refresh_completed=False
        try:
            window=DesktopWindow(ui)
        except Exception as exc:
            ui.close()
            self.skipTest(str(exc))
        try:
            self.assertFalse(window._device_refresh_rendered)
            ui.device_refresh_ready=('ok',[])
            ui.apply_device_refresh()
            window.tick()
            self.assertTrue(window._device_refresh_rendered)
        finally:
            window.close()


if __name__=='__main__':
    unittest.main()
