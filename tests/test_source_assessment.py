"""Guided source assessment & mounted ISO regressions."""
import unittest
from unittest.mock import patch
from src.services.source_assessment import assess_source
from src.infrastructure import linux_devices
from src.app import UI

class SourceAssessmentTests(unittest.TestCase):
    def test_ntfs_volume_recommends_metadata_and_disallows_partition_search(self):
        data=dict(path='/dev/sdb1',fs='NTFS',supported=True,partition_disk=False,mounted=True)
        a=assess_source(data)
        self.assertEqual([m.key for m in a.methods],['metadata','signature'])
        self.assertTrue(a.methods[0].recommended)
        self.assertIn('unmount',a.mount_message)

    def test_iso9660_and_hybrid_iso_do_not_offer_testdisk(self):
        for disk in [dict(fs='ISO9660', partition_disk=False),
                     dict(fs='DISK',partition_disk=True,child_filesystems=['ISO9660'])]:
            with self.subTest(disk=disk):
                a=assess_source(disk)
                self.assertEqual([m.key for m in a.methods], ['signature'])
                self.assertIn('boot',a.description.lower())
                self.assertFalse(assess_source(disk,desktop=False).methods)

    def test_unknown_unpartitioned_disk_recommends_testdisk(self):
        a=assess_source(dict(fs='UNKNOWN',partition_disk=True,supported=False))
        self.assertEqual([m.key for m in a.methods],['partition','signature'])

    def test_disk_image_keeps_both_file_and_partition_routes(self):
        a=assess_source(dict(fs='IMAGE',is_image=True,supported=True))
        self.assertEqual([m.key for m in a.methods],['metadata','partition','signature'])

    def test_iso_superfloppy_visible_but_not_falsely_marked_as_missing_partitions(self):
        tree=[dict(name='sdb',path='/dev/sdb',type='disk',fstype='iso9660',
                   size=2200000000,tran='usb',mountpoints=['/media/pi/BOOT'],children=[])]
        devices=linux_devices.detect_devices(lambda:tree)
        self.assertEqual(len(devices),1)
        self.assertEqual(devices[0]['fs'],'ISO9660')
        self.assertFalse(devices[0]['partition_disk'])
        self.assertTrue(devices[0]['mounted'])
        self.assertEqual([m.key for m in assess_source(devices[0]).methods],['signature'])

    def test_iso_child_prevents_partition_search_in_hybrid(self):
        tree=[dict(name='sdc',path='/dev/sdc',type='disk',fstype=None,size=2200000000,
                   tran='usb',mountpoints=[],children=[dict(path='/dev/sdc1',type='part',
                   fstype='iso9660',name='sdc1',size=1500000,mountpoints=['/media/pi/ISO'])])]
        d=linux_devices.detect_devices(lambda:tree)
        self.assertEqual(len(d),2)
        self.assertTrue(d[0]['partition_disk'])
        self.assertIn('ISO9660',d[0]['child_filesystems'])
        self.assertEqual([m.key for m in assess_source(d[0]).methods],['signature'])

    def test_iso_hybrid_parent_fs_without_child_iso_still_detected(self):
        tree=[dict(name='sdd',path='/dev/sdd',type='disk',fstype='iso9660',size=3400000000,
                   tran='usb',mountpoints=[],children=[dict(path='/dev/sdd1',type='part',
                   fstype='vfat',name='sdd1',size=30000000,mountpoints=[])])]
        selected=linux_devices.detect_devices(lambda:tree)[0]
        self.assertEqual(selected['fs'],'DISK')
        self.assertIn('ISO9660',selected['child_filesystems'])
        self.assertNotIn('partition',[m.key for m in assess_source(selected).methods])

    def test_mount_helper_called_without_forcing(self):
        tree=[dict(name='sdb',path='/dev/sdb',type='disk',fstype='iso9660',size=2048,
                   tran='usb',mountpoints=['/media/pi/CD'],children=[])]
        devices=linux_devices.detect_devices(lambda:tree)
        with patch.object(linux_devices.shutil,'which',return_value='/usr/bin/udisksctl'), \
             patch.object(linux_devices.subprocess,'run',return_value=type('R',(),{'returncode':0})()) as run:
            linux_devices.unmount_volume('/dev/sdb',lambda:devices, lambda *args: [])
            self.assertEqual(run.call_args.args[0],['/usr/bin/udisksctl','unmount','--block-device','/dev/sdb','--no-user-interaction'])

    def test_controller_disallows_invalid_method_on_iso(self):
        ui=UI(demo=True,desktop=True)
        try:
            ui.devices=[dict(path='/dev/sdb',fs='ISO9660',supported=False,partition_disk=False,label='Boot',size=1)]
            ui.selection=0
            ui.stage='desktop_methods'
            with self.assertRaisesRegex(ValueError,'does not apply'):
                ui.desktop_select_method('partition')
            ui.desktop_select_method('signature')
            self.assertEqual(ui.stage,'signature_setup')
        finally:ui.close()

if __name__=='__main__':unittest.main()

class AlreadyPartitionedTests(unittest.TestCase):
    def test_whole_disk_with_existing_partition_is_not_assumed_damaged(self):
        a=assess_source({'fs':'DISK','supported':False,'partition_disk':True,
                         'child_filesystems':('VFAT','NTFS')})
        self.assertEqual(a.headline,'Whole disk selected')
        self.assertFalse(a.methods[0].recommended)
        self.assertIn('already visible',a.description)
