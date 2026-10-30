"""Imaging must not compete with a completed scan's O_EXCL reservation."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.views.desktop_native import DesktopWindow


def _window(active=False):
    window = object.__new__(DesktopWindow)
    source = {'path': '/dev/sdb', 'size': 1024, 'partition_disk': True,
              'label': 'USB', 'is_system': False}
    scan = Mock()
    scan.is_alive.return_value = active
    window.ui = SimpleNamespace(device=source, stage='desktop_methods',
        platform=SimpleNamespace(name='linux'), scan=scan, recover=None,
        signature=None, partition_search=None, unmount_thread=None,
        release_source=Mock())
    window._ensure_windows_raw_access = Mock(return_value=True)
    window.show_modal_notice = Mock()
    window.show = Mock()
    return window


def test_switching_from_completed_partition_scan_releases_exclusive_guard():
    window = _window()
    with patch('src.views.desktop_native.imaging_option_available', return_value=True):
        window.create_disk_image()
    window.ui.release_source.assert_called_once_with()
    assert window.ui.stage == 'desktop_image_setup'
    window.show_modal_notice.assert_not_called()


def test_no_imaging_while_a_scan_still_owns_source():
    window = _window(active=True)
    with patch('src.views.desktop_native.imaging_option_available', return_value=True):
        window.create_disk_image()
    window.ui.release_source.assert_not_called()
    assert window.ui.stage == 'desktop_methods'
    window.show_modal_notice.assert_called_once()
