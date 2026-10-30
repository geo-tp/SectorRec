"""GUI interaction contracts, independent of a physical LCD or block devices."""
import unittest
from src.app import UI

class DesktopActionsTests(unittest.TestCase):
    def test_scan_requires_explicit_confirmation_with_right_source(self):
        ui=UI(demo=True)
        try:
            ui.handle('OK')
            ui.selection=1
            ui.handle('OK')
            self.assertEqual(ui.stage,'scan_ready')
            self.assertIsNone(ui.scan)
            self.assertIn('External HDD',ui.device['label'])
            self.assertEqual(ui.render().size,(320,170))
            ui.handle('BACK')
            self.assertEqual(ui.stage,'devices')
            ui.handle('OK');ui.handle('OK')
            self.assertEqual(ui.stage,'scanning')
        finally:ui.close()

    def test_filter_dropdown_matches_controller_semantics(self):
        ui=UI(demo=True)
        try:
            ui.stage='results';ui.build_result_filters()
            ui.category_filter_index=1
            ui.rebuild_extensions()
            self.assertEqual(ui.category_options[1],'PHOTOS')
            self.assertEqual(ui.extension_options,['ALL','.jpg'])
            ui.extension_filter_index=1
            self.assertEqual(ui.result_count(),1)
        finally:ui.close()

if __name__=='__main__':unittest.main()
