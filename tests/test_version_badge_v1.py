"""Minimal regression tests for the application version badge."""
import tkinter as tk
import os
from unittest import mock

# An older test deliberately removes DISPLAY and does not restore it.
_INITIAL_DISPLAY = os.environ.get("DISPLAY")

from src import __version__
from src.views.desktop_native import DesktopWindow
from src.views.view import PillowView


def _widget_texts(widget):
    texts = []
    try:
        if 'text' in widget.keys():
            texts.append(widget.cget('text'))
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        texts.extend(_widget_texts(child))
    return texts


def test_desktop_brand_has_small_dynamic_version_badge():
    if not _INITIAL_DISPLAY:
        import pytest
        pytest.skip('A graphical display is required for Tk tests')
    with mock.patch.dict(os.environ, {'DISPLAY': _INITIAL_DISPLAY}):
        root = tk.Tk()
        root.withdraw()
        try:
            view = DesktopWindow.__new__(DesktopWindow)
            view.root = root
            view.top = tk.Frame(root)
            view.top.pack()
            view._brand()
            texts = _widget_texts(view.top)
            assert texts.count('SectorRec') == 1
            assert texts.count('v' + __version__) == 1
        finally:
            root.destroy()


def test_cardputer_header_badge_stays_clear_of_stage_tag():
    view = PillowView(object())
    view.viewport_height = 170
    seen = []
    original = view.label
    def capture(d, xy, s, *args, **kwargs):
        seen.append((xy, s))
        return original(d, xy, s, *args, **kwargs)
    view.label = capture
    image, _ = view.base('PARTITION')
    assert image.size == (320, 170)
    assert any(text == 'v' + __version__ and pos[0] < 200 for pos, text in seen)
    assert any(text == 'PARTITION' and pos[0] > 200 for pos, text in seen)
