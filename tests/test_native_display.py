"""A native host build checks the same C source cross-compiled for Cardputer Zero."""
import mmap
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from src.views.cardputer.video import Framebuffer, load_native_blitter
from src.app import UI


class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('C compiler not installed on the test host')
        cls.temp = tempfile.TemporaryDirectory()
        source = Path(__file__).resolve().parent.parent / 'src' / 'views' / 'cardputer' / 'pixel565.c'
        cls.library_path = Path(cls.temp.name) / 'libsectorrec565.so'
        subprocess.run([cc, '-O2', '-fPIC', '-shared', '-o', str(cls.library_path), str(source)], check=True)
        cls.native_lib, cls.native_blit = load_native_blitter([cls.library_path])
        assert cls.native_blit is not None

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def dummy(width, height, y_offset, x_offset, extra_pitch, layout, native):
        class FB:
            pass
        fb = FB()
        fb.width, fb.height = width, height
        fb.yoffset, fb.xoffset = y_offset, x_offset
        fb.line = width*2 + x_offset*2 + extra_pitch
        fb.bpp = 16
        fb.r, fb.g, fb.b = layout
        fb.map = mmap.mmap(-1, fb.line*(height+y_offset+1))
        fb.native_blit = native
        fb.native_frames = fb.fallback_frames = 0
        return fb

    def test_native_565_byte_for_byte_matches_original_with_stride_and_offset(self):
        for layout in (((11,5),(5,6),(0,5)), ((10,5),(5,5),(0,5))):
            with self.subTest(layout=layout):
                img=Image.new('RGB',(320,170))
                img.putdata([((x*17)%256,(y*11)%256,(x*7+y*13)%256)
                             for y in range(170) for x in range(320)])
                fast=self.dummy(320,170,1,2,12,layout,self.native_blit)
                old=self.dummy(320,170,1,2,12,layout,None)
                try:
                    Framebuffer.render(fast,img)
                    Framebuffer.render(old,img)
                    self.assertEqual(fast.map[:],old.map[:])
                    self.assertEqual(fast.native_frames,1)
                    self.assertEqual(old.fallback_frames,1)
                finally:
                    fast.map.close();old.map.close()

    def test_native_565_bounds_check(self):
        img=Image.new('RGB',(320,170))
        fb=self.dummy(320,170,0,0,0,((11,5),(5,6),(0,5)),self.native_blit)
        fb.line=400  # Cannot accommodate width*2 bytes; must reject before native call.
        try:
            with self.assertRaises(RuntimeError):
                Framebuffer.render(fb,img)
        finally: fb.map.close()

    def test_badge_moves_as_one_block(self):
        ui=UI(demo=True)
        try:
            image=ui.render()
            self.assertEqual(image.getpixel((300,122)),(24,61,64))
            self.assertEqual(image.getpixel((300,136)),(12,18,32))
        finally:ui.close()


if __name__ == '__main__':
    unittest.main()
