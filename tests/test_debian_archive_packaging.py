"""Debian standard source packaging for all Linux .deb editions."""
from pathlib import Path
from unittest.mock import patch

from src.infrastructure.privileged import privileged_command

ROOT = Path(__file__).resolve().parents[1]


def test_debian_archive_uses_one_session_broker_not_individual_pkexec():
    with patch.dict('os.environ', {'SECTORREC_DEBIAN_ARCHIVE': '1',
                                'SECTORREC_CARDPUTER': '0'}, clear=False), \
         patch('src.infrastructure.privileged.shutil.which', return_value='/usr/bin/pkexec'), \
         patch('src.infrastructure.privileged.os.access', return_value=False), \
         patch('src.infrastructure.session_worker.session_environment', return_value=('/tmp/sectorrec-abc', '123', '456')):
        for role in ('reader', 'photo', 'image'):
            cmd = privileged_command(role, '/dev/sdb')
            assert cmd[:4] == ['/usr/bin/python3', '-I', '-m', 'src.infrastructure.session_worker']
            assert cmd[4:] == ['--proxy', '/tmp/sectorrec-abc', '123', '456', role, '/dev/sdb']


def test_debian_existing_device_permission_needs_no_polkit():
    with patch.dict('os.environ', {'SECTORREC_DEBIAN_ARCHIVE': '1',
                                'SECTORREC_CARDPUTER': '0'}, clear=False), \
         patch('src.infrastructure.privileged.shutil.which', return_value='/usr/bin/pkexec'), \
         patch('src.infrastructure.privileged.os.access', return_value=True), \
         patch('src.infrastructure.session_worker.session_environment', return_value=('/tmp/sectorrec-a', '123', '456')):
        assert privileged_command('reader', 'scan', '/dev/sdb') == [
            '/usr/lib/sectorrec/sectorrec-reader', 'scan', '/dev/sdb']
        assert '--proxy' in privileged_command('reader', 'unmount', '/dev/sdb')
        assert '--proxy' in privileged_command('reader', 'smart', '/dev/sdb')


def test_debian_cardputer_uses_its_own_passwordless_policy():
    with patch.dict('os.environ', {'SECTORREC_DEBIAN_ARCHIVE': '1',
                                'SECTORREC_CARDPUTER': '1'}, clear=False), \
         patch('src.infrastructure.privileged.shutil.which', return_value='/usr/bin/pkexec'), \
         patch('src.infrastructure.privileged.os.access', return_value=False), \
         patch('src.infrastructure.session_worker.session_environment', return_value=('/tmp/sectorrec-a', '123', '456')):
        assert privileged_command('reader', 'scan', '/dev/mmcblk0') == [
            '/usr/bin/pkexec', '/usr/lib/sectorrec/sectorrec-reader', 'scan', '/dev/mmcblk0']
        assert privileged_command('backlight', 'panel', '0') == [
            '/usr/bin/pkexec', '/usr/lib/sectorrec/sectorrec-backlight', 'panel', '0']


def test_debian_archive_fails_closed_without_polkit():
    import pytest
    with patch.dict('os.environ', {'SECTORREC_DEBIAN_ARCHIVE': '1'}, clear=False), \
         patch('src.infrastructure.privileged.shutil.which', return_value=None):
        with pytest.raises(RuntimeError, match='Polkit'):
            privileged_command('reader', '/dev/sdb')


def test_all_debs_share_standard_debhelper_source_and_desktop_polkit():
    rules = (ROOT / 'debian/rules').read_text()
    control = (ROOT / 'debian/control').read_text()
    build = (ROOT / 'build-deb.sh').read_text()
    assert 'dpkg-buildpackage -us -uc' in build
    assert 'dh $@ --buildsystem=pybuild' in rules
    assert 'dh_auto_install --destdir=debian/tmp' in rules
    assert 'Package: sectorrec-cardputer' in control
    assert 'Architecture: amd64 arm64' in control
    assert 'Architecture: arm64' in control
    assert '/etc/sudoers' not in (ROOT / 'debian/sectorrec-wrapper').read_text()
    for dependency in ('sleuthkit', 'testdisk', 'gddrescue', 'pkexec'):
        assert dependency in control
    assert 'SECTORREC_DEBIAN_ARCHIVE=1' in (ROOT / 'debian/sectorrec-wrapper').read_text()
    assert 'sudoers' not in (ROOT / 'debian/sectorrec.desktop').read_text()
    assert not (ROOT / 'debian/sectorrec-cardputer.sudoers').exists()
    assert 'sectorrec-cardputer.sudoers' not in rules
    assert 'sectorrec-cardputer.policy' in rules
    assert 'SECTORREC_DEBIAN_ARCHIVE=1' in (ROOT / 'debian/cardputer-wrapper').read_text()


