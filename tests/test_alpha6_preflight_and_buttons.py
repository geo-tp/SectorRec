"""Alpha6 regressions: no recursive lsblk, safe PhotoRec target, and button repaint."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import signature
from src.controllers.controller import Controller
from src.views.desktop_native import DesktopWindow, TEAL, TEAL_DARK


class DiskAncestorTests(unittest.TestCase):
    def setUp(self):
        # These tests exercise the lsblk fallback. Host sysfs must not supply
        # real NVMe ancestors for the synthetic /dev names used below.
        sysfs = tempfile.TemporaryDirectory()
        self.addCleanup(sysfs.cleanup)
        def path(value):
            return (Path(sysfs.name) / str(value).lstrip('/')
                    if str(value).startswith('/sys/') else Path(value))
        patcher = patch.object(signature, 'Path', side_effect=path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_physical_parent_query_never_descends_into_partitions(self):
        queried = []
        def fake_run(argv, **kw):
            self.assertIn('--nodeps', argv)
            self.assertIn('--paths', argv)
            path = argv[-1]
            queried.append(path)
            return subprocess.CompletedProcess(argv, 0,
                    '/dev/sdd\n' if path == '/dev/sdd1' else '', '')
        with patch.object(signature.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(signature._base_device('/dev/sdd1'), '/dev/sdd')
        self.assertEqual(queried, ['/dev/sdd1', '/dev/sdd'])

    def test_cycle_does_not_raise_recursion_error_and_is_rejected(self):
        def fake_run(argv, **kw):
            mapping = {'/dev/sdx': '/dev/sdy\n', '/dev/sdy': '/dev/sdx\n'}
            return subprocess.CompletedProcess(argv, 0, mapping[argv[-1]], '')
        with patch.object(signature.subprocess, 'run', side_effect=fake_run):
            self.assertIsNone(signature._base_device('/dev/sdx'))

    def test_same_physical_disk_is_rejected_even_on_another_partition(self):
        def fake_run(argv, **kw):
            if argv[0] == 'findmnt':
                return subprocess.CompletedProcess(argv, 0, '/dev/sdd2\n', '')
            disk = {'/dev/sdd1': '/dev/sdd\n', '/dev/sdd2': '/dev/sdd\n', '/dev/sdd': ''}
            return subprocess.CompletedProcess(argv, 0, disk[argv[-1]], '')
        with patch.object(signature.subprocess, 'run', side_effect=fake_run), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/sdd2'):
            with self.assertRaisesRegex(ValueError, 'source disk'):
                signature.ensure_different_disk('/dev/sdd1', '/home/user/Downloads/assets')

    def test_separate_disk_allows_destination_folder(self):
        def fake_run(argv, **kw):
            if argv[0] == 'findmnt':
                return subprocess.CompletedProcess(argv, 0, '/dev/nvme0n1p2\n', '')
            disk = {'/dev/sdd1': '/dev/sdd\n', '/dev/sdd': '',
                    '/dev/nvme0n1p2': '/dev/nvme0n1\n', '/dev/nvme0n1': ''}
            return subprocess.CompletedProcess(argv, 0, disk[argv[-1]], '')
        with patch.object(signature.subprocess, 'run', side_effect=fake_run), \
             patch.object(signature, '_mounted_linux_block_device', return_value='/dev/nvme0n1p2'):
            signature.ensure_different_disk('/dev/sdd1', '/home/user/Downloads/assets')


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'Tk display required')
class SelectedSourceButtonTests(unittest.TestCase):
    def setUp(self):
        self.ui = Controller(demo=True, desktop=True)
        self.app = DesktopWindow(self.ui)
        self.app.root.update()

    def tearDown(self):
        self.app.confirm_action = lambda *args, **kwargs: True
        self.app.close()

    def test_button_repainted_when_becoming_enabled(self):
        button = self.app.source_continue
        self.app.set_button_enabled(button, False)
        self.assertEqual(button.cget('state'), 'disabled')
        self.app.select_source(0)
        self.app.root.update()
        self.assertEqual(button.cget('state'), 'normal')
        self.assertEqual(button.cget('bg'), TEAL)
        self.assertEqual(button.cget('fg'), TEAL_DARK)

    def test_continue_navigation_after_select(self):
        self.app.select_source(1)
        self.app.source_continue.invoke()
        self.assertEqual(self.ui.stage, 'desktop_methods')


if __name__ == '__main__':
    unittest.main()
