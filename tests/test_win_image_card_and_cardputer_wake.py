"""Regression tests: Windows partition imaging option, inert LCD wake on Zero.

No tests touch physical devices or invoke ddrescue / recovery utilities.
"""
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.views.desktop_native import imaging_option_available
from src.views.cardputer.framebuffer_frontend import dispatch_input
from src.views.cardputer.keyboard import Keyboard, RepeatTracker
from src.views.cardputer.idle_dim import IdleDimmer
from src.app import UI


def test_cardputer_keyboard_import_without_unix_fcntl():
    """Windows can collect wake/repeat tests without Linux-only fcntl."""
    import builtins
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'src/views/cardputer/keyboard.py'
    spec = importlib.util.spec_from_file_location('cardputer_keyboard_without_fcntl', path)
    module = importlib.util.module_from_spec(spec)
    original_import = builtins.__import__

    def no_fcntl(name, *args, **kwargs):
        if name == 'fcntl':
            raise ModuleNotFoundError("No module named 'fcntl'")
        return original_import(name, *args, **kwargs)

    with patch('builtins.__import__', side_effect=no_fcntl):
        spec.loader.exec_module(module)

    assert module.fcntl is None
    assert module.RepeatTracker().feed(17, 103, 1, 0.0) == ['UP']
    with pytest.raises(RuntimeError, match='requires Linux'):
        module.Keyboard()


DISK = r'\\.\PhysicalDrive7'
VOLUME = r'\\.\E:'


def source(path=VOLUME, **extra):
    return dict(path=path, disk_path=DISK, fs='FAT32', size=80_000,
                label='SD card', mounted=False, supported=True,
                partition_disk=path == DISK, is_system=False,
                **extra)


@pytest.mark.parametrize('path', [VOLUME, DISK])
def test_win_option_for_whole_disk_and_partition(path):
    assert imaging_option_available(source(path), 'win32')


def test_win_partition_choice_not_hidden_if_partition_number_missing():
    assert imaging_option_available(source(partition_number=0), 'win32')


def test_image_option_not_offered_for_system_image_or_invalid_paths():
    for extra in ({'is_system': True}, {'is_image': True}, {'diagnostics_only': True}):
        assert not imaging_option_available({**source(), **extra}, 'win32')
    assert not imaging_option_available(source(path='C:\\Windows'), 'win32')
    assert not imaging_option_available(source(path='/dev/sda1'), 'win32')
    assert imaging_option_available(source(path='/dev/sda1'), 'linux')
    assert not imaging_option_available(source(path='/dev/sda1'), 'linux-image')


@pytest.mark.skipif(not os.environ.get('DISPLAY') and os.name != 'nt', reason='Tk display required')
def test_win_partition_image_card_visible_and_opens_setup():
    from src.views.desktop_native import DesktopWindow
    platform = SimpleNamespace(name='win32', raw_drives=True)
    ui = UI(demo=True, desktop=True, platform=platform)
    ui.devices = [source(partition_number=0)]
    ui.selection = 0
    window = DesktopWindow(ui)
    try:
        ui.devices = [source(partition_number=0)]
        ui.selection = 0
        ui.stage = 'desktop_methods'
        window.show(force=True)
        window.root.update()
        assert 'image' in window._method_available
        assert window._image_card.winfo_exists()
        assert any('Create a partition image' in w.cget('text') for w in
                   window._image_card.winfo_children()[0].winfo_children()[1].winfo_children()
                   if 'text' in w.keys())
        with patch.object(window, '_ensure_windows_raw_access', return_value=True):
            window.select_method('image', advance=True)
        assert ui.stage == 'desktop_image_setup'
        assert window._image_source['path'] == VOLUME
    finally:
        ui.stage = 'devices'
        window.close()
        ui.close()


class DummyDimmer:
    def __init__(self, off):
        self.off = off
        self.last_input = 0
    def input(self):
        self.last_input += 1
        self.off = False


def fake_keyboard():
    kb = Keyboard.__new__(Keyboard)  # no /dev/input access
    kb.fd = []
    kb.esc_pressed = time.monotonic()
    kb.repeat = RepeatTracker()
    kb.repeat.held = {(3, 103): ('UP', time.monotonic(), time.monotonic() + .1)}
    return kb


