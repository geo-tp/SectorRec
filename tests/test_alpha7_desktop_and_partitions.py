"""Regression coverage for PhotoRec exit, links, TestDisk existing volumes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from src.services.partition import (parse_testdisk_log, parse_existing_lsblk,
                                   merge_partition_candidates, PartitionCandidate, PartitionSearchWorker)
from src.app import UI
from src.views.desktop_native import DesktopWindow

FAT32_LOG = '''Disk /dev/sdd - 4 GB - CHS 480 255 63, sector size=512
Current partition structure:
 1 P FAT32 LBA 0 32 33 477 191 61 7659520
search_part()
Results
 1 P FAT32 LBA 0 32 33 477 191 61 7659520
interface_write()
'''

class PartitionParsingAlpha7Tests(unittest.TestCase):
    def test_real_fat32_current_structure_is_not_missing(self):
        parts = parse_testdisk_log(FAT32_LOG, 4_000_000_000)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].start, 2048)
        self.assertEqual(parts[0].origin, 'existing')

    def test_current_without_results_is_still_existing(self):
        parts = parse_testdisk_log(FAT32_LOG.split('Results')[0], 4_000_000_000)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].origin, 'existing')

    def test_deleted_entries_in_results_are_search_candidates(self):
        report = 'Results\n D FAT32 LBA 2048 4095 2048\ninterface_write()'
        parts = parse_testdisk_log(report, 4_000_000)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].origin, 'found')

    def test_live_lsblk_partitions_are_included_if_valid(self):
        tree={'blockdevices':[{'path':'/dev/sdz','type':'disk','log-sec':512,'children':[
            {'path':'/dev/sdz1','type':'part','fstype':'vfat','start':2048,'size':2_048_000},
            {'path':'/dev/sdz2','type':'part','fstype':'swap','start':7000,'size':1_024_000},
        ]}]}
        parts = parse_existing_lsblk(tree,'/dev/sdz', 4_000_000)
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0].label,'VFAT')
        self.assertEqual(parts[0].origin,'existing')
        self.assertEqual(len(parse_existing_lsblk(tree,'/dev/sdz', 10000)),0)

    def test_os_overrides_duplicate_found_partition(self):
        live=[PartitionCandidate(2048,4000,'VFAT',origin='existing')]
        found=[PartitionCandidate(2048,4000,'FAT32',origin='found'),
               PartitionCandidate(8192,2000,'NTFS',origin='found')]
        combined=merge_partition_candidates(live,found)
        self.assertEqual([(p.origin,p.start) for p in combined], [('existing',2048),('found',8192)])

    def test_scripted_testdisk_report_with_current_partition_only(self):
        """Integration: a real worker path for the formerly invisible FAT32."""
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'source.img'
            image.write_bytes(b'\x00' * 512)
            status = {}
            worker = PartitionSearchWorker(str(image), 4_000_000_000, True, status)
            class FakeProcess:
                returncode = 0
                def poll(self): return 0
                def communicate(self): return b'', b''
                def wait(self, timeout=None): return 0
            def launch(args, **_kw):
                report = Path(args[args.index('/logname') + 1])
                report.write_text(FAT32_LOG.split('Results')[0], encoding='utf-8')
                return FakeProcess()
            with patch('src.services.partition.testdisk_binary',return_value='/usr/bin/testdisk'), \
                 patch('src.services.partition.subprocess.Popen', side_effect=launch):
                worker.run()
            self.assertEqual(status['state'], 'done', status)
            self.assertEqual(status['existing_count'], 1)
            self.assertEqual(status['found_count'], 0)
            self.assertEqual(status['candidates'][0].label, 'FAT32 LBA')

    def test_unknown_testdisk_report_explains_unconfirmed_result(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'source.img'
            image.write_bytes(b'\0'*512)
            status = {}
            worker = PartitionSearchWorker(str(image), 4_000_000_000, True, status)
            class FakeProcess:
                returncode = 0
                def poll(self): return 0
                def communicate(self): return b'', b''
                def wait(self, timeout=None): return 0
            def launch(args, **_kw):
                Path(args[args.index('/logname')+1]).write_text('nonstandard report')
                return FakeProcess()
            with patch('src.services.partition.testdisk_binary',return_value='/usr/bin/testdisk'), \
                 patch('src.services.partition.subprocess.Popen',side_effect=launch):
                worker.run()
            self.assertEqual(status['state'],'done',status)
            self.assertEqual(status['candidates'],[])
            self.assertIn('no partition was confirmed',status['note'])


@unittest.skipUnless(os.getenv('DISPLAY'), 'Tk display required')
class DesktopAlpha7Tests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.win=DesktopWindow(self.ui)
        self.win.root.update()

    def tearDown(self):
        self.win.confirm_action = lambda *args, **kwargs: True
        self.win.close()

    def test_github_badge_opens_project_repository(self):
        self.assertEqual(self.win.github_link.cget('text'), 'GitHub')
        self.assertEqual(self.win.github_border.cget('bg'), '#326C68')
        self.assertEqual(self.win.github_link.master, self.win.github_border)
        with patch('src.views.desktop_native.webbrowser.open') as opener:
            self.win.github_link.event_generate('<Button-1>')
            self.win.root.update()
            opener.assert_called_once_with('https://github.com/geo-tp/SectorRec')
            opener.reset_mock()
            self.win.github_border.event_generate('<Button-1>')
            self.win.root.update()
            opener.assert_called_once_with('https://github.com/geo-tp/SectorRec')

    def test_open_photo_destination_after_early_stop(self):
        with tempfile.TemporaryDirectory(prefix='sectorrec-session-') as folder:
            self.ui.signature_destination=folder
            self.ui.signature_status={'state':'cancelled','output':str(Path(folder)/'SectorRec-session'),
                                      'files':2,'written':1024,'free':100000}
            self.ui.stage='signature_summary'
            self.win.show(force=True)
            captions=[c.cget('text') for c in self.win.footer.winfo_children()]
            self.assertTrue(any('Open destination' in c for c in captions))
            with patch('src.views.desktop_native.subprocess.Popen') as launch:
                self.win.open_signature_destination()
                self.assertEqual(launch.call_args.args[0],['xdg-open',folder])

    def test_open_photo_destination_after_full_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            self.ui.signature_destination=folder
            self.ui.signature_status={'state':'done','files':3,'output':str(Path(folder)/'SectorRec-session')}
            self.ui.stage='signature_summary'
            self.win.show(force=True)
            with patch('src.views.desktop_native.subprocess.Popen') as launch:
                self.win.open_signature_destination()
                launch.assert_called_once()

    def test_partition_status_labels(self):
        self.ui.partition_status={'state':'done','candidates':[
            PartitionCandidate(2048,4096,'FAT32',origin='existing'),
            PartitionCandidate(60000,4096,'NTFS',origin='found')],
            'existing_count':1, 'found_count':1}
        self.ui.stage='partition_results'
        self.win.show(force=True)
        self.assertEqual(self.win.partition_tree.item('0')['values'][0], 'Existing')
        self.assertEqual(self.win.partition_tree.item('1')['values'][0], 'Detected')

if __name__=='__main__':unittest.main()
