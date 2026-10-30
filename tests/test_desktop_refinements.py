"""Regressions for SectorRec desktop alpha4, no hardware access required."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import UI
from src.views.desktop_native import DesktopWindow, SURFACE_2
from src.views.scan_guidance import SCAN_TIPS, scan_tip
from src.services import signature

class GuideTests(unittest.TestCase):
    def test_full_cycle_includes_limited_information(self):
        for mode in ('metadata','signature','partition'):
            self.assertGreaterEqual(len(SCAN_TIPS[mode]),8)
            self.assertTrue(all(len(x)>30 for x in SCAN_TIPS[mode]))
            self.assertEqual(scan_tip(mode,0),SCAN_TIPS[mode][0])
            self.assertEqual(scan_tip(mode,6.6),SCAN_TIPS[mode][1])
            self.assertEqual(scan_tip(mode,len(SCAN_TIPS[mode])*6.5),SCAN_TIPS[mode][0])
        self.assertIn('Other',' '.join(SCAN_TIPS['metadata']))
        self.assertIn('already written', ' '.join(SCAN_TIPS['signature']))
        self.assertIn('SD card',' '.join(SCAN_TIPS['partition']))

    def test_destination_selection_persists_until_preflight(self):
        ui=UI(demo=True,desktop=True)
        try:
            with tempfile.TemporaryDirectory() as home:
                ui.selection=0
                ui.set_signature_destination(home)
                self.assertEqual(ui.signature_destination,str(Path(home).resolve()))
                with patch('src.services.signature.validate_destination',side_effect=ValueError('Destination is on the source disk')):
                    with patch('src.services.signature.photorec_binary',return_value='/usr/bin/photorec'):
                        with self.assertRaisesRegex(ValueError,'source disk'):
                            ui.signature_preflight()
                self.assertEqual(ui.signature_destination,str(Path(home).resolve()))
        finally:ui.close()

    def test_pc_requires_selection_without_changing_cardputer(self):
        disks=[dict(path='/dev/sdb',partition_disk=True,supported=False),
               dict(path='/dev/sdb1',partition_disk=False,supported=True)]
        pc=UI(demo=True,desktop=True)
        lcd=UI(demo=True,desktop=False)
        try:
            for controller in (pc,lcd):
                controller.devices=[]
                controller.device_refresh_ready=('ok',list(disks))
                controller.apply_device_refresh()
            self.assertIsNone(pc.device)
            self.assertEqual(lcd.device['path'],'/dev/sdb')
        finally:
            pc.close();lcd.close()

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','Tk requires a graphical display')
class DesktopRefinementTests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.app=DesktopWindow(self.ui)
        self.app.root.update()

    def tearDown(self):
        self.app.confirm_action = lambda *args, **kwargs: True
        self.app.close()

    def test_selected_and_deselected_labels_share_frame_color(self):
        self.app.select_source(1)
        self.app.root.update()
        for index,(frame,caption) in enumerate(self.app._device_rows):
            expected='#23443F' if index==1 else SURFACE_2
            for child in self.app._device_backgrounds(frame):
                if getattr(child, '_sector_badge', False):
                    # Status badges deliberately retain their semantic color.
                    continue
                self.assertEqual(child.cget('bg'),expected)

    def test_method_caption_is_not_search_file_content(self):
        self.app.select_source(1,True)
        self.app.root.update()
        from tkinter import Label
        def texts(widget):
            for child in widget.winfo_children():
                if isinstance(child, Label):
                    yield child.cget('text')
                yield from texts(child)
        self.assertIn('Recover deleted files by signatures', list(texts(self.app.body)))
        self.assertIn('signature', {key for _, key, _ in self.app.method_cards})

    def test_mount_info_is_in_summary_not_body(self):
        self.ui.devices=[{'path':'/dev/sdz1','label':'USB','fs':'NTFS','size':10**9,
                          'transport':'USB','mounted':True,'supported':True}]
        self.ui.selection=0
        self.ui.stage='desktop_methods'
        self.app.show(force=True)
        self.app.root.update()
        badge=self.app.body.winfo_children()
        def text_nodes(root):
            result=[]
            for child in root.winfo_children():
                if hasattr(child,'cget') and 'text' in child.keys():result.append(child.cget('text'))
                result+=text_nodes(child)
            return result
        texts=text_nodes(self.app.body)
        self.assertEqual(sum('Will unmount when you start' in t for t in texts),1)

    def test_chosen_folder_visible_if_preflight_rejects_it(self):
        with tempfile.TemporaryDirectory() as folder:
            self.app.select_source(1,True)
            self.app.choose_method('signature')
            self.app.root.update()
            with patch.object(self.app,'pick_destination_directory',return_value=folder), \
                 patch('src.services.signature.photorec_binary',return_value='/usr/bin/photorec'), \
                 patch('src.services.signature.validate_destination',side_effect=ValueError('Unsafe source disk')):
                self.app.choose_destination()
                self.app.root.update()
                self.assertEqual(self.ui.signature_destination,str(Path(folder).resolve()))
                self.assertEqual(self.ui.stage,'signature_setup')
                def texts(root):
                    found=[]
                    for child in root.winfo_children():
                        if hasattr(child,'cget') and 'text' in child.keys():found.append(str(child.cget('text')))
                        found+=texts(child)
                    return found
                all_text=' '.join(texts(self.app.body))
                self.assertIn(folder,all_text)
                self.assertIn('Unsafe source disk',all_text)
                self.assertNotIn('No destination selected',all_text)

if __name__=='__main__':unittest.main()
