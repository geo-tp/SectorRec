"""Desktop device affordances + physical same-disk PhotoRec regression tests."""
import os
import subprocess
import tempfile
import tkinter as tk
import unittest
from pathlib import Path as RealPath
from unittest.mock import patch

from src.controllers.controller import Controller
from src.services import signature
from src.views.desktop_native import (
    DesktopWindow, GITHUB_PAGES_URL, GITHUB_RELEASES_URL,
    IMAGE_IDLE, IMAGE_SELECTED,
)


class PhysicalSourceSafety(unittest.TestCase):
    @staticmethod
    def _fake_lsblk(argv, **kwargs):
        if argv[0] == 'findmnt':
            return subprocess.CompletedProcess(argv, 0, '/dev/dm-0\n', '')
        if '--json' in argv:
            # This legacy mock deliberately has no topology inventory.
            return subprocess.CompletedProcess(argv, 1, '', 'lsblk JSON unavailable')
        parents = {
            '/dev/dm-0': '', '/dev/nvme0n1p3': '/dev/nvme0n1\n',
            '/dev/nvme0n1': '', '/dev/sdb1': '/dev/sdb\n', '/dev/sdb': '',
        }
        return subprocess.CompletedProcess(argv, 0, parents[argv[-1]], '')

    def _sysfs_layers(self, root):
        base = RealPath(root) / 'sys' / 'class' / 'block'
        for name in ('dm-0', 'nvme0n1p3', 'nvme0n1'):
            (base / name / 'slaves').mkdir(parents=True)
        (base / 'dm-0' / 'slaves' / 'nvme0n1p3').touch()
        def real_path(value):
            if str(value) == '/sys/class/block':
                return base
            return RealPath(value)
        return real_path

    def test_luks_dm_on_same_nvme_rejected_for_whole_system_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(signature, 'Path', side_effect=self._sysfs_layers(temp)), \
                 patch.object(signature.subprocess, 'run', side_effect=self._fake_lsblk), \
                 patch.object(signature, '_mounted_linux_block_device', return_value='/dev/dm-0'):
                with self.assertRaisesRegex(ValueError, 'source disk'):
                    signature.ensure_different_disk('/dev/nvme0n1', '/home/user/Documents')

    def test_different_disk_allowed_even_when_destination_uses_dm(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(signature, 'Path', side_effect=self._sysfs_layers(temp)), \
                 patch.object(signature.subprocess, 'run', side_effect=self._fake_lsblk), \
                 patch.object(signature, '_mounted_linux_block_device', return_value='/dev/dm-0'):
                signature.ensure_different_disk('/dev/sdb', '/home/user/Documents')

    def test_unmapped_dm_is_not_automatically_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            base = RealPath(temp) / 'sys' / 'class' / 'block'
            (base / 'dm-0' / 'slaves').mkdir(parents=True)
            def real_path(value):
                return base if str(value) == '/sys/class/block' else RealPath(value)
            with patch.object(signature, 'Path', side_effect=real_path), \
                 patch.object(signature.subprocess, 'run', side_effect=self._fake_lsblk), \
                 patch.object(signature, '_mounted_linux_block_device', return_value='/dev/dm-0'):
                signature.ensure_different_disk('/dev/nvme0n1', '/home/user/Documents')

    def test_warning_writes_explicit_same_physical_disk_message(self):
        msg = DesktopWindow._explain_destination_error('Destination is on the source disk.')
        self.assertIn('SAME PHYSICAL DISK', msg)
        self.assertIn('different physical disk', msg)


class DevicePresentation(unittest.TestCase):
    def test_whole_device_retains_original_usb_icon_and_partition_is_separate(self):
        common = {'path':'/dev/sdb', 'disk_path':'/dev/sdb', 'transport':'USB'}
        with patch('src.views.desktop_native.sys.platform','linux'):
            self.assertEqual(DesktopWindow.device_icon_type({**common, 'partition_disk':True}), 'usb')
            self.assertEqual(DesktopWindow.device_icon_type({**common, 'partition_disk':True, 'rotational':True}), 'usb')
        self.assertEqual(DesktopWindow.device_icon_type({**common, 'path':'/dev/sdb1', 'partition_disk':False}), 'partition')
        self.assertEqual(DesktopWindow.device_icon_type({**common, 'partition_disk':False}, nested=True), 'partition')
        self.assertEqual(DesktopWindow.device_icon_type({**common, 'partition_disk':True, 'is_image':True}), 'image')


    def test_whole_disk_title_is_desktop_only_and_does_not_strip_normal_names(self):
        raw = {'label':'Samsung SSD  [WHOLE DISK]', 'partition_disk':True}
        self.assertEqual(DesktopWindow.device_display_title(raw), 'Samsung SSD')
        self.assertEqual(raw['label'], 'Samsung SSD  [WHOLE DISK]')
        self.assertEqual(DesktopWindow.device_display_title(
            {'label':'WHOLE DISK Backup', 'partition_disk':False}), 'WHOLE DISK Backup')
        self.assertEqual(DesktopWindow.device_display_title(
            {'label':'Drive [WHOLE DISK]', 'partition_disk':False}), 'Drive [WHOLE DISK]')

    def test_image_tint_is_subtle_and_system_takes_precedence(self):
        idle, border = DesktopWindow._device_row_palette({'is_image':True}, False)
        self.assertEqual(idle, IMAGE_IDLE)
        self.assertEqual(DesktopWindow._device_row_palette({'is_image':True}, True)[0], IMAGE_SELECTED)
        self.assertNotEqual(idle, DesktopWindow._device_row_palette({},False)[0])
        self.assertNotEqual(border, DesktopWindow._device_row_palette({'is_system':True},False)[1])
        self.assertNotEqual(DesktopWindow._device_row_palette({'is_image':True,'is_system':True},False)[0], idle)


@unittest.skipUnless(os.environ.get('DISPLAY'), 'requires Tk display')
class DesktopInteraction(unittest.TestCase):
    def setUp(self):
        self.ui = Controller(demo=True, desktop=True)
        self.window = DesktopWindow(self.ui)
        self.window.root.update()

    def tearDown(self):
        self.window.close()

    def test_disk_image_row_has_only_one_disk_image_badge(self):
        image = {'path':'/home/user/Downloads/SectorRec_sdb_2.img',
                 'label':'SectorRec_sdb_2.img', 'size':519569408,
                 'fs':'IMAGE', 'transport':'IMAGE', 'is_image':True,
                 'supported':True, 'mounted':False}
        self.ui.devices=[image]
        self.ui.selection=0
        self.ui.stage='devices'
        self.window.show(force=True)
        self.window.root.update_idletasks()
        item=self.window._device_rows[0][0]
        def labels(w):
            for c in w.winfo_children():
                if isinstance(c,tk.Label):
                    yield c
                yield from labels(c)
        all_labels=list(labels(item))
        badges=[label.cget('text') for label in all_labels if getattr(label,'_sector_badge',False)]
        self.assertEqual(badges, ['495.5 MB','DISK IMAGE'])
        self.assertEqual(sum('IMAGE' in value for value in badges),1)
        self.assertEqual(sum(label.cget('text') == 'Read-only recovery source' for label in all_labels),1)
        self.assertEqual(item.cget('bg'), IMAGE_SELECTED)

    def test_whole_disk_has_one_visible_badge_and_no_title_suffix(self):
        disk = {'path':'/dev/nvme0n1', 'disk_path':'/dev/nvme0n1',
                'label':'Samsung SSD  [WHOLE DISK]', 'size':100_000_000_000,
                'fs':'DISK', 'transport':'NVME', 'partition_disk':True,
                'supported':False, 'mounted':False}
        self.ui.devices=[disk]
        self.ui.selection=0
        self.ui.stage='devices'
        self.window.show(force=True)
        self.window.root.update_idletasks()
        row=self.window._device_rows[0][0]
        def labels(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.Label):
                    yield child
                yield from labels(child)
        rendered=list(labels(row))
        texts=[child.cget('text') for child in rendered]
        badges=[child.cget('text') for child in rendered if getattr(child,'_sector_badge',False)]
        self.assertIn('Samsung SSD', texts)
        self.assertNotIn('Samsung SSD  [WHOLE DISK]', texts)
        self.assertEqual(badges, ['93.1 GB', 'WHOLE DISK', 'NVME'])
        badge=next(child for child in rendered if child.cget('text') == 'WHOLE DISK')
        self.assertEqual(badge.cget('bg'), '#345573')
        self.assertEqual(disk['label'], 'Samsung SSD  [WHOLE DISK]')

    def test_system_whole_disk_preserves_system_badge(self):
        disk = {'path':'/dev/nvme0n1', 'label':'System  [WHOLE DISK]',
                'size':100_000_000_000, 'fs':'DISK', 'transport':'NVME',
                'partition_disk':True, 'is_system':True}
        self.ui.devices=[disk]
        self.ui.selection=0
        self.ui.stage='devices'
        self.window.show(force=True)
        self.window.root.update_idletasks()
        row=self.window._device_rows[0][0]
        def badges(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.Label) and getattr(child,'_sector_badge',False):
                    yield child.cget('text')
                yield from badges(child)
        result=list(badges(row))
        self.assertEqual(result, ['93.1 GB', 'WHOLE DISK', 'NVME', 'SYSTEM DISK'])

    def test_original_usb_icon_unchanged_and_partition_icon_is_distinct(self):
        parent=tk.Frame(self.window.root)
        usb=self.window._device_icon(parent, 'usb', IMAGE_IDLE)
        partition=self.window._device_icon(parent, 'partition', IMAGE_IDLE)
        self.assertEqual(usb.type(1),'rectangle')
        self.assertEqual(usb.coords(1),[11.0,11.0,21.0,25.0])
        self.assertNotEqual([partition.coords(i) for i in partition.find_all()],
                            [usb.coords(i) for i in usb.find_all()])
        parent.destroy()

    def test_click_logo_opens_pages_and_version_opens_project_releases(self):
        row=self.window.top.winfo_children()[0]
        brand_container=row.grid_slaves(row=0, column=0)[0]
        logo=next(x for x in brand_container.winfo_children() if isinstance(x,tk.Label) and x.cget('image'))
        title_container=next(x for x in brand_container.winfo_children() if isinstance(x,tk.Frame))
        title_row=next(x for x in title_container.winfo_children() if isinstance(x,tk.Frame))
        title=next(x for x in title_row.winfo_children() if isinstance(x,tk.Label) and x.cget('text')=='SectorRec')
        badge=next(x for x in title_row.winfo_children() if isinstance(x,tk.Frame))
        version=next(x for x in badge.winfo_children() if isinstance(x,tk.Label))
        with patch('src.views.desktop_native.webbrowser.open') as open_web:
            for widget, url in ((logo,GITHUB_PAGES_URL), (title,GITHUB_PAGES_URL),
                                (version,GITHUB_RELEASES_URL)):
                widget.event_generate('<Button-1>')
                self.window.root.update()
                open_web.assert_called_with(url)
        self.assertIn('/SectorRec/', GITHUB_PAGES_URL)
        self.assertIn('/SectorRec/releases', GITHUB_RELEASES_URL)

    def test_unsafe_photorec_destination_warns_on_selection_and_disables_start(self):
        with tempfile.TemporaryDirectory() as temp:
            self.ui.devices=[{'path':'/dev/nvme0n1','label':'System', 'is_system':True,
                              'partition_disk':True, 'size':4_000_000_000}]
            self.ui.selection=0
            self.ui.engine='signature'
            self.ui.stage='signature_setup'
            self.ui.demo=False
            self.window.show(force=True)
            with patch.object(self.window,'pick_destination_directory', return_value=temp), \
                 patch.object(self.window,'show_modal_notice') as notice, \
                 patch('src.services.signature.photorec_binary',return_value='/usr/bin/photorec'), \
                 patch('src.services.signature.validate_destination',side_effect=ValueError('Destination is on the source disk.')):
                self.window.choose_destination()
                notice.assert_called_once()
                self.assertEqual(notice.call_args.args[0], 'Unsafe recovery destination')
                self.assertIn('SAME PHYSICAL DISK',notice.call_args.args[1])
                self.assertEqual(self.ui.signature_destination, str(RealPath(temp).resolve()))
                button = next(x for x in self.window.footer.winfo_children()
                              if isinstance(x,tk.Button) and 'Start extraction' in x.cget('text'))
                self.assertEqual(button.cget('state'), 'disabled')
                with patch.object(self.ui,'start_signature_after_confirmation') as start:
                    self.window.start_photo()
                    start.assert_not_called()


if __name__=='__main__':
    unittest.main()
