"""Windows-specific scan and imaging regressions, with simulated disks only."""
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services import partition as part
from src.services.image_rescue import ImageWorker
from src.infrastructure import windows_ddrescue

DISK = r'\\.\PhysicalDrive3'


class WindowsExistingPartitions(unittest.TestCase):
    def test_includes_no_drive_letter_and_validates_geometry(self):
        records = [
            {'Offset': str(2048 * 512), 'Size': str(8000 * 512), 'Type': 'Basic', 'Letter': 'F'},
            {'Offset': str(12000 * 512), 'Size': str(4000 * 512), 'Type': 'Recovery', 'Letter': ''},
            {'Offset': str(30000 * 512), 'Size': str(4000 * 512), 'Type': 'Bad', 'Letter': ''},
            {'Offset': 'not-a-number', 'Size': '1000', 'Type': 'Bad', 'Letter': ''},
        ]
        captured = []
        def run(args, **kwargs):
            captured.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(records))
        partitions = part.existing_windows_partitions(DISK, source_bytes=20_000 * 512, run=run)
        self.assertEqual([(p.start, p.sectors, p.origin) for p in partitions],
                         [(2048,8000,'existing'),(12000,4000,'existing')])
        self.assertIn('Get-Partition -DiskNumber 3', captured[0][0][-1])
        self.assertIn('LogicalSectorSize', captured[0][0][-1])
        self.assertEqual(captured[0][1]['timeout'],12)

    def test_bad_disk_and_bad_powershell_are_ignored_safely(self):
        self.assertEqual(part.existing_windows_partitions(r'\\.\E:', 0), [])
        self.assertEqual(part.existing_windows_partitions(DISK, 0,
                         run=lambda *args, **kw: SimpleNamespace(returncode=4, stdout='')), [])

    def test_worker_merges_windows_partition_after_testdisk(self):
        events=[]
        report=('Disk /dev/sdc - 10240000 bytes, sector size=512\n'
                'Results\n P Other 2048 4095 2048\ninterface_write()\n')
        class Proc:
            returncode=0
            def poll(self): return 0
            def wait(self): events.append('wait');return 0
        def fake_popen(args, **kwargs):
            self.assertNotEqual(kwargs['stderr'],part.subprocess.PIPE)
            Path(args[args.index('/logname')+1]).write_text(report)
            return Proc()
        def fake_existing(source, size):
            events.append('lookup')
            return [part.PartitionCandidate(2048, 2048, 'FAT32', origin='existing'),
                    part.PartitionCandidate(12000, 4000, 'Recovery', origin='existing')]
        status={}
        with patch.object(part.sys,'platform','win32'),\
             patch('src.infrastructure.windows_raw.require_admin'),\
             patch.object(part,'testdisk_binary',return_value='testdisk_win.exe'),\
             patch.object(part.subprocess,'Popen',side_effect=fake_popen),\
             patch.object(part,'existing_windows_partitions',side_effect=fake_existing):
            part.PartitionSearchWorker(DISK, 20_000*512, False, status).run()
        self.assertEqual(status['state'],'done',status)
        self.assertEqual((status['existing_count'],status['found_count']),(2,0))
        self.assertEqual(events[:2],['wait','lookup'])
        self.assertGreaterEqual(status['testdisk_seconds'],0)

    def test_windows_existing_survives_unparseable_report(self):
        class Proc:
            returncode=1
            def poll(self):return 0
            def wait(self):return 1
        with patch.object(part.sys, 'platform', 'win32'),\
             patch('src.infrastructure.windows_raw.require_admin'),\
             patch.object(part, 'testdisk_binary', return_value='testdisk_win.exe'),\
             patch.object(part.subprocess,'Popen',return_value=Proc()),\
             patch.object(part,'existing_windows_partitions',return_value=[
                 part.PartitionCandidate(2048,2000,'FAT32', origin='existing')]):
            state={}
            part.PartitionSearchWorker(DISK,10_000_000,False,state).run()
        self.assertEqual(state['state'],'done',state)
        self.assertTrue(state['partial'])
        self.assertIn('did not generate an analysis report',state['note'])


class WindowsImagingDiagnosticTests(unittest.TestCase):
    def test_raw_process_error_from_stdout_is_preserved(self):
        import json
        identity=json.dumps({'serial':'x','unique':'','size':4096},sort_keys=True,separators=(',',':'))
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'rescue.img'
            state={}
            cmd=[sys.executable,'-c',"import sys; print('Cannot read source: permission denied');sys.exit(7)"]
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'),\
                 patch.object(windows_ddrescue,'source_identity', return_value=identity),\
                 patch.object(windows_ddrescue,'image_lock',return_value=nullcontext()),\
                 patch.object(windows_ddrescue,'command', return_value=(cmd,{})):
                ImageWorker(DISK,output,4096,state)._run_windows()
            self.assertEqual(state['state'],'error',state)
            self.assertIn('permission denied',state['error'].lower())
            self.assertIn('code 7',state['error'])

    def test_mapping_error_identifies_stage(self):
        identity=json.dumps({'serial':'x','unique':'','size':4096},sort_keys=True,separators=(',',':'))
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'rescue.img'
            state={}
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'),\
                 patch.object(windows_ddrescue,'source_identity', return_value=identity),\
                 patch.object(windows_ddrescue,'image_lock',return_value=nullcontext()),\
                 patch.object(windows_ddrescue,'command',side_effect=ValueError('unverified /dev/sdd')):
                ImageWorker(DISK,output,4096,state)._run_windows()
            self.assertEqual(state['state'],'error',state)
            self.assertIn('Cygwin disk mapping',state['error'])
            self.assertIn('unverified /dev/sdd',state['error'])

if __name__=='__main__':unittest.main()
