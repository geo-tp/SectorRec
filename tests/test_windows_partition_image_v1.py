"""Windows ddrescue partition imaging: simulated only, never opens a raw disk."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure import windows_ddrescue as bridge
from src.services import image_rescue

VOLUME = r'\\.\E:'
DISK = r'\\.\PhysicalDrive4'
SIZE = 4096
OFFSET = 1024 * 1024
PARTITION = {
    'path': VOLUME, 'disk_path': DISK, 'size': SIZE,
    'partition_number': 2, 'partition_offset': OFFSET,
    'partition_style': 'GPT', 'imaging_size_verified': True,
}
SCOPE = {'disk': DISK, 'number': 2, 'style': 'GPT', 'offset': OFFSET, 'size': SIZE}
IDENTITY = bridge.canonical_source_identity('SERIAL-4', 'UNIQUE-4', 2 * 1024 * 1024)


class WindowsPartitionImageTests(unittest.TestCase):
    def test_partition_uses_verified_physical_disk_and_bounded_range(self):
        with patch('src.infrastructure.windows_raw.photorec_partition_source',
                   return_value=(DISK, 2, 'partition_gpt')) as mapping, \
             patch.object(bridge, 'require_admin'), \
             patch.object(bridge, 'mapped_source', return_value='/dev/sde') as mapped, \
             patch.object(bridge, 'cygwin_file_path', side_effect=['/cygdrive/d/part.img', '/cygdrive/d/part.img.map']):
            cmd, _ = bridge.command(VOLUME, 'D:/part.img', source_info=PARTITION, folder='C:/ddrescue-bin')
        mapping.assert_called_once_with(VOLUME, PARTITION)
        mapped.assert_called_once_with(DISK, folder=Path('C:/ddrescue-bin'), run=subprocess.run)
        self.assertIn(f'--input-position={OFFSET}', cmd)
        self.assertIn('--output-position=0', cmd)
        self.assertIn(f'--size={SIZE}', cmd)
        self.assertEqual(cmd[-3:], ['/dev/sde', '/cygdrive/d/part.img', '/cygdrive/d/part.img.map'])
        self.assertNotIn('-f', cmd)

    def test_missing_selection_refuses_volume_and_disk_behavior_unchanged(self):
        with self.assertRaisesRegex(ValueError, 'Select the Windows partition again'):
            bridge.imaging_disk(VOLUME)
        self.assertEqual(bridge.imaging_disk(DISK), (DISK, None))
        with patch.object(bridge, 'require_admin'), \
             patch.object(bridge, 'mapped_source', return_value='/dev/sde'), \
             patch.object(bridge, 'cygwin_file_path', side_effect=['/d/disk.img', '/d/disk.img.map']):
            cmd, _ = bridge.command(DISK, 'D:/disk.img', folder='C:/ddrescue-bin')
        self.assertNotIn('--output-position=0', cmd)
        self.assertFalse(any(a.startswith('--input-position=') or a.startswith('--size=') for a in cmd))

    def test_rejects_invalid_partition_size_or_offset(self):
        with patch('src.infrastructure.windows_raw.photorec_partition_source',
                   return_value=(DISK, 2, 'partition_gpt')):
            for changed in ({**PARTITION, 'partition_offset': 0},
                            {**PARTITION, 'size': 0},
                            {**PARTITION, 'partition_offset': -1}):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'invalid geometry'):
                    bridge.imaging_disk(VOLUME, changed)

    def test_worker_uses_partition_size_and_records_scope(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / 'part.img'
            state = {}
            snippet = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]);"
                       "p.write_bytes(b'X'*4096);"
                       "pathlib.Path(str(p)+'.map').write_text('0x0 0x1000 +\\n')")
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk') as separate, \
                 patch.object(bridge, 'imaging_disk', return_value=(DISK, SCOPE)), \
                 patch.object(bridge, 'source_identity', return_value=IDENTITY), \
                 patch.object(bridge, 'image_lock', return_value=nullcontext()), \
                 patch.object(bridge, 'command', return_value=([sys.executable, '-c', snippet, str(image)], {})) as command:
                image_rescue.ImageWorker(VOLUME, image, SIZE, state, source_info=PARTITION)._run_windows()
            self.assertEqual(state['state'], 'done', state)
            self.assertEqual(state['size'], SIZE)
            meta = json.loads(Path(str(image) + '.source.json').read_text())
            self.assertEqual(meta['schema'], 2)
            self.assertEqual(meta['size'], SIZE)
            self.assertEqual(meta['identity'], IDENTITY)
            self.assertEqual(meta['partition'], SCOPE)
            separate.assert_called_once_with(VOLUME, image.parent)
            command.assert_called_once_with(VOLUME, image, resume=False, source_info=PARTITION)

    def test_different_partition_refuses_resume_even_on_same_disk(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / 'partition.img'
            image.write_bytes(b'X' * 1024)
            Path(str(image) + '.map').write_text('0x0 0x400 +\n')
            Path(str(image) + '.source.json').write_text(json.dumps({
                'schema': 2, 'identity': IDENTITY, 'size': SIZE,
                'device': VOLUME, 'partition': SCOPE,
            }))
            windows_os = SimpleNamespace(name='nt')
            with patch.object(image_rescue, 'os', windows_os), \
                 patch('src.infrastructure.windows_raw.require_admin'), \
                 patch.object(bridge, 'imaging_disk', return_value=(DISK, {**SCOPE, 'offset': OFFSET + SIZE})), \
                 patch.object(image_rescue, 'source_identity', return_value=IDENTITY), \
                 patch.object(image_rescue, 'ensure_different_disk'):
                with self.assertRaisesRegex(ValueError, 'Partition geometry'):
                    image_rescue.plan_resume(VOLUME, image, SIZE, source_info=PARTITION)

    def test_matching_partition_can_resume_same_bounded_image(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / 'partition.img'
            image.write_bytes(b'X' * 1024)
            Path(str(image) + '.map').write_text('0x0 0x400 +\n')
            Path(str(image) + '.source.json').write_text(json.dumps({
                'schema': 2, 'identity': IDENTITY, 'size': SIZE,
                'device': VOLUME, 'partition': SCOPE,
            }))
            with patch.object(image_rescue, 'os', SimpleNamespace(name='nt')), \
                 patch('src.infrastructure.windows_raw.require_admin'), \
                 patch.object(bridge, 'imaging_disk', return_value=(DISK, SCOPE)), \
                 patch.object(image_rescue, 'source_identity', return_value=IDENTITY), \
                 patch.object(image_rescue, 'ensure_different_disk') as separate, \
                 patch.object(bridge, 'assert_image_unlocked'):
                # Windows may return a canonical long path (GamingWin10)
                # when tempfile supplied an 8.3 short alias (GAMING~1).
                # Compare the canonical path, not the original spelling.
                resolved = image_rescue.plan_resume(VOLUME, image, SIZE, source_info=PARTITION)
                self.assertEqual(resolved, image.resolve(strict=True))
            separate.assert_called_once_with(VOLUME, resolved.parent)

    def test_partition_resume_does_not_accept_whole_disk_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / 'old.img'
            image.write_bytes(b'X' * 100)
            Path(str(image) + '.map').write_text('0x0 0x64 +\n')
            Path(str(image) + '.source.json').write_text(json.dumps({
                'schema': 1, 'identity': IDENTITY, 'size': SIZE, 'device': DISK,
            }))
            with patch.object(image_rescue, 'os', SimpleNamespace(name='nt')), \
                 patch('src.infrastructure.windows_raw.require_admin'), \
                 patch.object(bridge, 'imaging_disk', return_value=(DISK, SCOPE)), \
                 patch.object(image_rescue, 'source_identity', return_value=IDENTITY), \
                 patch.object(image_rescue, 'ensure_different_disk'):
                with self.assertRaisesRegex(ValueError, 'Partition geometry'):
                    image_rescue.plan_resume(VOLUME, image, SIZE, source_info=PARTITION)

    def test_partition_plan_never_uses_entire_disk_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            fake_os = SimpleNamespace(name='nt', access=os.access, W_OK=os.W_OK, X_OK=os.X_OK)
            with patch.object(image_rescue, 'os', fake_os), \
                 patch('src.infrastructure.windows_raw.require_admin'), \
                 patch.object(bridge, 'imaging_disk', return_value=(DISK, SCOPE)), \
                 patch.object(image_rescue, 'source_identity', return_value=IDENTITY), \
                 patch.object(image_rescue, 'ensure_different_disk') as separate:
                output = image_rescue.plan_image(VOLUME, td, SIZE, source_info=PARTITION)
                self.assertTrue(output.name.startswith('SectorRec_E_'))
                separate.assert_called_once()
                with self.assertRaisesRegex(ValueError, 'geometry'):
                    image_rescue.plan_image(VOLUME, td, SIZE * 2, source_info=PARTITION)


if __name__ == '__main__':
    unittest.main()
