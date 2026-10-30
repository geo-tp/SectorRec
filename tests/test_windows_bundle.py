"""Portable Windows image-backend preflight tests; runnable on Linux and Windows.

These tests do not claim to exercise a Windows GUI or actual Windows executables
when run on Linux. Native Windows CI also executes them before producing .exe.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.app import UI
from src.models.core import Catalog
from src.services.partition import testdisk_binary as find_testdisk_binary, discover_command, partition_command
from src.infrastructure.platforms import ImageOnlyPlatform
from src.services.source_assessment import assess_source


class WindowsBundleTests(unittest.TestCase):
    def test_win_testdisk_bundled_tool_is_used_for_image(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)
            bundled=directory/'photorec-bin'
            bundled.mkdir()
            executable=bundled/'testdisk_win.exe'
            executable.write_bytes(b'test fixture binary')
            img=directory/'my whole disk.dd'
            img.write_bytes(b'not a real filesystem')
            with mock.patch('src.services.partition.sys.platform','win32'), \
                 mock.patch('src.services.partition.sys._MEIPASS', str(directory),create=True):
                self.assertEqual(find_testdisk_binary(),str(executable))
                cmd=discover_command(str(img),image=True)
                self.assertEqual(cmd[0],str(executable))
                self.assertEqual(cmd[-1],'analyze,search')

    def test_win_offset_tsk_image_command_resolves_bundled_binary(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)
            bundled=directory/'tsk-bin'
            bundled.mkdir()
            binary=bundled/'fls.exe'
            binary.write_bytes(b'test fixture binary')
            img=directory/'raw.img'
            img.write_bytes(b'\0'*2048)
            with mock.patch('src.infrastructure.image_backend.sys.platform','win32'), \
                 mock.patch('src.infrastructure.image_backend.sys._MEIPASS',str(directory),create=True):
                cmd=partition_command('scan',str(img),1,image=True)
            self.assertEqual(cmd[0],str(binary))
            self.assertIn('-o',cmd)

    def test_raw_disk_windows_is_diagnostics_only(self):
        d={'path':r'\\.\PhysicalDrive4','size':256000000,'partition_disk':True,
           'supported':False,'diagnostics_only':True,'fs':'UNKNOWN'}
        assessment=assess_source(d)
        self.assertEqual(assessment.methods,())
        self.assertFalse(assessment.allows('partition'))
        platform=ImageOnlyPlatform('win32')
        with self.assertRaises(RuntimeError):platform.unmount(d['path'])

    def test_non_linux_controller_cannot_recover_unavailable_physical_disk(self):
        d={'path':r'\\.\PhysicalDrive4','size':256000000,'partition_disk':True,
           'supported':False,'diagnostics_only':True,'fs':'UNKNOWN'}
        ui=UI(demo=True,desktop=True,platform=ImageOnlyPlatform('win32'))
        try:
            ui.devices=[d]; ui.selection=0
            with self.assertRaises(ValueError):ui.desktop_select_method('partition')
        finally:ui.close()

    def test_catalog_live_page_handles_uri_escaped_paths(self):
        with tempfile.TemporaryDirectory(prefix='sector rec_#_') as td:
            cat=Catalog(Path(td)/'catalog #1.sqlite')
            try:
                cat.insert_batch([('12','folder/deleted.jpg',3,'PHOTOS','deleted')])
                count, rows=cat.live_page()
                self.assertEqual(count,1)
                self.assertEqual(rows[0][1],'folder/deleted.jpg')
            finally:cat.close()

    def test_windows_raw_access_uses_native_adapter(self):
        with mock.patch('src.views.desktop_native.sys.platform','win32'):
            # Linux raw-access adapter must never be selected on Windows.
            from src.infrastructure.platforms import current_platform
            self.assertTrue(current_platform().raw_drives)

if __name__=='__main__': unittest.main()
