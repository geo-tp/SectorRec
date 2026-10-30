"""Prevent package and runtime naming from diverging during releases."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_entry_point_is_named_sectorrec_once():
    config = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    scripts = config.split('[project.scripts]', 1)[1].split('[', 1)[0]
    assert scripts.strip() == 'sectorrec = "src.app:main"'


def test_debian_paths_and_app_launcher_match_runtime():
    builder = (ROOT / 'build-deb.sh').read_text(encoding='utf-8')
    rules = (ROOT / 'debian/rules').read_text(encoding='utf-8')
    assert 'dpkg-buildpackage' in builder
    assert 'dh_auto_install' in rules
    assert 'src/helpers/reader.py' in rules
    assert 'sectorrec-cardputer.policy' in rules
    assert 'sectorrec_applaunch.png' in rules
    assert 'libsectorrec565.so' in rules
    assert 'ln -s sectorrec' not in builder
    launcher = (ROOT / 'debian/sectorrec-cardputer.desktop').read_text()
    assert '/usr/bin/sectorrec' in launcher
    assert '/usr/share/APPLaunch/share/images/sectorrec.png' in launcher


def test_native_blitter_library_and_symbol_agree():
    video = (ROOT / 'src/views/cardputer/video.py').read_text(encoding='utf-8')
    c_source = (ROOT / 'src/views/cardputer/pixel565.c').read_text(encoding='utf-8')
    assert 'libsectorrec565.so' in video
    assert 'native.sr_blit_rgb16' in video
    assert 'void sr_blit_rgb16(' in c_source


def test_named_artwork_matches_ui_and_package():
    assets = ROOT / 'src/assets'
    view = (ROOT / 'src/views/view.py').read_text(encoding='utf-8')
    for name in ('sectorrec.png', 'sectorrec_small.png', 'sectorrec_welcome.png',
                 'sectorrec_applaunch.png', 'sectorrec_desktop_icon.png'):
        assert (assets / name).is_file(), name
    assert 'sectorrec_welcome.png' in view
