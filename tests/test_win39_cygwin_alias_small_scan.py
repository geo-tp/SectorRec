"""Windows device-alias safety and bounded TestDisk scan (mock devices only)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure import windows_ddrescue as bridge
from src.services import partition

GUID = r'\\.\Disk{4e45ab26-c086-826c-3cd8-fe75fff353f4}'
DRIVE = r'\\.\PhysicalDrive1'


class AliasProofTests(unittest.TestCase):
    def test_disk_guid_verified_by_native_disk_number(self):
        def cygpath(*a, **kw):
            return SimpleNamespace(returncode=0, stdout=GUID + '\n')
        with patch.object(bridge, '_native_disk_identity', side_effect=[(7, 1, 4096), (7, 1, 4096)]) as native:
            self.assertEqual(bridge.mapped_source(DRIVE, '.', run=cygpath), '/dev/sdb')
        self.assertEqual([x.args[0] for x in native.call_args_list], [DRIVE, GUID.lower()])

    def test_alias_that_points_to_another_disk_is_refused(self):
        with patch.object(bridge, '_native_disk_identity', side_effect=[(7, 1, 4096), (7, 0, 4096)]):
            with self.assertRaisesRegex(ValueError, 'Refusing imaging'):
                bridge.mapped_source(DRIVE, '.', run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=GUID))

    def test_alias_with_mismatched_size_is_refused(self):
        with patch.object(bridge, '_native_disk_identity', side_effect=[(7, 1, 4096), (7, 1, 8192)]):
            with self.assertRaisesRegex(ValueError, 'Refusing imaging'):
                bridge.mapped_source(DRIVE, '.', run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=GUID))

    def test_unverifiable_alias_is_refused(self):
        with patch.object(bridge, '_native_disk_identity', side_effect=OSError('access denied')):
            with self.assertRaisesRegex(ValueError, 'could not be verified'):
                bridge.mapped_source(DRIVE, '.', run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=GUID))

    def test_other_physical_disk_is_refused_without_native_probing(self):
        with patch.object(bridge, '_native_disk_identity') as native:
            with self.assertRaisesRegex(ValueError, 'Refusing imaging'):
                bridge.mapped_source(DRIVE, '.', run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=r'\\.\PhysicalDrive0'))
            native.assert_not_called()

    def test_partition_or_volume_alias_is_never_accepted(self):
        with patch.object(bridge, '_native_disk_identity') as native:
            with self.assertRaisesRegex(ValueError, 'Refusing imaging'):
                bridge.mapped_source(DRIVE, '.', run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=r'\\.\Volume{4e45ab26-c086-826c-3cd8-fe75fff353f4}'))
            native.assert_not_called()


class SmallDiskTimeoutTests(unittest.TestCase):
    def test_timeout_preserves_existing_windows_partition_with_partial_flag(self):
        class Proc:
            returncode = None
            def poll(self):
                return self.returncode
            def wait(self):
                return self.returncode
        proc=Proc()
        def stop_proc(p): p.returncode = -1
        status={}
        known=[partition.PartitionCandidate(2048,1000,'FAT32',origin='existing')]
        with patch.object(partition.sys,'platform','win32'), \
             patch('src.infrastructure.windows_raw.require_admin'), \
             patch.object(partition,'testdisk_binary',return_value='testdisk_win.exe'), \
             patch.object(partition.subprocess,'Popen',return_value=proc) as popen, \
             patch.object(partition,'existing_windows_partitions',return_value=known), \
             patch.object(partition,'WINDOWS_SMALL_DISK_MAX_SECONDS',0), \
             patch.object(partition, 'stop_proc', side_effect=stop_proc):
            partition.PartitionSearchWorker(DRIVE,512*1024*1024,False,status).run()
        self.assertEqual(status['state'],'done',status)
        self.assertTrue(status['partial'])
        self.assertTrue(status['testdisk_timed_out'])
        self.assertEqual(status['candidates'],known)
        self.assertIn('did not finish', status['note'])
        self.assertEqual(popen.call_args.kwargs['stdin'],partition.subprocess.DEVNULL)

if __name__=='__main__':
    unittest.main()
