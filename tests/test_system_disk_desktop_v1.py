"""Read-only live-system source controls and off-source catalogue regression tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.controllers.controller import Controller
from src.infrastructure.platform_disks import windows_inventory
from src.infrastructure import linux_devices
from src.services.source_assessment import assess_source
from src.views.desktop_native import DesktopWindow

INITIAL_DISPLAY = os.environ.get('DISPLAY')


class SystemDiscoveryTests(unittest.TestCase):
    def test_windows_system_drive_and_whole_disk_are_last(self):
        def disk(index, letter, is_system):
            return {'DeviceID':f'\\\\.\\PhysicalDrive{index}', 'DiskSize':'8192',
                    'Model':f'Disk{index}', 'Partitions': [{'Letter':letter,
                     'Size':4096,'Fs':'NTFS', 'IsSystem':is_system}]}
        raw=[disk(0,'C',True),disk(1,'E',False)]
        result=windows_inventory(run=lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(raw)))
        self.assertEqual([x['path'] for x in result],
                         ['\\\\.\\PhysicalDrive1','\\\\.\\E:',
                          '\\\\.\\PhysicalDrive0','\\\\.\\C:'])
        self.assertTrue(all(x['is_system'] for x in result[-2:]))

    def test_linux_live_root_is_last_and_unmount_refused(self):
        osdisk={'type':'disk','path':'/dev/sda','name':'sda',
                'children':[{'type':'part','path':'/dev/sda2','name':'sda2',
                             'fstype':'ext4','size':4096,'mountpoints':['/']} ]}
        usb={'type':'disk','path':'/dev/sdb','name':'sdb','fstype':'vfat','size':4096}
        found=linux_devices.detect_devices(tree_supplier=lambda:[osdisk,usb])
        self.assertEqual([x['path'] for x in found],['/dev/sdb','/dev/sda','/dev/sda2'])
        self.assertTrue(found[-1]['is_system'])
        with self.assertRaisesRegex(RuntimeError,'never be unmounted'):
            linux_devices.unmount_volume('/dev/sda2', detect_devices=lambda:found)

    def test_system_methods_never_offer_testdisk(self):
        volume={'path':'\\\\.\\C:','fs':'NTFS','supported':True,'is_system':True}
        choices=assess_source(volume,desktop=True)
        self.assertEqual({x.key for x in choices.methods}, {'metadata','signature'})
        whole=assess_source({'path':'\\\\.\\PhysicalDrive0',
                             'fs':'DISK','partition_disk':True,'is_system':True},desktop=True)
        self.assertNotIn('partition', {x.key for x in whole.methods})

    def test_privileged_reader_allows_only_read_only_system_access(self):
        import io
        import json as _json
        import stat as _stat
        from src.helpers import reader
        root={'type':'disk','path':'/dev/sda','mountpoints':[],
              'children':[{'type':'part','path':'/dev/sda2','fstype':'ext4',
                           'mountpoints':['/']}]}
        def fake_open(path,*args,**kwargs):
            if path == '/proc/swaps':
                return io.StringIO('Filename\tType\tSize\tUsed\tPriority\n')
            raise AssertionError('Unexpected file read '+str(path))
        with patch.object(reader.os.path,'realpath',side_effect=lambda x:x), \
             patch.object(reader.os,'stat',return_value=SimpleNamespace(st_mode=_stat.S_IFBLK)), \
             patch.object(reader.subprocess,'check_output',return_value=_json.dumps({'blockdevices':[root]})), \
             patch('builtins.open',side_effect=fake_open):
            self.assertEqual(reader.verify_device('/dev/sda2',allow_system_read=True),'/dev/sda2')
            with self.assertRaises(SystemExit):
                reader.verify_device('/dev/sda2')

    def test_cardputer_ignores_os_drive(self):
        ui=Controller(demo=True,desktop=False)
        try:
            ui.devices=[]
            ui.device_refresh_ready=('ok',[{'path':'/dev/sda2','is_system':True},
                                            {'path':'/dev/sdb1','is_system':False}])
            # apply_device_refresh filters on desktop=False as well, including injected refresh results
            ui.apply_device_refresh()
            self.assertEqual([x['path'] for x in ui.devices],['/dev/sdb1'])
        finally:
            ui.close()


class SystemCatalogTests(unittest.TestCase):
    def test_sqlite_catalog_is_on_recovery_destination_then_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'external';destination.mkdir()
            ui=Controller(demo=False,desktop=True)
            try:
                ui.devices=[{'path':'\\\\.\\C:', 'is_system':True,'size':8192,'supported':True},
                            {'path':'\\\\.\\E:', 'size':8192,'supported':True}]
                ui.selection=0
                with patch.object(ui,'metadata_preflight', return_value=(destination,10**9)):
                    ui._prepare_system_catalog()
                private=Path(ui.catalog.path)
                self.assertTrue(private.exists())
                self.assertEqual(private.parent.parent,destination)
                ui.selection=1
                ui._prepare_system_catalog()
                self.assertNotEqual(private,Path(ui.catalog.path))
                self.assertFalse(private.exists())
            finally:
                ui.close()

    def test_system_source_never_calls_unmount(self):
        ui=Controller(demo=True,desktop=True)
        try:
            ui.devices=[{'path':'/dev/sda2', 'is_system':True,'size':4096,
                         'mounted':True,'fs':'EXT4','supported':True}]
            ui.selection=0
            ui.engine='metadata'
            with patch.object(ui,'begin_unmount') as unmount:
                ui.start_selected_scan()
                unmount.assert_not_called()
                self.assertEqual(ui.stage,'scanning')
            with self.assertRaisesRegex(RuntimeError,'must not be unmounted'):
                ui.begin_unmount()
        finally:
            ui.close()


class SystemDesktopWarningTests(unittest.TestCase):
    def test_confirmation_before_system_methods(self):
        ui=Controller(demo=True,desktop=True)
        window=None
        try:
            # An unrelated historic test temporarily removes DISPLAY.
            with patch.dict(os.environ, {'DISPLAY': INITIAL_DISPLAY} if INITIAL_DISPLAY else {}):
                window=DesktopWindow(ui)
            ui.show_devices()
            ui.devices=[{'path':'/dev/sda2', 'is_system':True,'size':4096,
                         'mounted':True,'fs':'EXT4','supported':True,'label':'System'}]
            ui.selection=0
            ui.stage='devices'
            with patch.object(window,'confirm_action',return_value=False) as confirm:
                window.dispatch('OK')
            self.assertEqual(ui.stage,'devices')
            self.assertIn('Active system disk',confirm.call_args.args[0])
            with patch.object(window,'confirm_action',return_value=True):
                window.dispatch('OK')
            self.assertEqual(ui.stage,'desktop_methods')
            window.show(force=True)
            self.assertNotIn('image',window._method_available)
        finally:
            if window is not None:
                window.root.destroy()
            ui.close()


if __name__=='__main__':
    unittest.main()
