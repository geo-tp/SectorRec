import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.models import core
from src.app import UI


class ParseTests(unittest.TestCase):
    def test_common_fls_long_format(self):
        line = 'r/r * 1304-128-1: DCIM/holiday.jpg\t1700000000\t1700000000\t1700000000\t1700000000\t2048\t0\t0\n'
        self.assertEqual(core.parse_fls(line), ('1304-128-1','DCIM/holiday.jpg',2048,'PHOTOS'))

    def test_short_and_reallocated(self):
        self.assertEqual(core.parse_fls('r/r * 19: lost.pdf'), ('19','lost.pdf',None,'DOCS'))
        self.assertIsNone(core.parse_fls('r/r * 23(realloc): secret.txt'))
        self.assertIsNone(core.parse_fls('d/d * 23: dir'))
        self.assertIsNone(core.parse_fls('bad output'))

    def test_safe_name_and_command(self):
        self.assertEqual(core.safe_filename('../lost.jpg'),'lost.jpg')
        self.assertEqual(core.safe_filename('../../../../../etc/passwd'),'passwd')
        self.assertEqual(core.safe_filename('C:\\docs\\me?.txt'),'me_.txt')
        with self.assertRaises(ValueError):core.helper_command('scan','/etc/passwd')
        with self.assertRaises(ValueError):core.helper_command('recover','/dev/sda1','10;id')
        self.assertTrue(str(core.target_root(Path('/home/pi'),'USB Stick',datetime.datetime(2026,10,27,1,2))).endswith('USB-Stick_20261027'))

    def test_devices_places_system_last_and_reports_mount(self):
        mock_trees=[
         {'path':'/dev/mmcblk0','name':'mmcblk0','type':'disk','children':[{'path':'/dev/mmcblk0p2','type':'part','fstype':'ext4','mountpoints':['/']}]},
         {'path':'/dev/sda','name':'sda','type':'disk','tran':'usb','rm':False,'children':[
             {'path':'/dev/sda1','name':'sda1','type':'part','fstype':'vfat','size':10000,'label':'TEST','mountpoints':[]},
             {'path':'/dev/sda2','name':'sda2','type':'part','fstype':'ext4','size':2000,'label':'MORE','mountpoints':['/media/pi/MORE']}]}]
        with mock.patch.object(core,'lsblk_tree',return_value=mock_trees):
            ds=core.detect_devices()
        self.assertEqual(len(ds),5)
        self.assertEqual(ds[0]['path'],'/dev/sda')
        self.assertTrue(ds[0]['partition_disk'])
        self.assertFalse(ds[1]['mounted'])
        self.assertTrue(ds[2]['mounted'])
        self.assertTrue(all(d['is_system'] for d in ds[3:]))
        self.assertEqual(ds[-1]['path'], '/dev/mmcblk0p2')


class CatalogTests(unittest.TestCase):
    def test_results_filtered_and_paged(self):
        with tempfile.TemporaryDirectory() as folder:
            c=core.Catalog(Path(folder)/'scan.sqlite')
            c.insert_batch([('1','a.jpg',120,'PHOTOS'),('2','b.mp4',2048,'VIDEOS'),('3','c.jpg',150,'PHOTOS')])
            self.assertEqual(c.summary('PHOTOS'),(2,270))
            self.assertEqual(c.at(1,'PHOTOS')[2],'c.jpg')
            self.assertEqual([x[2] for x in c.all_rows('ALL')],['a.jpg','b.mp4','c.jpg'])
            c.clear()
            self.assertEqual(c.summary('ALL'),(0,0))
            c.close()

    def test_synthetic_scan_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            db=core.Catalog(Path(folder)/'scan.sqlite')
            scan_data=(
                'r/r * 101: deleted_a.jpg\t1\t1\t1\t1\t12\t0\t0\n'
                'r/r * 102: deleted_b.pdf\t1\t1\t1\t1\t12\t0\t0\n'
            )
            script=f'import sys;sys.stdout.write({scan_data!r})'
            status={}
            with mock.patch.object(core,'helper_command',return_value=['/usr/bin/python3','-c',script]):
                worker=core.ScanWorker(db,'/dev/test1',status)
                worker.start();worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(status.get('state'),'done', status)
            self.assertEqual(db.summary('ALL')[0],2)
            saved={}
            with mock.patch.object(core,'helper_command',return_value=['/usr/bin/python3','-c',"import sys;sys.stdout.buffer.write(b'Hello World!')"]):
                worker=core.RecoverWorker(db,'ALL',db.at(0),'/dev/test1','testdisk',Path(folder),saved)
                worker.start();worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(saved.get('state'),'done',saved)
            output=list(Path(saved['outdir']).iterdir())
            self.assertEqual(len(output),1)
            self.assertEqual(output[0].read_bytes(), b'Hello World!')
            db.close()


class UIFlowTests(unittest.TestCase):
    def test_demo_flow(self):
        ui=UI(demo=True)
        self.assertEqual(ui.stage,'welcome')
        ui.handle('OK')
        self.assertEqual(ui.stage,'devices')
        ui.handle('OK')
        self.assertEqual(ui.stage,'scan_ready')
        ui.handle('OK')
        self.assertEqual(ui.stage,'scanning')
        ui.tick()
        self.assertEqual(ui.stage,'results')
        ui.handle('ALL')
        self.assertEqual(ui.stage,'confirm')
        ui.handle('OK');ui.tick()
        self.assertEqual(ui.stage,'complete')
        ui.handle('OK')
        self.assertEqual(ui.stage,'results')
        ui.close()

    def test_esc_cancel_once(self):
        ui=UI(demo=True)
        ui.show_devices()
        ui.stage='scanning'
        ui.handle('ESC_DOWN')
        self.assertEqual(ui.stage,'scanning')  # hold displays exit overlay, does not cancel
        ui.handle('BACK')
        self.assertEqual(ui.stage,'scanning')  # Cancellation finalizes before opening partial results
        self.assertTrue(ui.scan_stop_requested)
        ui.status={'state':'cancelled','count':0}
        ui.tick()
        self.assertEqual(ui.stage,'results')
        self.assertTrue(ui.scan_partial)
        ui.close()

if __name__=='__main__':unittest.main()
