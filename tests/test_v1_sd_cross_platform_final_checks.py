"""Targeted 1.0 pre-Windows retest regressions; no real disks are touched."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.platform_disks import windows_inventory, windows_transport
from src.models.core import Catalog, ScanWorker, MAX_RECORDS_DESKTOP
from src.services import signature
from src.views.desktop_native import DesktopWindow


class SDWindowsBadgeTests(unittest.TestCase):
    def test_empty_bus_never_creates_an_empty_badge(self):
        self.assertEqual(windows_transport('', ''), 'DISK')
        self.assertEqual(windows_transport(None, 'SD\\DISK&VEN_GENERIC'), 'MMC')
        self.assertEqual(windows_transport('SCSI', 'SDSTOR\\DISK&VEN_GENERIC'), 'MMC')
        self.assertEqual(windows_transport('MMC', ''), 'MMC')
        self.assertEqual(windows_transport('SCSI', 'SCSI\\DISK', 'SD'), 'MMC')
        self.assertEqual(windows_transport('USB', 'USBSTOR\\DISK'), 'USB')
        self.assertEqual(windows_transport('SCSI', 'SCSI\\DISK'), 'SCSI')

    def test_native_reader_shows_sd_icon_and_mmc_badge(self):
        disks = [{'DeviceID': '\\\\.\\PhysicalDrive4', 'DiskSize': '16000000000',
                  'Model': 'Generic SD Reader', 'DiskPnP': 'SD\\DISK&VEN_GENERIC',
                  'InterfaceType': '', 'NativeBus': 'SD', 'Partitions': []}]
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps(disks), '')
        inv = windows_inventory(run=fake_run)
        self.assertEqual(inv[0]['transport'], 'MMC')
        self.assertEqual(DesktopWindow.device_icon_type(inv[0]), 'sd')
        self.assertEqual(DesktopWindow.device_icon_type({'transport': 'MMC'}), 'sd')


    @unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'Tk display required')
    def test_empty_windows_transport_has_visible_fallback_and_whole_disk_caption(self):
        import tkinter as tk
        from src.app import UI
        ui = UI(demo=True, desktop=True)
        window = DesktopWindow(ui)
        try:
            ui.devices = [{'path': r'\\.\PhysicalDrive4', 'label': 'SD Reader  [WHOLE DISK]',
                           'size': 16000000000, 'fs': 'DISK', 'transport': '',
                           'partition_disk': True, 'mounted': False}]
            ui.selection = 0
            ui.stage = 'devices'
            window.show(force=True)
            row = window._device_rows[0][0]
            def labels(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Label):
                        yield child
                    yield from labels(child)
            rendered = list(labels(row))
            badges = [str(label.cget('text')) for label in rendered if getattr(label, '_sector_badge', False)]
            texts = [str(label.cget('text')) for label in rendered]
            self.assertEqual(badges, ['14.9 GB', 'WHOLE DISK', 'DISK'])
            self.assertIn('Whole disk · partition search or file-signature recovery', texts)
            self.assertNotIn('', badges)
        finally:
            ui.stage = 'devices'
            window.close()
            ui.close()


class LiveMetadataPreviewTests(unittest.TestCase):
    def test_98_discoveries_are_visible_before_worker_finishes(self):
        with tempfile.TemporaryDirectory() as work:
            cat = Catalog(Path(work) / 'catalog.sqlite')
            try:
                status = {}
                scan = ScanWorker(cat, '/dev/fake', status, max_records=MAX_RECORDS_DESKTOP)
                self.assertEqual(scan.batch_size, 500)  # Bulk efficiency unchanged.
                for i in range(98):
                    scan.accept((str(200+i), f'recovered_{i}.jpg', 512, 'PHOTOS'))
                self.assertEqual(status['count'], 98)
                found, rows = cat.live_page()
                self.assertGreaterEqual(found, 75)
                self.assertEqual(found, 75)
                self.assertIn('recovered_0.jpg', {row[1] for row in rows})
                scan.flush()
                self.assertEqual(cat.live_page()[0], 98)
            finally:
                cat.close()

    def test_cardputer_batching_still_unmodified(self):
        with tempfile.TemporaryDirectory() as work:
            cat = Catalog(Path(work) / 'catalog.sqlite')
            try:
                scan = ScanWorker(cat, '/dev/fake', {}, max_records=100_000)
                self.assertEqual(scan.batch_size, 100)
                for i in range(98):
                    scan.accept((str(400+i), f'file_{i}.jpg', 100, 'PHOTOS'))
                self.assertEqual(cat.live_page()[0], 0)
                scan.flush()
                self.assertEqual(cat.live_page()[0], 98)
            finally:
                cat.close()


class LinuxSDDestinationsTests(unittest.TestCase):
    @staticmethod
    def roots(dev):
        return {
            '/dev/mmcblk0': frozenset({'/dev/mmcblk0'}),
            '/dev/mmcblk0p1': frozenset({'/dev/mmcblk0'}),
            '/dev/mmcblk0p2': frozenset({'/dev/mmcblk0'}),
            '/dev/nvme0n1p3': frozenset({'/dev/nvme0n1'}),
            '/dev/sdb1': frozenset({'/dev/sdb'}),
        }.get(dev)

    def check(self, destination_device):
        def proc(argv, **kwargs):
            self.assertEqual(argv[0], 'findmnt')
            return subprocess.CompletedProcess(argv, 0, destination_device + '\n', '')
        with patch.object(signature.sys, 'platform', 'linux'), \
             patch.object(signature, '_physical_disk_roots', side_effect=self.roots), \
             patch.object(signature, '_mounted_linux_block_device', return_value=destination_device), \
             patch.object(signature.subprocess, 'run', side_effect=proc):
            signature.ensure_different_disk('/dev/mmcblk0p1', '/home/user/Recovered')

    def test_card_to_os_disk_permitted(self):
        self.check('/dev/nvme0n1p3')

    def test_card_to_secondary_hdd_permitted(self):
        self.check('/dev/sdb1')

    def test_card_to_another_partition_of_same_sd_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source disk'):
            self.check('/dev/mmcblk0p2')

    def test_sysfs_parent_for_card_does_not_depend_on_lsblk_pkname(self):
        with tempfile.TemporaryDirectory() as work:
            tmp = Path(work)
            sys_root = tmp / 'sys' / 'class' / 'block'
            real_root = tmp / 'devices' / 'block' / 'mmcblk0'
            part = real_root / 'mmcblk0p1'
            part.mkdir(parents=True)
            (part / 'partition').write_text('1\n')
            (part / 'slaves').mkdir()
            (real_root / 'slaves').mkdir()
            sys_root.mkdir(parents=True)
            (sys_root / 'mmcblk0').symlink_to(real_root, target_is_directory=True)
            (sys_root / 'mmcblk0p1').symlink_to(part, target_is_directory=True)
            original_path = Path
            def path_alias(value):
                if str(value) == '/sys/class/block':
                    return sys_root
                return original_path(value)
            with patch.object(signature, 'Path', side_effect=path_alias), \
                 patch.object(signature, '_base_device', side_effect=lambda path: path) as base:
                self.assertEqual(signature._physical_disk_roots('/dev/mmcblk0p1'),
                                 frozenset({'/dev/mmcblk0'}))
                self.assertEqual(base.call_count, 1)  # Disk only; no PKNAME on partition.


if __name__ == '__main__':
    unittest.main()
