"""Device icon refresh: pure desktop presentation, no storage/scan changes."""
import unittest
from unittest.mock import patch

from src.views.desktop_native import DesktopWindow, BLUE, TEAL


class DeviceIconRefreshTests(unittest.TestCase):
    def paint(self, kind):
        with patch('src.views.desktop_native.tk.Canvas') as factory:
            canvas = DesktopWindow._device_icon(None, object(), kind, '#1B2A3F')
        factory.assert_called_once_with(
            factory.call_args.args[0], width=31, height=31,
            bg='#1B2A3F', highlightthickness=0, bd=0,
        )
        return canvas

    def test_hdd_shows_platter_and_independent_actuator(self):
        icon = self.paint('hdd')
        self.assertEqual(icon.create_polygon.call_count, 1)
        self.assertGreaterEqual(icon.create_oval.call_count, 2)
        arm = icon.create_line.call_args
        self.assertEqual(arm.kwargs['fill'], TEAL)
        self.assertEqual(arm.kwargs['width'], 2)
        self.assertEqual(arm.args[:2], (23, 24))

    def test_ssd_has_drive_enclosure_and_label_without_platter(self):
        icon = self.paint('ssd')
        self.assertEqual(icon.create_oval.call_count, 0)
        self.assertEqual(icon.create_rectangle.call_count, 1)
        self.assertEqual(icon.create_polygon.call_args.kwargs['outline'], TEAL)

    def test_generic_drive_looks_like_two_bays(self):
        icon = self.paint('disk')
        self.assertEqual(icon.create_rectangle.call_count, 2)
        self.assertEqual(icon.create_oval.call_count, 2)
        for call in icon.create_rectangle.call_args_list:
            self.assertEqual(call.kwargs['outline'], BLUE)

    def test_disk_image_has_page_fold_and_disc(self):
        icon = self.paint('image')
        self.assertEqual(icon.create_polygon.call_count, 1)
        self.assertEqual(icon.create_oval.call_count, 2)
        self.assertEqual(icon.create_line.call_count, 1)

    def test_verified_usb_sd_and_partition_unchanged(self):
        usb = self.paint('usb')
        sd = self.paint('sd')
        partition = self.paint('partition')
        self.assertEqual(usb.create_rectangle.call_args_list[0].args, (11, 11, 21, 25))
        self.assertEqual(sd.create_polygon.call_args.args[:4], (8, 5, 21, 5))
        self.assertEqual(partition.create_rectangle.call_args_list[0].args, (4, 4, 23, 23))

    def test_icon_choice_preserves_partition_and_disk_classification(self):
        disk = {'path': '/dev/nvme0n1', 'disk_path': '/dev/nvme0n1',
                'partition_disk': True, 'transport': 'NVME'}
        self.assertEqual(DesktopWindow.device_icon_type(disk), 'ssd')
        self.assertEqual(DesktopWindow.device_icon_type(
            {**disk, 'path': '/dev/nvme0n1p1', 'partition_disk': False}
        ), 'partition')
        self.assertEqual(DesktopWindow.device_icon_type(
            {**disk, 'is_image': True}
        ), 'image')


if __name__ == '__main__':
    unittest.main()
