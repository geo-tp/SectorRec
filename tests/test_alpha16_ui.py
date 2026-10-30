"""Regression checks for PC-only browsing and pre-imaging setup."""
from pathlib import Path
from src.views.desktop_native import DesktopWindow

def test_streaming_does_not_offer_name_search():
    import inspect
    scan = inspect.getsource(DesktopWindow.show_scanning)
    assert "self.label(toolbar, 'Search:'" not in scan
    assert "self._live_category" in scan
    assert "self._live_extension" in scan

def test_imaging_has_destination_review_before_launch():
    import inspect
    setup=inspect.getsource(DesktopWindow.show_image_setup)
    assert 'Available:' in setup
    assert 'Image size:' in setup
    assert 'plan_image' in setup
    start=inspect.getsource(DesktopWindow.create_disk_image)
    assert "self.ui.stage='desktop_image_setup'" in start
    assert 'self.show(force=True)' in start

def test_optional_image_card_is_fully_clickable():
    import inspect
    method=inspect.getsource(DesktopWindow.show_methods)
    assert 'click_image(outside)' in method
