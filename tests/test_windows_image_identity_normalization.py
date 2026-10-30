"""Identity-snapshot consistency tests; no physical device ever opened."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.platform_disks import windows_inventory
from src.infrastructure import windows_ddrescue as bridge
from src.services.image_rescue import ImageWorker

DRIVE = r'\\.\PhysicalDrive1'


class WindowsImagingIdentityTests(unittest.TestCase):
    def test_inventory_and_worker_use_same_schema_and_case_variants_compare(self):
        data = [{
            'DeviceID': DRIVE, 'Model': 'USB Flash', 'Size': '1000', 'DiskSize': '1024',
            'DiskSerial': '  USB-SerIAL  ', 'DiskUnique': ' {ABcd-1234} ', 'Partitions': []
        }]
        info = windows_inventory(run=lambda *a, **k: SimpleNamespace(returncode=0,stdout=json.dumps(data)))[0]
        self.assertEqual(info['size'], 1024)
        second = bridge.canonical_source_identity('usb-serial', '{abcd-1234}', 1024)
        self.assertEqual(bridge.identity_difference(info['imaging_identity'], second), '')

    def test_power_shell_snapshot_schema_match(self):
        source = bridge.source_identity(
            DRIVE, run=lambda *a, **k: SimpleNamespace(returncode=0,stdout=json.dumps(
                {'Serial': 'usb-serial', 'Unique': '{ABCD-1234}', 'Size': '1024'})))
        initial = bridge.canonical_source_identity('USB-SERIAL ', '{abcd-1234}', 1024)
        self.assertEqual(bridge.identity_difference(initial, source), '')

    def test_serial_change_is_refused_even_if_size_matches(self):
        old = bridge.canonical_source_identity('USB-A', '', 1024)
        new = bridge.canonical_source_identity('USB-B', '', 1024)
        detail = bridge.identity_difference(old, new)
        self.assertIn('serial number', detail)
        self.assertNotIn('USB-A', detail)
        self.assertNotIn('USB-B', detail)
        with tempfile.TemporaryDirectory() as temp:
            status = {}
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'), \
                 patch.object(bridge, 'source_identity', return_value=new), \
                 patch('src.services.image_rescue.subprocess.Popen') as proc:
                ImageWorker(DRIVE, Path(temp) / 'test.img', 1024, status,
                            expected_identity=old)._run_windows()
            proc.assert_not_called()
            self.assertEqual(status['state'], 'error')
            self.assertIn('serial number', status['error'])

    def test_unique_id_change_or_missing_id_is_refused(self):
        first = bridge.canonical_source_identity('', 'USB-UID-A', 1024)
        other = bridge.canonical_source_identity('', 'USB-UID-B', 1024)
        self.assertIn('unique ID', bridge.identity_difference(first, other))
        self.assertIn('does not contain', bridge.identity_difference(first, None))
        self.assertIsNone(bridge.canonical_source_identity('', '', 1024))

    def test_size_difference_is_refused_and_reported_exactly(self):
        initial = bridge.canonical_source_identity('A', 'B', 1024)
        new = bridge.canonical_source_identity('A', 'B', 2048)
        self.assertIn('selected 1024 bytes, now 2048 bytes',
                      bridge.identity_difference(initial, new))


if __name__ == '__main__':
    unittest.main()
