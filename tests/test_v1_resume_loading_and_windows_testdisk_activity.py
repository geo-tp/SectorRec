"""Windows TestDisk stall diagnostics and responsive desktop image-resume checks."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import partition
from src.infrastructure.windows_process import process_io_bytes


class TestDiskSafetyTests(unittest.TestCase):
    def test_physical_small_drive_has_no_fixed_timeout_in_production(self):
        self.assertIsNone(partition.WINDOWS_SMALL_DISK_MAX_SECONDS)

    def test_windows_process_activity_is_best_effort(self):
        with patch('src.infrastructure.windows_process.sys.platform', 'linux'):
            self.assertIsNone(process_io_bytes(object()))

    def test_native_log_phase_is_not_mistaken_for_scan_progress(self):
        self.assertEqual(partition.testdisk_report_phase('search_part()'),
                         'Searching for partition signatures')
        self.assertEqual(partition.testdisk_report_phase('Disk /dev/sda'),
                         'Opening and identifying the disk')

    def test_manual_stop_preserves_known_windows_partitions(self):
        class Proc:
            returncode=None
            def poll(self): return self.returncode
            def wait(self): return self.returncode
        proc=Proc()
        known=[partition.PartitionCandidate(2048,1024,'FAT32',origin='existing')]
        status={}
        with patch.object(partition.sys,'platform','win32'), \
             patch('src.infrastructure.windows_raw.require_admin'), \
             patch.object(partition,'testdisk_binary',return_value='testdisk_win.exe'), \
             patch.object(partition.subprocess,'Popen',return_value=proc), \
             patch.object(partition,'existing_windows_partitions',return_value=known), \
             patch.object(partition,'stop_proc',side_effect=lambda p:setattr(p,'returncode',-1)):
            worker=partition.PartitionSearchWorker(r'\\.\PhysicalDrive1',512*1024*1024,False,status)
            worker.cancel()
            worker.run()
        self.assertEqual(status['state'],'cancelled')
        self.assertTrue(status['partial'])
        self.assertEqual(status['candidates'],known)
        self.assertIn('reported by Windows',status['note'])


class ResumeLoadingTests(unittest.TestCase):
    def setUp(self):
        from src.app import UI
        from src.views.desktop_native import DesktopWindow
        try:
            self.window=DesktopWindow(UI(demo=True,desktop=True))
        except Exception as exc:
            self.skipTest(str(exc))

    def tearDown(self):
        if hasattr(self,'window'):
            self.window.close()

    def pump(self,seconds):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            self.window.root.update()
            time.sleep(.015)

    def test_resume_validation_does_not_block_tk(self):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'partial.img'
            image.write_bytes(b'data')
            Path(str(image)+'.source.json').write_text('{"size":1000}')
            def slow_check(*_a, **_kw):
                time.sleep(.22)
                return image
            with patch.object(self.window,'pick_source_image',return_value=str(image)), \
                 patch('src.services.image_rescue.plan_resume',side_effect=slow_check), \
                 patch('src.services.image_rescue.rescued_percent_from_map',return_value=32.0), \
                 patch.object(self.window,'confirm_action',return_value=False) as confirm:
                start=time.monotonic()
                self.window._select_resume_image({'path':'/dev/sda','size':1000})
                self.assertLess(time.monotonic()-start,.20)
                self.assertIsNotNone(self.window._resume_check)
                self.pump(.5)
                self.assertIsNone(self.window._resume_check)
                confirm.assert_called_once()

    def test_resume_validation_preserves_windows_source_identity(self):
        # Windows preflight must keep source_info for strict disk validation.
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'partial.img'
            image.write_bytes(b'data')
            Path(str(image)+'.source.json').write_text('{"size":1000}')
            source={'path':r'\\.\PhysicalDrive3','size':1000,'imaging_identity':'disk-id'}
            with patch.object(self.window,'pick_source_image',return_value=str(image)), \
                 patch('src.views.desktop_native.sys.platform','win32'), \
                 patch('src.services.image_rescue.plan_resume',return_value=image) as resume, \
                 patch('src.services.image_rescue.rescued_percent_from_map',return_value=32.0), \
                 patch.object(self.window,'confirm_action',return_value=False):
                self.window._select_resume_image(source)
                self.pump(.4)
                self.assertIsNone(self.window._resume_check)
                resume.assert_called_once_with(source['path'],str(image),1000,source_info=source)

    def test_unexpected_validation_error_is_shown_instead_of_crashing_tk(self):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'partial.img'
            image.write_bytes(b'data')
            with patch.object(self.window,'pick_source_image',return_value=str(image)), \
                 patch('src.services.image_rescue.plan_resume',side_effect=TypeError('bad validation argument')), \
                 patch.object(self.window,'show_modal_notice') as notice, \
                 patch.object(self.window,'confirm_action') as confirm:
                self.window._select_resume_image({'path':'/dev/sda','size':1000})
                self.pump(.4)
                self.assertIsNone(self.window._resume_check)
                notice.assert_called_once()
                self.assertIn('bad validation argument',str(notice.call_args))
                confirm.assert_not_called()

    def test_cancel_does_not_show_confirmation_later(self):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'partial.img'
            image.write_bytes(b'data')
            Path(str(image)+'.source.json').write_text('{"size":1000}')
            def slow_check(*_a, **_kw):
                time.sleep(.30)
                return image
            with patch.object(self.window,'pick_source_image',return_value=str(image)), \
                 patch('src.services.image_rescue.plan_resume',side_effect=slow_check), \
                 patch('src.services.image_rescue.rescued_percent_from_map',return_value=32.0), \
                 patch.object(self.window,'confirm_action',return_value=False) as confirm:
                self.window._select_resume_image({'path':'/dev/sda','size':1000})
                self.window._resume_check['cancel']()
                self.assertIsNone(self.window._resume_check)
                self.pump(.45)
                confirm.assert_not_called()

if __name__=='__main__':
    unittest.main()
