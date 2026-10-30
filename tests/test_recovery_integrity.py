"""Regression tests against realistic FAT/exFAT/NTFS fls layouts and icat results.

Live deleted-file disk images require fls + mkfs/ntfs tools and are not bundled;
these mocks exercise the same parser and subprocess byte stream as the device.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.models import core
from src.app import UI


def pyout(payload, code=0):
    return [sys.executable, '-c', f'import sys;sys.stdout.buffer.write({payload!r});sys.exit({code})']


class ForensicParsingTests(unittest.TestCase):
    def test_ntfs_orphan_unknown_name_type(self):
        self.assertEqual(core.parse_fls('''-/r * 1982-128-1: Windows/old.jpg\t1\t2\t3\t4\t4096\t0\t0'''),
                         ('1982-128-1','Windows/old.jpg',4096,'PHOTOS'))
        self.assertEqual(core.parse_fls('''-/r *\t1982-128-1\told.jpg\t1\t2\t3\t4\t4096\t0\t0'''),
                         ('1982-128-1','old.jpg',4096,'PHOTOS'))
        self.assertIsNone(core.parse_fls('''-/d * 100: Documents\t1\t2\t3\t4\t0\t0\t0'''))
        self.assertIsNone(core.parse_fls('''-/r * 200-128-1(realloc): old.jpg\t1\t2\t3\t4\t4096\t0\t0'''))

    def test_fat_and_exfat_deleted_files(self):
        self.assertEqual(core.parse_fls('''r/r * 11: DCIM/PIC0001.JPG\t1\t2\t3\t4\t512\t0\t0'''),
                         ('11','DCIM/PIC0001.JPG',512,'PHOTOS'))
        self.assertEqual(core.parse_fls('''r/r * 99: docs/report.odt\t1\t2\t3\t4\t10000\t0\t0'''),
                         ('99','docs/report.odt',10000,'DOCS'))

    def test_trash_containers_only(self):
        for x in ('$RECYCLE.BIN','.Trashes','.Trash','.Trash-1000'):
            self.assertTrue(core.trash_root(x))
        for x in ('.Trash-foo','.Trash-1000/secret','Documents','System Volume Information','.'):
            self.assertFalse(core.trash_root(x))
        self.assertFalse(core.trash_content('$RECYCLE.BIN/S-1-5-21/$IABCDEF.txt'))
        self.assertTrue(core.trash_content('$RECYCLE.BIN/S-1-5-21/$RABCDEF.txt'))
        self.assertFalse(core.trash_content('.Trashes/1000/info/abc.trashinfo'))

    def test_directory_parsing(self):
        self.assertEqual(core.parse_entry('d/d * 11: lost_folder\t1\t2\t3\t4\t0\t0\t0','dir')[1], 'lost_folder')


class PathsTests(unittest.TestCase):
    def test_reject_parent_and_deep(self):
        for path in ('../etc/passwd','photos/../../etc/passwd','a/'*25+'x','a\x00b'):
            with self.assertRaises(ValueError):
                core.safe_relative_path(path)
        self.assertEqual(str(core.safe_relative_path('C:\\users\\photos\\test.jpg')),'users/photos/test.jpg')

    def test_no_symlink_tree_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'bad').symlink_to('/etc')
            with self.assertRaises(ValueError):
                core.safe_target_path(root,Path('bad/secret.txt'),'12')
            (root/'album').mkdir()
            (root/'album'/'a.jpg').write_bytes(b'keep')
            candidate = core.safe_target_path(root,Path('album/a.jpg'),'100-128-1')
            self.assertEqual(candidate.name,'a_100_128_1.jpg')
            self.assertEqual((root/'album'/'a.jpg').read_bytes(),b'keep')

    def test_existing_db_schema_migration(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            db = Path(td)/'scan.sqlite'
            con=sqlite3.connect(db)
            con.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, inode TEXT, path TEXT, size INTEGER, category TEXT, extension TEXT DEFAULT '', UNIQUE(inode,path))")
            con.execute("INSERT INTO entries(inode,path,size,category) VALUES('10','old.jpg',9,'PHOTOS')")
            con.commit();con.close()
            catalog=core.Catalog(db)
            self.assertEqual(catalog.at(0)[-1], 'deleted')
            catalog.close()


class ScanTests(unittest.TestCase):
    def test_scan_uses_targeted_trash_and_deleted_dir_and_ntfs(self):
        fixtures={
            'scan': b'-/r * 10-128-1: vanished.jpg\t1\t1\t1\t1\t8\t0\t0\nd/d * 33-144-1: LOST\t1\t1\t1\t1\t0\t0\t0\n',
            'deleted_children': b'r/r * 34-128-1: inside.pdf\t1\t1\t1\t1\t12\t0\t0\n',
            'trash_roots': b'd/d 90-144-1: $RECYCLE.BIN\t1\t1\t1\t1\t0\t0\t0\n',
            'trash_scan': b'r/r 91-128-1: SID/$RFILE.zip\t1\t1\t1\t1\t16\t0\t0\n',
        }
        def helper(action,device,inode=None):
            return pyout(fixtures[action])
        with tempfile.TemporaryDirectory() as td:
            cat=core.Catalog(Path(td)/'db.sqlite')
            status={}
            with mock.patch.object(core,'helper_command',side_effect=helper):
                w=core.ScanWorker(cat,'/dev/test',status)
                w.start();w.join(4)
            self.assertFalse(w.is_alive())
            self.assertEqual(status['state'],'done',status)
            self.assertEqual(cat.summary()[0],3)
            rows=list(cat.all_rows())
            self.assertIn(('LOST/inside.pdf','deleted'),[(x[2],x[-1]) for x in rows])
            self.assertIn(('$RECYCLE.BIN/SID/$RFILE.zip','trash'),[(x[2],x[-1]) for x in rows])
            cat.close()

    def test_nonzero_fls_retains_partial_rows_for_ui(self):
        data=b'r/r * 13: deleted.png\t1\t1\t1\t1\t2\t0\t0\n'
        with tempfile.TemporaryDirectory() as td:
            cat=core.Catalog(Path(td)/'c.db')
            status={}
            with mock.patch.object(core,'helper_command',return_value=pyout(data,5)):
                w=core.ScanWorker(cat,'/dev/x',status)
                w.start();w.join(4)
            self.assertEqual(status['state'],'error')
            self.assertEqual(cat.summary()[0],1)
            ui=UI(demo=True)
            try:
                ui.stage='scanning';ui.status=status
                ui.tick()
                self.assertEqual(ui.stage,'results')
                self.assertTrue(ui.scan_partial)
            finally:ui.close();cat.close()


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, payload, exit_code, expected, origin='deleted', path='a/file.bin'):
        with tempfile.TemporaryDirectory() as td:
            cat=core.Catalog(Path(td)/'db.sqlite')
            cat.insert_batch([('12',path,expected,'OTHER',origin)])
            row=cat.at(0)
            status={}
            with mock.patch.object(core,'helper_command',return_value=pyout(payload,exit_code)),\
                 mock.patch.object(core,'MIN_FREE',0):
                w=core.RecoverWorker(cat,'ALL',row,'/dev/x','TEST',Path(td),status)
                w.start();w.join(4)
            self.assertFalse(w.is_alive())
            outdir=Path(status['outdir'])
            files=[p for p in outdir.rglob('*') if p.is_file()]
            result=(status, [(p.relative_to(outdir).as_posix(),p.read_bytes()) for p in files])
            cat.close()
            return result

    def test_full_verified_recovery_and_original_tree(self):
        s,files=self.run_recovery(b'abcdef',0,6,path='folder/sub/file.bin')
        self.assertEqual((s['recovered'],s['partial'],s['failed']), (1,0,0))
        self.assertEqual(files,[('folder/sub/file.bin',b'abcdef')])

    def test_short_success_is_partial_not_false_success(self):
        s,files=self.run_recovery(b'abc',0,9)
        self.assertEqual((s['recovered'],s['partial'],s['failed']),(0,1,0))
        self.assertEqual(files,[('a/file.bin.partial',b'abc')])

    def test_nonzero_exit_with_bytes_is_partial(self):
        s,files=self.run_recovery(b'abcdef',3,6)
        self.assertEqual((s['recovered'],s['partial'],s['failed']),(0,1,0))
        self.assertEqual(files,[('a/file.bin.partial',b'abcdef')])

    def test_empty_failed_extraction_is_not_saved(self):
        s,files=self.run_recovery(b'',2,12)
        self.assertEqual((s['recovered'],s['partial'],s['failed']),(0,0,1))
        self.assertEqual(files,[])

    def test_unknown_size_is_unverified(self):
        s,files=self.run_recovery(b'hello',0,None)
        self.assertEqual((s['recovered'],s['unverified'],s['failed']),(0,1,0))
        self.assertEqual(files,[('a/file.bin',b'hello')])

    def test_known_zero_byte(self):
        s,files=self.run_recovery(b'',0,0)
        self.assertEqual((s['recovered'],s['partial'],s['failed']),(1,0,0))
        self.assertEqual(files,[('a/file.bin',b'')])

    def test_trash_uses_nonrecovery_icat(self):
        with tempfile.TemporaryDirectory() as td:
            cat=core.Catalog(Path(td)/'db.sqlite')
            cat.insert_batch([('12','.Trashes/1000/files/cat.png',3,'PHOTOS','trash')])
            calls=[]
            def helper(action,dev,inode=None):
                calls.append(action)
                return pyout(b'abc')
            with mock.patch.object(core,'helper_command',side_effect=helper),mock.patch.object(core,'MIN_FREE',0):
                status={};w=core.RecoverWorker(cat,'ALL',cat.at(0),'/dev/x','TEST',Path(td),status)
                w.start();w.join(4)
            self.assertEqual(calls,['recover_allocated'])
            self.assertEqual(status['recovered'],1)
            cat.close()


if __name__ == '__main__': unittest.main()

class RootHelperSafetyTests(unittest.TestCase):
    def test_new_actions_remain_read_only(self):
        from src.helpers import reader
        arguments={}
        def capture(binary,args):
            arguments['binary']=binary
            arguments['args']=args
            raise RuntimeError('captured')
        for action, inode in [('scan',None),('trash_roots',None),
                              ('trash_scan','100'),('deleted_children','101'),
                              ('recover','102-128-1'),('recover_allocated','103-128-1')]:
            argv=['reader',action,'/dev/sda1']+([inode] if inode else [])
            # The helper sanitizes its process environment before execv.
            # execv is mocked here; preserve DISPLAY for later Tk tests.
            with mock.patch.dict(reader.os.environ, {}), \
                 mock.patch.object(reader.os,'geteuid',return_value=0),\
                 mock.patch.object(reader.sys,'argv',argv),\
                 mock.patch.object(reader,'verify_device',return_value='/dev/sda1'),\
                 mock.patch.object(reader.os.path,'isfile',return_value=True),\
                 mock.patch.object(reader.os,'execv',side_effect=capture):
                with self.assertRaisesRegex(RuntimeError,'captured'):
                    reader.main()
            args=arguments['args']
            self.assertEqual(args[0],arguments['binary'])
            self.assertIn('/dev/sda1', args)
            self.assertFalse(set(args).intersection({'-w','-W','--write','-s'}))
            if action == 'recover_allocated':
                self.assertNotIn('-r',args)
            if action == 'recover':
                self.assertIn('-r',args)

    def test_reject_untrusted_inode_before_exec(self):
        from src.helpers import reader
        with mock.patch.object(reader.os,'geteuid',return_value=0),\
             mock.patch.object(reader.sys,'argv',['reader','trash_scan','/dev/sda1','999;rm -rf /']),\
             mock.patch.object(reader,'verify_device') as verified,\
             mock.patch.object(reader.os,'execv') as execv:
            with self.assertRaises(SystemExit):
                reader.main()
            verified.assert_not_called()
            execv.assert_not_called()
