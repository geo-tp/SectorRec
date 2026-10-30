"""New tests for the platform-neutral controller and multiple view sizes."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from src.app import UI
from src.infrastructure.image_backend import image_command
from src.infrastructure.platforms import ImageOnlyPlatform, LinuxPlatform
from src.models.core import Catalog, ScanWorker, RecoverWorker


def fake_tsk(output, code=0):
    return [sys.executable, '-c',
            f'import sys;sys.stdout.buffer.write({output!r});sys.exit({code})']


class PortabilityTests(unittest.TestCase):
    def test_platform_adapter_prevents_raw_access_outside_linux(self):
        for platform in ('unsupported', 'win32'):
            host=ImageOnlyPlatform(platform)
            self.assertEqual(host.devices(),[])
            with self.assertRaises(RuntimeError):host.unmount('/dev/sda')

    def test_controller_can_select_portable_filesystem_image(self):
        with tempfile.TemporaryDirectory() as td:
            raw=Path(td)/'sample.img';raw.write_bytes(b'FS')
            ui=UI(demo=False,platform=ImageOnlyPlatform('win32'))
            try:
                vol=ui.add_image(raw)
                self.assertEqual(vol['fs'],'IMAGE')
                self.assertTrue(vol['is_image'])
                self.assertEqual(ui.device['path'],str(raw))
                self.assertEqual(ui.stage,'devices')
                ui.handle('OK')
                self.assertEqual(ui.stage,'scan_ready')
                ui.handle('OK')
                self.assertEqual(ui.stage,'scanning')
            finally:ui.close()

    def test_image_commands_never_use_sudo_or_unsafe_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'image.raw';path.write_bytes(b'FILE SYSTEM')
            with mock.patch('src.infrastructure.image_backend.shutil.which',side_effect=lambda name:'/opt/'+name):
                scan=image_command('scan',str(path))
                self.assertEqual(scan[0],'/opt/fls')
                self.assertEqual(scan[-1],str(path))
                self.assertNotIn('sudo',scan)
                recovery=image_command('recover',str(path),'1982-128-1')
                self.assertEqual(recovery[0],'/opt/icat')
                self.assertEqual(recovery[-1],'1982-128-1')
                with self.assertRaises(ValueError):
                    image_command('recover',str(path),'1; touch /tmp/hacked')
                with self.assertRaises(ValueError):
                    image_command('unmount',str(path))

    def test_workers_accept_explicit_command_builder_without_global_helper(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cat=Catalog(root/'scan.sqlite')
            calls=[]
            def builder(action,source,inode=None):
                calls.append(action)
                output={'scan': b'r/r * 14: restored.jpg\t1\t1\t1\t1\t4\t0\t0\n',
                        'trash_roots':b'',
                        'recover':b'ABCD'}[action]
                return fake_tsk(output)
            try:
                status={}
                scanner=ScanWorker(cat,str(root/'volume.img'),status,command_builder=builder)
                scanner.start();scanner.join(5)
                self.assertEqual(status['state'],'done',status)
                self.assertEqual(cat.summary()[0],1)
                self.assertIn('scan',calls)
                row=cat.at(0)
                with mock.patch('src.models.core.MIN_FREE',0):
                    recstatus={}
                    rec=RecoverWorker(cat,'ALL',row,str(root/'volume.img'),'sample',root,recstatus,
                                      command_builder=builder)
                    rec.start();rec.join(5)
                self.assertEqual(recstatus['recovered'],1,recstatus)
                self.assertEqual(recstatus['failed'],0)
                self.assertEqual(list(Path(recstatus['outdir']).rglob('*.jpg'))[0].read_bytes(),b'ABCD')
            finally:cat.close()

    def test_desktop_tall_view_and_lcd_layout(self):
        ui=UI(demo=True)
        try:
            self.assertEqual(ui.render().size,(320,170))
            ui.stage='results';ui.build_result_filters()
            image=ui.render(height=380)
            self.assertEqual(image.size,(320,380))
            self.assertEqual(image.getpixel((2,2)),(78,225,193))
            # Explicit extra row at y=167 is possible on the PC viewport.
            self.assertNotEqual(image.getpixel((10,167)),(16,27,45))
            self.assertEqual(image.getpixel((1,380-2)),(16,27,45))
        finally:ui.close()

if __name__=='__main__':unittest.main()
