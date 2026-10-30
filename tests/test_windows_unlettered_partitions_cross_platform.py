"""Windows unlettered partitions remain in inventory and never turn into full-disk reads."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.infrastructure.platform_disks import windows_inventory
from src.infrastructure.linux_devices import detect_devices
from src.infrastructure.windows_raw import (windows_device_path, photorec_partition_source,
                                             raw_tsk_command, ensure_different_windows_disk)
from src.infrastructure.windows_ddrescue import imaging_disk
from src.services.signature import PhotoRecWorker
from src.services.source_assessment import assess_source
from src.views.desktop_native import imaging_option_available
from src.controllers.controller import Controller

DISK = r'\\.\PhysicalDrive8'


def inventory():
    return windows_inventory(run=lambda *_a, **_k: SimpleNamespace(
        returncode=0, stdout=json.dumps([{
            'DeviceID': DISK, 'DiskSize': str(500_000_000_000),
            'DiskSerial': 'EXTERNAL-8', 'DiskUnique': 'DISK-ID-8',
            'LogicalSector': '512', 'Model': 'External HDD',
            'PartitionStyle': 'GPT', 'Partitions': [
                {'Number': 1, 'Offset': 1048576, 'Size': 100 * 1024**2,
                 'Letter': '', 'Fs': 'FAT32', 'GptType': '{C12A7328-F81F-11D2-BA4B-00A0C93EC93B}'},
                {'Number': 2, 'Offset': 105906176, 'Size': 16 * 1024**2,
                 'Letter': '', 'Fs': '', 'GptType': '{E3C9E316-0B5C-4DB8-817D-F92DF00215AE}'},
                {'Number': 3, 'Offset': 122683392, 'Size': 465 * 1024**3,
                 'Letter': 'F', 'Fs': 'NTFS', 'Label': 'DATA'},
                {'Number': 4, 'Offset': 499000000000, 'Size': 523 * 1024**2,
                 'Letter': '', 'Fs': 'NTFS', 'GptType': '{DE94BBA4-06D1-4D40-A16A-BFD50179D6AC}'},
            ]}])))


def fresh(part, *, offset=None, disk=8):
    record = {'Disk': str(disk), 'Number': str(part['partition_number']),
              'Offset': str(part['partition_offset'] if offset is None else offset),
              'Size': str(part['size']), 'Style': 'GPT'}
    return lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=json.dumps(record))


class UnletteredInventoryTests(unittest.TestCase):
    def test_windows_four_partitions_evinces_no_letter_and_correct_roles(self):
        rows = inventory()
        self.assertEqual(len(rows), 5)  # parent + 4 children
        self.assertEqual([r['path'] for r in rows], [
            DISK, DISK+'#Partition1', DISK+'#Partition2', r'\\.\F:', DISK+'#Partition4'])
        self.assertEqual([r.get('partition_role') for r in rows[1:]],
                         ['EFI SYSTEM', 'MSR', '', 'RECOVERY'])
        self.assertTrue(rows[1]['supported'])
        self.assertFalse(rows[2]['supported'])
        self.assertTrue(rows[4]['supported'])
        self.assertTrue(assess_source(rows[1],desktop=True).allows('metadata'))
        self.assertTrue(imaging_option_available(rows[4], 'win32'))
        self.assertFalse(rows[1]['mounted'])

    def test_linux_unmounted_unknown_partitions_still_listed_swap_excluded(self):
        base = {'path': '/dev/sdb', 'name': 'sdb', 'type': 'disk', 'size': 500_000_000_000}
        base['children'] = [
            {'path': '/dev/sdb1', 'name': 'sdb1', 'type': 'part', 'size': 104857600,
             'fstype': 'vfat', 'mountpoints': [None]},
            {'path': '/dev/sdb2', 'name': 'sdb2', 'type': 'part', 'size': 16777216,
             'fstype': None, 'mountpoints': []},
            {'path': '/dev/sdb3', 'name': 'sdb3', 'type': 'part', 'size': 1_000_000,
             'fstype': 'swap', 'mountpoints': []},
        ]
        rows = detect_devices(tree_supplier=lambda: [base])
        self.assertEqual([x['path'] for x in rows], ['/dev/sdb', '/dev/sdb1', '/dev/sdb2'])
        self.assertEqual([x['supported'] for x in rows[1:]], [True, False])
        self.assertFalse(any(x['mounted'] for x in rows))

    def test_synthetic_path_is_only_internal_and_has_strict_syntax(self):
        self.assertTrue(windows_device_path(DISK+'#Partition1'))
        for value in (DISK+'#Partition0', DISK+'#Partition1\\path', DISK+'#Partition-1'):
            self.assertFalse(windows_device_path(value))

    def test_no_letter_partition_resolves_by_disk_number_and_partition_number(self):
        part = inventory()[1]
        calls = []
        def run(args, **kwargs):
            calls.append(args[-1])
            return fresh(part)(args, **kwargs)
        result = photorec_partition_source(part['path'], part, run=run)
        self.assertEqual(result, (DISK, 1, 'partition_gpt'))
        self.assertIn('Get-Partition -DiskNumber 8 -PartitionNumber 1', calls[0])
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            photorec_partition_source(part['path'], part, run=fresh(part, offset=999))
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            photorec_partition_source(part['path'], part, run=fresh(part, disk=9))
        with self.assertRaisesRegex(ValueError, 'not captured'):
            photorec_partition_source(part['path'], {})

    def test_photorec_never_receives_synthetic_source_or_whole_disk_selector(self):
        part = inventory()[4]
        job = PhotoRecWorker(part['path'], '/safe', {}, binary='photorec_win.exe', source_info=part)
        with patch('src.services.signature.sys.platform', 'win32'), \
             patch('src.infrastructure.windows_raw.require_admin'), \
             patch('src.infrastructure.windows_raw.photorec_partition_source',
                   return_value=(DISK, 4, 'partition_gpt')):
            cmd = job._commands('/safe/Session')
        self.assertEqual(cmd[-2], DISK)
        self.assertEqual(cmd[-1].split(',')[:2], ['partition_gpt', '4'])

    def test_tsk_never_receives_synthetic_source_or_reads_offset_zero(self):
        part = inventory()[1]
        with patch('src.infrastructure.windows_raw.require_admin'), \
             patch('src.infrastructure.image_backend.find_tsk_binary', return_value='fls.exe'), \
             patch('src.infrastructure.windows_raw.photorec_partition_source',
                   return_value=(DISK, 1, 'partition_gpt')):
            cmd = raw_tsk_command('scan', part['path'], source_info=part)
        self.assertEqual(cmd[-1], DISK)
        self.assertEqual(cmd[cmd.index('-o')+1], str(part['partition_offset'] // 512))
        self.assertEqual(cmd[cmd.index('-b')+1], '512')
        with patch('src.infrastructure.windows_raw.require_admin'), \
             patch('src.infrastructure.image_backend.find_tsk_binary', return_value='fls.exe'):
            with self.assertRaises(ValueError):
                raw_tsk_command('scan', part['path'])

    def test_imaging_uses_verified_partition_range_not_whole_disk(self):
        part = inventory()[4]
        with patch('src.infrastructure.windows_raw.photorec_partition_source',
                   return_value=(DISK, 4, 'partition_gpt')):
            disk, found = imaging_disk(part['path'], part)
        self.assertEqual(disk, DISK)
        self.assertEqual(found['offset'], part['partition_offset'])
        self.assertEqual(found['size'], part['size'])

    def test_destination_guard_on_unlettered_partition_uses_parent_disk(self):
        part = inventory()[1]
        with self.assertRaisesRegex(ValueError, 'source physical disk'):
            ensure_different_windows_disk(part['path'], 'D:/Safe',
                run=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout='8'))

    def test_controller_metadata_builder_retains_the_selected_partition(self):
        class Win:
            name='win32'
        ui = Controller(demo=True, desktop=True, platform=Win())
        try:
            part = inventory()[1]
            ui.devices = [part]
            ui.selection = 0
            builder = ui._metadata_command()
            with patch('src.infrastructure.windows_raw.require_admin'), \
                 patch('src.infrastructure.image_backend.find_tsk_binary', return_value='fls.exe'), \
                 patch('src.infrastructure.windows_raw.photorec_partition_source', return_value=(DISK, 1, 'partition_gpt')) as resolver:
                self.assertEqual(builder('scan', part['path'])[-1], DISK)
                self.assertEqual(builder('scan', part['path'])[-1], DISK)
                resolver.assert_called_once()
                self.assertEqual(resolver.call_args.args[1]['partition_number'], 1)
        finally:
            ui.close()

if __name__ == '__main__':
    unittest.main()
