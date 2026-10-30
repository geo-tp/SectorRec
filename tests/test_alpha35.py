"""Alpha35 regression checks: LCD off, consistent preview, resumable imaging."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.views.cardputer.idle_dim import IdleDimmer
from src.services.image_rescue import plan_resume, plan_image, ImageWorker


class LCDIdlePolicy(unittest.TestCase):
    def test_pwm_60_dim_120_off_wake_and_close(self):
        with tempfile.TemporaryDirectory() as d:
            node=Path(d)/'lcd';node.mkdir()
            (node/'brightness').write_text('150')
            (node/'max_brightness').write_text('255')
            dim=IdleDimmer(backlight_dir=Path(d))
            t=dim.last_input
            self.assertFalse(dim.update(t+59))
            self.assertTrue(dim.update(t+60))
            self.assertEqual((node/'brightness').read_text(),'15')
            self.assertTrue(dim.update(t+120))
            self.assertTrue(dim.off)
            self.assertTrue(dim.physical_off)
            self.assertEqual((node/'brightness').read_text(),'0')
            self.assertTrue(dim.input(t+121))
            self.assertEqual((node/'brightness').read_text(),'150')
            self.assertFalse(dim.off)
            dim.close()
    def test_binary_backlight_can_go_entirely_off(self):
        with tempfile.TemporaryDirectory() as d:
            node=Path(d)/'gpio';node.mkdir()
            (node/'brightness').write_text('1')
            (node/'max_brightness').write_text('1')
            dim=IdleDimmer(backlight_dir=Path(d));t=dim.last_input
            dim.update(t+61)
            self.assertTrue(dim.software_dim)
            dim.update(t+121)
            self.assertTrue(dim.physical_off)
            self.assertEqual((node/'brightness').read_text(),'0')
            dim.close()
            self.assertEqual((node/'brightness').read_text(),'1')
    def test_no_backlight_uses_black_frame(self):
        with tempfile.TemporaryDirectory() as d:
            dim=IdleDimmer(backlight_dir=Path(d));t=dim.last_input
            dim.update(t+121)
            self.assertTrue(dim.software_off)
            self.assertFalse(dim.physical_off)
            dim.input(t+122)
            self.assertFalse(dim.software_off)


class ResumeProtection(unittest.TestCase):
    def test_accept_matching_original(self):
        with tempfile.TemporaryDirectory() as d, \
             patch('src.services.image_rescue.ensure_different_disk'), \
             patch('src.services.image_rescue.source_identity',return_value='USB-123'):
            image=Path(d)/'SectorRec_sdb.img'
            image.write_bytes(b'xxx')
            (Path(str(image)+'.map')).write_text('ddrescue map')
            (Path(str(image)+'.source.json')).write_text(json.dumps({'schema':1,'identity':'USB-123','size':12345}))
            self.assertEqual(plan_resume('/dev/sdb',image,12345),image)
            with self.assertRaisesRegex(ValueError,'identity or size'):
                plan_resume('/dev/sdb',image,23456)
    def test_refuses_old_or_missing_map(self):
        with tempfile.TemporaryDirectory() as d,patch('src.services.image_rescue.ensure_different_disk'):
            image=Path(d)/'SectorRec_sdb.img';image.write_bytes(b'xxx')
            with self.assertRaisesRegex(ValueError,'map'):
                plan_resume('/dev/sdb',image,10000)
    def test_mapfile_identifies_unreadable_bytes(self):
        from src.services.image_rescue import rescued_percent_from_map
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.img.map'
            p.write_text('# GNU ddrescue mapfile\n0x0 0x1000 +\n0x1000 0x1000 -\n')
            self.assertEqual(rescued_percent_from_map(p,0x2000),50.0)
            p.write_text('0x0 0x2000 +\n')
            self.assertEqual(rescued_percent_from_map(p,0x2000),100.0)
    def test_resume_worker_uses_explicit_mode(self):
        worker=ImageWorker('/dev/sdb','/tmp/SectorRec_sdb.img',12345,{},resume=True)
        self.assertTrue(worker.resume)


class DesktopAlpha35UI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from src.app import UI
            from src.views.desktop_native import DesktopWindow
            cls.win=DesktopWindow(UI(demo=True,desktop=True))
            cls.win.root.update()
        except Exception as e:
            raise unittest.SkipTest('Tk display unavailable: '+str(e))
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'win'):cls.win.root.destroy()
    def test_photorec_all_has_category_and_extension(self):
        w=self.win;w.ui.stage='signature_running';w.ui.signature_format='all';w.show(force=True)
        w.root.update()
        self.assertEqual(w._live_category.get(),'ALL')
        self.assertTrue(hasattr(w,'_live_cat_combo'))
        self.assertIn('jpg',w._live_ext_combo['values'])
    def test_photorec_photos_has_only_extension(self):
        w=self.win;w.ui.signature_format='photos';w.show(force=True);w.root.update()
        self.assertEqual(w._live_category.get(),'PHOTOS')
        self.assertNotIn('mp3',w._live_ext_combo['values'])
        self.assertIn('jpg',w._live_ext_combo['values'])
        self.assertFalse(any(child.winfo_exists() and getattr(child,'_w','')==getattr(w,'_live_cat_combo',None)._w for child in w.root.winfo_children() if hasattr(w,'_live_cat_combo')))
    def test_photorec_live_filter_exact_extension(self):
        w=self.win;w.ui.signature_format='all';w.show(force=True);w.root.update()
        w.ui.signature_status['recent_files']=[('a.jpg',120,'/tmp/a.jpg',0),
                                              ('a.jpeg',130,'/tmp/a.jpeg',0),
                                              ('notes',20,'/tmp/notes',0)]
        w._live_update_extensions()
        self.assertIn('NO EXT',w._live_ext_combo['values'])
        w._live_extension.set('jpg')
        w._refresh_live(force=True)
        self.assertEqual(len(w.live_tree.get_children()),1)
        w._live_extension.set('NO EXT')
        w._refresh_live(force=True)
        self.assertEqual(len(w.live_tree.get_children()),1)
    def test_transition_is_opaque(self):
        w=self.win;w._soft_page_transition()
        self.assertEqual(w._fade_after_ids,[])
        self.assertEqual(float(w.root.attributes('-alpha')),1.0)
