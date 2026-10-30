"""Protect recovery from /dev/nvme0n1 to any filesystem on its partitions."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services import signature
from src.controllers.controller import Controller


class LinuxDestinationSafety(unittest.TestCase):
    def setUp(self):
        # Keep fake lsblk topology independent of the machine running tests.
        # Kernel/sysfs topology has its own dedicated regression tests.
        sysfs = tempfile.TemporaryDirectory()
        self.addCleanup(sysfs.cleanup)
        def path(value):
            return (Path(sysfs.name) / str(value).lstrip('/')
                    if str(value).startswith('/sys/') else Path(value))
        patcher = patch.object(signature, 'Path', side_effect=path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, findmnt_source='/dev/nvme0n1p2'):
        parents = {
            '/dev/nvme0n1': '', '/dev/nvme0n1p1': '/dev/nvme0n1\n', '/dev/nvme0n1p2': '/dev/nvme0n1\n',
            '/dev/sdb': '', '/dev/sdb1': '/dev/sdb\n', '/dev/sdc': '',
        }
        def fake_run(argv, **kw):
            if argv[0] == 'findmnt':
                return subprocess.CompletedProcess(argv, 0, findmnt_source+'\n', '')
            if argv[0] == 'lsblk':
                return subprocess.CompletedProcess(argv, 0, parents[argv[-1]], '')
            raise AssertionError(argv)
        return fake_run

    def test_same_nvme_disk_is_rejected_for_whole_disk_source(self):
        with patch.object(signature.subprocess, 'run',side_effect=self._run()), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p2'):
            with self.assertRaisesRegex(ValueError,'source disk'):
                signature.ensure_different_disk('/dev/nvme0n1','/home/user/Documents')

    def test_same_nvme_disk_rejected_for_other_system_partition(self):
        with patch.object(signature.subprocess, 'run',side_effect=self._run()), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p2'):
            with self.assertRaisesRegex(ValueError,'source disk'):
                signature.ensure_different_disk('/dev/nvme0n1p1','/home/user/Documents')

    def test_stat_identity_catches_misleading_findmnt_source(self):
        with patch.object(signature.subprocess, 'run',side_effect=self._run('/dev/sdb1')), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p2'):
            with self.assertRaisesRegex(ValueError,'source disk'):
                signature.ensure_different_disk('/dev/nvme0n1','/home/user/Documents')

    def test_unrelated_destination_with_alias_disagreement_is_not_blocked(self):
        with patch.object(signature.subprocess, 'run',side_effect=self._run('/dev/sdb1')), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p2'):
            signature.ensure_different_disk('/dev/sdc','/home/user/Documents')

    def test_unverifiable_stat_identity_is_not_automatically_blocked(self):
        with patch.object(signature.subprocess, 'run',side_effect=self._run('/dev/sdb1')), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None):
            signature.ensure_different_disk('/dev/nvme0n1','/home/user/Documents')

    def test_other_disk_allowed_only_if_both_mappings_agree(self):
        with patch.object(signature.subprocess, 'run',side_effect=self._run('/dev/sdb1')), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/sdb1'):
            signature.ensure_different_disk('/dev/nvme0n1','/home/user/Documents')

    def test_stat_linux_mount_lookup_uses_kernel_filesystem_id(self):
        with patch.object(signature.os, 'stat', return_value=SimpleNamespace(st_dev=os.makedev(259, 2))), \
             patch.object(signature, 'Path') as paths:
            link = paths.return_value.__truediv__.return_value
            link.is_dir.return_value = True
            link.resolve.return_value.name = 'nvme0n1p2'
            self.assertEqual(signature._mounted_linux_block_device('/home/user/Documents'),
                             '/dev/nvme0n1p2')
            paths.assert_called_once_with('/sys/dev/block')

    def test_photo_destination_is_checked_before_green_check(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = Controller(demo=True, desktop=True)
            try:
                ui.devices=[{'path':'/dev/nvme0n1','label':'System','is_system':True,
                             'partition_disk':True,'size':500_000_000_000}]
                ui.selection=0
                ui.demo=False  # Exercise physical-disk checks without opening a real device.
                with patch('src.services.signature.validate_destination',
                           side_effect=ValueError('Destination is on the source disk')) as validate:
                    with self.assertRaisesRegex(ValueError,'source disk'):
                        ui.set_signature_destination(folder)
                self.assertEqual(ui.signature_destination, str(Path(folder).resolve()))
                validate.assert_called_once()
            finally:
                ui.close()


if __name__ == '__main__':
    unittest.main()
