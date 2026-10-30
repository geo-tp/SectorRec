#!/usr/bin/env bash
# One standard Debian packaging pipeline for desktop amd64/arm64 and Cardputer arm64.
# For a Debian archive upload, use dpkg-buildpackage against debian/ directly.
set -euo pipefail
cd "$(dirname "$0")"
requested_arch="${1:-$(dpkg --print-architecture)}"
profile="${2:-all}"
native_arch="$(dpkg --print-architecture)"
if [[ "$requested_arch" != "$native_arch" ]]; then
  echo "Build natively on ${requested_arch}; this script does not silently cross-compile." >&2
  exit 2
fi
case "$requested_arch:$profile" in
  amd64:desktop|amd64:all|arm64:all|arm64:desktop|arm64:cardputer) ;;
  *) echo 'Usage: build-deb.sh {amd64|arm64} {all|desktop|cardputer}' >&2; exit 2;;
esac
for tool in dpkg-buildpackage dpkg-parsechangelog dh; do
  command -v "$tool" >/dev/null || { echo "Install Debian packaging dependency: $tool (debhelper, dh-python, pybuild-plugin-pyproject)." >&2; exit 2; }
done
version="$(dpkg-parsechangelog -SVersion)"
upstream="${version%-*}"
app_version="$(python3 -c 'from src import __version__; print(__version__.replace("-", "~"))')"
if [[ "$upstream" != "$app_version" ]]; then
  echo "Debian version $upstream does not match application version $app_version" >&2
  exit 2
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
src="$work/sectorrec-$upstream"
mkdir -p "$src" dist
# Preserve dotfiles such as .github, omit generated state and downloaded tools.
tar -cf - --exclude='./.git' --exclude='./.agents' --exclude='./.codex' --exclude='./.aws' \
  --exclude='./.pytest_cache' --exclude='./.ruff_cache' --exclude='./.pybuild' --exclude='./build' \
  --exclude='./dist' --exclude='./.windows-build' --exclude='./.venv*' \
  --exclude='./squashfs-root' --exclude='*.spec' \
  --exclude='./__pycache__' --exclude='*/__pycache__' --exclude='*.pyc' \
  --exclude='./tsk-bin' --exclude='./photorec-bin' \
  --exclude='./ddrescue-bin' --exclude='./smartctl-bin' --exclude='./*.egg-info' \
  --exclude='./debian/.debhelper' --exclude='./debian/sectorrec' \
  --exclude='./debian/sectorrec-cardputer' --exclude='./debian/tmp' \
  --exclude='./debian/python3-sectorrec' \
  --exclude='./debian/files' --exclude='./debian/*.substvars' \
  --exclude='*.buildinfo' --exclude='*.changes' --exclude='*.dsc' \
  --exclude='*.orig.tar.*' --exclude='*.debian.tar.*' \
  --exclude='./debian/*.debhelper.log' . | tar -xf - -C "$src"
# Non-native 3.0 (quilt) source package needs a matching upstream orig tarball.
tar -czf "$work/sectorrec_${upstream}.orig.tar.gz" -C "$work" \
    --exclude=debian "sectorrec-$upstream"
(cd "$src" && dpkg-buildpackage -us -uc)
shopt -s nullglob
for deb in "$work"/*.deb; do
  name="$(basename "$deb")"
  if [[ "$profile" == desktop && "$name" == sectorrec-cardputer_* ]] || \
     [[ "$profile" == cardputer && "$name" == sectorrec_* ]]; then continue; fi
  case "$name" in
    sectorrec_"$version"_amd64.deb)
      public_name="SectorRec-${app_version}-Linux-amd64.deb" ;;
    sectorrec_"$version"_arm64.deb)
      public_name="SectorRec-${app_version}-Linux-arm64.deb" ;;
    sectorrec-cardputer_"$version"_arm64.deb)
      public_name="SectorRec-${app_version}-Cardputer-Zero-arm64.deb" ;;
    *) echo "Unexpected Debian binary package: $name" >&2; exit 1 ;;
  esac
  # Keep the .deb package's real Debian version; only the GitHub/download
  # filename has the simpler upstream release label.
  test "$(dpkg-deb -f "$deb" Version)" = "$version"
  cp "$deb" "dist/$public_name"
  echo "Built dist/$public_name (Debian package revision $version)"
done
for pkg in "$work"/*.dsc "$work"/*.debian.tar.* "$work"/*.orig.tar.*; do
  [[ -f "$pkg" ]] && cp "$pkg" dist/
done
