"""PC-only regression checks for inline imaging and native image file selection."""
import inspect
from src.views.desktop_native import DesktopWindow
from src.services.partition import discover_command

def test_whole_optional_card_clicks_without_redundant_button():
    code=inspect.getsource(DesktopWindow.show_methods)
    assert 'click_image(outside)' in code
    assert "self.button(inside,'Create image →'" not in code

def test_inline_imaging_no_popup_and_full_bar_on_success():
    code=inspect.getsource(DesktopWindow._start_disk_image)
    assert 'self.clear(' in code
    assert 'tk.Toplevel(' not in code
    assert "progress.configure(mode='determinate',maximum=100,value=100)" in code
    assert "progress.configure(mode='determinate',maximum=100,value=max" in code

def test_native_image_dialog():
    code=inspect.getsource(DesktopWindow.pick_source_image)
    assert 'zenity' in code and 'kdialog' in code
    assert 'askopenfilename' in code

def test_partition_search_runs_real_deep_search(tmp_path):
    source=tmp_path/'disk.img'
    source.write_bytes(b'\x00'*512)
    assert discover_command(str(source),image=True)[-1]=='analyze,search'
