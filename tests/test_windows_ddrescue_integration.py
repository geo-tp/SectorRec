"""Portable simulations for the Cygwin bridge, never touching a physical disk."""
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure import windows_ddrescue as bridge
from src.services.image_rescue import ImageWorker


class CygwinPhysicalMapping(unittest.TestCase):
    def test_whole_disk_only_and_disk_index_letters(self):
        self.assertEqual(bridge.require_whole_disk(r'\\.\PhysicalDrive27'),27)
        self.assertEqual(bridge._letters(0),'a')
        self.assertEqual(bridge._letters(25),'z')
        self.assertEqual(bridge._letters(26),'aa')
        self.assertEqual(bridge._letters(27),'ab')
        with self.assertRaisesRegex(ValueError, 'whole PhysicalDrive'):
            bridge.require_whole_disk(r'\\.\D:')

    def test_cygpath_must_confirm_exact_physical_device(self):
        requested=r'\\.\PhysicalDrive2'
        calls=[]
        def fake(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout=requested+'\n')
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(bridge.mapped_source(requested,root,run=fake), '/dev/sdc')
        self.assertEqual(calls[0][1:],['-w','/dev/sdc'])
        def wrong(*a,**k):
            return SimpleNamespace(returncode=0,stdout=r'\\.\PhysicalDrive3')
        with self.assertRaisesRegex(ValueError,'Refusing imaging'):
            bridge.mapped_source(requested,Path('.'),run=wrong)

    def test_missing_identity_fails_closed(self):
        def anonymous(*a,**kw):
            return SimpleNamespace(returncode=0,stdout='{"Serial":"", "Unique":"", "Size":"5000"}')
        self.assertIsNone(bridge.source_identity(r'\\.\PhysicalDrive2',run=anonymous))
        def good(*a,**kw):
            return SimpleNamespace(returncode=0,stdout='{"Serial":"UNIT-42", "Unique":"", "Size":"5000"}')
        data=json.loads(bridge.source_identity(r'\\.\PhysicalDrive2',run=good))
        self.assertEqual(data['serial'],'UNIT-42')
        self.assertEqual(data['size'],5000)

    def test_command_includes_mapfile_and_retry_and_converts_paths(self):
        with patch.object(bridge,'require_admin'),\
             patch.object(bridge,'mapped_source',return_value='/dev/sdb'),\
             patch.object(bridge,'cygwin_file_path',side_effect=['/cygdrive/d/backup.img', '/cygdrive/d/backup.img.map']):
            cmd,env=bridge.command(r'\\.\PhysicalDrive1','D:/backup.img',resume=True,folder='C:/ddrescue-bin')
        self.assertEqual(cmd[-3:],['/dev/sdb','/cygdrive/d/backup.img','/cygdrive/d/backup.img.map'])
        self.assertIn('-r3',cmd)
        self.assertEqual(env['LC_ALL'],'C')

    def test_worker_creates_source_metadata_and_map_is_measured(self):
        source=r'\\.\PhysicalDrive1'
        with tempfile.TemporaryDirectory() as root:
            image=Path(root)/'SectorRec_PhysicalDrive1.img'
            state={}
            identity=json.dumps({'serial':'SERIAL-1','unique':'','size':4096},sort_keys=True,separators=(',',':'))
            # Native simulated child copies 20% into the output and writes a map.
            snippet=("import pathlib,sys;"
                     "path=pathlib.Path(sys.argv[1]);"
                     "path.write_bytes(b'A'*819);"
                     "pathlib.Path(str(path)+'.map').write_text('0x0 0x333 +\\n0x333 0xccd ?\\n')")
            with patch('src.infrastructure.windows_raw.ensure_different_windows_disk'),\
                 patch.object(bridge,'source_identity',return_value=identity),\
                 patch.object(bridge,'image_lock',side_effect=lambda p:nullcontext()),\
                 patch.object(bridge,'command',return_value=(
                     [sys.executable,'-c',snippet,str(image)],{})):
                ImageWorker(source,image,4096,state)._run_windows()
            self.assertEqual(state['state'],'incomplete')
            self.assertAlmostEqual(state['percent'],819/4096*100)
            self.assertEqual(json.loads(Path(str(image)+'.source.json').read_text())['identity'],identity)


if __name__=='__main__': unittest.main()