@pytest.mark.parametrize('stage', ['scanning', 'partition_scanning', 'recovering'])
def test_esc_only_wakes_off_screen_does_not_stop_work(stage):
    kb = fake_keyboard()
    dim = DummyDimmer(off=True)
    ui = SimpleNamespace(stage=stage, handle=Mock(), exit_progress=0.9)
    assert dispatch_input(ui, kb, dim, ['ESC_DOWN'])
    assert ui.stage == stage
    ui.handle.assert_not_called()
    assert kb.esc_pressed is None
    assert kb.repeat.held == {}
    assert kb.esc_hold_fraction() == 0.0
    assert ui.exit_progress == 0.0
    # The matching ESC release, even much later, must not become BACK.
    with patch('src.views.cardputer.keyboard.select.select', return_value=([], [], [])):
        assert kb.poll(0) == []
    assert dim.last_input == 1
    assert dispatch_input(ui, kb, dim, ['BACK'])
    ui.handle.assert_called_once_with('BACK')  # new press after wake


def test_real_evdev_esc_down_then_release_is_inert_after_wake():
    from src.views.cardputer.keyboard import INPUT_EVENT, EV_KEY, ESC
    kb = Keyboard.__new__(Keyboard)
    kb.fd = [17]
    kb.buffers = {17: bytearray()}
    kb.esc_pressed = None
    kb.repeat = RepeatTracker()
    ui = SimpleNamespace(stage='partition_scanning', handle=Mock(), exit_progress=0.0)
    dim = DummyDimmer(off=True)
    def key(value):
        return INPUT_EVENT.pack(0, 0, EV_KEY, ESC, value)
    with patch('src.views.cardputer.keyboard.select.select', return_value=([17], [], [])), \
         patch('src.views.cardputer.keyboard.os.read', side_effect=[key(1), key(0)]):
        assert dispatch_input(ui, kb, dim, kb.poll(0))
        # ESC release after the screen lights up must NOT be dispatched.
        assert kb.poll(0) == []
    ui.handle.assert_not_called()
    assert ui.stage == 'partition_scanning'


def test_wake_batch_consumes_exit_and_arrow_repeat():
    kb = fake_keyboard()
    ui = SimpleNamespace(stage='partition_scanning', handle=Mock(), exit_progress=.5)
    dim = DummyDimmer(off=True)
    dispatch_input(ui, kb, dim, ['ESC_DOWN', 'UP', 'EXIT'])
    ui.handle.assert_not_called()
    assert kb.repeat.held == {}
    assert ui.stage == 'partition_scanning'


def test_dimmed_but_not_off_key_still_works():
    kb = fake_keyboard()
    ui = SimpleNamespace(stage='scanning', handle=Mock(), exit_progress=0.0)
    dim = DummyDimmer(off=False)
    dispatch_input(ui, kb, dim, ['BACK'])
    ui.handle.assert_called_once_with('BACK')


def test_real_idle_policy_uses_wake_batch_no_action(tmp_path):
    dim = IdleDimmer(backlight_dir=tmp_path)
    dim.update(dim.last_input + 121)
    assert dim.software_off
    ui = SimpleNamespace(stage='scanning', handle=Mock(), exit_progress=0.0)
    kb = fake_keyboard()
    dispatch_input(ui, kb, dim, ['ESC_DOWN'])
    assert not dim.off
    ui.handle.assert_not_called()


def test_cardputer_partition_scan_has_no_new_counter():
    from src.views.view import PillowView
    ui = UI(demo=True)
    try:
        ui.stage = 'partition_scanning'
        ui.partition_status = dict(started=time.monotonic(),
                                   candidates=[SimpleNamespace(origin='found')],
                                   testdisk_io_bytes=4096)
        with patch.object(PillowView, 'label', autospec=True, wraps=PillowView.label) as mock_label:
            ui.render()
            labels = [str(c.args[3]) for c in mock_label.call_args_list if len(c.args) >= 4]
        assert not any('PARTITIONS (' in text or ' NEW)' in text for text in labels)
    finally:
        ui.close()
