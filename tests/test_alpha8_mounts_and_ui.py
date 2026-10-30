"""Regression for auto-remount, scan status, desktop polish and safe folders."""
import datetime
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.app import UI
from src.models import core
from src.infrastructure.source_guard import SourceMountGuard


class DesktopRecoverySafetyTests(unittest.TestCase):
    def test_sectorrec_short_directory_label_and_collision_safety(self):
        date = datetime.datetime(2026, 10, 27, 12, 43)
        result = core.target_root(Path('/home/user'), 'SanDisk 4GB',date,product='SectorRec')
        self.assertEqual(str(result), '/home/user/Backups/SectorRec/SanDisk-4GB_20261027')
        self.assertTrue(str(core.target_root(Path('/home/pi'),'SD',date)).endswith('SD_20261027'))
        with tempfile.TemporaryDirectory() as tmp:
            with patch('src.models.core.dt') as datetime_mock:
                datetime_mock.datetime.now.return_value=date
                a=core.create_output_directory(Path(tmp),'SanDisk 4GB','SectorRec')
                b=core.create_output_directory(Path(tmp),'SanDisk 4GB','SectorRec')
            self.assertNotEqual(a,b)
            self.assertTrue(b.name.endswith('_1'))
            self.assertTrue(a.exists())

    def test_partition_file_exploration_automatically_unmounts_on_pc(self):
        from src.services.partition import PartitionCandidate
        ui=UI(demo=False,desktop=True)
        try:
            ui.devices=[{'path':'/dev/sdZ','mounted':True,'is_image':False}]
            ui.selection=0
            ui.partition_selected=None
            ui.partition_status={'candidates':[PartitionCandidate(2048,8192,'FAT32')]}
            ui.partition_index=0
            with patch.object(ui,'begin_unmount') as unmount, patch.object(ui,'begin_scan') as scan:
                ui.begin_partition_file_scan()
                unmount.assert_called_once_with(next_action='partition_files')
                scan.assert_not_called()
            self.assertEqual(ui.partition_selected.start,2048)
        finally: ui.close()

    def test_done_with_unreadable_deleted_directories_explains_partial(self):
        ui=UI(demo=True,desktop=True)
        try:
            ui.stage='scanning'
            ui.status={'state':'done','unreadable_dirs':2,'count':5}
            ui.tick()
            self.assertEqual(ui.stage,'results')
            self.assertTrue(ui.scan_partial)
            self.assertIn('2 deleted folder(s)',ui.scan_partial_reason)
        finally: ui.close()

    def test_scan_error_is_not_misidentified_as_user_cancel(self):
        ui=UI(demo=True,desktop=True)
        try:
            ui.stage='scanning'
            ui.status={'state':'error','error':'Source is mounted','count':5}
            ui.tick()
            self.assertEqual(ui.stage,'results')
            self.assertIn('Source is mounted',ui.scan_partial_reason)
            self.assertNotIn('Stopped early',ui.scan_partial_reason)
        finally: ui.close()

    def test_source_guard_is_read_only_and_closed_on_owner_release(self):
        # The fake process exercises the complete protocol; no actual block
        # device or elevated helper is involved in this test.
        script='import sys;print("READY",flush=True);sys.stdin.buffer.read()'
        with patch('src.infrastructure.source_guard.helper_command',
                   return_value=[sys.executable,'-u','-c',script]):
            guard=SourceMountGuard.acquire('/dev/sda1', timeout=2)
        self.assertTrue(guard.alive())
        guard.release()
        self.assertFalse(guard.alive())

    def test_scan_worker_reuses_held_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=core.Catalog(Path(tmp)/'r.sqlite')
            class Guard:
                source='/dev/sda1'
                def alive(self):return True
            held=Guard()
            scan=core.ScanWorker(db,'/dev/sda1',{}, protect_source=True,
                source_guard=held, command_builder=lambda action,dev,ino=None:
                    [sys.executable,'-c','pass'])
            scan.run()
            self.assertIs(scan.status['_guard'],held)
            db.close()


