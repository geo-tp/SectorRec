"""Desktop brand must not turn into a magnifier on Linux WM icon errors."""
import os
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import UI
from src.views.desktop_native import DesktopWindow


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Requires Tk/X display')
class DesktopBrandTests(unittest.TestCase):
    def setUp(self):
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()

    def _launch(self):
        self.window = DesktopWindow(UI(demo=True, desktop=True))
        self.window.root.update_idletasks()
        return self.window

    @staticmethod
    def _brand_container(window):
        # Both full-size and compact headers use a single grid row:
        # brand (column 0), steps (column 1), GitHub (column 2).
        header = window.top.winfo_children()[0]
        brand = header.grid_slaves(row=0, column=0)
        assert len(brand) == 1, 'SectorRec branding must remain in the header left column'
        return brand[0]

    def test_linux_wm_rejecting_icon_does_not_hide_hdd_logo(self):
        with patch.object(tk.Tk, 'iconphoto', side_effect=tk.TclError('WM denied icon')):
            w = self._launch()
        self.assertIsNotNone(w._brand_image)
        self.assertEqual((w._brand_image.width(), w._brand_image.height()), (64, 64))
        labels = [x for x in self._brand_container(w).winfo_children() if isinstance(x, tk.Label)]
        self.assertTrue(any(label.cget('image') for label in labels), 'HDD/recover PNG must remain visible')

    def test_missing_brand_png_uses_hdd_recovery_symbol(self):
        with patch('src.views.desktop_native.files', return_value=Path('/missing-sectorrec-assets')):
            w = self._launch()
        self.assertIsNone(w._brand_image)
        canvases = [x for x in self._brand_container(w).winfo_children() if isinstance(x, tk.Canvas)]
        self.assertEqual(len(canvases), 1)
        shapes = {canvases[0].type(item) for item in canvases[0].find_all()}
        self.assertTrue({'rectangle', 'oval', 'arc', 'polygon'} <= shapes)


if __name__ == '__main__':
    unittest.main()
