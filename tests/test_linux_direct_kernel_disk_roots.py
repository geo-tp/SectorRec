"""Linux physical disk identity: kernel sysfs leaf and Btrfs subvolume cases.

No real device is opened or written by these tests.
"""
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services import signature


class KernelLeafTests(unittest.TestCase):
    def check(self, resolved, *, name='nvme0n1', partition=False, expected=None):
        real_path = Path
        block = MagicMock()
        block.is_dir.return_value = True
        block.resolve.return_value = real_path(resolved)
        paths = {'slaves': MagicMock(), 'partition': MagicMock(), 'dev': MagicMock()}
        paths['slaves'].iterdir.return_value = []
        paths['partition'].is_file.return_value = partition
        paths['dev'].read_text.return_value = '259:0\n'
        block.__truediv__.side_effect = lambda k: paths[k]
        root = MagicMock()
        root.__truediv__.return_value = block

        def make_path(arg):
            return root if str(arg) == '/sys/class/block' else real_path(arg)

        with patch.object(signature, 'Path', side_effect=make_path), \
             patch.object(signature, '_base_device', side_effect=AssertionError('Do not consult lsblk')):
            self.assertEqual(signature._physical_disk_roots('/dev/' + name), expected)

    def test_nvme_whole_disk_confirmed_directly_by_kernel(self):
        self.check('/sys/devices/pci0000:00/0000:00:17.0/nvme/nvme0/nvme0n1',
                   expected=frozenset({'/dev/nvme0n1'}))

    def test_sd_whole_disk_confirmed_directly_by_kernel(self):
        self.check('/sys/devices/platform/mmc_host/mmc0/mmc0:0001/block/mmcblk0',
                   name='mmcblk0', expected=frozenset({'/dev/mmcblk0'}))

    def test_usb_disk_confirmed_directly_by_kernel(self):
        self.check('/sys/devices/pci0000:00/usb2/block/sdb',
                   name='sdb', expected=frozenset({'/dev/sdb'}))

    def test_virtual_block_disk_is_not_a_physical_destination(self):
        self.check('/sys/devices/virtual/block/ram0', name='ram0', expected=None)


class BtrfsSysfsTests(unittest.TestCase):
    uuid = '12345678-1234-1234-1234-123456789abc'

    def check(self, names, expected):
        real_path = Path
        root = MagicMock()
        folder = MagicMock()
        folder.is_dir.return_value = True
        entries = []
        for name in names:
            link = MagicMock()
            link.name = name
            link.is_symlink.return_value = True
            entries.append(link)
        folder.iterdir.return_value = entries
        root.__truediv__.return_value.__truediv__.return_value = folder
        def make_path(arg):
            return root if str(arg) == '/sys/fs/btrfs' else real_path(arg)
        def fake_findmnt(args, **kwargs):
            self.assertEqual(args[:4], ['findmnt', '-n', '-o', 'UUID'])
            return subprocess.CompletedProcess(args, 0, self.uuid + '\n', '')
        with patch.object(signature, 'Path', side_effect=make_path), \
             patch.object(signature.subprocess, 'run', side_effect=fake_findmnt):
            self.assertEqual(signature._kernel_btrfs_single_device('/home/user', '/dev/nvme0n1p3'), expected)

    def test_one_device_matches_mount_source(self):
        self.check(['nvme0n1p3'], True)

    def test_multiple_devices_are_not_approved(self):
        self.check(['nvme0n1p3', 'sdb1'], False)

    def test_wrong_source_is_not_approved(self):
        self.check(['mmcblk0p1'], False)


if __name__ == '__main__':
    unittest.main()
