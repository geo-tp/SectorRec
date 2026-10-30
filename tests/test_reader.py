"""Privileged helper guard tests, without accessing real block devices."""
import json
import stat
import unittest
from unittest import mock
from src.helpers import reader


class ReaderUnmountSafetyTests(unittest.TestCase):
    disks = [
        {'type':'disk','path':'/dev/mmcblk0','children':[
            {'type':'part','path':'/dev/mmcblk0p1','fstype':'vfat',
             'mountpoints':['/boot/firmware']} ]},
        {'type':'disk','path':'/dev/sda','children':[
            {'type':'part','path':'/dev/sda1','fstype':'vfat',
             'mountpoints':['/media/pi/USB']} ]},
    ]

    def test_mounted_usb_only_permitted_for_unmount(self):
        mode=mock.Mock(st_mode=stat.S_IFBLK | 0o660)
        with mock.patch.object(reader.os.path,'realpath',side_effect=lambda p: p), \
             mock.patch.object(reader.os,'stat',return_value=mode), \
             mock.patch.object(reader.subprocess,'check_output',return_value=json.dumps({'blockdevices':self.disks})):
            self.assertEqual(reader.verify_device('/dev/sda1', allow_mounted=True),'/dev/sda1')
            with self.assertRaises(SystemExit):
                reader.verify_device('/dev/sda1')
            with self.assertRaises(SystemExit):
                reader.verify_device('/dev/mmcblk0p1',allow_mounted=True)

    def test_unmount_runs_non_forced_umount_on_verified_device(self):
        with mock.patch.object(reader.os,'geteuid',return_value=0), \
             mock.patch.object(reader.sys,'argv',['reader','unmount','/dev/sda1']), \
             mock.patch.object(reader,'verify_device',return_value='/dev/sda1') as guard, \
             mock.patch.object(reader.subprocess,'run',return_value=mock.Mock(returncode=0)) as run:
            reader.main()
        guard.assert_called_once_with('/dev/sda1',allow_mounted=True,allow_unknown=True)
        self.assertEqual(run.call_args.args[0],['/usr/bin/umount','--','/dev/sda1'])
        self.assertNotIn('-f', run.call_args.args[0])
        self.assertNotIn('-l', run.call_args.args[0])


if __name__=='__main__': unittest.main()
