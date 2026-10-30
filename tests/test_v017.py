"""v0.1.7: transparent welcome art, readable hints and 3s turbo scrolling."""
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from src.app import UI
from src.views.view import BG, SCAN_TIPS, STOPPING_TIP, SCAN_CANCEL_LABEL
from src.views.cardputer.keyboard import RepeatTracker, REPEAT_DELAY, FAST_REPEAT_AFTER, \
    FAST_REPEAT_INTERVAL, TURBO_REPEAT_AFTER, TURBO_REPEAT_INTERVAL


class V017Tests(unittest.TestCase):
    def test_three_stage_repeat_is_one_row_per_tick_and_releases(self):
        tracker=RepeatTracker()
        self.assertEqual(tracker.feed(6,45,1,100.0),['DOWN'])
        self.assertEqual(tracker.poll(100.12),[])
        self.assertEqual(tracker.poll(100.161),['DOWN'])
        self.assertEqual(tracker.poll(100.18),[])
        self.assertEqual(tracker.poll(100.9),['DOWN'])
        self.assertAlmostEqual(tracker.held[(6,45)][2],100.9+FAST_REPEAT_INTERVAL)
        self.assertEqual(tracker.poll(103.001),['DOWN'])
        self.assertAlmostEqual(tracker.held[(6,45)][2],103.001+TURBO_REPEAT_INTERVAL)
        self.assertEqual(tracker.poll(103.008),[])
        self.assertEqual(tracker.poll(103.014),['DOWN'])
        self.assertEqual(tracker.feed(6,45,0,103.02),[])
        self.assertEqual(tracker.poll(104.0),[])
        self.assertEqual(TURBO_REPEAT_AFTER,3.0)
        self.assertLess(TURBO_REPEAT_INTERVAL,FAST_REPEAT_INTERVAL)

    def test_welcome_icon_has_no_background_mat(self):
        art=Image.open(Path(__file__).resolve().parent.parent/'src'/'assets'/'sectorrec_welcome.png')
        self.assertEqual(art.mode,'RGBA')
        self.assertEqual(art.getpixel((0,0))[3],0)
        alpha=art.getchannel('A')
        self.assertGreater(alpha.getbbox()[2]-alpha.getbbox()[0],60)
        ui=UI(demo=True)
        try:
            self.assertEqual(ui.render().getpixel((8,53)),(12,18,32))
        finally:ui.close()

    def test_scan_tips_fit_one_line_and_warn_about_early_stop(self):
        ui=UI(demo=True)
        try:
            canvas=ImageDraw.Draw(Image.new('RGB',(320,170)))
            for tip in (*SCAN_TIPS,STOPPING_TIP):
                with self.subTest(tip=tip):
                    self.assertLessEqual(canvas.textlength(tip,font=ui.view.f['micro']),296)
            self.assertIn('early',SCAN_CANCEL_LABEL)
            self.assertIn('partial',SCAN_CANCEL_LABEL)
        finally:ui.close()

if __name__ == '__main__': unittest.main()
