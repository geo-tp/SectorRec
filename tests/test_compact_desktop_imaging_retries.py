"""Compact desktop layout and imaging mount-race regressions."""
import errno
import os
import time
import tkinter as tk
from unittest.mock import Mock

import pytest

from src.controllers.imaging import ImagingJob
from src.views.desktop_native import DesktopWindow


def test_compact_threshold_has_normal_and_small_defaults():
    assert DesktopWindow.compact_required(800, 600)
    assert DesktopWindow.compact_required(890, 635)
    assert DesktopWindow.compact_required(1210, 650)
    assert not DesktopWindow.compact_required(1210, 865)
    assert not DesktopWindow.compact_required(1050, 770)


def test_spacing_scaling_is_reversible():
    assert DesktopWindow._scaled_spacing((12, 6), 0.88) == (11, 5)
    assert DesktopWindow._scaled_spacing('23 20', 1.0) == (23, 20)


def _header_labels(widget):
    """Find visible labels even when the GitHub badge adds a border frame."""
    labels = []
    if isinstance(widget, tk.Label) and widget.winfo_ismapped():
        labels.append(widget)
    for child in widget.winfo_children():
        labels.extend(_header_labels(child))
    return labels


def test_compact_header_and_page_survive_window_resizing():
    from src.controllers.controller import Controller
    ui = Controller(demo=True, desktop=True)
    window = DesktopWindow(ui)
    try:
        window.root.geometry('1210x800')
        window.root.update()
        time.sleep(0.16)
        window.root.update()
        assert not window._compact
        normal_labels = [w.cget('text') for w in _header_labels(window.top)]
        assert 'GitHub' in normal_labels
        assert window.github_link.master is window.github_border
        assert window.github_border.winfo_ismapped()
        original_list = window.device_list
        window.root.geometry('800x600')
        window.root.update()
        time.sleep(0.16)
        window.root.update()
        assert window._compact
        assert window.device_list is original_list  # Never rebuild active pages
        compact_labels = [w.cget('text') for w in _header_labels(window.top)]
        assert 'GitHub' in compact_labels
        assert window.github_link.master is window.github_border
        assert window.github_border.winfo_ismapped()
        assert window._header_steps_strip.winfo_manager() == "grid"
        assert not any('USB · SD cards' in caption for caption in compact_labels)
        window.root.geometry('1210x800')
        window.root.update()
        time.sleep(0.16)
        window.root.update()
        assert not window._compact
        assert window.device_list is original_list
    finally:
        window.close()


@pytest.fixture
def busy_mount(monkeypatch, tmp_path):
    from src.controllers import imaging
    from src.infrastructure import linux_devices, source_guard
    from src.services import image_rescue
    monkeypatch.setattr(imaging.sys, 'platform', 'linux')
    umount = Mock()
    settle = Mock()
    guard = Mock()
    worker = Mock()
    monkeypatch.setattr(linux_devices, 'unmount_disk_children', umount)
    monkeypatch.setattr(linux_devices, 'wait_for_unmounted_source', settle)
    monkeypatch.setattr(source_guard.SourceMountGuard, 'acquire', guard)
    monkeypatch.setattr(image_rescue, 'ImageWorker', worker)
    source = {'path': '/dev/sdz', 'size': 1000, 'partition_disk': True}
    job = ImagingJob(source, tmp_path / 'backup.img', {'state': 'preparing'})
    job.cancel_event.wait = Mock(return_value=False)
    return job, umount, settle, guard, worker


def test_busy_mount_rechecks_unmount_and_reserves_again(busy_mount):
    job, umount, settle, guard, worker = busy_mount
    guard.side_effect = [OSError(errno.EBUSY, 'Device or resource busy'), Mock()]
    job.run()
    assert umount.call_count == 2
    assert settle.call_count == 2
    assert guard.call_count == 2
    worker.return_value.run.assert_called_once()
    assert job.status['state'] != 'error'


def test_automounter_remount_during_settle_is_retried(busy_mount):
    job, umount, settle, guard, worker = busy_mount
    settle.side_effect = [RuntimeError('The source was mounted again by the system'), None]
    job.run()
    assert umount.call_count == 2
    guard.assert_called_once()
    worker.return_value.run.assert_called_once()


def test_persistently_busy_disk_never_starts_imaging(busy_mount):
    job, umount, settle, guard, worker = busy_mount
    guard.side_effect = OSError(errno.EBUSY, 'Device or resource busy')
    job.run()
    assert umount.call_count == 3
    assert settle.call_count == 3
    assert guard.call_count == 3
    worker.assert_not_called()
    assert job.status['state'] == 'error'
    assert 'Close the file manager' in job.status['error']


def test_permission_failure_is_not_retried(busy_mount):
    job, umount, settle, guard, worker = busy_mount
    guard.side_effect = RuntimeError('Polkit authorization denied')
    job.run()
    umount.assert_called_once()
    guard.assert_called_once()
    worker.assert_not_called()
    assert job.status['state'] == 'error'


def test_cancel_between_reservations_stops_without_imaging(busy_mount):
    job, umount, settle, guard, worker = busy_mount
    guard.side_effect = OSError(errno.EBUSY, 'Device or resource busy')
    job.cancel_event.wait = lambda _: (job.cancel() or True)
    job.run()
    assert job.status['state'] == 'cancelled'
    guard.assert_called_once()
    worker.assert_not_called()


def test_device_cards_are_compact_before_first_idle_flush(monkeypatch):
    """Returning to Devices must not paint large cards before shrinking them."""
    from src.controllers.controller import Controller
    window = DesktopWindow(Controller(demo=True, desktop=True))
    try:
        window.root.geometry('800x600')
        window.root.update()
        time.sleep(0.16)
        window.root.update()
        assert window._compact
        assert window._device_rows

        painted = []
        original_update = window.device_list.update_idletasks

        def inspect_first_flush():
            item, _caption = window._device_rows[0]
            painted.append((int(item.cget('padx')), int(item.cget('pady'))))
            original_update()

        monkeypatch.setattr(window.device_list, 'update_idletasks', inspect_first_flush)
        window._render_devices()
        assert painted == [(11, 6)]  # 12x7px card padding scaled by 0.88
    finally:
        window.close()