@unittest.skipUnless(os.environ.get('DISPLAY'), 'X display needed')
class DesktopAppearanceTests(unittest.TestCase):
    def setUp(self):
        from src.views.desktop_native import DesktopWindow
        self.ui=UI(demo=True,desktop=True)
        self.win=DesktopWindow(self.ui)
        self.win.root.update()
    def tearDown(self):
        self.win.confirm_action = lambda *args, **kwargs: True
        self.win.close()
    def test_treeview_heading_has_no_white_hover(self):
        from tkinter import ttk
        from src.views.desktop_native import SURFACE_2
        sty=ttk.Style(self.win.root)
        self.assertEqual(sty.lookup('Sector.Treeview.Heading','background',('active',)),SURFACE_2)
    def test_native_folder_picker_selection_and_cancel(self):
        from unittest.mock import Mock
        with patch('src.views.desktop_native.sys.platform','linux'),\
             patch('src.views.desktop_native.shutil.which',side_effect=lambda tool: '/usr/bin/zenity' if tool=='zenity' else None),\
             patch('src.views.desktop_native.subprocess.run',return_value=Mock(returncode=0,stdout='/home/u/Downloads/assets\n')) as proc:
            self.assertEqual(self.win.pick_destination_directory(), '/home/u/Downloads/assets')
            self.assertIn('--directory',proc.call_args.args[0])
        with patch('src.views.desktop_native.sys.platform','linux'),\
             patch('src.views.desktop_native.shutil.which',return_value='/usr/bin/zenity'),\
             patch('src.views.desktop_native.subprocess.run',return_value=Mock(returncode=1,stdout='')),\
             patch('src.views.desktop_native.filedialog.askdirectory') as fallback:
            self.assertEqual(self.win.pick_destination_directory(),'')
            fallback.assert_not_called()

class PhotoRecNameTests(unittest.TestCase):
    def test_short_recovery_prefixes_preserve_previous_outputs(self):
        from src.services.signature import PhotoRecWorker
        from unittest.mock import patch
        script = ('import pathlib,sys; p=pathlib.Path(sys.argv[sys.argv.index("/d")+1]+".1");'
                  'p.mkdir(); (p/"file.jpg").write_bytes(b"abc")')
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp);source=target/'source.img';source.write_bytes(b'img')
            saved=[]
            for i in range(2):
                state={}
                job=PhotoRecWorker(str(source),tmp,state,binary=sys.executable,
                    launcher=lambda args: [sys.executable,'-c',script,*args],interval=.02)
                job.run()
                self.assertEqual(state['state'],'done',state)
                self.assertIn('SectorRec_',state['output'])
                self.assertRegex(Path(state['output']).name, r'^SectorRec_\d{6}(?:_[2-9]\d*)?$')
                saved.append(Path(state['output']+'.1/file.jpg'))
            self.assertNotEqual(saved[0],saved[1])
            self.assertTrue(all(file.read_bytes()==b'abc' for file in saved))

class PartitionUnmountTransitionTests(unittest.TestCase):
    def test_unmount_completion_routes_to_selected_partition_files_not_testdisk_again(self):
        ui=UI(demo=False,desktop=True)
        try:
            ui.devices=[{'path':'/dev/sdz','mounted':True,'partition_disk':True,
                         'supported':False, 'label':'test', 'fs':'DISK'}]
            ui.selection=0
            ui.engine='partition'
            ui.unmount_path='/dev/sdz'
            ui.unmount_next='partition_files'
            ui.unmount_status={'state':'done'}
            ui.stage='unmounting'
            ready=[{'path':'/dev/sdz','mounted':False,'partition_disk':True,
                    'supported':False,'label':'test', 'fs':'DISK'}]
            with patch.object(ui.platform,'devices',return_value=ready),\
                 patch.object(ui,'begin_scan') as scan,\
                 patch.object(ui,'begin_partition_search') as partition:
                ui.tick()
            scan.assert_called_once_with()
            partition.assert_not_called()
        finally:ui.close()
