"""Safe Cardputer results exit and small desktop presentation regressions."""
import unittest
from unittest.mock import patch

from src.app import UI
from src.views.desktop_native import DesktopWindow


class CardputerResultsExitTests(unittest.TestCase):
    def test_escape_shows_confirmation_and_escape_again_keeps_list(self):
        ui = UI(demo=True)
        try:
            ui.stage = 'results'
            ui.build_result_filters()
            ui.result_index = 2
            original_count = ui.result_count()
            ui.handle('BACK')
            self.assertEqual(ui.stage, 'results_exit_confirm')
            self.assertEqual(ui.render().size, (320, 170))
            ui.handle('BACK')
            self.assertEqual(ui.stage, 'results')
            self.assertEqual(ui.result_index, 2)
            self.assertEqual(ui.result_count(), original_count)
        finally:
            ui.close()

    def test_confirming_exit_returns_to_devices(self):
        ui = UI(demo=True)
        try:
            ui.stage = 'results'
            ui.handle('BACK')
            ui.handle('OK')
            self.assertEqual(ui.stage, 'devices')
        finally:
            ui.close()

    def test_partition_results_return_to_partition_screen_on_confirm(self):
        ui = UI(demo=True)
        try:
            ui.engine = 'partition'
            ui.stage = 'results'
            ui.handle('BACK')
            self.assertEqual(ui.stage, 'results_exit_confirm')
            ui.handle('OK')
            self.assertEqual(ui.stage, 'partition_results')
        finally:
            ui.close()

    def test_desktop_results_back_has_no_new_dialog(self):
        ui = UI(demo=True, desktop=True)
        try:
            ui.stage = 'results'
            ui.handle('BACK')
            self.assertEqual(ui.stage, 'devices')
        finally:
            ui.close()


class DesktopMethodVisualTests(unittest.TestCase):
    def test_method_icon_border_is_two_pixels(self):
        # Mock canvas rather than requiring a real Tk DISPLAY.
        with patch('src.views.desktop_native.tk.Canvas') as canvas:
            DesktopWindow._method_icon(None, object(), 'metadata')
        first_circle = canvas.return_value.create_oval.call_args_list[0]
        self.assertEqual(first_circle.args, (3, 3, 61, 61))
        self.assertEqual(first_circle.kwargs['width'], 2)

    def test_system_disk_badge_does_not_imply_others_are_writable(self):
        import inspect
        source = inspect.getsource(DesktopWindow._render_devices)
        self.assertIn("text='SYSTEM DISK'", source)
        self.assertNotIn('SYSTEM DISK · READ-ONLY', source)


if __name__ == '__main__':
    unittest.main()
