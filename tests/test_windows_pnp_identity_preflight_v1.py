"""Regression: USB serial fluctuations must not bypass physical-source safety.

Tests use mock Windows queries and temporary files. No disks are opened.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.platform_disks import windows_inventory
from src.infrastructure import windows_ddrescue as bridge
from src.services import image_rescue
from src.services.image_rescue import ImageWorker

DRIVE = r'\\.\PhysicalDrive1'
PNP = r'USBSTOR\DISK&VEN_EXAMPLE&PROD_CARD\ABC123&0'


def fingerprint(serial, unique='A1B2C3D4', pnp=PNP, size=8_000_000_000):
    return bridge.canonical_source_identity(serial, unique, size, pnp)


class WindowsPnPIdentityChecks(unittest.TestCase):
    def test_inventory_records_pnp_and_preserves_old_fields(self):
        disk = {'DeviceID': DRIVE, 'Model': 'Removable', 'DiskSize': '4096',
                'DiskSerial': 'first', 'DiskUnique': 'A1B2', 'DiskPnP': PNP,
                'Partitions': []}
        output = windows_inventory(run=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps([disk])))
        fields = json.loads(output[0]['imaging_identity'])
        self.assertEqual(fields['pnp'], PNP)
        self.assertEqual(fields['unique'], 'A1B2')
        self.assertEqual(fields['size'], 4096)

    def test_preflight_collects_same_independent_device_instance(self):
        seen = []
        def fake_run(cmd, **kwargs):
            seen.append(cmd[-1])
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {'Size': '4096', 'Serial': 'other', 'Unique': 'A1B2', 'PnP': PNP}))
        snapshot = bridge.source_identity(DRIVE, run=fake_run)
        self.assertEqual(json.loads(snapshot)['pnp'], PNP)
        self.assertIn('Win32_DiskDrive', seen[0])
        self.assertIn('Get-Disk', seen[0])

    def test_serial_variation_allowed_only_with_other_two_independent_ids(self):
        selected = fingerprint('garbled usb serial')
        observed = fingerprint('another usb serial')
        self.assertEqual(bridge.identity_difference(selected, observed), '')

    def test_changed_pnp_is_rejected_even_when_serial_and_unique_match(self):
        detail = bridge.identity_difference(fingerprint('SERIAL'),
                                            fingerprint('SERIAL', pnp=PNP+'2'))
        self.assertIn('PnP device identity:', detail)
        self.assertNotIn(PNP, detail)

    def test_changed_unique_rejected_even_if_pnp_and_capacity_match(self):
        detail = bridge.identity_difference(fingerprint('serial1'),
                                            fingerprint('serial2', unique='other'))
        self.assertIn('unique ID:', detail)

    def test_serial_mismatch_rejected_when_pnp_is_missing(self):
        old = bridge.canonical_source_identity('old', 'A1B2', 100)
        new = bridge.canonical_source_identity('new', 'A1B2', 100, PNP)
        self.assertIn('serial number:', bridge.identity_difference(old, new))
        # No silent downgrade if an initial PnP ID cannot be re-read.
        self.assertIn('PnP device identity:', bridge.identity_difference(new, old))

    def test_serial_mismatch_rejected_for_empty_or_placeholder_unique(self):
        old = fingerprint('one', unique='')
        new = fingerprint('two', unique='')
        self.assertIn('serial number:', bridge.identity_difference(old, new))
        self.assertIn('serial number:', bridge.identity_difference(
            fingerprint('one', unique='00000000'),
            fingerprint('two', unique='00000000')))

    def test_size_change_never_allowed(self):
        detail = bridge.identity_difference(fingerprint('one'),
                                            fingerprint('two', size=8_000_000_001))
        self.assertIn('disk capacity:', detail)

    def test_resume_with_sidecar_accepts_verified_serial_variation(self):
        old = fingerprint('USB INITIAL SERIAL', size=8192)
        live = fingerprint('USB ALTERED SERIAL', size=8192)
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'copy.img'
            image.write_bytes(b'test partial copy')
            Path(str(image)+'.map').write_text('0x0 0x10 +\n')
            Path(str(image)+'.source.json').write_text(json.dumps({
                'schema': 1, 'identity': old, 'size': 8192, 'device': DRIVE,
            }))
            with patch.object(image_rescue, 'os', SimpleNamespace(name='nt')), \
                 patch('src.infrastructure.windows_raw.require_admin'), \
                 patch('src.infrastructure.windows_ddrescue.assert_image_unlocked'), \
                 patch.object(image_rescue, 'ensure_different_disk'), \
                 patch.object(image_rescue, 'source_identity', return_value=live):
                # Windows may expand an 8.3 short path (GAMING~1) to its long
                # spelling (GamingWin10) during Path.resolve(). Both refer to
                # the same file even though WindowsPath.__eq__ is False.
                resumed = image_rescue.plan_resume(DRIVE, image, 8192)
                self.assertTrue(resumed.samefile(image))

    def test_worker_accepts_verified_serial_fluctuation_and_writes_sidecar(self):
        first = fingerprint('SOURCE SERIAL')
        live = fingerprint('USB REPORTED ANOTHER SERIAL')
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'copy.img'
            state = {}
            class Finished:
                returncode = 0
                def poll(self): return 0
                def wait(self): return 0
            from contextlib import nullcontext
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'), \
                 patch.object(bridge, 'source_identity', return_value=live), \
                 patch.object(bridge, 'image_lock', return_value=nullcontext()), \
                 patch.object(bridge, 'command', return_value=(['ddrescue'], {})), \
                 patch('src.services.image_rescue.subprocess.Popen', return_value=Finished()) as popen:
                ImageWorker(DRIVE, image, 8_000_000_000, state,
                            expected_identity=first)._run_windows()
            popen.assert_called_once()
            self.assertNotEqual(state['state'], 'error')
            sidecar = json.loads(Path(str(image)+'.source.json').read_text())
            self.assertEqual(json.loads(sidecar['identity'])['pnp'], PNP)


if __name__ == '__main__':
    unittest.main()
