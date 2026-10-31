"""Windows PhotoRec partition regression (pure mocks; never reads raw storage)."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.windows_raw import photorec_partition_source
from src.services.signature import PhotoRecWorker, photorec_command


class WindowsPhotoRecPartitionTests(unittest.TestCase):
    def setUp(self):
        self.selected = {
            'path': r'\\.\G:',
            'disk_path': r'\\.\PhysicalDrive3',
            'partition_number': 1,
            'partition_offset': 1048576,
            'size': 128 * 1024 * 1024,
            'partition_style': 'MBR',
        }

    def fake_query(self, disk='3', number='1', offset='1048576', size=None, style='MBR'):
        record = {'Disk': disk, 'Number': number, 'Offset': offset,
                  'Size': str(size or self.selected['size']), 'Style': style}
        return lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(record))

    def test_partition_number_and_table_type_are_explicit(self):
        src, number, style = photorec_partition_source(r'\\.\G:', self.selected,
                                                        run=self.fake_query())
        self.assertEqual((src, number, style), (r'\\.\PhysicalDrive3', 1, 'partition_i386'))
        command = photorec_command('photorec.exe', src, '/out/recovery', partition=(style, number))
        self.assertEqual(command[-2], r'\\.\PhysicalDrive3')
        self.assertEqual(command[-1],
                         'partition_i386,1,fileopt,dovecot,disable,wholespace,search')

    def test_gpt_partitions_are_explicit(self):
        selected = {**self.selected, 'partition_number': 2, 'partition_style': 'GPT'}
        _, number, style = photorec_partition_source(r'\\.\G:', selected,
                          run=self.fake_query(number='2', style='GPT'))
        self.assertEqual((style, number), ('partition_gpt', 2))

    def test_reassigned_letter_or_repartition_refuses_to_scan(self):
        for changed in (dict(disk='4'), dict(number='2'), dict(offset='2097152'),
                        dict(size='8388608'), dict(style='GPT')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'identity changed'):
                photorec_partition_source(r'\\.\G:', self.selected, run=self.fake_query(**changed))

    def test_missing_identity_rejects_instead_of_scanning_whole_disk(self):
        with self.assertRaisesRegex(ValueError, 'not captured'):
            photorec_partition_source(r'\\.\G:', {})

    def test_worker_generates_physical_partition_command_only_on_windows(self):
        job = PhotoRecWorker(r'\\.\G:', '/safe/out', {}, binary='photorec.exe',
                             source_info=self.selected)
        with patch('src.services.signature.sys.platform', 'win32'), \
             patch('src.infrastructure.windows_raw.require_admin'), \
             patch('src.infrastructure.windows_raw.photorec_partition_source',
                   return_value=(r'\\.\PhysicalDrive3', 1, 'partition_i386')) as resolver:
            command = job._commands(Path('/safe/out/SectorRec_20261029'))
        resolver.assert_called_once()
        self.assertEqual(command[-2], r'\\.\PhysicalDrive3')
        self.assertEqual(command[-1], 'partition_i386,1,fileopt,dovecot,disable,wholespace,search')

    def test_whole_disk_retains_original_command(self):
        job = PhotoRecWorker(r'\\.\PhysicalDrive3', '/safe/out', {},
                             binary='photorec.exe', source_info={'disk_path':r'\\.\PhysicalDrive3'})
        with patch('src.services.signature.sys.platform', 'win32'), \
             patch('src.infrastructure.windows_raw.require_admin'), \
             patch('src.infrastructure.windows_raw.photorec_partition_source') as resolver:
            command = job._commands(Path('/safe/out/SectorRec_20261029'))
        resolver.assert_not_called()
        self.assertEqual(command[-2], r'\\.\PhysicalDrive3')
        self.assertEqual(command[-1], 'partition_none,fileopt,dovecot,disable,wholespace,search')

if __name__ == '__main__':
    unittest.main()
