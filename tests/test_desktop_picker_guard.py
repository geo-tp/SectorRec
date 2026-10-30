"""Regression: desktop native dialogs must not replay queued Browse clicks."""
import subprocess
from unittest.mock import patch

from src.views.desktop_native import DesktopWindow


class _FakeRoot:
    def __init__(self):
        self.pending = []

    def after(self, milliseconds, callback):
        self.pending.append((milliseconds, callback))

    def drain(self):
        pending, self.pending = self.pending, []
        for _, callback in pending:
            callback()


def _window():
    window = DesktopWindow.__new__(DesktopWindow)
    window.root = _FakeRoot()
    window._native_picker_busy = False
    return window


def test_linux_zenity_browse_does_not_reopen_from_queued_click():
    window = _window()
    seen = []

    def run_native(command, **kwargs):
        seen.append(command)
        # A second click while Zenity runs must not start another chooser.
        assert window.pick_destination_directory() == ''
        return subprocess.CompletedProcess(command, 0, '/mnt/external\n', '')

    with patch('src.views.desktop_native.sys.platform', 'linux'), \
         patch('src.views.desktop_native.shutil.which', return_value='/usr/bin/zenity'), \
         patch('src.views.desktop_native.subprocess.run', side_effect=run_native), \
         patch('src.views.desktop_native.filedialog.askdirectory') as fallback:
        assert window.pick_destination_directory() == '/mnt/external'
        # Tk may deliver a queued click immediately after Zenity exits.
        assert window.pick_destination_directory() == ''
        assert len(seen) == 1
        assert window.root.pending[0][0] >= 200
        window.root.drain()
        assert window.pick_destination_directory() == '/mnt/external'
        assert len(seen) == 2
        fallback.assert_not_called()


def test_windows_tk_directory_picker_ignores_reentrant_and_queued_browse():
    window = _window()
    selections = []

    def show_dialog(**kwargs):
        selections.append(kwargs)
        assert window.pick_destination_directory() == ''
        return 'D:/Recovered'

    with patch('src.views.desktop_native.sys.platform', 'win32'), \
         patch('src.views.desktop_native.filedialog.askdirectory', side_effect=show_dialog):
        assert window.pick_destination_directory() == 'D:/Recovered'
        assert window.pick_destination_directory() == ''
        assert len(selections) == 1
        window.root.drain()
        assert window.pick_destination_directory() == 'D:/Recovered'
        assert len(selections) == 2


def test_file_picker_and_folder_picker_share_the_same_guard():
    window = _window()

    def show_file_dialog(**kwargs):
        assert window.pick_destination_directory() == ''
        assert window.pick_source_image() == ''
        return 'C:/backup.img'

    with patch('src.views.desktop_native.sys.platform', 'win32'), \
         patch('src.views.desktop_native.filedialog.askopenfilename', side_effect=show_file_dialog) as choose_file, \
         patch('src.views.desktop_native.filedialog.askdirectory') as choose_folder:
        assert window.pick_source_image() == 'C:/backup.img'
        assert window.pick_destination_directory() == ''
        choose_folder.assert_not_called()
        assert choose_file.call_count == 1
        window.root.drain()
        assert window.pick_source_image() == 'C:/backup.img'
        assert choose_file.call_count == 2
