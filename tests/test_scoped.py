"""SectorRec scoped scanning, extension filters and exit overlay regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.models import core
from src.views.cardputer import keyboard
from src.app import UI


class ScopeTests(unittest.TestCase):
    def test_scan_only_catalogs_requested_type(self):
        with tempfile.TemporaryDirectory() as folder:
            cat=core.Catalog(Path(folder)/'db.sqlite')
            data=(
                'r/r * 101: readme.txt\t1\t1\t1\t1\t7\t0\t0\n'
                'r/r * 102: IMG_001.JPG\t1\t1\t1\t1\t13\t0\t0\n'
                'r/r * 103: IM_002.png\t1\t1\t1\t1\t17\t0\t0\n'
                'r/r * 104: doc.PDF\t1\t1\t1\t1\t12\t0\t0\n'
            )
            status={}
            with mock.patch.object(core, 'helper_command', return_value=[
                '/usr/bin/python3','-c',f'import sys;sys.stdout.write({data!r})']):
                worker=core.ScanWorker(cat,'/dev/test',status,scope='PHOTOS')
                worker.start();worker.join(4)
            self.assertEqual(status.get('state'),'done',status)
            self.assertEqual(status['count'],2)
            self.assertEqual(cat.summary('ALL')[0],2)
            self.assertEqual(cat.summary('PHOTOS')[0],2)
            self.assertEqual(cat.available_extensions('PHOTOS'),['.jpg','.png'])
            cat.close()

    def test_scan_is_universal_and_category_extension_filters_follow(self):
        ui=UI(demo=True)
        try:
            ui.show_devices()
            ui.handle('OK')  # Explicit preview before any work.
            self.assertEqual(ui.stage,'scan_ready')
            ui.handle('OK')
            self.assertEqual(ui.stage,'scanning')
            ui.tick()
            self.assertEqual(ui.stage,'results')
            self.assertEqual(ui.result_count(),5)
            self.assertEqual(ui.category_options,
                ['ALL','PHOTOS','VIDEOS','AUDIO','DOCS','ARCHIVES'])
            ui.handle('FILTER')
            self.assertEqual(ui.active_filter(),('PHOTOS','PHOTOS',None))
            self.assertEqual(ui.result_count(),1)
            self.assertEqual(ui.extension_options,['ALL','.jpg'])
            ui.handle('EXTENSION')
            self.assertEqual(ui.active_filter(),('PHOTOS','PHOTOS','.jpg'))
            self.assertEqual(ui.result_count(),1)
            ui.handle('FILTER')  # Changing category resets extension filter.
            self.assertEqual(ui.active_filter(),('VIDEOS','VIDEOS',None))
        finally: ui.close()

    def test_unrecognized_extensions_remain_in_all_and_other(self):
        import shutil
        from src.models.core import Catalog
        with tempfile.TemporaryDirectory() as td:
            cat=Catalog(Path(td)/'test.db')
            cat.insert_batch([('1','photo.JPG',3,'PHOTOS'),
                              ('2','data.unknownxyz',4,'OTHER'),
                              ('3','orphan',7,'OTHER'),
                              ('4','report.pdf',8,'DOCS')])
            self.assertEqual(cat.summary('ALL'),(4,22))
            self.assertEqual(cat.available_extensions('OTHER'),['','.unknownxyz'])
            self.assertEqual(cat.summary('OTHER',''),(1,7))
            cat.close()

    def test_catalog_extension_filter_size_and_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            cat=core.Catalog(Path(folder)/'db.sqlite')
            cat.insert_batch([('10','z.jpg',100,'PHOTOS'),('11','x.png',200,'PHOTOS'),
                              ('12','y.JPG',400,'PHOTOS'),('13','d.pdf',500,'DOCS')])
            self.assertEqual(cat.summary('PHOTOS','.jpg'),(2,500))
            self.assertEqual([r[2] for r in cat.page(0,4,'PHOTOS','.png')],['x.png'])
            self.assertEqual([r[2] for r in cat.all_rows('PHOTOS','.jpg')],['z.jpg','y.JPG'])
            cat.close()

    def test_swap_removed_from_external_candidates(self):
        tree=[{'name':'sda','path':'/dev/sda','type':'disk','tran':'usb',
               'children':[{'name':'sda1','path':'/dev/sda1','type':'part','fstype':'swap','mountpoints':[]},
                           {'name':'sda2','path':'/dev/sda2','type':'part','fstype':'vfat','mountpoints':[]}]}]
        with mock.patch.object(core,'lsblk_tree',return_value=tree):
            self.assertEqual([d['path'] for d in core.detect_devices()],['/dev/sda','/dev/sda2'])

    def test_esc_hold_is_visible_and_not_an_early_scan_cancel(self):
        ui=UI(demo=True)
        try:
            ui.stage='scanning'
            ui.handle('ESC_DOWN')
            self.assertEqual(ui.stage,'scanning')
            ui.exit_progress=0.5
            self.assertEqual(ui.render().size,(320,170))
            ui.handle('EXIT')
            self.assertEqual(ui.stage,'exit')
        finally:ui.close()

    def test_nav_has_no_ten_row_skip(self):
        ui=UI(demo=True)
        try:
            ui.stage='results';ui.build_result_filters()
            ui.handle('RIGHT');self.assertEqual(ui.result_index,1)
            ui.handle('RIGHT');self.assertEqual(ui.result_index,2)
            ui.handle('LEFT');self.assertEqual(ui.result_index,1)
            self.assertLess(keyboard.REPEAT_DELAY,0.2)
            self.assertLess(keyboard.FAST_REPEAT_INTERVAL,0.04)
        finally:ui.close()

if __name__=='__main__': unittest.main()
