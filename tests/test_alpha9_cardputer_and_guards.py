"""SectorRec alpha9 focused regression tests. Never touch actual devices."""
import datetime
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from src.app import UI
from src.models import core
from src.views.view import SCAN_TIPS, PillowView


class CardputerUXTests(unittest.TestCase):
    def test_volume_indent_only_when_parent_is_displayed(self):
        base = {'label':'SD', 'path':'/dev/sdb', 'disk_path':'/dev/sdb',
                'partition_disk':True, 'fs':'DISK','supported':False,
                'size':4_000_000_000,'mounted':False}
        volume={**base,'path':'/dev/sdb1','partition_disk':False,'fs':'VFAT',
                'label':'Photos','supported':True}
        ui=UI(demo=True)
        try:
            ui.devices=[base,volume]
            ui.stage='devices';ui.selection=1
            with_parent=ui.render()
            ui.devices=[volume]
            ui.selection=0
            alone=ui.render()
            self.assertNotEqual(with_parent.tobytes(),alone.tobytes())
            # Parent layout is unchanged, while child row shifts right.
            ui.devices=[base,volume];ui.selection=0
            img_parent=ui.render()
            self.assertEqual(img_parent.size,(320,170))
        finally:ui.close()

    def test_bootable_iso_warning_fits_compact_screen(self):
        ui=UI(demo=True)
        try:
            ui.stage='devices'
            ui.devices=[{'label':'Linux ISO','path':'/dev/sdZ','name':'sdZ',
                         'size':4096,'fs':'ISO9660','supported':False,
                         'partition_disk':False,'child_filesystems':(),
                         'mounted':False}]
            ui.selection=0
            ui.handle('OK')
            self.assertEqual(ui.stage,'notice')
            self.assertLessEqual(len(ui.message.splitlines()),3)
            self.assertIn('ISO',ui.message)
            self.assertEqual(ui.render().size,(320,170))
        finally:ui.close()

    def test_cardputer_tip_about_desktop_is_one_line(self):
        self.assertTrue(any('PC SectorRec' in s for s in SCAN_TIPS))
        ui=UI(demo=True)
        try:
            view=ui.view
            from PIL import ImageDraw, Image
            draw=ImageDraw.Draw(Image.new('RGB',(320,170)))
            self.assertTrue(all(draw.textlength(tip,font=view.f['micro'])<=296 for tip in SCAN_TIPS))
        finally:ui.close()


class MountReservationTests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=False,desktop=True)
        self.selected={'path':'/dev/sdz','partition_disk':True,'mounted':False,
                        'is_image':False, 'size':1000000,'fs':'DISK',
                        'supported':False,'label':'usb'}
        self.ui.devices=[self.selected]
        self.ui.selection=0

    def tearDown(self):self.ui.close()

    def test_partition_scan_preflights_even_unmounted_whole_disk_on_pc(self):
        with patch.object(self.ui,'begin_unmount') as preflight,\
             patch.object(self.ui,'begin_partition_search') as search:
            self.ui.start_partition_search()
        preflight.assert_called_once_with(next_action='partition_search')
        search.assert_not_called()

    def test_partition_scan_releases_existing_guard_before_preflight(self):
        class LiveGuard:
            source='/dev/sdz'
            def alive(self):return True
            def release(self):pass
        self.ui.scan_guard=LiveGuard()
        with patch.object(self.ui,'begin_unmount') as prepare,\
             patch.object(self.ui,'begin_partition_search') as search:
            self.ui.start_partition_search()
        search.assert_not_called()
        prepare.assert_called_once_with(next_action='partition_search')
        self.assertIsNone(self.ui.scan_guard)

    def test_mount_preflight_routes_partition_search_after_guard(self):
        self.ui.stage='unmounting'
        self.ui.engine='partition'
        self.ui.unmount_next='partition_search'
        self.ui.unmount_status={'state':'done'}
        self.ui.unmount_path='/dev/sdz'
        self.ui.devices=[self.selected]
        self.ui.selection=0
        with patch.object(self.ui.platform,'devices',return_value=[self.selected]),\
             patch.object(self.ui,'begin_partition_search') as run:
            self.ui.tick()
        run.assert_called_once_with()

    def test_remount_prevents_search_and_releases_guard(self):
        self.ui.stage='unmounting'
        self.ui.engine='partition'
        self.ui.unmount_next='partition_search'
        self.ui.unmount_status={'state':'done'}
        self.ui.unmount_path='/dev/sdz'
        now_mounted={**self.selected,'mounted':True}
        with patch.object(self.ui.platform,'devices',return_value=[now_mounted]),\
             patch.object(self.ui,'begin_partition_search') as run:
            self.ui.tick()
        run.assert_not_called()
        self.assertEqual(self.ui.stage,'notice')
        self.assertIn('mounted',self.ui.message.lower())

    def test_recovery_default_uses_sectorrec_name(self):
        result=core.target_root(Path('/home/pi'),'Card',datetime.datetime(2026,10,27))
        self.assertIn('/Backups/SectorRec/',str(result))


if __name__=='__main__':unittest.main()
