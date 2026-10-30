"""Alpha33: read-only partition navigation, partial-result UX and Cardputer cap."""
from __future__ import annotations
import os
import types
import unittest
from unittest.mock import patch

from src.app import UI
from src.models.core import MAX_RECORDS, MAX_RECORDS_DESKTOP, ScanWorker
from src.views.desktop_native import DesktopWindow
from src.services.partition import PartitionCandidate


class CapacityAndCoverageTests(unittest.TestCase):
    def test_cardputer_capacity_and_desktop_are_independent(self):
        self.assertEqual(MAX_RECORDS, 100_000)
        self.assertEqual(MAX_RECORDS_DESKTOP, 20_000_000)
        from src.controllers.controller import Controller
        for desktop, expected in ((False, 100_000), (True, 20_000_000)):
            ui = Controller(demo=False, desktop=desktop)
            try:
                ui.selection=0
                ui.devices = [{'path':'/dev/sdz1','size':10_000,'fs':'VFAT',
                               'supported':True,'label':'test','mounted':False}]
                with patch('src.controllers.controller.ScanWorker') as worker:
                    ui.begin_scan()
                self.assertEqual(worker.call_args.kwargs['max_records'], expected)
                self.assertEqual(ScanWorker(None,'/dev/sdz1',{},max_records=expected).batch_size,
                                 500 if desktop else 100)
            finally:
                ui.close()

    def test_enter_never_cancels_running_scan(self):
        ui=UI(demo=True,desktop=True)
        ui.stage='scanning';ui.status={'state':'pending','count':30_734}
        ui.handle('OK')
        self.assertEqual(ui.stage,'scanning')
        self.assertFalse(ui.scan_stop_requested)
        ui.close()

    def test_done_with_skipped_folders_is_not_user_cancelled(self):
        ui=UI(demo=True,desktop=True)
        ui.stage='scanning'
        ui.status={'state':'done','count':30_734,'limited_dirs':True}
        ui.tick()
        self.assertEqual(ui.stage,'results')
        self.assertTrue(ui.scan_partial)
        self.assertIn('128',ui.scan_partial_reason)
        self.assertNotIn('Stopped early',ui.scan_partial_reason)
        ui.close()


@unittest.skipUnless(os.environ.get('DISPLAY'),'Tk test requires Xvfb')
class DesktopAlpha33Tests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.win=DesktopWindow(self.ui)
        self.win.root.update()

    def tearDown(self):
        self.win.confirm_action = lambda *args, **kwargs: True
        self.ui.stage='devices'
        self.win.close()

    def _set_partition_result(self, from_image=False):
        self.ui.selection=0
        self.ui.engine='partition'
        self.ui.partition_status={'state':'done','candidates':[PartitionCandidate(2048,8192,'NTFS')]}
        if from_image:
            self.ui.partition_status['source']='image_metadata'
        self.ui.stage='partition_results'
        self.win.show(force=True)

    def test_find_lost_partitions_about_visible(self):
        self.ui.selection=0
        self.ui.stage='partition_ready'
        self.win.show(force=True)
        def texts(widget):
            own=[]
            try: own.append(str(widget.cget('text')))
            except Exception: pass
            for child in widget.winfo_children(): own.extend(texts(child))
            return own
        contents=' '.join(texts(self.win.body))
        self.assertIn('ABOUT TESTDISK',contents)
        self.assertIn('partition boundaries',contents)
        self.assertIn('Nothing is written to the source',contents)

    def test_back_from_partition_search_setup_returns_methods(self):
        self.ui.selection=0
        self.ui.stage='partition_ready'
        self.win.show(force=True)
        self.win.dispatch('BACK')
        self.assertEqual(self.ui.stage,'desktop_methods')

    def test_back_from_partition_results_goes_methods_not_stale_results(self):
        for from_image in (False, True):
            self._set_partition_result(from_image=from_image)
            self.win.dispatch('BACK')
            self.assertEqual(self.ui.stage,'desktop_methods')

    def test_back_from_image_sleuth_config_still_goes_partition_picker(self):
        self._set_partition_result(from_image=True)
        self.ui.partition_status['candidates'].append(PartitionCandidate(17000,8192,'FAT32'))
        self.ui.stage='scan_ready'
        self.win.show(force=True)
        self.win.dispatch('BACK')
        self.assertEqual(self.ui.stage,'partition_results')
        self.win.dispatch('BACK')
        self.assertEqual(self.ui.stage,'desktop_methods')

    def test_return_key_does_not_trigger_stop_confirmation(self):
        self.ui.engine='metadata'
        self.ui.status={'state':'pending','count':30_734}
        self.ui.stage='scanning'
        self.win.show(force=True)
        with patch.object(self.win,'confirm_action') as confirm:
            self.win.on_key(types.SimpleNamespace(widget=self.win.root,keysym='Return'))
        confirm.assert_not_called()
        self.assertEqual(self.ui.stage,'scanning')

    def test_completed_primary_scan_warns_limited_coverage_not_stop(self):
        self.ui.selection=0
        self.ui.stage='results'
        self.ui.status={'state':'done','count':30_734,'limited_dirs':True}
        self.ui.scan_partial=True
        self.ui.scan_partial_reason='Main metadata listing finished; some deleted folders were skipped'
        self.win.show(force=True)
        self.assertIn('LIMITED COVERAGE', ' '.join(str(w.cget('text')) for w in self.win.body.winfo_children()[1].winfo_children() if hasattr(w, 'cget') and 'text' in w.keys()))

if __name__=='__main__': unittest.main()
