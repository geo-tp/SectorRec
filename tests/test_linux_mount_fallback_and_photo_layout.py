"""Linux FUSE destination verification and compact system PhotoRec layout."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import signature
from src.views.desktop_native import DesktopWindow


class LinuxVirtualMountIdentityTests(unittest.TestCase):
    def setUp(self):
        self.folder_ctx = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder_ctx.cleanup)
        self.mount = Path(self.folder_ctx.name) / 'external disk'
        self.folder = self.mount / 'Recovered'
        self.folder.mkdir(parents=True)

    @staticmethod
    def _roots(path):
        path = str(path)
        if path in ('/dev/nvme0n1', '/dev/nvme0n1p3'):
            return frozenset({'/dev/nvme0n1'})
        if path in ('/dev/sdb', '/dev/sdb1'):
            return frozenset({'/dev/sdb'})
        return None

    def _run(self, *, source='/dev/sdb1', inventory='/dev/sdb1', fstype='fuseblk',
             total_devices=1, mountpoint=None):
        root = str(self.mount if mountpoint is None else mountpoint)
        def fake(cmd, **kwargs):
            if cmd[0] == 'findmnt' and '--json' not in cmd:
                return subprocess.CompletedProcess(cmd, 0, source + '\n', '')
            if cmd[0] == 'findmnt':
                body={'filesystems':[{'source':source,'target':root,'fstype':fstype}]}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(body), '')
            if cmd[0] == 'lsblk':
                body={'blockdevices':[{'path':'/dev/sdb', 'mountpoints':[None],
                       'children':[{'path':inventory,'mountpoints':[root]}]}]}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(body), '')
            if cmd[0] == 'btrfs':
                return subprocess.CompletedProcess(cmd, 0, f'Total devices {total_devices}\n', '')
            raise AssertionError(cmd)
        return fake

    def test_usb_fuse_mount_accepted_when_findmnt_and_lsblk_agree(self):
        with patch.object(signature, '_physical_disk_roots', side_effect=self._roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=None), \
             patch.object(signature.subprocess,'run', side_effect=self._run()):
            signature.ensure_different_disk('/dev/nvme0n1', self.folder)

    def test_source_disk_still_rejected_without_kernel_stat_mapping(self):
        with patch.object(signature,'_physical_disk_roots',side_effect=self._roots), \
             patch.object(signature,'_mounted_linux_block_device',return_value=None), \
             patch.object(signature.subprocess,'run',side_effect=self._run(source='/dev/nvme0n1p3')):
            with self.assertRaisesRegex(ValueError, 'source disk'):
                signature.ensure_different_disk('/dev/nvme0n1', self.folder)

    def test_unverified_usb_no_longer_auto_blocked(self):
        with patch.object(signature,'_physical_disk_roots',side_effect=self._roots), \
             patch.object(signature,'_mounted_linux_block_device',return_value=None), \
             patch.object(signature.subprocess,'run',side_effect=self._run(inventory='/dev/unknown')):
            signature.ensure_different_disk('/dev/nvme0n1', self.folder)

    def test_incorrect_mountpoint_no_longer_auto_blocked(self):
        with patch.object(signature,'_physical_disk_roots',side_effect=self._roots), \
             patch.object(signature,'_mounted_linux_block_device',return_value=None), \
             patch.object(signature.subprocess,'run',side_effect=self._run(mountpoint=self.folder_ctx.name + '/other')):
            signature.ensure_different_disk('/dev/nvme0n1', self.folder)

    def test_multidevice_btrfs_no_longer_auto_blocked(self):
        with patch.object(signature,'_physical_disk_roots',side_effect=self._roots), \
             patch.object(signature,'_mounted_linux_block_device',return_value=None), \
             patch.object(signature.subprocess,'run',side_effect=self._run(fstype='btrfs',total_devices=2)):
            signature.ensure_different_disk('/dev/nvme0n1', self.folder)

    def test_single_device_btrfs_mount_can_be_verified(self):
        with patch.object(signature,'_physical_disk_roots',side_effect=self._roots), \
             patch.object(signature,'_mounted_linux_block_device',return_value=None), \
             patch.object(signature.subprocess,'run',side_effect=self._run(fstype='btrfs',total_devices=1)):
            signature.ensure_different_disk('/dev/nvme0n1', self.folder)


class PhotoRecHintsTests(unittest.TestCase):
    def test_error_messages_are_actionable(self):
        same = DesktopWindow._explain_destination_error('Destination is on the source disk.')
        unknown = DesktopWindow._explain_destination_error('Destination physical disk cannot be verified.')
        self.assertIn('SAME PHYSICAL DISK', same)
        self.assertIn('USB drive', same)
        self.assertIn('USB drive', unknown)
        self.assertNotIn('Choose a local disk', unknown)

    def test_only_one_info_banner_on_system_sources(self):
        from src.controllers.controller import Controller
        ui = Controller(demo=True, desktop=True)
        try:
            ui.devices=[{'path':'/dev/nvme0n1', 'fs':'DISK','size':4000000,
                         'label':'OS', 'is_system':True,'partition_disk':True}]
            ui.selection=0
            ui.stage='signature_setup'
            window = DesktopWindow(ui)
            try:
                banners=[]
                with patch.object(window, '_info_banner', side_effect=lambda head, desc: banners.append(head)):
                    window.show_signature_setup()
                self.assertEqual(banners, ['SYSTEM RECOVERY · LIMITATIONS'])
                ui.devices[0]['is_system']=False
                banners.clear()
                with patch.object(window, '_info_banner', side_effect=lambda head, desc: banners.append(head)):
                    window.show_signature_setup()
                self.assertEqual(banners, ['ABOUT PHOTOREC'])
            finally:
                window.close()
        finally:
            ui.close()
