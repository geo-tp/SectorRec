"""SectorRec desktop workflow and PhotoRec family safety regressions."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.controllers.controller import Controller
from src.models.core import target_root
from src.services.signature import FORMAT_PRESETS, photorec_command


class DesktopWorkflowTests(unittest.TestCase):
    def test_source_before_method(self):
        ui = Controller(demo=True, desktop=True)
        try:
            ui.show_devices()
            ui.selection=0
            self.assertEqual(ui.stage, 'devices')
            ui.handle('OK')
            self.assertEqual(ui.stage, 'desktop_methods')
            ui.desktop_select_method('metadata')
            self.assertEqual(ui.stage, 'scan_ready')
            ui.handle('BACK')
            self.assertEqual(ui.stage, 'devices')
        finally:ui.close()

    def test_signature_method_after_selection(self):
        ui = Controller(demo=True, desktop=True)
        try:
            ui.show_devices()
            ui.selection=0
            ui.handle('OK')
            ui.desktop_select_method('signature')
            self.assertEqual(ui.stage, 'signature_setup')
        finally:ui.close()

    def test_unsupported_method_rejected(self):
        ui = Controller(demo=True, desktop=True)
        try:
            with self.assertRaises(ValueError):ui.desktop_select_method('magic')
        finally:ui.close()

    def test_cardputer_cannot_select_photo(self):
        ui = Controller(demo=True, desktop=False)
        try:
            with self.assertRaises(ValueError):ui.desktop_select_method('signature')
        finally:ui.close()

    def test_default_photorec_keeps_all_families(self):
        cmd = photorec_command('photorec','/tmp/volume.img','/tmp/out')
        self.assertEqual(cmd[-1],'partition_none,fileopt,everything,enable,wholespace,search')

    def test_photo_family_uses_photorec_ids(self):
        cmd = photorec_command('photorec','/tmp/volume.img','/tmp/out','photos')
        self.assertIn('everything,disable,jpg,enable,png,enable',cmd[-1])
        self.assertTrue(cmd[-1].endswith(',search'))

    def test_invalid_file_family_cannot_inject_command(self):
        with self.assertRaises(ValueError):
            photorec_command('photorec','/tmp/volume.img','/tmp/out','all,format,search')

    def test_office_shared_zip_signature(self):
        self.assertIn('zip',FORMAT_PRESETS['documents'])

    def test_desktop_save_path_does_not_change_cardputer_path(self):
        home=Path('/tmp/user')
        self.assertIn('Backups/SectorRec/',str(target_root(home,'USB')))
        self.assertIn('Backups/SectorRec/',str(target_root(home,'USB',product='SectorRec')))

    def test_native_frontend_does_not_import_bitmap_scaler(self):
        import inspect
        from src.views import desktop_native
        contents=inspect.getsource(desktop_native)
        self.assertNotIn('from PIL import',contents)
        self.assertNotIn('Image.Resampling.NEAREST',contents)


if __name__=='__main__':unittest.main()
