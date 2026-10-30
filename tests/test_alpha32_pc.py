"""Alpha32 PC-only UI changes and existing-image (no-TestDisk) Sleuth routing."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import UI
from src.services.partition import image_table_partitions
from src.views.desktop_native import DesktopWindow


def mbr_image(path, offsets):
    with open(path,'wb') as f:
        f.truncate(20*1024*1024)
        sector=bytearray(512)
        for i,(start,count) in enumerate(offsets):
            sector[446+i*16+4]=0x0c if i==0 else 0x07
            struct.pack_into('<II', sector, 446+i*16+8, start,count)
        sector[510:512]=b'\x55\xaa'
        f.seek(0)
        f.write(sector)


def gpt_image(path, offsets):
    with open(path,'wb') as f:
        f.truncate(20*1024*1024)
        mbr=bytearray(512)
        mbr[446+4]=0xee
        struct.pack_into('<II',mbr,446+8,1,40959)
        mbr[510:512]=b'\x55\xaa'
        f.seek(0);f.write(mbr)
        hdr=bytearray(512)
        hdr[:8]=b'EFI PART'
        struct.pack_into('<I',hdr,12,92)
        struct.pack_into('<Q',hdr,72,2)
        struct.pack_into('<II',hdr,80,128,128)
        f.seek(512);f.write(hdr)
        for i,(start,count) in enumerate(offsets):
            ent=bytearray(128)
            ent[:16]=bytes([0x01])*16
            struct.pack_into('<QQ',ent,32,start,start+count-1)
            ent[56:56+len('Disk'.encode('utf-16le'))]='Disk'.encode('utf-16le')
            f.seek(1024+128*i);f.write(ent)


class DiskImageTableTests(unittest.TestCase):
    def test_existing_mbr_partition_and_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'disk.img'
            mbr_image(img,[(2048,8192),(12000,8192)])
            partitions=image_table_partitions(img)
            self.assertEqual([p.start for p in partitions],[2048,12000])
            self.assertTrue(all(p.origin=='existing' for p in partitions))
            mbr_image(img,[(2048,8192),(41000,200)])
            self.assertEqual([p.start for p in image_table_partitions(img)],[2048])

    def test_existing_gpt_partition(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'disk.img'
            gpt_image(img,[(2048,8192),(16000,5000)])
            partitions=image_table_partitions(img)
            self.assertEqual([p.start for p in partitions],[2048,16000])
            self.assertEqual(partitions[0].label,'Disk')

    def test_no_partition_no_testdisk(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'empty.img'
            mbr_image(img,[])
            ui=UI(demo=True,desktop=True)
            ui.add_image(str(img))
            # No valid table is a raw filesystem image, not a partition search.
            ui.desktop_select_method('metadata')
            self.assertEqual(ui.stage,'scan_ready')

    def test_single_partition_goes_to_sleuth_setup_without_testdisk(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'one.img'
            mbr_image(img,[(2048,8192)])
            ui=UI(demo=True,desktop=True)
            ui.add_image(str(img))
            ui.desktop_select_method('metadata')
            self.assertEqual(ui.stage,'scan_ready')
            self.assertEqual(ui.engine,'partition')
            self.assertEqual(ui.partition_source['path'],str(img))
            self.assertEqual(ui.partition_selected.start,2048)
            with patch('src.services.partition.find_tsk_binary',return_value='/usr/bin/fls'):
                command=ui.partition_builder('scan',str(img))
            self.assertIn('2048', [str(item) for item in command])

    def test_multi_partition_picker_not_testdisk(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'two.img'
            mbr_image(img,[(2048,8192),(12000,8192)])
            ui=UI(demo=True,desktop=True)
            ui.add_image(str(img))
            ui.desktop_select_method('metadata')
            self.assertEqual(ui.stage,'partition_results')
            self.assertEqual(ui.partition_status['source'],'image_metadata')
            self.assertEqual(len(ui.partition_status['candidates']),2)
            ui.desktop_select_method('partition')
            self.assertEqual(ui.stage,'partition_ready')
            self.assertFalse(ui.partition_status)


@unittest.skipUnless(os.environ.get('DISPLAY'),'Tk UI requires display')
class DesktopAlpha32Tests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.win=DesktopWindow(self.ui)
        self.win.root.update()

    def tearDown(self):
        self.win.confirm_action = lambda *args, **kwargs: True
        self.ui.stage='devices'  # Don't prompt on teardown of simulated scans.
        self.win.close()

    def test_single_image_sleuth_scan_reaches_results_with_correct_offset(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'one.img'
            mbr_image(img,[(2048,8192)])
            self.ui.add_image(str(img))
            self.ui.desktop_select_method('metadata')
            self.win.show(force=True)
            self.ui.metadata_destination=d
            with patch('src.services.signature.validate_destination',
                       return_value=(Path(d),3*1024**3)):
                self.win.start_metadata_scan()
            self.assertEqual(self.ui.stage,'results')
            self.assertEqual(self.ui.partition_selected.start,2048)
            with patch.object(self.win,'confirm_action',return_value=True):
                self.win.dispatch('BACK')
            self.assertEqual(self.ui.stage,'desktop_methods')

    def test_border_element_removed(self):
        from tkinter import ttk
        layout=ttk.Style(self.win.root).layout('Sector.Treeview')
        self.assertNotIn('Treeview.field',str(layout))
        self.assertIn('Treeview.treearea',str(layout))

    def test_no_footer_sentence_photo_and_no_paging_buttons(self):
        self.ui.stage='signature_running'
        self.ui.signature_status={'state':'running','recent_files':[],'written':0,'files':0,'free':1024}
        self.win.show(force=True)
        self.win.root.update()
        self.assertIsNone(self.win._live_position)
        self.assertIsNone(self.win._live_prev)
        self.assertIsNone(self.win._live_next)

    def test_pagination_smaller(self):
        btn=self.win.page_button(self.win.body,'<',lambda:None)
        self.assertEqual(btn.cget('width'),2)
        from tkinter.font import Font
        self.assertEqual(Font(root=self.win.root,font=btn.cget('font')).actual('size'),11)

    def test_back_sleuth_from_one_partition_returns_methods(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'one.img'
            mbr_image(img,[(2048,8192)])
            self.ui.add_image(str(img))
            self.ui.desktop_select_method('metadata')
            self.win.show(force=True)
            self.assertEqual(self.ui.stage,'scan_ready')
            self.win.back_to_methods()
            self.assertEqual(self.ui.stage,'desktop_methods')

    def test_back_sleuth_from_multiple_returns_partition_picker(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'two.img'
            mbr_image(img,[(2048,8192),(12000,8192)])
            self.ui.add_image(str(img))
            self.ui.desktop_select_method('metadata')
            self.win.show(force=True)
            self.assertEqual(self.ui.stage,'partition_results')
            self.win.open_partition()
            self.assertEqual(self.ui.stage,'scan_ready')
            self.win.back_to_methods()
            self.assertEqual(self.ui.stage,'partition_results')

    def test_stop_scan_confirmation_mentions_partial_results(self):
        self.ui.stage='scanning'
        self.ui.engine='metadata'
        with patch.object(self.win,'confirm_action',return_value=False) as confirm:
            self.win.dispatch('BACK')
        self.assertIn('partial',confirm.call_args.args[1])
        self.assertEqual(self.ui.stage,'scanning')

    def test_confirmation_dialog_in_theme_and_cancel(self):
        import tkinter as tk
        self.win.root.after(120,lambda:[w.destroy() for w in self.win.root.winfo_children()
                                        if isinstance(w,tk.Toplevel)])
        # The themed dialog must cancel safely when destroyed without a click.
        self.assertFalse(self.win.confirm_action('Stop metadata scan?','Partial results can be reviewed.','Stop scan'))

if __name__=='__main__':unittest.main()
