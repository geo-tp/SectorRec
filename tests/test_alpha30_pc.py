"""Alpha30 PC-only refinements and compatibility of existing Cardputer defaults."""
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models.core import target_root, create_output_directory
from src.views.desktop_native import DesktopWindow
from src.helpers.photo_reader import validate_output_prefix, session_name

class RecoveryDestinationTests(unittest.TestCase):
    def test_short_new_prefix_and_legacy_helper_compatibility(self):
        for prefix in ('SectorRec_261027', 'SectorRec_261027_2',
                       'SectorRec_20261027', 'SectorRec_20261027_2'):
            self.assertTrue(validate_output_prefix(prefix))
            self.assertTrue(session_name(prefix).startswith('.sectorrec_'))
        for prefix in ('SectorRec_261027_0','SectorRec_261027_01','SectorRec_261027_1000',
                       'SectorRec_261027/../../escape'):
            self.assertFalse(validate_output_prefix(prefix))

    def test_compact_pc_backup_and_unchanged_cardputer_default(self):
        when=datetime.datetime(2026,10,27)
        home=Path('/home/pi')
        self.assertEqual(target_root(home,'My USB',when).name,'My-USB_20261027')
        self.assertEqual(target_root(home,'My USB',when,compact=True).name,'My-USB_261027')
        with tempfile.TemporaryDirectory() as temp:
            with patch('src.models.core.dt') as dt_mock:
                dt_mock.datetime.now.return_value=when
                output=create_output_directory(home,'My USB',destination_root=temp)
            self.assertEqual(output.name,'My-USB_261027')

    def test_session_destination_avoids_symlink_and_other_scans(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            prefix=root/'SectorRec_261027'
            expected=root/'SectorRec_261027.2'
            expected.mkdir()
            (root/'SectorRec_261026.1').mkdir()
            (root/'SectorRec_261027.1').symlink_to(root/'SectorRec_261026.1')
            self.assertEqual(DesktopWindow.signature_output_folder(root,prefix),expected)
            self.assertEqual(DesktopWindow.signature_output_folder(root,root/'../escape'),root)

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk GUI tests need an X display')
class DesktopAlpha30Tests(unittest.TestCase):
    def setUp(self):
        from src.app import UI
        self.ui=UI(demo=True,desktop=True)
        self.win=DesktopWindow(self.ui)
        self.win.root.update()

    def tearDown(self):
        self.win.confirm_action = lambda *args, **kwargs: True
        self.win.close()

    def test_partition_requires_setup_then_retains_candidate(self):
        from src.services.partition import PartitionCandidate
        self.ui.devices.insert(0,dict(label='Raw USB',path='/dev/sdZ',size=2**30,
             fs='DISK',transport='USB',partition_disk=True,mounted=False,supported=False))
        self.ui.selection=0
        self.ui.engine='partition'
        found=PartitionCandidate(2048,65536,'FAT32')
        self.ui.partition_status={'state':'done','candidates':[found]}
        self.ui.stage='partition_results'
        self.win.show(force=True)
        self.win.open_partition()
        self.assertEqual(self.ui.stage,'scan_ready')
        self.assertEqual(self.ui.partition_selected,found)
        self.win.back_to_methods()
        self.assertEqual(self.ui.stage,'partition_results')
        self.win.open_partition()
        with tempfile.TemporaryDirectory() as target, patch(
                'src.services.signature.validate_destination',return_value=(Path(target),2**32)):
            self.ui.metadata_destination=target
            self.win.show(force=True)
            self.win.start_metadata_scan()
        self.assertEqual(self.ui.stage,'results')
        self.assertEqual(self.ui.partition_selected.start,2048)

    def test_smart_swaps_views_without_nested_popup(self):
        from src.services.device_diagnostics import interpret_smart
        self.ui.selection=0
        self.ui.diagnostic_loading=False
        self.ui.diagnostic_report=interpret_smart({'smart_status':{'passed':True}},
                                      {'path':'/dev/sda','size':2**30})
        self.win.show_drive_details()
        self.win.root.update()
        dialog=self.win._smart_window
        dialog._sector_show_view('full')
        self.win.root.update()
        self.assertEqual(len([t for t in self.win.root.winfo_children()
                              if t.winfo_class()=='Toplevel']),1)
        self.assertEqual(dialog._sector_views[1].winfo_manager(),'pack')
        dialog._sector_show_view('overview')
        self.assertEqual(dialog._sector_views[0].winfo_manager(),'pack')
        dialog.destroy()

    def test_report_opens_actual_created_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=root/'SectorRec_261027.1'
            output.mkdir()
            self.ui.signature_destination=temp
            self.ui.signature_status={'state':'cancelled','output':str(root/'SectorRec_261027')}
            with patch('src.views.desktop_native.subprocess.Popen') as p:
                self.win.open_signature_destination()
            self.assertEqual(p.call_args.args[0],['xdg-open',str(output)])

if __name__=='__main__':unittest.main()
