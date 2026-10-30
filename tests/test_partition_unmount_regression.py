"""TestDisk -> selected partition -> Recover Files, including auto-remount."""
from unittest import TestCase, mock
import time

from src.infrastructure import linux_devices
from src.infrastructure.platforms import LinuxPlatform
from src.controllers.controller import Controller
from src.services.partition import PartitionCandidate


class PartitionAutoRemountTests(TestCase):
    @staticmethod
    def tree(mounted=True):
        return [{'path': '/dev/sdz', 'type': 'disk', 'name': 'sdz', 'mountpoints': [],
                 'children': [{'path': '/dev/sdz1', 'type': 'part', 'name': 'sdz1',
                               'mountpoints': ['/media/user/USB'] if mounted else [],
                               'fstype': 'vfat', 'size': 1024}]}]

    def test_whole_disk_fallback_reuses_privileged_helper(self):
        command = mock.Mock(return_value=['sudo', '-A', 'reader', 'unmount', '/dev/sdz1'])
        bad_udisks = mock.Mock(returncode=1, stderr='Not authorized', stdout='')
        good_helper = mock.Mock(returncode=0, stderr='', stdout='')
        with mock.patch.object(linux_devices, 'lsblk_tree', return_value=self.tree()), \
             mock.patch.object(linux_devices, 'detect_devices',
                 return_value=[{'path':'/dev/sdz1', 'mounted':True, 'is_system':False,
                                'partition_disk':False}]), \
             mock.patch.object(linux_devices.shutil, 'which', return_value='/usr/bin/udisksctl'), \
             mock.patch.object(linux_devices.subprocess, 'run', side_effect=[bad_udisks, good_helper]) as run:
            linux_devices.unmount_disk_children('/dev/sdz', helper_command=command)
        command.assert_called_once_with('unmount', '/dev/sdz1')
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[-1].args[0][0:3], ['sudo','-A','reader'])

    def test_whole_disk_direct_fallback_has_clear_error_if_helper_missing(self):
        with mock.patch.object(linux_devices, 'lsblk_tree', return_value=self.tree()), \
             mock.patch.object(linux_devices, 'detect_devices',
                 return_value=[{'path':'/dev/sdz1', 'mounted':True}]), \
             mock.patch.object(linux_devices.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Privileged recovery helper is unavailable'):
                linux_devices.unmount_disk_children('/dev/sdz')

    def test_platform_routes_helper_for_whole_disk(self):
        whole={'path':'/dev/sdz','partition_disk':True}
        with mock.patch.object(linux_devices, 'detect_devices', return_value=[whole]), \
             mock.patch.object(linux_devices,'unmount_disk_children') as unmount:
            LinuxPlatform().unmount('/dev/sdz')
        self.assertEqual(unmount.call_args.args, ('/dev/sdz',))
        self.assertTrue(callable(unmount.call_args.kwargs['helper_command']))

    def test_testdisk_recover_files_prepares_source_before_scan(self):
        ui=Controller(desktop=True)
        try:
            ui.devices=[{'path':'/dev/sdz', 'mounted':False, 'partition_disk':True,
                         'size':100000, 'fs':'DISK', 'supported':False}]
            ui.selection=0
            ui.partition_status={'candidates':[PartitionCandidate(2048,8192,'FAT32')]}
            with mock.patch.object(ui,'begin_unmount') as unmount, \
                 mock.patch.object(ui,'begin_scan') as scan:
                ui.begin_partition_file_scan()
            unmount.assert_called_once_with(next_action='partition_files')
            scan.assert_not_called()
        finally: ui.close()

    def test_remount_during_settle_rejects_recovery(self):
        states=[False, False, True]
        def devices():
            state=states.pop(0) if len(states)>1 else states[0]
            return [{'path':'/dev/sdz','mounted':state}]
        with mock.patch.object(linux_devices.time, 'sleep', return_value=None):
            # Simulated time gives deterministic settle progression.
            clock=iter([0.0, 0.3, 0.7, 1.0])
            with self.assertRaisesRegex(RuntimeError, 'mounted again'):
                linux_devices.wait_for_unmounted_source('/dev/sdz', devices,
                    clock=lambda:next(clock), sleep=lambda _:None)
