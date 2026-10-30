import unittest
from unittest import mock
from src.models import core
from src.app import UI


class DeviceDiscoveryTests(unittest.TestCase):
    def test_usb_drive_without_partition_table_or_filesystem_is_visible(self):
        disks = [{'name':'sda','path':'/dev/sda','type':'disk','tran':'usb','model':' USB Stick ',
                  'size':1000000000,'rm':False,'fstype':None,'mountpoints':[None]}]
        with mock.patch.object(core, 'lsblk_tree', return_value=disks):
            result = core.detect_devices()
        self.assertEqual([x['path'] for x in result], ['/dev/sda'])
        self.assertEqual(result[0]['label'], 'USB Stick')
        self.assertFalse(result[0]['supported'])
        self.assertEqual(result[0]['fs'], 'UNKNOWN')

    def test_mounted_usb_partition_is_visible_even_without_known_fs(self):
        disks = [{'name':'sda','path':'/dev/sda','type':'disk','tran':'usb','model':'SanDisk',
                  'fstype':None,'mountpoints':[None], 'children':[
                      {'name':'sda1','path':'/dev/sda1','type':'part','fstype':'iso9660',
                       'size':5000000,'mountpoints':['/media/pi/USB']},
                      {'name':'sda2','path':'/dev/sda2','type':'part','fstype':'ntfs',
                       'size':30000000,'mountpoints':[None]}]}]
        with mock.patch.object(core, 'lsblk_tree', return_value=disks):
            result = core.detect_devices()
        self.assertEqual([x['path'] for x in result], ['/dev/sda','/dev/sda1','/dev/sda2'])
        self.assertTrue(result[0]['partition_disk'])
        self.assertTrue(result[1]['mounted'])
        self.assertFalse(result[1]['supported'])
        self.assertFalse(result[2]['mounted'])
        self.assertTrue(result[2]['supported'])

    def test_boot_mounted_disk_is_last_and_marked_system(self):
        disks = [{'name':'mmcblk0','path':'/dev/mmcblk0','type':'disk','tran':None,
                  'children':[{'name':'mmcblk0p1','path':'/dev/mmcblk0p1','type':'part',
                               'fstype':'vfat','mountpoints':['/boot/firmware']},
                              {'name':'mmcblk0p2','path':'/dev/mmcblk0p2','type':'part',
                               'fstype':'ext4','mountpoints':[None]}]},
                 {'name':'sda','path':'/dev/sda','type':'disk','tran':'usb','fstype':'vfat'}]
        with mock.patch.object(core,'lsblk_tree',return_value=disks):
            self.assertEqual([d['path'] for d in core.detect_devices()], ['/dev/sda', '/dev/mmcblk0', '/dev/mmcblk0p1', '/dev/mmcblk0p2'])
            self.assertTrue(all(d['is_system'] for d in core.detect_devices()[1:]))

    def test_mounts_string_normalized(self):
        self.assertEqual(core.mount_list({'mountpoints':'/media/pi/Foo'}), ['/media/pi/Foo'])
        self.assertEqual(core.mount_list({'mountpoints':[None, '', '/media/pi/Foo']}), ['/media/pi/Foo'])

    def test_mounted_usb_unmounts_only_after_scan_confirmation(self):
        ui = UI(demo=True)
        ui.show_devices()
        ui.devices=[{'name':'sda1','path':'/dev/sda1','size':1000,'label':'USB',
                     'fs':'VFAT','mounted':True,'supported':True}]
        with mock.patch.object(ui,'begin_unmount') as unmount:
            ui.handle('OK')
            unmount.assert_not_called()
            self.assertEqual(ui.stage,'scan_ready')
            ui.handle('OK')
        unmount.assert_called_once_with()
        ui.close()

    def test_successful_unmount_begins_full_scan(self):
        ui=UI(demo=True)
        ui.show_devices()
        ui.devices=[{'name':'sda1','path':'/dev/sda1','size':1000,'label':'USB',
                     'fs':'VFAT','mounted':True,'supported':True}]
        ui.unmount_path='/dev/sda1'
        ui.unmount_status={'state':'done'}
        ui.stage='unmounting'
        def refresh():
            ui.devices[0]['mounted']=False
            ui.stage='devices'
        with mock.patch.object(ui,'show_devices',side_effect=refresh):
            ui.tick()
        self.assertEqual(ui.stage,'results') # demo scan immediately completes in tick
        ui.close()

    def test_failed_unmount_stays_in_device_selector(self):
        ui=UI(demo=True)
        ui.unmount_status={'state':'error','error':'Drive busy'}
        ui.stage='unmounting'
        ui.tick()
        self.assertEqual(ui.stage,'notice')
        self.assertEqual(ui.return_stage,'devices')
        self.assertIn('Drive busy',ui.message)
        ui.close()

    def test_unrecognised_usb_is_shown_but_never_scanned(self):
        ui=UI(demo=True)
        ui.show_devices()
        ui.devices=[{'name':'sda1','path':'/dev/sda1','size':1000,'label':'USB',
                     'fs':'UNKNOWN','mounted':False,'supported':False}]
        ui.handle('OK')
        self.assertEqual(ui.stage,'notice')
        self.assertIn('Unsupported',ui.message)
        ui.close()

    def test_unmount_uses_udisks_if_allowed(self):
        device={'path':'/dev/sda1','supported':True,'mounted':True}
        with mock.patch.object(core,'detect_devices',return_value=[device]):
            with mock.patch.object(core.shutil,'which',return_value='/usr/bin/udisksctl'):
                with mock.patch.object(core.subprocess,'run', return_value=mock.Mock(returncode=0)) as run:
                    core.unmount_volume('/dev/sda1')
                    self.assertIn('--no-user-interaction', run.call_args.args[0])
                    self.assertNotIn('sudo', run.call_args.args[0])
                    run.assert_called_once()

    def test_root_fallback_when_udisks_has_no_polkit_agent(self):
        device={'path':'/dev/sda1','supported':True,'mounted':True}
        no_auth=mock.Mock(returncode=1,stderr='Not authorized')
        success=mock.Mock(returncode=0)
        with mock.patch.object(core,'detect_devices',return_value=[device]):
            with mock.patch.object(core.shutil,'which',return_value='/usr/bin/udisksctl'):
                with mock.patch.object(core.subprocess,'run',side_effect=[no_auth,success]) as run:
                    core.unmount_volume('/dev/sda1')
        cmd=run.call_args_list[-1].args[0]
        self.assertEqual(cmd[0:3],['/usr/bin/sudo','-n',core.ROOT_HELPER])
        self.assertEqual(cmd[-2:],['unmount','/dev/sda1'])

    def test_unmount_skips_disappeared_or_system_disk(self):
        with mock.patch.object(core,'detect_devices',return_value=[]):
            with mock.patch.object(core.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'missing'):
                    core.unmount_volume('/dev/sda1')
                run.assert_not_called()
        with mock.patch.object(core,'detect_devices',return_value=[
                {'path':'/dev/sda1','mounted':False,'supported':True}]):
            with mock.patch.object(core.subprocess,'run') as run:
                core.unmount_volume('/dev/sda1')
                run.assert_not_called()

    def test_busy_unmount_reports_actionable_message(self):
        device={'path':'/dev/sda1','supported':True,'mounted':True}
        with mock.patch.object(core,'detect_devices',return_value=[device]):
            with mock.patch.object(core.shutil,'which',return_value=None):
                with mock.patch.object(core.subprocess,'run',return_value=mock.Mock(
                    returncode=32,stderr='umount: /dev/sda1: target is busy')):
                    with self.assertRaisesRegex(RuntimeError,'Close any program'):
                        core.unmount_volume('/dev/sda1')


if __name__=='__main__': unittest.main()
