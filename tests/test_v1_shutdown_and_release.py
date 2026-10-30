"""Version 1.0 lifecycle regressions (only mocked devices, never live disks)."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from src import __version__
from src.app import main
from src.controllers.controller import Controller
from src.models.core import Catalog
from src.services.image_rescue import ImageWorker

_INITIAL_DISPLAY = os.environ.get('DISPLAY')


class ReleaseLifecycleTests(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual(__version__, '1.0')

    def test_catalog_is_not_closed_while_cancelled_scanner_is_still_using_it(self):
        with tempfile.TemporaryDirectory() as root:
            controller = Controller(demo=True)
            controller.catalog = Catalog(Path(root) / 'scan.sqlite')
            catalog = controller.catalog
            entered, release, was_cancelled = threading.Event(), threading.Event(), threading.Event()

            class SlowScanner(threading.Thread):
                def __init__(self):
                    super().__init__(daemon=True)
                def cancel(self):
                    was_cancelled.set()
                def run(self):
                    entered.set()
                    release.wait(8)
                    catalog.insert_batch([('100', 'safe/deleted.png', 30, 'PHOTOS')])

            scanner = SlowScanner()
            controller.scan = scanner
            scanner.start()
            self.assertTrue(entered.wait(2))
            controller.close()  # Must not close catalog after the timeout.
            self.assertTrue(was_cancelled.is_set())
            self.assertTrue(scanner.is_alive())
            self.assertIs(controller.catalog, catalog)
            try:
                release.set()
                scanner.join(3)
                self.assertFalse(scanner.is_alive())
                controller._close_reaper.join(3)
                self.assertFalse(controller._close_reaper.is_alive())
                self.assertIsNone(controller.catalog)
                with self.assertRaises(Exception):
                    catalog.db.execute('SELECT COUNT(*) FROM entries').fetchone()
            finally:
                release.set()

    def test_cancelled_image_worker_never_launches_process(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            worker = ImageWorker('/dev/mock', Path(td) / 'out.img', 1000, state)
            worker.cancel()
            with mock.patch('src.services.image_rescue.subprocess.Popen') as launch:
                worker.run()
            launch.assert_not_called()
            self.assertEqual(state['state'], 'cancelled')

    def test_cli_device_inventory_uses_platform_adapter_not_linux_lsblk(self):
        class FakePlatform:
            raw_drives = False
            def devices(self):
                return [{'path':'diagnostic-only','diagnostics_only':True}]
        output = io.StringIO()
        with mock.patch('src.app.current_platform', return_value=FakePlatform()), \
             mock.patch.object(sys, 'argv', ['sectorrec', '--list-devices']), \
             contextlib.redirect_stdout(output):
            self.assertIsNone(main())
        self.assertIn('diagnostic-only', output.getvalue())
        with mock.patch('src.app.current_platform', return_value=FakePlatform()), \
             mock.patch.object(sys, 'argv', ['sectorrec', '--known-partitions', 'diagnostic-only']), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 2)

    @unittest.skipUnless(_INITIAL_DISPLAY, 'Tk virtual display needed')
    @mock.patch.dict(os.environ, {'DISPLAY': _INITIAL_DISPLAY} if _INITIAL_DISPLAY else {})
    def test_imaging_cannot_start_after_window_is_closed_during_preparation(self):
        # Legacy package tests may clear DISPLAY; restore it locally while
        # checking Tk, without changing the process environment afterward.
        from src.views.desktop_native import DesktopWindow
        entered, resume = threading.Event(), threading.Event()
        ui = Controller(demo=True, desktop=True)
        window = DesktopWindow(ui)
        window.root.update()
        window.confirm_action = lambda *a, **kw: True
        try:
            with tempfile.TemporaryDirectory() as td:
                output = Path(td) / 'image.img'
                source = {'path':'/dev/mockdisk','size':1000,'partition_disk':True,'label':'mockdisk'}
                def slow_unmount(_dev, **_kwargs):
                    entered.set()
                    resume.wait(5)
                with mock.patch('src.services.image_rescue.plan_image', return_value=output), \
                     mock.patch('src.infrastructure.linux_devices.unmount_disk_children', side_effect=slow_unmount), \
                     mock.patch('src.infrastructure.source_guard.SourceMountGuard.acquire') as acquire, \
                     mock.patch('src.services.image_rescue.ImageWorker') as worker:
                    window._start_disk_image(source, td, output)
                    self.assertTrue(entered.wait(3))
                    window.close()  # Sets cancellation even though no ImageWorker exists.
                    self.assertFalse(window._closed)
                    resume.set()
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline and not window._closed:
                        window.root.update()
                        time.sleep(.02)
                    self.assertTrue(window._closed)
                    acquire.assert_not_called()
                    worker.assert_not_called()
        finally:
            resume.set()
            if not window._closed:
                window.close()
            try: window.root.destroy()
            except Exception: pass


if __name__ == '__main__':
    unittest.main()
