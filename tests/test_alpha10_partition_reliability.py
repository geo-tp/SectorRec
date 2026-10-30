"""Alpha10 regression tests: no real devices are modified or mounted."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models import core
from src.app import UI
from src.services.partition import PartitionSearchWorker, existing_partitions, PartitionCandidate


class KnownPartitionsTests(unittest.TestCase):
    def test_existing_usb_fat32_survives_testdisk_empty_report(self):
        tree = {'blockdevices': [{'path':'/dev/sdz','type':'disk','log-sec':512,
               'children':[{'path':'/dev/sdz1','type':'part','fstype':'vfat',
                            'start':2048,'size':3_000_000_000}]}]}
        class FakeProc:
            returncode = 1
            def poll(self):return 0
            def wait(self):return 1
        with patch('src.services.partition.subprocess.check_output',
                   return_value=json.dumps(tree)), \
             patch('src.services.partition.discover_command',
                   return_value=['/bin/true']), \
             patch('src.services.partition.subprocess.Popen',return_value=FakeProc()):
            status={}
            PartitionSearchWorker('/dev/sdz',4_000_000_000,False,status).run()
        # Valid geometry from Linux must still show even if TestDisk has no log.
        self.assertEqual(status['state'],'error',status)
        self.assertIn('no analysis report',status['error'])
        self.assertEqual(status['existing_count'],1)
        self.assertEqual(status['candidates'][0].start,2048)
        self.assertEqual(status['candidates'][0].origin,'existing')

    def test_fallback_to_global_lsblk_tree_for_external_adapter(self):
        empty={'blockdevices':[{'path':'/dev/sdz','type':'disk','log-sec':512}]}
        full={'blockdevices':[{'path':'/dev/sdz','type':'disk','log-sec':512,
              'children':[{'path':'/dev/sdz1','type':'part','fstype':'vfat',
                           'start':2048,'size':1_024_000}]}]}
        with patch('src.services.partition.subprocess.check_output',
                   side_effect=[json.dumps(empty),json.dumps(full)]) as cmd:
            parts=existing_partitions('/dev/sdz',4_000_000)
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0].origin,'existing')
        self.assertEqual(cmd.call_count,2)


class PartitionFailureUXTests(unittest.TestCase):
    def _simulate(self,desktop):
        ui=UI(demo=True,desktop=desktop)
        ui.devices=[{'label':'USB disk','path':'/dev/sdz','partition_disk':True,
                     'size':4_000_000_000,'fs':'DISK','mounted':False}]
        ui.selection=0
        ui.engine='partition';ui.stage='scanning'
        ui.status={'state':'error','error':'Possible encryption detected (high entropy)'}
        with patch.object(ui, 'result_count', return_value=0):
            ui.tick()
        self.assertEqual(ui.stage,'notice')
        self.assertIn('not confirmed',ui.message)
        self.assertEqual(ui.return_stage,'partition_results')
        ui.close()

    def test_suspected_encryption_not_presented_as_fact_cardputer(self):
        self._simulate(False)

    def test_suspected_encryption_not_presented_as_fact_desktop(self):
        self._simulate(True)


class CatalogScalabilityTests(unittest.TestCase):
    def test_incremental_unique_count_no_full_sum_for_each_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=core.Catalog(Path(tmp)/'db.sqlite')
            c.insert_batch([('2','a.jpg',20,'PHOTOS'),('2','a.jpg',20,'PHOTOS')])
            self.assertEqual(c.indexed_count,1)
            c.insert_batch([('3','b.png',40,'PHOTOS')])
            self.assertEqual(c.indexed_count,2)
            c.clear();self.assertEqual(c.indexed_count,0)
            c.close()

    def test_platform_specific_catalog_limits(self):
        from src.controllers.controller import Controller
        from unittest.mock import Mock
        for desktop, expected in ((False, 100_000),(True,20_000_000)):
            ui=Controller(demo=False,desktop=desktop)
            try:
                ui.devices=[{'path':'/dev/sdz1','size':10_000,'fs':'VFAT',
                             'supported':True,'label':'usb','mounted':False}]
                ui.selection=0
                with patch('src.controllers.controller.ScanWorker') as worker:
                    ui.begin_scan()
                self.assertEqual(worker.call_args.kwargs['max_records'],expected)
                actual = core.ScanWorker(None,'/dev/sdz1',{},max_records=expected)
                self.assertEqual(actual.batch_size,500 if desktop else 100)
            finally:
                ui.close()

if __name__=='__main__':unittest.main()
