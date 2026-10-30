"""Packaging regression: optional Windows installer reuses the tested portable EXE."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_reuses_portable_exe_without_elevation():
    iss = (ROOT / 'SectorRec.iss').read_text(encoding='utf8')
    script = (ROOT / 'build-windows-installer.ps1').read_text(encoding='utf8')
    assert 'Source: "dist\\{#AppExe}"' in iss
    assert 'PrivilegesRequired=lowest' in iss
    assert 'DefaultDirName={localappdata}' in iss
    assert 'OutputBaseFilename=SectorRec-{#AppVersion}-Windows-x64-Setup' in iss
    assert '{app}\\{#AppExe}' in iss
    assert "build-windows.ps1 first" in script
    assert 'ISCC' in script
    assert 'build-desktop-bundle.py' not in script
    assert 'Start-Process' not in script


def test_release_and_preflight_publish_both_portable_and_installer():
    for name in ('packages.yml', 'windows-preflight.yml'):
        workflow = (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf8')
        assert 'build-windows.ps1' in workflow
        assert 'build-windows-installer.ps1' in workflow
        assert 'dist/SectorRec.exe' in workflow
        assert 'dist/SectorRec-*-Windows-x64-Setup.exe' in workflow
        assert workflow.index('build-windows.ps1') < workflow.index('build-windows-installer.ps1')


def test_appimage_includes_guarded_privileged_helpers_without_installing_sudoers():
    appimage = (ROOT / 'build-appimage.sh').read_text(encoding='utf8')
    assert 'export SECTORREC_APPIMAGE=1' in appimage
    assert 'export SECTORREC_ROOT_BUNDLE=' in appimage
    assert 'export SUDO_ASKPASS=' in appimage
    assert 'sectorrec-root.pyz' in appimage
    assert 'sudoers.d' not in appimage


def test_windows_source_notices_are_required_for_release():
    installer = (ROOT / 'SectorRec.iss').read_text(encoding='utf8')
    build = (ROOT / 'build-windows.ps1').read_text(encoding='utf8')
    workflow = (ROOT / '.github/workflows/packages.yml').read_text(encoding='utf8')
    assert 'THIRD_PARTY_NOTICES.txt' in installer
    assert 'THIRD_PARTY_NOTICES.txt' in build
    assert 'THIRD_PARTY_NOTICES.txt' in workflow
    assert 'SectorRec-Windows-thirdparty-sources.zip' in build
    assert 'SectorRec-Cygwin-GPL-sources.zip' in build
    assert 'Get-FileHash' in build


def test_windows_release_zip_name_is_consistent_across_build_and_workflows():
    build = (ROOT / 'build-windows.ps1').read_text(encoding='utf8')
    release = (ROOT / '.github/workflows/packages.yml').read_text(encoding='utf8')
    preflight = (ROOT / '.github/workflows/windows-preflight.yml').read_text(encoding='utf8')
    assert 'SectorRec-$version-Windows-x64-Portable.zip' in build
    assert 'SectorRec-${version_app}-Windows-x64-Portable.zip' in release
    for content in (release, preflight):
        assert 'dist/SectorRec-*-Windows-x64-Portable.zip' in content
    for content in (build, release, preflight):
        assert '-experimental' not in content.lower()
    assert 'SectorRec-${version_app}-Windows-x64-Setup.exe' in release


def test_public_release_has_no_unsupported_platform_builds():
    for filename in ('.github/workflows/packages.yml',
                     '.github/workflows/windows-preflight.yml',
                     'build-desktop-bundle.py', 'RELEASE_NOTES_1.0.md'):
        contents = (ROOT / filename).read_text(encoding='utf8').lower()
        assert 'macos' not in contents
        assert 'darwin' not in contents
