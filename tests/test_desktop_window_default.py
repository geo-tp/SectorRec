"""Desktop starts full-size on all architectures; compact UI is resize-driven."""
from unittest.mock import patch

from src.views.desktop_native import DesktopWindow


def test_linux_desktop_default_is_full_size_on_all_architectures():
    for architecture in ('aarch64', 'arm64', 'x86_64', 'amd64'):
        with patch('src.views.desktop_native.sys.platform', 'linux'):
            assert DesktopWindow.default_window_size(1920, 1080) == (1210, 865)


def test_windows_desktop_default_is_full_size():
    with patch('src.views.desktop_native.sys.platform', 'win32'):
        assert DesktopWindow.default_window_size(1920, 1080) == (1210, 865)


def test_small_displays_keep_existing_margins():
    assert DesktopWindow.default_window_size(800, 600) == (760, 540)
    assert DesktopWindow.default_window_size(1280, 720) == (1210, 635)


def test_resizing_to_800x600_enables_compact_ui_and_can_expand():
    assert DesktopWindow.compact_required(800, 600)
    assert not DesktopWindow.compact_required(1210, 865)


def test_startup_geometry_is_centered_at_default_resolution():
    # 1210x865 on a Full HD screen: the first paint must be central.
    assert DesktopWindow.centered_window_geometry(1210, 865, 1920, 1080) == '1210x865+355+107'
    assert DesktopWindow.centered_window_geometry(800, 600, 1280, 720) == '800x600+240+60'


def test_center_geometry_never_uses_negative_window_coordinates():
    assert DesktopWindow.centered_window_geometry(1210, 865, 800, 600) == '1210x865+0+0'


def test_real_window_opens_in_screen_center():
    # Verifies actual startup Tk geometry, not just the helper calculation.
    from src.controllers.controller import Controller
    window = DesktopWindow(Controller(demo=True, desktop=True))
    try:
        window.root.update_idletasks()
        window.root.update()
        geometry = window.root.geometry()
        width, height = DesktopWindow.default_window_size(
            window.root.winfo_screenwidth(), window.root.winfo_screenheight())
        assert geometry.startswith(f'{width}x{height}+')
        assert window.root.winfo_x() == max(0, (window.root.winfo_screenwidth() - width) // 2)
        assert window.root.winfo_y() == max(0, (window.root.winfo_screenheight() - height) // 2)
    finally:
        window.close()
