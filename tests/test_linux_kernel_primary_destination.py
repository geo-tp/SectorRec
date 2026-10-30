"""Regression for a trusted kernel st_dev whose findmnt SOURCE alias is opaque.

These tests exercise ensure_different_disk, not merely stubbed preflight.
The real user's ext4 filesystem lives on LVM atop LUKS; a kernel-resolved
DM backing chain is authoritative even without a findmnt textual alias.
"""
import subprocess
import unittest
from unittest.mock import patch
from src.services import signature


class KernelFirstDestination(unittest.TestCase):
    source = '/dev/sdb'
    destination = '/home/user/Documents'
    physical = frozenset({'/dev/nvme0n1'})
    usb = frozenset({'/dev/sdb'})

    def _roots(self, path):
        return {'/dev/sdb': self.usb, '/dev/dm-2': self.physical,
                '/dev/nvme0n1': self.physical,
                '/dev/mapper/ubuntu--vg-ubuntu--lv': self.physical}.get(str(path))

    def _run_case(self, *, reported='/dev/root', actual='/dev/dm-2', source='/dev/sdb',
                  other_roots=None, findmnt_rc=0):
        def roots(path):
            if str(path) == '/dev/sda1' and other_roots is not None:
                return other_roots
            return self._roots(path)
        def run(cmd, **kwargs):
            if cmd[:4] == ['findmnt', '-n', '-o', 'SOURCE']:
                return subprocess.CompletedProcess(cmd, findmnt_rc, reported+'\n', '')
            raise AssertionError('Unexpected subprocess: '+repr(cmd))
        with patch.object(signature, '_physical_disk_roots', side_effect=roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=actual), \
             patch.object(signature, '_kernel_btrfs_destination_roots', return_value=None), \
             patch.object(signature, '_mount_device_from_kernel_number', return_value=None), \
             patch.object(signature, '_mount_source_corroborated', return_value=False), \
             patch.object(signature.subprocess, 'run', side_effect=run):
            signature.ensure_different_disk(source, self.destination)

    def test_lvm_ext4_to_home_known_kernel_device_and_opaque_alias(self):
        self._run_case()

    def test_lvm_ext4_to_home_findmnt_lookup_failed(self):
        self._run_case(findmnt_rc=1)

    def test_lvm_ext4_to_home_findmnt_valid(self):
        self._run_case(reported='/dev/mapper/ubuntu--vg-ubuntu--lv')

    def test_source_mmc_to_lvm_destination_accepted(self):
        with patch.object(self, '_roots', side_effect=lambda p: (
                frozenset({'/dev/mmcblk0'}) if str(p) == '/dev/mmcblk0'
                else self.physical if str(p) == '/dev/dm-2' else None)):
            self._run_case(source='/dev/mmcblk0')

    def test_destination_on_source_disk_refused_even_when_findmnt_unresolvable(self):
        with self.assertRaisesRegex(ValueError, 'source disk'):
            self._run_case(actual='/dev/sdb')

    def test_conflicting_findmnt_and_stat_mapping_does_not_block_unrelated(self):
        self._run_case(reported='/dev/sda1', other_roots=frozenset({'/dev/sda'}))

    def test_unverifiable_destination_not_automatically_blocked(self):
        self._run_case(actual=None)

    def test_lvm_destination_from_same_nvme_different_partition_refused(self):
        with self.assertRaisesRegex(ValueError, 'source disk'):
            self._run_case(source='/dev/nvme0n1')

if __name__ == '__main__':
    unittest.main()
