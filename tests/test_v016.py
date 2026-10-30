"""v0.1.6: async device discovery, partial catalog, ESC and frame latency."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from PIL import Image
from src.models import core
from src.app import UI
from src.views.cardputer.framebuffer_frontend import FrameWriter

class V016Tests(unittest.TestCase):
    def test_welcome_esc_release_never_exits(self):
        ui=UI(demo=True)
        try:
            ui.handle('BACK')
            self.assertEqual(ui.stage,'welcome')
            ui.exit_progress=0.14
            ui.render()
            ui.handle('EXIT')
            self.assertEqual(ui.stage,'exit')
        finally: ui.close()

    def test_stopped_demo_scan_opens_partial_results(self):
        ui=UI(demo=True)
        try:
            ui.handle('OK'); ui.handle('OK'); ui.handle('OK')
            self.assertEqual(ui.stage,'scanning')
            ui.handle('BACK')
            self.assertTrue(ui.scan_stop_requested)
            self.assertEqual(ui.stage,'scanning')
            ui.tick()
            self.assertEqual(ui.stage,'results')
            self.assertTrue(ui.scan_partial)
            self.assertEqual(ui.result_count(),5)
        finally:ui.close()

    def test_cancel_flushes_incomplete_scan_batch(self):
        with tempfile.TemporaryDirectory() as path:
            catalog=core.Catalog(Path(path)/'catalog.db')
            data=''.join(f'r/r * {i}: photo_{i}.jpg\t1\t1\t1\t1\t100\t0\t0\n' for i in range(1,20))
            source='import sys,time;sys.stdout.write('+repr(data)+');sys.stdout.flush();time.sleep(2)'
            status={}
            try:
                with mock.patch.object(core,'helper_command',return_value=['/usr/bin/python3','-c',source]):
                    worker=core.ScanWorker(catalog,'/dev/test',status,'PHOTOS')
                    worker.start()
                    deadline=time.monotonic()+2
                    while status.get('count',0)<19 and time.monotonic()<deadline:
                        time.sleep(.005)
                    worker.cancel();worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(status['state'],'cancelled')
                    self.assertEqual(catalog.summary('PHOTOS')[0],19)
            finally:catalog.close()

    def test_device_refresh_keeps_selection_by_path(self):
        ui=UI(demo=False)
        try:
            ui.stage='devices'
            ui.devices=[{'path':'/dev/sda1'}, {'path':'/dev/sdb1'}]
            ui.selection=1
            ui.device_refresh_ready=('ok',[{'path':'/dev/sdb1'}, {'path':'/dev/sdc1'}])
            ui.apply_device_refresh()
            self.assertEqual(ui.selection,0)
            self.assertEqual(ui.device['path'],'/dev/sdb1')
        finally:ui.close()

    def test_frame_writer_does_not_block_keyboard_thread(self):
        class Slow:
            def __init__(self):self.images=[]
            def render(self,im):
                time.sleep(.1)
                self.images.append(im)
            def close(self):pass
        fb=Slow()
        writer=FrameWriter(fb)
        try:
            start=time.perf_counter()
            for i in range(10):writer.put(Image.new('RGB',(320,170),(i,i,i)))
            self.assertLess(time.perf_counter()-start,.04)
        finally:writer.close()

if __name__=='__main__':unittest.main()
