"""Regressions for scan-all / post-scan category+extension filtering."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.models import core
from src.app import UI
from src.infrastructure.platforms import ImageOnlyPlatform, current_platform
from src.infrastructure.image_backend import find_tsk_binary


class UniversalFilterTests(unittest.TestCase):
    def test_real_worker_keeps_every_extension_and_finds_recoverable_other(self):
        output=''.join((
            'r/r * 101: photo.jpg\t0\t0\t0\t0\t10\t0\t0\n',
            '-/r * 102: recording.mp4\t0\t0\t0\t0\t20\t0\t0\n',
            'r/r * 103: unclassified.customext\t0\t0\t0\t0\t30\t0\t0\n',
            'r/r * 104: noextension\t0\t0\t0\t0\t40\t0\t0\n',
        ))
        with tempfile.TemporaryDirectory() as td:
            cat=core.Catalog(Path(td)/'db.sqlite')
            calls=[]
            def fake_builder(action,path,inode=None):
                calls.append(action)
                return [sys.executable,'-c',f'import sys;sys.stdout.write({output!r})'] if action=='scan' else [sys.executable,'-c','pass']
            try:
                status={}
                worker=core.ScanWorker(cat,'/test.img',status,scope='ALL',command_builder=fake_builder)
                worker.start();worker.join(5)
                self.assertEqual(status.get('state'),'done',status)
                self.assertEqual(cat.summary('ALL'),(4,100))
                self.assertEqual(cat.summary('OTHER'),(2,70))
                self.assertEqual(cat.summary('VIDEOS','.mp4'),(1,20))
                self.assertEqual(cat.summary('OTHER',''),(1,40))
                self.assertIn('scan',calls)
                ui=UI(demo=True)
                try:
                    ui.demo_results=[(1,'101','photo.jpg',10,'PHOTOS'),
                                     (2,'102','recording.mp4',20,'VIDEOS'),
                                     (3,'103','unclassified.customext',30,'OTHER'),
                                     (4,'104','noextension',40,'OTHER')]
                    ui.stage='results';ui.build_result_filters()
                    for _ in range(ui.category_options.index('OTHER')):ui.handle('FILTER')
                    self.assertEqual(ui.result_count(),2)
                    self.assertEqual(set(ui.extension_options), {'ALL', '.customext', ''})
                    for _ in range(len(ui.extension_options)):
                        if ui.extension_label() == 'NO EXT':break
                        ui.handle('EXTENSION')
                    self.assertEqual(ui.extension_label(),'NO EXT')
                    self.assertEqual(ui.result_count(),1)
                    self.assertEqual(ui.found_total(),(1,40))
                finally:ui.close()
            finally:cat.close()

    def test_image_only_mode_never_lists_raw_linux_drives(self):
        with mock.patch.dict('os.environ',{'SECTORREC_IMAGE_ONLY':'1'}):
            plat=current_platform()
        self.assertFalse(plat.raw_drives)
        self.assertEqual(plat.devices(),[])

    def test_source_build_uses_path_for_tsk_cli(self):
        with mock.patch('src.infrastructure.image_backend.shutil.which',return_value='/opt/tsk/fls'):
            self.assertEqual(find_tsk_binary('fls'),'/opt/tsk/fls')

    def test_scanning_screen_no_preliminary_category_selection(self):
        ui=UI(demo=True)
        try:
            ui.handle('OK');ui.handle('OK')
            self.assertEqual(ui.stage,'scan_ready')
            ui.handle('OK')
            self.assertEqual(ui.stage,'scanning')
            ui.tick();self.assertEqual(ui.stage,'results')
            self.assertEqual(ui.render().size,(320,170))
        finally:ui.close()
