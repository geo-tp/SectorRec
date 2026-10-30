"""Regression: AppImage library ownership on merged-/usr Ubuntu runners."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == 'nt', reason='Linux AppImage packaging only')
@pytest.mark.parametrize('case, expected', [
    ('diversion_then_owner', 'libext2fs2t64:arm64'),
    ('usrmerge_fallback', 'libext2fs2t64:arm64'),
    ('multiowner', 'libext2fs2t64:arm64'),
    ('only_diversion', None),
    ('unrelated_file', None),
])
def test_appimage_library_owner_ignores_diversions(case, expected):
    """Do not treat 'diversion by ... from' as an installed package name."""
    source = (ROOT / 'build-appimage.sh').read_text()
    start = source.index('find_library_deb_owner() {')
    end = source.index('\n}\n', start) + 2
    helper = source[start:end]
    cmd = f'''set -euo pipefail
{helper}
dpkg-query() {{
  local path="${{@: -1}}"
  case "{case}:$path" in
    diversion_then_owner:/lib/aarch64-linux-gnu/libext2fs.so.2)
      printf 'diversion by libext2fs2t64 from: %s\\nlibext2fs2t64:arm64: %s\\n' "$path" "$path" ;;
    usrmerge_fallback:/lib/aarch64-linux-gnu/libext2fs.so.2)
      printf 'diversion by libext2fs2t64 from: %s\\n' "$path" ;;
    usrmerge_fallback:/usr/lib/aarch64-linux-gnu/libext2fs.so.2)
      printf 'libext2fs2t64:arm64: %s\\n' "$path" ;;
    multiowner:/lib/aarch64-linux-gnu/libext2fs.so.2)
      printf 'libext2fs2t64:arm64, libanother:arm64: %s\\n' "$path" ;;
    only_diversion:*)
      printf 'diversion by libext2fs2t64 from: %s\\n' "$path" ;;
    unrelated_file:*)
      printf 'libnotowner:arm64: /usr/lib/something-else.so.2\\n' ;;
    *) return 1 ;;
  esac
}}
find_library_deb_owner /lib/aarch64-linux-gnu/libext2fs.so.2
'''
    result = subprocess.run(['bash', '-c', cmd], capture_output=True, text=True)
    if expected is None:
        assert result.returncode != 0, result.stdout
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


@pytest.mark.skipif(os.name == 'nt', reason='Linux AppImage packaging only')
def test_appimage_license_checker_remains_fail_closed():
    source = (ROOT / 'build-appimage.sh').read_text()
    assert 'pkg="$(find_library_deb_owner "$library" || true)"' in source
    assert 'Cannot map bundled library $library' in source
    assert 'No license notice for bundled library $library' in source
    assert 'install -D -m 0644 "$copyright"' in source
    assert "dpkg-query -W -f=" in source
