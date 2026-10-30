"""No real raw-drive access; simulated Windows API/tool routing and guard checks."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.infrastructure.windows_raw import (windows_device_path, raw_tsk_command,
     ensure_different_windows_disk, require_admin)
from src.infrastructure.platform_disks import windows_inventory
from src.services.partition import partition_command, discover_command
from src.infrastructure.platforms import WindowsPlatform
from src.services.source_assessment import assess_source

D1=r'\\.\PhysicalDrive1'
D2=r'\\.\PhysicalDrive2'
V1=r'\\.\F:'

class WindowsRawTests(unittest.TestCase):
    def test_only_expected_device_paths(self):
        self.assertTrue(windows_device_path(D1))
        self.assertTrue(windows_device_path(V1))
        for text in ('C:/', r'\\.\PhysicalDrive1\x', r'\\.\PhysicalDrive-1',
                     r'\\.\GLOBALROOT\Device\Harddisk0', '/dev/sdb'):
            self.assertFalse(windows_device_path(text), text)

    def test_inventory_lists_disk_and_all_volumes(self):
        raw=[{'DeviceID':D1,'Model':'USB HDD','Manufacturer':'Acme','Size':500000000,
              'InterfaceType':'USB','Partitions':[{'Number':1,'Letter':'F',
                      'Size':250000000,'Fs':'NTFS','Label':'MYDISK'},
                        {'Number':2,'Offset':250000000,'Letter':'','Size':100000000,'Fs':'RAW'}]}]
        devices=windows_inventory(run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(raw)))
        self.assertEqual(len(devices),3)
        self.assertEqual(devices[0]['path'],D1)
        self.assertTrue(devices[0]['partition_disk'])
        self.assertEqual(devices[1]['path'],V1)
        self.assertTrue(devices[1]['supported'])
        self.assertEqual(devices[2]['path'], D1 + '#Partition2')
        self.assertFalse(devices[2]['has_drive_letter'])
        self.assertEqual(devices[2]['fs'], 'RAW')
        self.assertTrue(assess_source(devices[1]).allows('metadata'))
        self.assertTrue(assess_source(devices[0]).allows('partition'))
        self.assertTrue(WindowsPlatform.raw_drives)

    def test_tsk_raw_command_no_shell_or_write_flags(self):
        with patch('src.infrastructure.windows_raw.require_admin'), patch('src.infrastructure.image_backend.find_tsk_binary',side_effect=lambda name:name+'.exe'):
            self.assertEqual(raw_tsk_command('scan',V1),['fls.exe','-r','-d','-p','-l',V1])
            self.assertEqual(raw_tsk_command('recover',V1,'42'),['icat.exe','-r',V1,'42'])
            with self.assertRaises(ValueError):raw_tsk_command('recover',V1,'--write')
            with self.assertRaises(ValueError):raw_tsk_command('format',V1)

    def test_raw_partition_offset(self):
        with patch('src.services.partition.sys.platform','win32'),patch('src.infrastructure.windows_raw.require_admin'),patch('src.services.partition.find_tsk_binary',return_value='fls.exe'):
            result=partition_command('scan',D2,2048)
            self.assertEqual(result,['fls.exe','-o','2048','-r','-p','-l',D2])

    def test_windows_testdisk_raw_command(self):
        with patch('src.services.partition.sys.platform','win32'),patch('src.infrastructure.windows_raw.require_admin'),patch('src.services.partition.testdisk_binary',return_value='testdisk_win.exe'):
            self.assertEqual(discover_command(D1),['testdisk_win.exe','/log','/cmd',D1,'analyze,search'])

    def test_reject_same_physical_disk(self):
        def fake_run(cmd,**kw):
            if 'Get-Volume -FilePath' in cmd[-1]:return SimpleNamespace(returncode=0,stdout='1\n')
            return SimpleNamespace(returncode=0,stdout='1\n')
        with self.assertRaisesRegex(ValueError,'source physical disk'):
            ensure_different_windows_disk(D1,Path('/tmp/output'),run=fake_run)

    def test_allow_only_verified_different_disk(self):
        def fake_run(cmd,**kw):
            if 'Get-Volume -FilePath' in cmd[-1]:return SimpleNamespace(returncode=0,stdout='2\n')
            return SimpleNamespace(returncode=0,stdout='1\n')
        self.assertEqual(ensure_different_windows_disk(V1,'C:/Backup',run=fake_run),(1,2))

    def test_fail_closed_if_destination_unmappable(self):
        with self.assertRaisesRegex(ValueError,'destination physical disk'):
            ensure_different_windows_disk(D1,'C:/Backup',run=lambda *a,**k:SimpleNamespace(returncode=3,stdout=''))

    def test_admin_required_even_before_using_tool(self):
        with patch('src.infrastructure.windows_raw.sys.platform','win32'),patch('src.infrastructure.windows_raw.ctypes',create=True) as ctypes:
            ctypes.windll.shell32.IsUserAnAdmin.return_value=0
            with self.assertRaises(PermissionError):require_admin()

if __name__=='__main__':unittest.main()
