"""Regression checks for Windows image resume and timed-out USB partition scans.

All cases simulate Windows and temporary image files. No raw device is opened.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.windows_ddrescue import canonical_source_identity
from src.services import image_rescue, partition

DRIVE = r'\\.\PhysicalDrive1'


class ResumeUsingVerifiedDiskIdentity(unittest.TestCase):
    def _test_resume(self, live_identity, saved_identity, saved_size, selected_size):
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / 'SectorRec_PhysicalDrive1.img'
            image.write_bytes(b'only a partial image')
            Path(str(image) + '.map').write_text('0x0 0x10 +\n')
            Path(str(image) + '.source.json').write_text(json.dumps({
                'schema': 1, 'identity': saved_identity,
                'size': saved_size, 'device': DRIVE,
            }))
            with patch.object(image_rescue, 'os', SimpleNamespace(name='nt')), \
                 patch('src.infrastructure.windows_raw.require_admin'), \
                 patch('src.infrastructure.windows_ddrescue.assert_image_unlocked'), \
                 patch.object(image_rescue, 'ensure_different_disk'), \
                 patch.object(image_rescue, 'source_identity', return_value=live_identity):
                return image_rescue.plan_resume(DRIVE, image, selected_size)

    def test_cached_list_size_stale_but_saved_and_live_match(self):
        identity = canonical_source_identity('USB-SERIAL', 'USB-UID', 8192)
        self.assertEqual(self._test_resume(identity, identity, '8192', 8000).suffix, '.img')

    def test_different_usb_device_same_capacity_is_refused(self):
        original = canonical_source_identity('USB-A', 'UID-A', 8192)
        replacement = canonical_source_identity('USB-B', 'UID-B', 8192)
        with self.assertRaisesRegex(ValueError, 'source disk does not match'):
            self._test_resume(replacement, original, 8192, 8192)

    def test_real_device_size_change_is_refused(self):
        original = canonical_source_identity('USB-A', 'UID-A', 8192)
        changed = canonical_source_identity('USB-A', 'UID-A', 16384)
        with self.assertRaisesRegex(ValueError, 'disk capacity|capacity changed'):
            self._test_resume(changed, original, 8192, 8192)

    def test_malformed_saved_size_is_refused(self):
        identity = canonical_source_identity('USB-A', 'UID-A', 8192)
        with self.assertRaisesRegex(ValueError, 'Invalid source capacity'):
            self._test_resume(identity, identity, 'not-a-number', 8192)

    def test_worker_uses_verified_size_on_resume(self):
        from contextlib import nullcontext
        actual = canonical_source_identity('USB-A', 'UID-A', 8192)
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / 'old.img'
            status = {}
            class FinishedProcess:
                returncode = 0
                def poll(self): return 0
                def wait(self): return 0
            with patch.object(image_rescue, 'os', SimpleNamespace(name='nt', SEEK_END=os.SEEK_END)), \
                 patch('src.infrastructure.windows_raw.ensure_different_windows_disk'), \
                 patch('src.infrastructure.windows_ddrescue.source_identity', return_value=actual), \
                 patch('src.infrastructure.windows_ddrescue.image_lock', return_value=nullcontext()), \
                 patch('src.infrastructure.windows_ddrescue.command', return_value=(['ddrescue'], {})), \
                 patch.object(image_rescue, 'plan_resume', return_value=image) as checked, \
                 patch.object(image_rescue, 'rescued_percent_from_map', return_value=20.0), \
                 patch.object(image_rescue.subprocess, 'Popen', return_value=FinishedProcess()):
                worker = image_rescue.ImageWorker(DRIVE, image, 8000, status, resume=True, expected_identity=actual)
                worker._run_windows()
                checked.assert_called_once_with(DRIVE, image, 8000)
            self.assertEqual(worker.source_bytes, 8192)
            self.assertEqual(status['size'], 8192)
            self.assertEqual(status['state'], 'incomplete')


class WindowsTestDiskAttribution(unittest.TestCase):
    def test_timeout_shows_known_windows_partition_without_claiming_a_find(self):
        class Proc:
            returncode = None
            def poll(self): return self.returncode
            def wait(self): return self.returncode
        proc = Proc()
        known = [partition.PartitionCandidate(2048, 1000, 'FAT32', origin='existing')]
        status = {}
        with patch.object(partition.sys, 'platform', 'win32'), \
             patch('src.infrastructure.windows_raw.require_admin'), \
             patch.object(partition, 'testdisk_binary', return_value='testdisk_win.exe'), \
             patch.object(partition.subprocess, 'Popen', return_value=proc), \
             patch.object(partition, 'existing_windows_partitions', return_value=known), \
             patch.object(partition, 'WINDOWS_SMALL_DISK_MAX_SECONDS', 0), \
             patch.object(partition, 'stop_proc', side_effect=lambda p: setattr(p, 'returncode', -1)):
            partition.PartitionSearchWorker(DRIVE, 512 * 1024 * 1024, False, status).run()
        self.assertTrue(status['partial'])
        self.assertTrue(status['testdisk_timed_out'])
        self.assertEqual(status['existing_count'], 1)
        self.assertEqual(status['testdisk_count'], 0)
        self.assertIn('reported by Windows, NOT discovered by TestDisk', status['note'])


if __name__ == '__main__':
    unittest.main()
