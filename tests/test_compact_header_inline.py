"""A shared, single-line desktop header at normal and compact window sizes."""
import time
import tkinter as tk

from src.controllers.controller import Controller
from src.views.desktop_native import DesktopWindow


def _labels(widget):
    result = []
    if isinstance(widget, tk.Label) and widget.winfo_ismapped():
        result.append(widget)
    for child in widget.winfo_children():
        result.extend(_labels(child))
    return result


def _resize(window, width, height):
    window.root.geometry(f'{width}x{height}')
    window.root.update()
    time.sleep(0.18)  # allow resize debounce
    window.root.update()


def _check_one_line(window, width):
    window.root.update_idletasks()
    labels = {w.cget('text'): w for w in _labels(window.top)}
    names = ('SectorRec', '1 Source', '2 Method', '3 Configure', '4 Review', 'GitHub')
    assert set(names).issubset(labels), labels.keys()
    assert window._header_steps_strip.winfo_manager() == 'grid'
    assert len(window.top.winfo_children()) == 1
    spans = []
    for name in names:
        widget = labels[name]
        left = widget.winfo_rootx() - window.root.winfo_rootx()
        right = left + widget.winfo_width()
        assert 0 <= left < right <= width, (name, left, right, width)
        spans.append((left, right))
        assert abs(widget.winfo_rooty() - labels['SectorRec'].winfo_rooty()) <= 14
    assert all(a[1] < b[0] for a, b in zip(spans, spans[1:])), spans
    assert labels['GitHub'].winfo_rootx() > window.root.winfo_rootx() + width - 105
    assert not any('USB · SD cards' in label.cget('text') for label in _labels(window.top))
    assert not any('Select source' in label.cget('text') for label in _labels(window.top))


def test_unified_header_is_one_line_at_small_and_large_sizes():
    window = DesktopWindow(Controller(demo=True, desktop=True))
    try:
        for width, height in ((1210, 865), (800, 600), (760, 540), (1050, 770)):
            _resize(window, width, height)
            _check_one_line(window, width)
            assert window.top.winfo_height() <= (49 if window._compact else 82)
    finally:
        window.close()


def test_resize_keeps_same_page_and_tracker_in_header():
    window = DesktopWindow(Controller(demo=True, desktop=True))
    try:
        initial_page = window.device_list
        for width, height in ((800, 600), (1210, 865), (760, 540)):
            _resize(window, width, height)
            assert window.device_list is initial_page
            _check_one_line(window, width)
    finally:
        window.close()


def test_initial_compact_window_never_creates_second_tracker(monkeypatch):
    monkeypatch.setattr(DesktopWindow, 'default_window_size', staticmethod(lambda w, h: (800, 600)))
    window = DesktopWindow(Controller(demo=True, desktop=True))
    try:
        window.root.update()
        assert window._compact
        _check_one_line(window, 800)
    finally:
        window.close()


def test_full_size_header_saves_vertical_space_without_shifting_brand_top():
    """One-line header shouldn't reserve an obsolete second-row bottom gap."""
    window = DesktopWindow(Controller(demo=True, desktop=True))
    try:
        _resize(window, 1210, 865)
        brand = window.top.winfo_children()[0].grid_slaves(row=0, column=0)[0]
        assert not window._compact
        assert brand.winfo_rooty() - window.root.winfo_rooty() == 8
        assert window.top.winfo_height() <= 72
        assert window.body.winfo_y() <= 82
        assert str(window.rule.pack_info()['pady']).replace(' ', '') in ('2,0', '(2,0)')
        _resize(window, 800, 600)
        assert window._compact
        assert window.top.winfo_height() <= 49
        _resize(window, 1210, 865)
        assert not window._compact
        assert window.body.winfo_y() <= 82
    finally:
        window.close()
