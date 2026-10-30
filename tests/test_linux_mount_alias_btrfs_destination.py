"""Regression tests for real-world Linux destination identity corner cases.

All block data are simulated; never opens or writes any disk device.
"""
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services import signature


class MountedAliasAndBtrfsTests(unittest.TestCase):
    source = '/dev/sdc1'
    destination = '/home/user/Documents'
    btrfs_uuid = '12345678-1234-1234-1234-123456789abc'

    @staticmethod
    def _roots(value):
        return {
            '/dev/sdc1': frozenset({'/dev/sdc'}),
            '/dev/sdc': frozenset({'/dev/sdc'}),
            '/dev/nvme0n1p3': frozenset({'/dev/nvme0n1'}),
            '/dev/nvme0n1': frozenset({'/dev/nvme0n1'}),
            '/dev/sdb1': frozenset({'/dev/sdb'}),
            '/dev/sdb': frozenset({'/dev/sdb'}),
        }.get(str(value))

    def test_mounted_btrfs_documents_on_nvme_is_valid_destination_for_usb(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None), \
             patch.object(signature, '_mount_device_from_kernel_number', return_value=None), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=frozenset({'/dev/nvme0n1'})), \
             patch.object(signature.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/dev/root[/@home]\n', '')):
            signature.ensure_different_disk(self.source, self.destination)

    def test_btrfs_on_source_disk_is_rejected_even_with_unmapped_mount_alias(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=frozenset({'/dev/sdc'})), \
             patch.object(signature.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/dev/root[/@home]\n', '')):
            with self.assertRaisesRegex(ValueError, 'source disk'):
                signature.ensure_different_disk(self.source, self.destination)

    def test_btrfs_kernel_disagreement_does_not_block_if_both_different(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=frozenset({'/dev/nvme0n1'})), \
             patch.object(signature.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/dev/sdb1\n', '')):
            signature.ensure_different_disk(self.source, self.destination)

    def test_kernel_mount_major_minor_resolves_missing_dev_root_alias(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p3'), \
             patch.object(signature, '_mount_device_from_kernel_number', return_value='/dev/nvme0n1p3'), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=None), \
             patch.object(signature.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/dev/root\n', '')):
            signature.ensure_different_disk(self.source, self.destination)

    def test_mount_number_conflict_does_not_block_unrelated_disk(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p3'), \
             patch.object(signature, '_mount_device_from_kernel_number', return_value='/dev/nvme0n1p3'), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=None), \
             patch.object(signature.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '/dev/sdb1\n', '')):
            signature.ensure_different_disk(self.source, self.destination)

    def test_ambiguous_mount_no_longer_auto_blocks(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None), \
             patch.object(signature, '_mount_device_from_kernel_number', return_value=None), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=None), \
             patch.object(signature.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'overlay\n', '')):
            signature.ensure_different_disk(self.source, self.destination)

    def test_mount_number_requires_stat_device_match(self):
        stat_dev = os.makedev(259, 3)
        def fake_run(args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, '259:3\n', '')
        with patch.object(signature.subprocess, 'run', side_effect=fake_run), \
             patch.object(signature.os, 'stat', return_value=SimpleNamespace(st_dev=stat_dev)), \
             patch.object(signature.Path, 'resolve') as resolve, \
             patch.object(signature.Path, 'is_dir', return_value=True):
            resolve.return_value = Path('/sys/devices/pci0000:00/block/nvme0n1/nvme0n1p3')
            self.assertEqual(signature._mount_device_from_kernel_number(self.destination), '/dev/nvme0n1p3')
        with patch.object(signature.subprocess, 'run', side_effect=fake_run), \
             patch.object(signature.os, 'stat', return_value=SimpleNamespace(st_dev=os.makedev(259, 4))):
            self.assertIsNone(signature._mount_device_from_kernel_number(self.destination))

    def test_kernel_btrfs_resolver_requires_one_real_member(self):
        # An unrecognized filesystem identifier cannot authorize a destination.
        # Real resolver cases (UUID missing/multi-device/stat mismatch) are
        # tested without mocking the resolver itself in test_linux_btrfs_real_lookups.
        with patch.object(signature.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, 'ext4 259:3 /dev/nvme0n1p3\n', '')):
            self.assertIsNone(signature._kernel_btrfs_destination_roots(self.destination))


if __name__ == '__main__':
    unittest.main()
