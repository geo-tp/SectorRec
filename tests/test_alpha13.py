"""Alpha13: streaming progress, safe image planning, and LCD idle dimming."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from src.services.partition import parse_testdisk_log, PartitionSearchWorker
from src.views.cardputer.idle_dim import IdleDimmer


class LivePartitionTests(unittest.TestCase):
    REPORT='''Disk /dev/sdc - 4000 MB
CHS 486 255 63 - sector size=512
Current partition structure:
 1 P FAT32                 2048  7812500  7810453
Results
 1 P FAT32                 2048  7812500  7810453
 2 D Linux                 9000  15000    6001
'''
    def test_partial_report_parses_existing_and_new(self):
        values=parse_testdisk_log(self.REPORT)
        self.assertEqual(len(values),2)
        self.assertEqual([v.origin for v in values],['existing','found'])

    def test_worker_reports_launch_error_without_probing_source(self):
        state={}
        with mock.patch('src.services.partition.existing_partitions',return_value=[]) as existing, \
             mock.patch('src.services.partition.discover_command',side_effect=RuntimeError('blocked')):
            worker=PartitionSearchWorker('/dev/sdc',0,False,state)
            worker.run()
            existing.assert_not_called()
            self.assertEqual(state['candidates'],[])
            self.assertEqual(state['error'],'blocked')
            self.assertEqual(state['state'],'error')


class IdleDimTests(unittest.TestCase):
    def test_binary_backlight_uses_software_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            back=Path(tmp)/'binary';back.mkdir()
            (back/'brightness').write_text('1')
            (back/'max_brightness').write_text('1')
            dim=IdleDimmer(backlight_dir=Path(tmp))
            self.assertTrue(dim.update(now=dim.last_input+61))
            self.assertTrue(dim.software_dim)
            self.assertTrue(dim.input(now=dim.last_input+62))
            self.assertFalse(dim.software_dim)

    def test_pwm_backlight_restored_on_input_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            back=Path(tmp)/'lcd';back.mkdir()
            (back/'brightness').write_text('130')
            (back/'max_brightness').write_text('255')
            dim=IdleDimmer(backlight_dir=Path(tmp))
            self.assertTrue(dim.update(now=dim.last_input+61))
            self.assertEqual((back/'brightness').read_text(),'13')
            dim.close()
            self.assertEqual((back/'brightness').read_text(),'130')


class ImagePlanTests(unittest.TestCase):
    def test_output_is_new_and_separately_named(self):
        from src.services.image_rescue import plan_image
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('src.services.image_rescue.ensure_different_disk'):
            folder=Path(tmp)
            first=plan_image('/dev/sdb',folder,1024)
            first.write_bytes(b'')
            second=plan_image('/dev/sdb',folder,1024)
            self.assertNotEqual(first,second)
            self.assertEqual(first.suffix,'.img')

    def test_ddrescue_progress_parser(self):
        from src.services.image_rescue import progress_from_output
        self.assertEqual(progress_from_output('rescued: 1 GB, pct rescued: 25.00%'),(25.0,None))
        self.assertEqual(progress_from_output('waiting for data'),(None,None))

    def test_refuses_same_disk_and_low_free_space(self):
        from src.services.image_rescue import plan_image
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch('src.services.image_rescue.ensure_different_disk',side_effect=ValueError('same physical disk')):
                with self.assertRaisesRegex(ValueError,'same physical disk'):
                    plan_image('/dev/sdb',tmp,1024)
            with mock.patch('src.services.image_rescue.ensure_different_disk'):
                with self.assertRaisesRegex(ValueError,'Insufficient free space'):
                    plan_image('/dev/sdb',tmp,10**30)

if __name__=='__main__':unittest.main()
