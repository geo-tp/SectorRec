"""Regression: Fedora-style Btrfs subvolume with no findmnt UUID output.

Earlier tests only mocked the successful Btrfs resolver, leaving the actual
resolver untested. No raw disk writes/mounting are done here.
"""
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services import signature


class _Member:
    def __init__(self, name):
        self.name = name
    def is_symlink(self):
        return True


class _Members:
    def __init__(self, names):
        self._names = names
    def is_dir(self):
        return True
    def iterdir(self):
        return [_Member(n) for n in self._names]


class _Filesystem:
    name = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    def __init__(self, devices):
        self.devices = _Members(devices)
    def __truediv__(self, other):
        assert other == 'devices'
        return self.devices


class _Registry:
    def __init__(self, devices):
        self.fs = _Filesystem(devices)
    def is_dir(self):
        return True
    def iterdir(self):
        return [self.fs]


class MissingUUIDDestinationTests(unittest.TestCase):
    dest = '/home/user/Documents'

    @staticmethod
    def _roots(device):
        return {'/dev/sdb': frozenset({'/dev/sdb'}),
                '/dev/sdb1': frozenset({'/dev/sdb'}),
                '/dev/nvme0n1p3': frozenset({'/dev/nvme0n1'}),
                '/dev/nvme0n1': frozenset({'/dev/nvme0n1'})}.get(device)

    def _check(self, devices, source='/dev/sdb', uuid='', stat_dev=None):
        real_path = signature.Path
        registry = _Registry(devices)
        def fake_path(path):
            if path == '/sys/fs/btrfs':
                return registry
            return real_path(path)
        def run(cmd, **_kwargs):
            if cmd[0] == 'findmnt':
                if cmd[3] == 'SOURCE':
                    text = '/dev/nvme0n1p3[/@home]\n'
                elif cmd[3] == 'FSTYPE,UUID,MAJ:MIN,SOURCE':
                    text = (f'btrfs {uuid} 0:55 /dev/nvme0n1p3[/@home]\n'
                            if uuid else 'btrfs 0:55 /dev/nvme0n1p3[/@home]\n')
                else:
                    raise AssertionError(cmd)
                return subprocess.CompletedProcess(cmd, 0, text, '')
            raise AssertionError(cmd)
        with patch.object(signature, 'Path', side_effect=fake_path), \
             patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None), \
             patch.object(signature, '_mount_device_from_kernel_number', return_value=None), \
             patch.object(signature, '_mount_source_corroborated', return_value=False), \
             patch.object(signature.subprocess, 'run', side_effect=run), \
             patch.object(signature.os, 'stat', return_value=SimpleNamespace(
                 st_dev=stat_dev if stat_dev is not None else os.makedev(0, 55))):
            signature.ensure_different_disk(source, self.dest)

    def test_sd_to_btrfs_home_allowed_without_uuid_column(self):
        self._check(['nvme0n1p3'])

    def test_usb_to_btrfs_home_allowed_when_uuid_column_is_dash(self):
        self._check(['nvme0n1p3'], uuid='-')

    def test_btrfs_home_allowed_with_valid_uuid_too(self):
        self._check(['nvme0n1p3'], uuid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    def test_multidevice_btrfs_unknown_is_not_automatic_block(self):
        self._check(['nvme0n1p3', 'sdb1'])

    def test_wrong_st_dev_does_not_imply_same_disk(self):
        self._check(['nvme0n1p3'], stat_dev=os.makedev(0, 99))

    def test_same_disk_stays_blocked(self):
        with self.assertRaisesRegex(ValueError, 'source disk'):
            self._check(['nvme0n1p3'], source='/dev/nvme0n1')

    def test_other_btrfs_member_does_not_imply_same_disk(self):
        self._check(['sdb1'])


if __name__ == '__main__':
    unittest.main()
