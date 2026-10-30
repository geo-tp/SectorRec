"""Alpha36: paginated sorting, unified native picker, resumability and safe mount."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models.core import Catalog
from src.infrastructure.image_mount import ImageMountSession, ImageMountError
from src.services.image_rescue import plan_resume, rescued_percent_from_map
from src.views.table_sort import sorted_preview


class SqliteSorting(unittest.TestCase):
    def test_entire_catalog_order_then_recover_selection(self):
        with tempfile.TemporaryDirectory() as d:
            cat=Catalog(Path(d)/'catalog.sqlite')
            cat.insert_batch([
                ('inode1','/Z-large.jpg',300,'PHOTOS'),
                ('inode2','/B-small.txt',12,'DOCS'),
                ('inode3','/A-mid.jpg',100,'PHOTOS'),
                ('inode4','/C-unknown.txt',None,'DOCS'),
            ])
            for col,expected in (
                ('path',['/A-mid.jpg','/B-small.txt','/C-unknown.txt','/Z-large.jpg']),
                ('size',['/B-small.txt','/A-mid.jpg','/Z-large.jpg','/C-unknown.txt']),
                ('type',['/Z-large.jpg','/A-mid.jpg','/B-small.txt','/C-unknown.txt'])):
                with self.subTest(col=col):
                    rows=cat.page(0,4,sort=col)
                    self.assertEqual([x[2] for x in rows],expected)
                    for ix,row in enumerate(rows):
                        self.assertEqual(cat.at(ix,sort=col),row)
                    self.assertEqual([x[2] for x in cat.page(0,4,sort=col,descending=True)],
                                     list(reversed(expected)) if col!='size' else ['/Z-large.jpg','/A-mid.jpg','/B-small.txt','/C-unknown.txt'])
                    total,live=cat.live_page(sort=col,descending=False)
                    self.assertEqual(total,4)
                    self.assertEqual([x[1] for x in live],expected)
            self.assertEqual(cat.page(0,2,sort='path')[1][2],cat.page(1,2,sort='path')[0][2])
            cat.close()

    def test_preview_size_is_numeric(self):
        records=[('z.jpg','JPG','1 MiB'),('a.jpg','JPG','950 KiB')]
        self.assertEqual(sorted_preview(records,'size',False)[0][0],'a.jpg')
        self.assertEqual(sorted_preview(records,'size',True)[0][0],'z.jpg')


class SafeMount(unittest.TestCase):
    def test_mount_two_partitions_readonly_then_unmount(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'src.img'; path.write_bytes(b'123')
            folder=Path(d)/'mounted';folder.mkdir()
            commands=[]
            def fake(args,timeout=35):
                commands.append(args)
                if args[1]=='loop-setup':return 'Mapped file as /dev/loop5.'
                if args[0]=='lsblk':return json.dumps({'blockdevices':[{'path':'/dev/loop5','fstype':None,'children':[
                    {'path':'/dev/loop5p1','fstype':'ntfs'},
                    {'path':'/dev/loop5p2','fstype':'ext4'}]}]})
                if args[1]=='mount':return f'Mounted {args[3]} at {folder}.'
                return ''
            with patch('src.infrastructure.image_mount.available',return_value=True):
                session=ImageMountSession(path,runner=fake)
                mounts,failed=session.mount()
                self.assertEqual(mounts,[str(folder),str(folder)])
                self.assertFalse(failed)
                self.assertEqual(commands[0][:3],['udisksctl','loop-setup','--read-only'])
                self.assertTrue(all('ro,nosuid,nodev,noexec' in c for c in commands if c[1]=='mount'))
                session.unmount()
                self.assertIsNone(session.loop)
                self.assertEqual(sum(c[1]=='unmount' for c in commands),2)
                self.assertEqual(commands[-1][1],'loop-delete')

    def test_no_filesystem_does_not_leak_loop(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'empty.img';path.write_bytes(b'xxx')
            commands=[]
            def fake(args,timeout=35):
                commands.append(args)
                if args[1]=='loop-setup':return 'Mapped file as /dev/loop9.'
                if args[0]=='lsblk':return json.dumps({'blockdevices':[{'path':'/dev/loop9','fstype':None}]})
                return ''
            with patch('src.infrastructure.image_mount.available',return_value=True):
                session=ImageMountSession(path,runner=fake)
                with self.assertRaisesRegex(ImageMountError,'No mountable filesystem'):
                    session.mount()
                self.assertIsNone(session.loop)
                self.assertEqual(commands[-1][1],'loop-delete')


class InterruptedRescue(unittest.TestCase):
    def test_partial_map_is_accepted_for_resume(self):
        with tempfile.TemporaryDirectory() as d, \
             patch('src.services.image_rescue.ensure_different_disk'), \
             patch('src.services.image_rescue.source_identity',return_value='disk-abc'):
            image=Path(d)/'SectorRec_sdd.img'
            image.write_bytes(b'\0'*2048)
            Path(str(image)+'.map').write_text('0x0 0x200 +\n0x200 0x800 ?\n')
            Path(str(image)+'.source.json').write_text(json.dumps(
                {'schema':1,'identity':'disk-abc','size':2560}))
            self.assertEqual(rescued_percent_from_map(str(image)+'.map',2560),20.0)
            self.assertEqual(plan_resume('/dev/sdd',image,2560),image)


    def test_live_image_job_blocks_parallel_resume(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d, \
             patch('src.services.image_rescue.ensure_different_disk'), \
             patch('src.services.image_rescue.source_identity',return_value='disk-abc'):
            image=Path(d)/'SectorRec_sdd.img'; image.write_bytes(b'partial')
            identity=Path(str(image)+'.source.json')
            identity.write_text(json.dumps({'schema':1,'identity':'disk-abc','size':100000}))
            Path(str(image)+'.map').write_text('0x0 0x100 +\n0x100 0x185a0 ?\n')
            with identity.open('rb') as fd:
                fcntl.flock(fd.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'still being copied'):
                    plan_resume('/dev/sdd',image,100000)
            self.assertEqual(plan_resume('/dev/sdd',image,100000),image)


class DesktopSelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from src.app import UI
            from src.views.desktop_native import DesktopWindow
            cls.win=DesktopWindow(UI(demo=True,desktop=True))
        except Exception as exc:raise unittest.SkipTest(str(exc))
    @classmethod
    def tearDownClass(cls):cls.win.close()
    def test_resume_picker_uses_same_native_method(self):
        with patch.object(self.win,'pick_source_image',return_value='') as p:
            self.win._select_resume_image({'path':'/dev/sdb','size':1})
            self.assertIn('Raw disk image',str(p.call_args))

    def test_partition_headers_keep_recovery_index(self):
        from types import SimpleNamespace
        w=self.win
        candidates=[SimpleNamespace(origin='found',start=150,label='NTFS',size=100),
                    SimpleNamespace(origin='existing',start=2,label='ext4',size=200)]
        w.ui.partition_status={'candidates':candidates,'source':'test'}
        w.ui.stage='partition_results'
        w.ui.partition_index=0
        w.show(force=True)
        w.sort_partitions('offset')
        self.assertEqual(w.partition_tree.get_children(),('1','0'))
        w.partition_tree.selection_set('0')
        w._partition_selected()
        self.assertEqual(w.ui.partition_index,0)
        w.sort_partitions('offset')
        self.assertEqual(w.partition_tree.get_children(),('0','1'))
        w.ui.stage='devices'
        w.show(force=True)

    def test_heading_sort_toggle(self):
        import tkinter as tk
        from tkinter import ttk
        tree=ttk.Treeview(self.win.root,columns=('path',),show='headings')
        tree.pack()
        self.win._sort_heading(tree,'path','FILE')
        self.win.sort_table('path')
        self.assertEqual(self.win.ui.desktop_sort,'path')
        self.assertFalse(self.win.ui.desktop_sort_desc)
        self.win.sort_table('path')
        self.assertTrue(self.win.ui.desktop_sort_desc)
        tree.destroy()

if __name__=='__main__':unittest.main()