def test_polkit_policy_contains_only_named_root_helpers():
    import xml.etree.ElementTree as ET
    expected = {
        'sectorrec-desktop.policy': {'session'},
        'sectorrec-cardputer.policy': {'reader', 'backlight'},
    }
    for filename, actions in expected.items():
        document = ET.parse(ROOT / 'debian' / filename)
        elems = document.findall('.//action')
        assert {a.attrib['id'].rsplit('.', 1)[1] for a in elems} == actions
        for action in elems:
            path = action.find("annotate[@key='org.freedesktop.policykit.exec.path']")
            assert path is not None and path.text.startswith('/usr/lib/sectorrec/sectorrec-')
            assert path.text != '/usr/bin/python3'
            if filename == 'sectorrec-cardputer.policy':
                assert action.findtext('defaults/allow_any') == 'yes'
                assert action.findtext('defaults/allow_active') == 'yes'
            else:
                assert action.findtext('defaults/allow_any') == 'no'
                assert action.findtext('defaults/allow_active') == 'auth_admin'


def test_no_global_pkexec_or_sudoers_grants():
    rules = (ROOT / 'debian/rules').read_text()
    assert '/etc/sudoers' not in rules
    assert 'sectorrec-authorize-user' not in rules
    assert 'sectorrec-session-server' in rules
    assert '50-sectorrec-access.rules' not in rules
    assert 'etc/polkit-1/rules.d' not in rules
    assert not (ROOT / 'debian/sectorrec-cardputer.sudoers').exists()


def test_no_permanent_user_access_grants():
    assert not (ROOT / 'src/helpers/grant_access.py').exists()
    assert not (ROOT / 'debian/50-sectorrec-access.rules').exists()
    policy = (ROOT / 'debian/sectorrec-desktop.policy').read_text()
    assert 'sectorrec-session-server' in policy
    assert 'sectorrec-grant-access' not in policy
    assert 'auth_admin_keep' not in policy
    assert 'auth_admin' in policy
    postinst = (ROOT / 'debian/sectorrec.postinst').read_text()
    assert '50-sectorrec-user-' in postinst
    assert 'grep -Fqx' in postinst


def test_cardputer_defaults_passwordless_but_only_fixed_helpers():
    assert 'SECTORREC_CARDPUTER=1' in (ROOT / 'debian/cardputer-wrapper').read_text()
    assert not (ROOT / 'debian/sectorrec-wrapper').read_text().find('SECTORREC_CARDPUTER=1') >= 0
    assert 'sectorrec-grant-access' not in (ROOT / 'debian/sectorrec-cardputer.policy').read_text()


def test_appimage_cannot_call_cardputer_backlight_helper():
    import pytest
    from unittest.mock import patch
    with patch.dict('os.environ', {'SECTORREC_APPIMAGE': '1'}, clear=False):
        with pytest.raises(ValueError, match='Cardputer'):
            privileged_command('backlight', 'backlight0', '10')


def test_cardputer_uses_polkit_even_without_graphical_agent():
    with patch.dict('os.environ', {'SECTORREC_DEBIAN_ARCHIVE': '1',
                                'SECTORREC_CARDPUTER': '1'}, clear=False), \
         patch('src.infrastructure.privileged.shutil.which', return_value='/usr/bin/pkexec'):
        assert privileged_command('backlight', 'panel', '0') == [
            '/usr/bin/pkexec', '/usr/lib/sectorrec/sectorrec-backlight', 'panel', '0'
        ]
    assert 'SECTORREC_DEBIAN_ARCHIVE=1' in (ROOT / 'debian/cardputer-wrapper').read_text()


def test_previous_cardputer_sudoers_conffile_is_migrated():
    assert '1.0-6' in (ROOT / 'debian/changelog').read_text()
    migration = (ROOT / 'debian/sectorrec-cardputer.maintscript').read_text()
    assert 'rm_conffile /etc/sudoers.d/sectorrec-cardputer 1.0-6~ sectorrec-cardputer' in migration
    assert 'sectorrec-cardputer.maintscript' not in (ROOT / 'debian/rules').read_text()
