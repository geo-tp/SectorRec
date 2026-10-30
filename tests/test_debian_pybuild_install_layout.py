"""Regression tests for pybuild's Python-version-dependent staging paths.

These tests run debian/rules with a fake dh_auto_install, so they do not
require Debhelper and do not alter the host system or make a real .deb.
"""
from pathlib import Path
import os
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('layout', [
    'debian/tmp/usr/lib/python3/dist-packages',
    'debian/tmp/usr/lib/python3.14/site-packages',
    'debian/python3-sectorrec/usr/lib/python3/dist-packages',
])
def test_rules_install_both_supported_pybuild_layouts(tmp_path, layout):
    project = tmp_path / 'SectorRec'
    for folder in ('debian', 'src/helpers', 'src/assets'):
        shutil.copytree(ROOT / folder, project / folder)
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    stub = fake_bin / 'dh_auto_install'
    stub.write_text('''#!/bin/sh
set -eu
stage="${FAKE_PYBUILD_STAGE:?}"
mkdir -p "$stage/src" "$stage/sectorrec-1.0.dist-info"
printf '"""package"""\\n' > "$stage/src/__init__.py"
printf 'def main(): pass\\n' > "$stage/src/app.py"
printf 'Name: sectorrec\\nVersion: 1.0\\n' > "$stage/sectorrec-1.0.dist-info/METADATA"
''')
    stub.chmod(0o755)
    # Refuse accidental writes to /usr or /etc, including on root CI runners.
    # The packaging regression once lost its 'root' shell variable between
    # recipe lines and wrote to /usr instead of debian/sectorrec/usr.
    guarded_install = fake_bin / 'install'
    guarded_install.write_text('''#!/bin/sh
for argument do last="$argument"; done
case "$last" in
  debian/sectorrec/*|debian/sectorrec-cardputer/*) exec /usr/bin/install "$@" ;;
  *) echo "Refusing out-of-tree install destination: $last" >&2; exit 99 ;;
esac
''')
    guarded_install.chmod(0o755)
    env = os.environ.copy()
    env['PATH'] = f'{fake_bin}{os.pathsep}{env.get("PATH", "")}'
    env['FAKE_PYBUILD_STAGE'] = layout
    result = subprocess.run(
        ['make', '-f', 'debian/rules', 'override_dh_auto_install', 'DEB_HOST_ARCH=amd64'],
        cwd=project, env=env, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    installed = project / 'debian/sectorrec/usr/lib/python3/dist-packages'
    assert (installed / 'src/app.py').is_file()
    assert (installed / 'sectorrec-1.0.dist-info/METADATA').is_file()
    assert (project / 'debian/sectorrec/usr/bin/sectorrec').is_file()
    assert (project / 'debian/sectorrec/usr/lib/sectorrec/sectorrec-reader').is_file()
    # Every desktop file must be staged inside the package, not installed
    # into the developer's running system.
    for relative in (
        'usr/lib/sectorrec/sectorrec-session-server',
        'usr/lib/sectorrec/sectorrec-photo-reader',
        'usr/lib/sectorrec/sectorrec-image-reader',
        'usr/share/applications/sectorrec.desktop',
        'usr/share/metainfo/io.github.geo_tp.SectorRec.metainfo.xml',
        'usr/share/polkit-1/actions/io.github.geo_tp.sectorrec.policy',
        'usr/share/icons/hicolor/256x256/apps/sectorrec.png',
    ):
        assert (project / 'debian/sectorrec' / relative).is_file(), relative
    assert not (project / 'debian/tmp').exists()


def test_rules_reject_missing_pybuild_payload(tmp_path):
    project = tmp_path / 'SectorRec'
    for folder in ('debian', 'src/helpers', 'src/assets'):
        shutil.copytree(ROOT / folder, project / folder)
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    stub = fake_bin / 'dh_auto_install'
    stub.write_text('#!/bin/sh\nmkdir -p debian/tmp\n')
    stub.chmod(0o755)
    # Refuse accidental writes to /usr or /etc, including on root CI runners.
    # The packaging regression once lost its 'root' shell variable between
    # recipe lines and wrote to /usr instead of debian/sectorrec/usr.
    guarded_install = fake_bin / 'install'
    guarded_install.write_text('''#!/bin/sh
for argument do last="$argument"; done
case "$last" in
  debian/sectorrec/*|debian/sectorrec-cardputer/*) exec /usr/bin/install "$@" ;;
  *) echo "Refusing out-of-tree install destination: $last" >&2; exit 99 ;;
esac
''')
    guarded_install.chmod(0o755)
    env = os.environ.copy()
    env['PATH'] = f'{fake_bin}{os.pathsep}{env.get("PATH", "")}'
    result = subprocess.run(
        ['make', '-f', 'debian/rules', 'override_dh_auto_install', 'DEB_HOST_ARCH=amd64'],
        cwd=project, env=env, text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert 'did not stage the SectorRec Python package' in result.stderr
