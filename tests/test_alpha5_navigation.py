"""Regression coverage for return-to-device after a completed scan and hierarchy icons."""
import os
import unittest
from unittest.mock import patch
from src.controllers.controller import Controller
from src.views.desktop_native import DesktopWindow

class PostScanNavigationTest(unittest.TestCase):
    def test_back_from_results_does_not_overwrite_device_stage_with_none(self):
        ui=Controller(demo=True,desktop=True)
        try:
            ui.show_devices()
            ui.selection=0
            ui.handle('OK')
            ui.desktop_select_method('metadata')
            ui.handle('OK')
            self.assertEqual(ui.stage,'scanning')
            ui.tick()
            self.assertEqual(ui.stage,'results')
            ui.handle('BACK')
            self.assertEqual(ui.stage,'devices')
            ui.selection=1
            ui.handle('OK')
            self.assertEqual(ui.stage,'desktop_methods')
            self.assertEqual(ui.device['path'],'/dev/sdb1')
        finally: ui.close()

    def test_metadata_return_with_changed_source(self):
        ui=Controller(demo=True,desktop=True)
        try:
            ui.engine='metadata'
            ui.stage='results'
            ui.handle('BACK')
            self.assertEqual(ui.stage,'devices')
            ui.selection=2
            ui.handle('OK')
            self.assertEqual(ui.stage,'desktop_methods')
            ui.desktop_select_method('metadata')
            self.assertEqual(ui.stage,'scan_ready')
        finally: ui.close()

    def test_nested_partitions_grouped_under_parent_even_when_not_adjacent(self):
        devices=[{'path':'/dev/sdz','partition_disk':True,'disk_path':'/dev/sdz'},
                 {'path':'/dev/sdz1','partition_disk':False,'disk_path':'/dev/sdz'},
                 {'path':'/dev/sdz2','partition_disk':False,'disk_path':'/dev/sdz'}]
        self.assertFalse(DesktopWindow.is_nested_volume(devices,0))
        self.assertTrue(DesktopWindow.is_nested_volume(devices,1))
        self.assertTrue(DesktopWindow.is_nested_volume(devices,2))

    def test_icons_are_based_on_known_device_attributes(self):
        f=DesktopWindow.device_icon_type
        self.assertEqual(f({'transport':'SD'}),'sd')
        # Linux lsblk may return ROTA=1 for an ordinary USB thumb drive.
        # Prioritise the transport so such devices show the USB pictogram.
        with patch('src.views.desktop_native.sys.platform', 'linux'):
            self.assertEqual(f({'transport':'USB','media_type':'HDD'}),'usb')
            self.assertEqual(f({'transport':'usb','rotational':True}),'usb')
            self.assertEqual(f({'transport':'USB','model':'Portable SSD'}),'usb')
            self.assertEqual(f({'transport':'SATA','media_type':'HDD'}),'hdd')
            self.assertEqual(f({'transport':'NVME','media_type':'SSD'}),'ssd')
        # Windows deliberately retains its existing, validated behaviour.
        with patch('src.views.desktop_native.sys.platform', 'win32'):
            self.assertEqual(f({'transport':'USB','media_type':'HDD'}),'hdd')
            self.assertEqual(f({'transport':'USB','model':'Portable SSD'}),'ssd')
        self.assertEqual(f({'transport':'USB'}),'usb')
        self.assertEqual(f({'transport':'USB'},nested=True),'partition')
        self.assertEqual(f({'is_image':True}),'image')

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Graphical tests require X11')
class PostScanDesktopGuiTest(unittest.TestCase):
    def setUp(self):
        self.ui=Controller(demo=True,desktop=True)
        self.window=DesktopWindow(self.ui)
        self.window.root.update()
    def tearDown(self):
        self.window.confirm_action = lambda *args, **kwargs: True
        self.window.close()

    def test_click_device_after_completed_scan(self):
        self.window.select_source(0,True)
        self.window.choose_method('metadata')
        self.window.dispatch('OK')
        self.window.root.after_cancel(self.window._tick_after_id)
        self.window._tick_after_id=None
        self.window.tick()
        self.assertEqual(self.ui.stage,'results')
        with patch.object(self.window, 'confirm_action', return_value=True):
            self.window.dispatch('BACK')
        self.assertEqual(self.ui.stage,'devices')
        self.window.root.update()
        self.window.select_source(1,True)
        self.assertEqual(self.ui.stage,'desktop_methods')
        self.assertEqual(self.ui.device['path'],'/dev/sdb1')

    def test_method_titles_and_compact_controls(self):
        self.window.select_source(1,True)
        self.window.root.update()
        import tkinter as tk
        def texts(w):
            for child in w.winfo_children():
                if isinstance(child,tk.Label):yield child.cget('text')
                yield from texts(child)
        page=' '.join(texts(self.window.body))
        self.assertIn('Recover deleted files by metadata',page)
        self.assertIn('Recover deleted files by signatures',page)

    def test_icons_have_correct_row_background_after_select(self):
        self.ui.devices=[{'path':'/dev/sdz','partition_disk':True,'disk_path':'/dev/sdz','size':4096,'label':'Disk','fs':'DISK','transport':'USB','child_filesystems':['FAT32']},
                         {'path':'/dev/sdz1','partition_disk':False,'disk_path':'/dev/sdz','size':2048,'label':'Volume','fs':'FAT32','transport':'USB'}]
        self.window._render_devices()
        self.window.select_source(1)
        self.window.root.update()
        row=self.window._device_rows[1][0]
        icons=[w for w in self.window._device_backgrounds(row) if isinstance(w,__import__('tkinter').Canvas)]
        self.assertTrue(icons)
        self.assertTrue(all(icon.cget('bg')=='#23443F' for icon in icons))
