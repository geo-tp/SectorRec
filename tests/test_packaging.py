"""Portable bundle contents and frontend selection without device access."""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import app


class PackagingTests(unittest.TestCase):
    def test_windows_download_pins_are_valid_and_consistent(self):
        script = Path(__file__).resolve().parents[1] / 'build-windows.ps1'
        downloads = re.findall(
            r"DownloadVerified\s+'([^']+)'\s+'([^']+)'\s+'([^']+)'",
            script.read_text(encoding='utf-8'),
        )
        self.assertGreaterEqual(len(downloads), 2)
        seen = {}
        for url, filename, digest in downloads:
            self.assertRegex(digest, r'^[0-9a-f]{64}$', filename)
            self.assertEqual(seen.setdefault(filename, (url, digest)),
                             (url, digest), filename)

    @classmethod
    def setUpClass(cls):
        script = Path(__file__).resolve().parents[1] / 'build-desktop-bundle.py'
        spec = importlib.util.spec_from_file_location('sectorrec_bundle', script)
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_windows_bundle_requires_both_recovery_toolsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            tsk = base / 'tsk-bin'
            photo = base / 'photorec-bin'
            tsk.mkdir()
            photo.mkdir()
            ddrescue = base / 'ddrescue-bin'
            ddrescue.mkdir()
            for filename in ('ddrescue.exe', 'cygpath.exe', 'cygwin1.dll'):
                (ddrescue / filename).touch()
            for filename in ('fls.exe', 'icat.exe'):
                (tsk / filename).touch()
            (photo / 'photorec_win.exe').touch()
            with mock.patch.dict(self.builder.os.environ, {}, clear=True), \
                 mock.patch.object(self.builder.sys, 'platform', 'win32'):
                with self.assertRaisesRegex(RuntimeError, 'testdisk_win.exe'):
                    self.builder.bundle_command(base)
                (photo / 'testdisk_win.exe').touch()
                command = self.builder.bundle_command(base)
                self.assertIn('--onefile', command)
                self.assertIn('--collect-data', command)
                self.assertEqual(command.count('--add-binary'), 3)
                (ddrescue / 'cygwin1.dll').unlink()
                with self.assertRaisesRegex(RuntimeError, 'cygwin1.dll'):
                    self.builder.bundle_command(base)
                (ddrescue / 'cygwin1.dll').touch()
                (tsk / 'icat.exe').unlink()
                with self.assertRaisesRegex(RuntimeError, 'icat.exe'):
                    self.builder.bundle_command(base)

    def test_desktop_launch_does_not_create_lcd_view(self):
        with mock.patch.object(app.sys, 'argv', ['sectorrec', '--ui', 'desktop', '--demo']), \
             mock.patch.object(app, 'PillowView', side_effect=AssertionError('LCD view on desktop')), \
             mock.patch('src.views.desktop_native.DesktopWindow') as window:
            self.assertEqual(app.main(), 0)
            controller = window.call_args.args[0]
            self.assertTrue(controller.desktop)
            self.assertFalse(hasattr(controller, 'view'))
            window.return_value.run.assert_called_once_with()
            controller.close()

    def test_packaged_artwork_can_be_read_without_source_root(self):
        from importlib.resources import files
        from PIL import Image
        for name in ('sectorrec_welcome.png', 'sectorrec_desktop_mark.png',
                     'sectorrec_desktop_icon.png'):
            with files('src').joinpath('assets', name).open('rb') as asset:
                with Image.open(asset) as image:
                    image.verify()
