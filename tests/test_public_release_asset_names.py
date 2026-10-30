"""Public 1.0 asset names must agree across release build, CI and docs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_deb_names_are_consistent():
    script = (ROOT / 'build-deb.sh').read_text()
    workflow = (ROOT / '.github/workflows/packages.yml').read_text()
    # The website is published separately from the 'pages' branch.
    notes = (ROOT / 'RELEASE_NOTES_1.0.md').read_text()
    docs = (ROOT / 'docs/development.md').read_text()
    for name in ('SectorRec-1.0-Linux-amd64.deb',
                 'SectorRec-1.0-Linux-arm64.deb',
                 'SectorRec-1.0-Cardputer-Zero-arm64.deb'):
        assert name in notes and name in docs
    assert 'SectorRec-${version_app}-Linux-amd64.deb' in workflow
    assert 'SectorRec-${version_app}-Linux-arm64.deb' in workflow
    assert 'SectorRec-${version_app}-Cardputer-Zero-arm64.deb' in workflow
    assert 'SectorRec-${app_version}-Linux-amd64.deb' in script
    assert 'SectorRec-${app_version}-Linux-arm64.deb' in script
    assert 'SectorRec-${app_version}-Cardputer-Zero-arm64.deb' in script
    assert 'test "$(dpkg-deb -f "$deb" Version)" = "$version"' in workflow
    for old in ('sectorrec_1.0-9_amd64.deb', 'sectorrec_1.0-9_arm64.deb',
                'sectorrec-cardputer_1.0-9_arm64.deb'):
        assert old not in workflow + notes + docs


def test_release_notes_are_published_from_tag():
    workflow = (ROOT / '.github/workflows/packages.yml').read_text()
    assert "tags: ['v*']" in workflow
    assert 'notes="RELEASE_NOTES_${version}.md"' in workflow
    assert '--notes-file "$notes"' in workflow
    assert 'if: ${{ startsWith(github.ref' in workflow
    assert 'needs.linux.result' in workflow and 'needs.windows.result' in workflow
