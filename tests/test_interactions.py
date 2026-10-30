"""Regression checks for Cardputer Zero navigation and filtered recovery."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.models import core
from src.views.cardputer import keyboard
from src.app import UI


class KeyRepeatTests(unittest.TestCase):
    def test_cardputer_physical_keys_are_fn_free(self):
        self.assertIn(33, keyboard.UP)  # F
        self.assertIn(45, keyboard.DOWN)  # X
        self.assertIn(44, keyboard.LEFT)  # Z
        self.assertIn(46, keyboard.RIGHT)  # C
        self.assertEqual(keyboard.KEY_TAB, 15)

    def test_hold_repeats_and_accelerates_without_key_repeat_event(self):
        r = keyboard.RepeatTracker()
        self.assertEqual(r.feed(7, 45, 1, 10), ['DOWN'])
        self.assertEqual(r.feed(7, 45, 2, 10.10), [])  # Ignore kernel autorepeat
        self.assertEqual(r.poll(10.13), [])
        self.assertEqual(r.poll(10.17), ['DOWN'])
        self.assertEqual(r.poll(10.20), [])
        self.assertEqual(r.poll(10.23), ['DOWN'])
        self.assertEqual(r.poll(11.75), ['DOWN'])
        self.assertAlmostEqual(r.held[(7, 45)][2], 11.75 + keyboard.FAST_REPEAT_INTERVAL)
        self.assertEqual(r.feed(7, 45, 0, 11.78), [])
        self.assertEqual(r.poll(12), [])

    def test_only_directions_repeat(self):
        r = keyboard.RepeatTracker()
        for key in [keyboard.KEY_TAB, keyboard.KEY_A, 28, 1]:
            self.assertEqual(r.feed(7, key, 1, 10), [])
        self.assertEqual(r.poll(200), [])


class ResultFilterTests(unittest.TestCase):
    def test_tab_cycles_filters_and_resets_selection(self):
        ui=UI(demo=True)
        try:
            ui.stage='results'
            ui.build_result_filters()
            ui.result_index=4
            ui.handle('FILTER')
            self.assertEqual(ui.active_filter()[0],'PHOTOS')
            self.assertEqual(ui.result_index,0)
            self.assertEqual(ui.result_count(),1)
            ui.handle('FILTER')
            self.assertEqual(ui.active_filter()[0],'VIDEOS')
            for _ in range(len(ui.category_options)-2):
                ui.handle('FILTER')
            self.assertEqual(ui.active_filter()[0],'ALL')
            self.assertEqual(ui.result_count(),5)
        finally:
            ui.close()

    def test_zc_page_and_hold_is_bounded(self):
        ui=UI(demo=True)
        try:
            ui.stage='results'
            ui.build_result_filters()
            ui.handle('RIGHT')
            self.assertEqual(ui.result_index,1)
            ui.handle('LEFT')
            self.assertEqual(ui.result_index,0)
            ui.handle('UP')
            self.assertEqual(ui.result_index,0)
            for _ in range(33): ui.handle('DOWN')
            self.assertEqual(ui.result_index,4)
        finally:
            ui.close()

    def test_other_still_included_in_all_and_size_is_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            cat=core.Catalog(Path(directory)/'scan.sqlite')
            try:
                cat.insert_batch([
                    ('10','file.unknownext',None,core.category_of('file.unknownext')),
                    ('11','a.mkv',2048,core.category_of('a.mkv')),
                    ('12','something.exe',500,core.category_of('something.exe')),
                ])
                self.assertEqual(cat.summary_details('ALL'),(3,2548,1))
                self.assertEqual(cat.summary_details('OTHER'),(2,500,1))
                self.assertEqual(cat.summary_details('VIDEOS'),(1,2048,0))
                self.assertEqual(len(list(cat.all_rows('ALL'))),3)
            finally:
                cat.close()

    def test_preview_results_and_other_category_fits_lcd(self):
        ui=UI(demo=True)
        try:
            ui.stage='results'
            self.assertEqual(ui.render().size, (320,170))
            ui.build_result_filters()
            ui.handle('FILTER')
            self.assertEqual(ui.render().size,(320,170))
        finally:
            ui.close()

if __name__ == '__main__':
    unittest.main()
