"""Signature Recovery isolation / filesystem safety / automated extraction tests.

Tests use an executable fake PhotoRec instead of pretending that mock file
signatures prove actual recoverability on damaged physical media.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from src.services import signature
from src.controllers.controller import Controller


class SignatureTests(unittest.TestCase):
    def test_command_is_supported_noninteractive_photorec(self):
        self.assertEqual(signature.photorec_command('photorec', '/tmp/test.img','/out/files'),
            ['photorec','/d','/out/files','/cmd','/tmp/test.img',
             'partition_none,fileopt,everything,enable,wholespace,search'])

    def test_cardputer_cannot_offer_photorec(self):
        ctl = Controller(demo=True,desktop=False)
        with self.assertRaises(ValueError):
            ctl.desktop_select_method('signature')
        self.assertEqual(ctl.engine,'metadata')
        ctl.close()

    def test_desktop_method_choice_keeps_tsk_untouched(self):
        ctl=Controller(demo=True,desktop=True)
        ctl.show_devices()
        self.assertEqual(ctl.stage,'devices')
        ctl.selection=0
        ctl.handle('OK')
        self.assertEqual(ctl.stage,'desktop_methods')
        ctl.desktop_select_method('signature')
        self.assertEqual(ctl.stage,'signature_setup')
        ctl.handle('BACK')
        ctl.desktop_select_method('metadata')
        self.assertEqual(ctl.stage,'scan_ready')
        ctl.close()

    def test_destination_rejects_too_little_space(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'image.dd';source.write_bytes(b'x')
            with patch.object(signature,'free_space',return_value=signature.RESERVE_BYTES):
                with self.assertRaisesRegex(ValueError,'Not enough free space'):
                    signature.validate_destination(str(source),temp)

    def test_output_count_ignores_other_folders_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp);prefix=dest/'SectorRec-01'
            folder=dest/'SectorRec-01.1';folder.mkdir()
            (folder/'photo.jpg').write_bytes(b'hello')
            (folder/'shortcut.jpg').symlink_to(folder/'photo.jpg')
            (dest/'unrelated').mkdir()
            (dest/'unrelated'/'other').write_bytes(b'xxx')
            self.assertEqual(signature.extraction_totals(dest,prefix),(1,5))

    def test_simple_noninteractive_extraction_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/'source.img';source.write_bytes(b'header')
            fake=root/'fake_photo.py'
            fake.write_text('#!'+sys.executable+'\n'
                'import sys,pathlib\n'
                'p=pathlib.Path(sys.argv[sys.argv.index("/d")+1]+".1")\n'
                'p.mkdir()\n'
                '(p/"file.jpg").write_bytes(b"DATA123")\n')
            fake.chmod(0o755)
            st={}
            job=signature.PhotoRecWorker(str(source),str(root),st,binary=str(fake),interval=.01)
            job.start();job.join(timeout=8)
            self.assertFalse(job.is_alive())
            self.assertEqual(st['state'],'done',st)
            self.assertEqual(st['files'],1)
            self.assertEqual(st['written'],7)
            self.assertTrue(Path(st['output']+'.1/file.jpg').is_file())

    def test_manual_cancel_keeps_partial_extractions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'disk.img';source.write_bytes(b'x')
            fake=root/'fake_photo.py'
            fake.write_text('#!'+sys.executable+'\n'
                'import sys,pathlib,time\n'
                'p=pathlib.Path(sys.argv[sys.argv.index("/d")+1]+".1")\n'
                'p.mkdir()\n'
                '(p/"file.png").write_bytes(b"PARTIAL")\n'
                'time.sleep(60)\n')
            fake.chmod(0o755)
            st={};job=signature.PhotoRecWorker(str(source),str(root),st,
                                                binary=str(fake),interval=.02)
            job.start()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if st.get('output') and Path(st['output']+'.1/file.png').exists():break
                time.sleep(.02)
            job.cancel();job.join(timeout=8)
            self.assertFalse(job.is_alive())
            self.assertEqual(st['state'],'cancelled',st)
            self.assertEqual(st['files'],1)
            self.assertTrue(Path(st['output']+'.1/file.png').exists())

    def test_worker_low_space_stop_and_partial_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'disk.img';source.write_bytes(b'x')
            fake=root/'fake_photo.py'
            fake.write_text('#!'+sys.executable+'\n'
                'import sys,pathlib,time\n'
                'p=pathlib.Path(sys.argv[sys.argv.index("/d")+1]+".1")\n'
                'p.mkdir()\n'
                '(p/"file.dat").write_bytes(b"PARTIAL")\n'
                'time.sleep(60)\n')
            fake.chmod(0o755)
            st={};calls=[signature.RESERVE_BYTES*8]
            def remaining(path):
                calls.append(calls[-1]-signature.RESERVE_BYTES)
                return calls[-1]
            with patch.object(signature,'free_space',side_effect=remaining):
                job=signature.PhotoRecWorker(str(source),str(root),st,binary=str(fake),interval=.03)
                job.start();job.join(timeout=8)
            self.assertFalse(job.is_alive())
            self.assertEqual(st['state'],'cancelled',st)
            self.assertIn('space',st['stopped_reason'])

if __name__=='__main__':unittest.main()
