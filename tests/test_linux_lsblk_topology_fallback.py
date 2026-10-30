"""No raw disk access: realistic lsblk JSON tree when sysfs lookup fails."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.services import signature


class LsblkTopologyTests(unittest.TestCase):
    def _resolve(self, path, tree):
        def fake_run(args, **_kwargs):
            if '--json' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({'blockdevices': tree}), '')
            return subprocess.CompletedProcess(args, 1, '', 'sysfs unavailable')
        # This suite specifically tests the lsblk fallback when sysfs is
        # unavailable.  Do not accidentally consult the host machine's real
        # /sys/class/block: on a developer PC /dev/sdb may actually exist,
        # which correctly overrides the mocked lsblk tree in production.
        real_path = Path
        with tempfile.TemporaryDirectory() as tempdir:
            isolated_sysfs = real_path(tempdir) / 'sys' / 'class' / 'block'

            def fake_path(value):
                if str(value) == '/sys/class/block':
                    return isolated_sysfs
                return real_path(value)

            with patch.object(signature, 'Path', side_effect=fake_path), \
                 patch.object(signature, '_base_device', return_value=None), \
                 patch.object(signature.subprocess, 'run', side_effect=fake_run):
                return signature._physical_disk_roots(path)

    def test_usb_partition(self):
        tree = [{'path':'/dev/sdb','type':'disk','children':[
                 {'path':'/dev/sdb1','type':'part'}]}]
        self.assertEqual(self._resolve('/dev/sdb1', tree), frozenset({'/dev/sdb'}))

    def test_sd_mmc_partition(self):
        tree = [{'path':'/dev/mmcblk0','type':'disk','children':[
                 {'path':'/dev/mmcblk0p1','type':'part'}]}]
        self.assertEqual(self._resolve('/dev/mmcblk0p1', tree), frozenset({'/dev/mmcblk0'}))

    def test_lvm_spanning_two_disks_cannot_be_approved_by_lsblk_fallback(self):
        lvm={'path':'/dev/mapper/vg-home','type':'lvm'}
        tree = [
            {'path':'/dev/nvme0n1','type':'disk','children':[
                {'path':'/dev/nvme0n1p3','type':'part','children':[lvm]}]},
            {'path':'/dev/sdb','type':'disk','children':[
                {'path':'/dev/sdb2','type':'part','children':[lvm]}]},
        ]
        # Device-mapper aliases are resolved to /dev/dm-* on actual Linux.
        with patch.object(signature.os.path, 'realpath', side_effect=lambda x:
                          '/dev/dm-1' if str(x)=='/dev/mapper/vg-home' else str(x)):
            self.assertIsNone(self._resolve('/dev/dm-1', tree))

    def test_virtual_loop_cannot_pretend_to_be_physical(self):
        tree = [{'path':'/dev/loop0','type':'loop'}]
        self.assertIsNone(self._resolve('/dev/loop0', tree))

    def test_missing_block_path_not_authorized(self):
        tree = [{'path':'/dev/sda','type':'disk'}]
        self.assertIsNone(self._resolve('/dev/sdb', tree))

if __name__ == '__main__':
    unittest.main()
