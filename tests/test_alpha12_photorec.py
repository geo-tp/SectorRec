"""Regression tests for the Linux privileged PhotoRec handoff and presentation."""
import tempfile
import unittest
from pathlib import Path
from src.helpers import photo_reader
from src.services import signature


class PhotoRecLauncherRegressions(unittest.TestCase):
    def test_worker_and_root_helper_agree_on_session_names(self):
        for suffix in ('', '_2', '_16', '_999'):
            prefix = 'SectorRec_20261027' + suffix
            self.assertTrue(photo_reader.validate_output_prefix(prefix))
            self.assertEqual(photo_reader.session_name(prefix), '.sectorrec_20261027' + suffix)
        for bad in ('SectorRec-20261027-143002-123', 'SectorRec_20261027_0',
                    'SectorRec_20261027_1000', 'SectorRec_20261027/../../oops',
                    'Other_20261027', 'SectorRec_20261027_01'):
            self.assertFalse(photo_reader.validate_output_prefix(bad), bad)
            with self.assertRaises(ValueError):
                photo_reader.session_name(bad)

    def test_root_helper_chown_session_name(self):
        # Does not require privileges: verify the path accepted by the helper
        # matches the hidden directory created by the user-level worker.
        prefix='SectorRec_20261027_5'
        self.assertEqual(photo_reader.session_name(prefix), '.sectorrec_20261027_5')

    def test_exit_code_2_reports_actual_helper_error(self):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'photorec-output.log'
            log.write_text('noise\nSectorRec PhotoRec: Invalid output directory prefix\n')
            self.assertEqual(signature.PhotoRecWorker._failure_detail(log,2),
                             'PhotoRec could not start: Invalid output directory prefix')
            log.write_text('Unrelated PhotoRec text\n')
            self.assertIn('exit code 2',signature.PhotoRecWorker._failure_detail(log,2))

    def test_raw_selected_source_is_explicit_and_noninteractive(self):
        cmd=signature.photorec_command('photorec','/dev/sdb','/safe/SectorRec_20261027','photos')
        self.assertEqual(cmd[:5],['photorec','/d','/safe/SectorRec_20261027','/cmd','/dev/sdb'])
        opts=cmd[-1].split(',')
        self.assertEqual(opts[0],'partition_none')
        self.assertEqual(opts[-2:],['wholespace','search'])
        self.assertEqual(opts[1:4],['fileopt','everything','disable'])

if __name__=='__main__': unittest.main()

class PhotoRecSourceGuardTests(unittest.TestCase):
    def test_desktop_photorec_prepares_unmounted_disk_too(self):
        from unittest.mock import patch
        from src.controllers.controller import Controller
        ui=Controller(demo=True, desktop=True)
        ui.demo=False
        ui.engine='signature'
        ui.selection=0
        self.assertFalse(ui.device['mounted'])
        self.addCleanup(ui.close)
        with patch.object(ui,'signature_preflight',return_value={'free':2**30,'low':False}), \
             patch.object(ui,'begin_unmount') as prep, \
             patch.object(ui,'begin_signature_scan') as launch:
            ui.start_signature_after_confirmation()
        prep.assert_called_once_with(next_action='signature_scan')
        launch.assert_not_called()

    def test_signature_completion_releases_source_guard(self):
        from src.controllers.controller import Controller
        class Guard:
            def __init__(self): self.releases=0
            def release(self): self.releases+=1
        ui=Controller(demo=True,desktop=True)
        g=Guard()
        ui.scan_guard=g
        ui.stage='signature_running'
        ui.signature_status={'state':'error','error':'sample failure'}
        ui.tick()
        self.assertEqual(ui.stage,'signature_summary')
        self.assertEqual(g.releases,1)
        ui.close()
