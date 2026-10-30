"""v0.1.8: welcome heading and read-only badge layout."""
import unittest
from pathlib import Path
from src.app import UI
from src.views.view import BG

class WelcomeLayoutTests(unittest.TestCase):
    def test_both_heading_lines_fit_right_column(self):
        ui = UI(demo=True)
        try:
            for title in ('FIND DELETED FILES', 'SAVE WHAT MATTERS.'):
                self.assertLessEqual(ui.view.f['title'].getlength(title), 213)
        finally:
            ui.close()

    def test_read_only_badge_is_raised_and_vertically_centered(self):
        ui = UI(demo=True)
        try:
            im = ui.render()
            self.assertEqual(im.size, (320, 170))
            badge = (24, 61, 64)
            self.assertEqual(im.getpixel((300, 122)), badge)
            self.assertEqual(im.getpixel((300, 132)), badge)
            self.assertEqual(im.getpixel((300, 136)), (12, 18, 32))
        finally:
            ui.close()

if __name__ == '__main__':
    unittest.main()
