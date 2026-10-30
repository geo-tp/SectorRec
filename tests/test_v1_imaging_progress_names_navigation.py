"""Regression tests for imaging UI, collision-safe naming and source navigation."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_INITIAL_DISPLAY = os.environ.get('DISPLAY')

from src.services.image_rescue import plan_image


class ImageNamingTests(unittest.TestCase):
    def test_timestamp_filename_and_stable_preferred_name(self):
        with tempfile.TemporaryDirectory() as folder, patch('src.services.image_rescue.ensure_different_disk'):
            first = plan_image('/dev/sdb', folder, 1024)
            self.assertRegex(first.name, r'^SectorRec_sdb_\d{6}-\d{6}\.img$')
            # A stable name must survive a later preflight, independent of the wall clock.
            self.assertEqual(plan_image('/dev/sdb', folder, 1024, preferred=first), first)
            # Even when only the map or metadata exists, it cannot be reused.
            Path(str(first) + '.map').write_text('map', encoding='ascii')
            second = plan_image('/dev/sdb', folder, 1024)
            self.assertNotEqual(first, second)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                plan_image('/dev/sdb', folder, 1024, preferred=first)

    def test_metadata_alone_blocks_overwrite_and_cross_folder_reuse(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as different, \
             patch('src.services.image_rescue.ensure_different_disk'):
            first = plan_image('/dev/sdc', folder, 1024)
            Path(str(first) + '.source.json').write_text('{}')
            self.assertNotEqual(plan_image('/dev/sdc', folder, 1024), first)
            with self.assertRaisesRegex(ValueError, 'changed'):
                plan_image('/dev/sdc', different, 1024, preferred=first)


@unittest.skipUnless(_INITIAL_DISPLAY or os.name == 'nt', 'Graphical display required')
class DesktopImagingNavigationTests(unittest.TestCase):
    def setUp(self):
        from src.app import UI
        from src.views.desktop_native import DesktopWindow
        self.ui=UI(demo=True,desktop=True)
        # Other legacy tests clear DISPLAY globally; restore this test's
        # initial Xvfb display only while constructing its own Tk window.
        with patch.dict(os.environ, {'DISPLAY': _INITIAL_DISPLAY} if _INITIAL_DISPLAY else {}):
            self.window=DesktopWindow(self.ui)
            self.window.root.update()

    def tearDown(self):
        self.window.confirm_action=lambda *a,**k: True
        self.window.close()
        try:self.window.root.destroy()
        except Exception:pass

    def test_progress_resets_marquee_value_and_tracks_actual_percent(self):
        from tkinter import ttk
        p=ttk.Progressbar(self.window.body,mode='indeterminate',maximum=100)
        p.configure(value=98.0)
        self.window._set_imaging_progress(p, 3.5)
        self.assertEqual(str(p.cget('mode')), 'determinate')
        self.assertAlmostEqual(float(p['value']), 3.5)
        self.window._set_imaging_progress(p, 23.75)
        self.assertAlmostEqual(float(p['value']), 23.75)
        self.window._set_imaging_progress(p, 1000)
        self.assertEqual(float(p['value']), 100.0)

    def test_previous_image_setup_cannot_skip_next_device_methods(self):
        self.ui.stage='desktop_image_setup'
        self.window._image_source={'path':'/dev/sdb','size':1}
        self.window.show(force=True)
        # Exactly the state that the old "Choose another source" left behind.
        self.window.return_to_source_selection()
        self.assertEqual(self.ui.stage, 'devices')
        self.window.select_source(1,advance=True)
        self.assertEqual(self.ui.stage, 'desktop_methods')
        self.window.select_method('image')
        self.assertEqual(self.ui.stage, 'desktop_methods')
        self.window.ui.show_devices()
        self.window.show(force=True)
        # A stale method callback must not re-enter Create disk image.
        self.window.continue_method()
        self.assertEqual(self.ui.stage,'devices')
        self.assertIsNone(self.window._method_selection)
        self.assertIsNone(self.window._image_source)

if __name__=='__main__':unittest.main()
