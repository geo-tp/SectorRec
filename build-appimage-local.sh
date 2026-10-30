#!/usr/bin/env bash
# One-command build for Debian/Ubuntu desktop. Produces a real x86_64/aarch64 AppImage.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v apt-get >/dev/null 2>&1; then
  echo 'This bootstrap is for Debian/Ubuntu. On other distros, install the' >&2
  echo 'dependencies described in docs/development.md and run build-appimage.sh.' >&2
  exit 2
fi
arch="$(uname -m)"
case "$arch" in
  x86_64) PUBLIC_ARCH=amd64 ;;
  aarch64) PUBLIC_ARCH=arm64 ;;
  *) echo "Unsupported arch $arch" >&2; exit 2 ;;
esac

sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv python3-tk python3-pil \
  sleuthkit testdisk gddrescue smartmontools util-linux sudo udisks2 \
  fonts-dejavu-core curl ca-certificates

# python3-tk installed by apt is built for /usr/bin/python3.  The user's
# interactive `python3` can be a Conda/pyenv/custom interpreter with no _tkinter
# extension, so do not let PATH select that interpreter for this build.
BUILD_PYTHON="${SECTORREC_BUILD_PYTHON:-/usr/bin/python3}"
if [[ ! -x "$BUILD_PYTHON" ]]; then
  echo "Missing build Python: $BUILD_PYTHON" >&2
  exit 2
fi
if ! "$BUILD_PYTHON" -c 'import tkinter, tkinter.ttk' >/dev/null 2>&1; then
  echo "Tkinter is unavailable for $BUILD_PYTHON, even after installing python3-tk." >&2
  echo 'Check /usr/bin/python3 -c "import tkinter" and the python3-tk apt package.' >&2
  exit 2
fi
mkdir -p build dist
# A pre-existing venv may have been created using the wrong Python. Recreate it
# rather than silently reusing that incompatible interpreter.
rm -rf build/appimage-venv
"$BUILD_PYTHON" -m venv --system-site-packages build/appimage-venv
build/appimage-venv/bin/python -m pip install --disable-pip-version-check \
  'pyinstaller>=6,<7' 'Pillow>=9'
if ! build/appimage-venv/bin/python -c 'import tkinter, PIL, PyInstaller'; then
  echo 'AppImage build Python is missing Tkinter, Pillow or PyInstaller.' >&2
  exit 2
fi

# appimagetool's own AppImage is extracted to avoid requiring FUSE on the
# build machine; the produced artifact remains a normal type-2 AppImage.
tooldir="$(mktemp -d)"
trap 'rm -rf "$tooldir"' EXIT
curl -fL --retry 3 -o "$tooldir/appimagetool.AppImage" \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${arch}.AppImage"
chmod +x "$tooldir/appimagetool.AppImage"
(cd "$tooldir" && ./appimagetool.AppImage --appimage-extract >/dev/null)
export PATH="$PWD/build/appimage-venv/bin:$PATH"
APPIMAGETOOL_BIN="$tooldir/squashfs-root/AppRun" ./build-appimage.sh
version="$(python3 -c 'from src import __version__; print(__version__)')"
image="$PWD/dist/SectorRec-${version}-Linux-${PUBLIC_ARCH}.AppImage"
test -s "$image"
chmod +x "$image"

# Smoke-test actual contents with the AppImage's official extract feature.
smoke="$(mktemp -d)"
(cd "$smoke" && "$image" --appimage-extract >/dev/null)
test -s "$smoke/squashfs-root/usr/libexec/sectorrec-root.pyz"
for name in fls icat photorec testdisk ddrescue smartctl sectorrec-askpass; do
  test -x "$smoke/squashfs-root/usr/bin/$name"
done
rm -rf "$smoke"
echo
printf 'Built and inspected: %s\n' "$image"
echo 'Run it as your normal user. sudo/Polkit is requested only for physical disks.'
