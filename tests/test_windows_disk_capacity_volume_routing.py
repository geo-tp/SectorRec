"""Regressions for Windows USB capacity consistency and partition browsing.

All Windows APIs and external tools are simulated; no devices are accessed.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.platform_disks import windows_inventory
from src.services.partition import (PartitionCandidate, existing_windows_partitions,
                                    verify_windows_partition_volume)
from src.services.image_rescue import ImageWorker
from src.infrastructure import windows_ddrescue
from src.controllers.controller import Controller

DEVICE = r'\\.\PhysicalDrive3'
VOLUME = r'\\.\F:'

class WindowsCapacityRegression(unittest.TestCase):
    def test_native_size_takes_priority_over_wmi_rounded_size(self):
        rows=[dict(DeviceID=DEVICE, Model='8 GB USB', Size='8000000000',
                   DiskSize='8004304896',DiskSerial='UNIT42',DiskUnique='USB-ID-42', Partitions=[
                       dict(Letter='F',Fs='FAT32',Size='7999000000')])]
        calls=[]
        def run(args, **kwargs):
            calls.append(args[-1]); return SimpleNamespace(returncode=0,stdout=json.dumps(rows))
        whole,volume=windows_inventory(run=run)
        self.assertIn('Get-Disk -Number',calls[0]); self.assertIn('DiskSize=',calls[0])
        self.assertEqual(whole['size'],8004304896)
        self.assertTrue(whole['imaging_size_verified'])
        self.assertEqual(json.loads(whole['imaging_identity'])['serial'], 'UNIT42')
        self.assertEqual(volume['size'],7999000000)

    def test_swap_of_same_size_usb_device_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            state={}
            actual=json.dumps(dict(size=4096,serial='USB-B',unique=''),sort_keys=True,separators=(',',':'))
            expected=json.dumps(dict(size=4096,serial='USB-A',unique=''),sort_keys=True,separators=(',',':'))
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'),\
                 patch.object(windows_ddrescue,'source_identity',return_value=actual),\
                 patch('src.services.image_rescue.subprocess.Popen') as popen:
                ImageWorker(DEVICE,Path(temp)/'a.img',4096,state,expected_identity=expected)._run_windows()
            popen.assert_not_called()
            self.assertEqual(state['state'],'error')
            self.assertIn('disk has changed',state['error'])

    def test_failed_get_disk_does_not_claim_verified_capacity(self):
        disk=dict(DeviceID=DEVICE,Size='8000000000',DiskSize='',Partitions=[])
        result=windows_inventory(run=lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps([disk])))
        self.assertEqual(result[0]['size'],8000000000)
        self.assertFalse(result[0]['imaging_size_verified'])

    def test_worker_identifies_real_size_mismatch_without_starting_ddrescue(self):
        with tempfile.TemporaryDirectory() as temp:
            state={}
            identity=json.dumps(dict(size=8004304896,serial='ABC',unique=''),sort_keys=True,separators=(',',':'))
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'),\
                 patch.object(windows_ddrescue,'source_identity',return_value=identity),\
                 patch('src.services.image_rescue.subprocess.Popen') as popen:
                ImageWorker(DEVICE,Path(temp)/'a.img',8000000000,state)._run_windows()
            popen.assert_not_called()
            self.assertEqual(state['state'],'error')
            self.assertIn('Refresh the device list',state['error'])

class WindowsVolumeRouting(unittest.TestCase):
    def test_existing_partition_includes_raw_volume_alias(self):
        table=[dict(Offset=str(2048*512),Size=str(12340*512),Type='Basic',Letter='F')]
        partitions=existing_windows_partitions(DEVICE,source_bytes=20000*512,
            run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(table)))
        self.assertEqual(partitions[0].volume_path,VOLUME)
        self.assertEqual(partitions[0].origin,'existing')

    def test_letter_must_still_map_to_original_disk_offset_and_size(self):
        candidate=PartitionCandidate(2048,12340,'FAT32',origin='existing',volume_path=VOLUME)
        def run_with(disk=3, offset=2048*512, size=12340*512):
            return lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(
                dict(Disk=disk,Offset=offset,Size=size)))
        self.assertTrue(verify_windows_partition_volume(DEVICE,candidate,run=run_with()))
        self.assertFalse(verify_windows_partition_volume(DEVICE,candidate,run=run_with(disk=0)))
        self.assertFalse(verify_windows_partition_volume(DEVICE,candidate,run=run_with(offset=0)))
        self.assertFalse(verify_windows_partition_volume(DEVICE,candidate,run=run_with(size=5000)))

    def test_controller_uses_volume_only_after_verification(self):
        class Win:
            name='win32'
            def devices(self):return []
        controller=Controller(demo=True, desktop=True,platform=Win())
        candidate=PartitionCandidate(2048,12340,'FAT32',origin='existing',volume_path=VOLUME)
        controller.partition_selected=candidate
        controller.partition_source={'is_image':False}
        try:
            with patch('src.services.partition.verify_windows_partition_volume',return_value=True),\
                 patch('src.services.partition.sys.platform','win32'),\
                 patch('src.infrastructure.windows_raw.require_admin'),\
                 patch('src.services.partition.find_tsk_binary',return_value='fls.exe'):
                controller._partition_volume_checked=False
                cmd=controller.partition_builder('scan',DEVICE)
                self.assertEqual(cmd[-2:],[ '-l',VOLUME])
                self.assertEqual(cmd[cmd.index('-o')+1],'0')
            with patch('src.services.partition.verify_windows_partition_volume',return_value=False),\
                 patch('src.services.partition.sys.platform','win32'),\
                 patch('src.infrastructure.windows_raw.require_admin'),\
                 patch('src.services.partition.find_tsk_binary',return_value='fls.exe'):
                controller._partition_volume_checked=False
                cmd=controller.partition_builder('scan',DEVICE)
                self.assertEqual(cmd[-1],DEVICE)
                self.assertEqual(cmd[cmd.index('-o')+1],'2048')
        finally:
            controller.close()

if __name__=='__main__': unittest.main()
