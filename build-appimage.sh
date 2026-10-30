#!/usr/bin/env bash
# Native Linux AppImage: bundled recovery engines, read-only privileged helpers
# through interactive sudo/Polkit when physical block devices are selected.
set -euo pipefail
cd "$(dirname "$0")"
: "${APPIMAGETOOL_BIN:=appimagetool}"
command -v fls >/dev/null || { echo 'Install sleuthkit (fls and icat)' >&2; exit 2; }
command -v icat >/dev/null || exit 2
command -v testdisk >/dev/null || { echo 'Install testdisk' >&2; exit 2; }
command -v ddrescue >/dev/null || { echo 'Install gddrescue' >&2; exit 2; }
command -v smartctl >/dev/null || { echo 'Install smartmontools' >&2; exit 2; }
command -v photorec >/dev/null || { echo 'Install testdisk (PhotoRec)' >&2; exit 2; }
if ! python3 -c 'import PIL, tkinter, PyInstaller'; then
  echo 'AppImage build needs a Python environment with Tkinter, Pillow and PyInstaller.' >&2
  echo 'On Debian/Ubuntu, use ./build-appimage-local.sh (uses /usr/bin/python3).' >&2
  exit 2
fi
ARCH=$(uname -m)
VERSION=$(python3 -c 'from src import __version__; print(__version__)')
case "$ARCH" in
  x86_64) PUBLIC_ARCH=amd64 ;;
  aarch64) PUBLIC_ARCH=arm64 ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 2 ;;
esac
python3 -m PyInstaller --noconfirm --clean --onedir --windowed \
 --name SectorRec --collect-data src launch_desktop.py
APPDIR="$PWD/build/SectorRec.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/opt" "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/libexec" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a dist/SectorRec "$APPDIR/usr/opt/SectorRec"
# Tcl/Tk loads BLT dynamically: PyInstaller's dependency scan can miss it.
# Package it inside the *frozen Python runtime* (_internal), not just alongside
# the unrelated recovery-tool binaries. This makes the GUI portable to Linux
# systems where tk8.6-blt2.5 was never installed.
PYI_LIBDIR="$APPDIR/usr/opt/SectorRec/_internal"
if [[ ! -d "$PYI_LIBDIR" ]]; then
  echo "PyInstaller _internal directory missing: $PYI_LIBDIR" >&2
  exit 1
fi
BLT_SOURCES=()
for pattern in 'libBLT.2.5.so.*' 'libBLTlite.2.5.so.*'; do
  found=0
  for directory in /usr/lib /usr/lib/*-linux-gnu /lib /lib/*-linux-gnu; do
    for library in "$directory"/$pattern; do
      [[ -f "$library" ]] || continue
      name="$(basename "$library")"
      # Keep an additional copy in the AppImage recovery-engine library path;
      # the frozen GUI itself uses the copy in PyInstaller's _internal.
      if [[ ! -f "$PYI_LIBDIR/$name" ]]; then
        cp -L -- "$library" "$PYI_LIBDIR/$name"
      fi
      if [[ ! -f "$APPDIR/usr/lib/$name" ]]; then
        cp -L -- "$library" "$APPDIR/usr/lib/$name"
      fi
      BLT_SOURCES+=("$library")
      found=1
    done
  done
  if (( ! found )); then
    echo "Missing $pattern: install the tk8.6-blt2.5 package on the build host" >&2
    exit 1
  fi
done
# _tkinter is a dynamically loaded PyInstaller extension, never a pure .py.
if ! find "$PYI_LIBDIR" -type f -name '_tkinter*.so' | grep -q .; then
  echo "Missing frozen _tkinter.so in $PYI_LIBDIR" >&2
  exit 1
fi
install -m 0755 "$(command -v fls)" "$APPDIR/usr/bin/fls"
install -m 0755 "$(command -v icat)" "$APPDIR/usr/bin/icat"
install -m 0755 "$(command -v photorec)" "$APPDIR/usr/bin/photorec"
install -m 0755 "$(command -v testdisk)" "$APPDIR/usr/bin/testdisk"
install -m 0755 "$(command -v ddrescue)" "$APPDIR/usr/bin/ddrescue"
install -m 0755 "$(command -v smartctl)" "$APPDIR/usr/bin/smartctl"
# The helpers run under *system* Python 3 in isolated mode (stdlib only),
# not under the GUI's PyInstaller runtime. A single portable, read-only zipapp
# is used for all three fixed-operation helpers; no files installed on the host.
ROOTSRC="$(mktemp -d)"
trap 'rm -rf "$ROOTSRC"' EXIT
mkdir -p "$ROOTSRC/src"
find src -type f -name '*.py' -print0 | while IFS= read -r -d '' py; do
  mkdir -p "$ROOTSRC/$(dirname "$py")"
  cp "$py" "$ROOTSRC/$py"
done
cp appimage-root-main.py "$ROOTSRC/__main__.py"
python3 -m zipapp "$ROOTSRC" -o "$APPDIR/usr/libexec/sectorrec-root.pyz"
cat > "$APPDIR/usr/bin/sectorrec-askpass" <<'ASKPASS'
#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# This is a *new* instance of the frozen app, not a fork/worker sharing the
# parent PyInstaller bootloader state. Otherwise Tcl/Tk may fail to load BLT.
export PYINSTALLER_RESET_ENVIRONMENT=1
exec "$HERE/usr/opt/SectorRec/SectorRec" --sectorrec-askpass "$@"
ASKPASS
chmod +x "$APPDIR/usr/bin/sectorrec-askpass"
# Package binary dependencies of the TSK programs; do not overwrite core glibc
# libraries needed from the target host. Build on an older Linux for reach.
for prog in "$(command -v fls)" "$(command -v icat)" "$(command -v photorec)" "$(command -v testdisk)" "$(command -v ddrescue)" "$(command -v smartctl)"; do
  while IFS= read -r lib; do
    [[ -f "$lib" ]] || continue
    name=$(basename "$lib")
    case "$name" in libc.so.*|libm.so.*|libpthread.so.*|libdl.so.*|librt.so.*|ld-linux*.so.*|libresolv.so.*|libnss_*.so.*) continue;; esac
    # Avoid the deprecated cp -n warning, while preserving first-copy-wins.
    if [[ ! -e "$APPDIR/usr/lib/$name" ]]; then
      cp -L "$lib" "$APPDIR/usr/lib/$name"
    fi
  done < <(ldd "$prog" | awk '/=> \/|^\s*\// { for (i=1;i<=NF;i++) if ($i ~ /^\//) {print $i;break} }')
done
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PATH="$HERE/usr/bin:$PATH"
# Save the user's loader path BEFORE adding bundled recovery tool libraries.
# Host utilities (sudo, Zenity, KDialog, UDisks) must not see AppImage libraries.
if [ "${LD_LIBRARY_PATH+x}" = x ]; then
  export SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT=1
  export SECTORREC_HOST_LD_LIBRARY_PATH="$LD_LIBRARY_PATH"
else
  export SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT=0
  unset SECTORREC_HOST_LD_LIBRARY_PATH
fi
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Only the guarded helper process gets root rights, through an explicit
# sudo/Polkit prompt. The desktop process remains the invoking user.
export SECTORREC_APPIMAGE=1
export SECTORREC_ROOT_BUNDLE="$HERE/usr/libexec/sectorrec-root.pyz"
export SECTORREC_ASKPASS="$HERE/usr/bin/sectorrec-askpass"
export SUDO_ASKPASS="$SECTORREC_ASKPASS"
unset SECTORREC_IMAGE_ONLY SECTORREC_ROOT_STAGED
exec "$HERE/usr/opt/SectorRec/SectorRec" "$@"
EOF
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/sectorrec.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=SectorRec
Comment=Recover data from physical drives and disk images
Exec=SectorRec
Icon=sectorrec
Terminal=false
Categories=Utility;System;
EOF
install -m 0644 src/assets/sectorrec_desktop_icon.png "$APPDIR/sectorrec.png"
install -m 0644 src/assets/sectorrec_desktop_icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/sectorrec.png"
# The AppImage (unlike the Debian archive package) redistributes third-party
# binaries. Include their exact distro copyright notices, and fail closed if
# the host packaging is not able to provide them.
for pkg in sleuthkit testdisk gddrescue smartmontools; do
  notice="/usr/share/doc/$pkg/copyright"
  if [[ ! -s "$notice" ]]; then
    echo "Missing copyright for bundled AppImage component: $pkg ($notice)" >&2
    exit 1
  fi
  install -D -m 0644 "$notice" "$APPDIR/usr/share/doc/$pkg/copyright"
done
# dpkg-query -S can report file diversions *before* the owning package,
# especially on merged-/usr Ubuntu installations. Only accept actual package
# ownership lines, and account for /lib -> /usr/lib canonical paths.
find_library_deb_owner() {
  local original="$1" resolved path candidate raw owner
  local -a paths
  resolved="$(readlink -f -- "$original" 2>/dev/null || true)"
  paths=("$original")
  [[ -z "$resolved" ]] || paths+=("$resolved")
  for path in "$original" "$resolved"; do
    case "$path" in
      /lib/*|/lib64/*) paths+=("/usr$path") ;;
      /usr/lib/*|/usr/lib64/*) paths+=("${path#/usr}") ;;
    esac
  done
  for candidate in "${paths[@]}"; do
    raw="$(LC_ALL=C dpkg-query -S -- "$candidate" 2>/dev/null || true)"
    [[ -n "$raw" ]] || continue
    owner="$(awk -v target="$candidate" '
      {
        sep = index($0, ": ")
        if (!sep || substr($0, sep + 2) != target) next
        owners = substr($0, 1, sep - 1)
        n = split(owners, names, ", ")
        for (i = 1; i <= n; i++) {
          if (names[i] ~ /^[a-z0-9][a-z0-9+.-]*(:[a-z0-9]+)?$/) {
            print names[i]
            exit
          }
        }
      }
    ' <<< "$raw")"
    if [[ -n "$owner" ]]; then
      printf '%s\n' "$owner"
      return 0
    fi
  done
  return 1
}
# Track installed binary packages supplying bundled native libraries. These
# exact package/source versions must be included in the corresponding-source
# distribution alongside any public AppImage release.
manifest="$APPDIR/usr/share/doc/sectorrec/bundled-binary-packages.txt"
mkdir -p "$(dirname "$manifest")"
printf '%s\n' 'Bundled binary packages (verify corresponding sources when redistributing):' > "$manifest"
for pkg in sleuthkit testdisk gddrescue smartmontools; do
  dpkg-query -W -f='${binary:Package} ${Version} source=${source:Package} (${source:Version})\n' "$pkg" >> "$manifest"
done
for prog in "$(command -v fls)" "$(command -v icat)" "$(command -v photorec)" "$(command -v testdisk)" "$(command -v ddrescue)" "$(command -v smartctl)"; do
  while IFS= read -r library; do
    [[ -f "$library" ]] || continue
    name=$(basename "$library")
    case "$name" in libc.so.*|libm.so.*|libpthread.so.*|libdl.so.*|librt.so.*|ld-linux*.so.*|libresolv.so.*|libnss_*.so.*) continue;; esac
    pkg="$(find_library_deb_owner "$library" || true)"
    # dpkg-query -S may include a multi-arch qualifier (e.g. :arm64).
    if [[ -z "$pkg" ]]; then
      echo "Cannot map bundled library $library to a Debian package" >&2
      exit 1
    fi
    copyright="/usr/share/doc/${pkg%%:*}/copyright"
    if [[ ! -s "$copyright" ]]; then
      echo "No license notice for bundled library $library (package $pkg)" >&2
      exit 1
    fi
    install -D -m 0644 "$copyright" "$APPDIR/usr/share/doc/${pkg%%:*}/copyright"
    dpkg-query -W -f='${binary:Package} ${Version} source=${source:Package} (${source:Version})\n' "$pkg" >> "$manifest"
  done < <(ldd "$prog" | awk '/=> \/|^\s*\// { for (i=1;i<=NF;i++) if ($i ~ /^\//) {print $i;break} }')
done
# BLT is an additional bundled dependency of the frozen Tkinter runtime.
# Record the matching Debian package, license and source version too.
for library in "${BLT_SOURCES[@]}"; do
  pkg="$(find_library_deb_owner "$library" || true)"
  if [[ -z "$pkg" ]]; then
    echo "Cannot map bundled Tk library $library to a Debian package" >&2
    exit 1
  fi
  copyright="/usr/share/doc/${pkg%%:*}/copyright"
  if [[ ! -s "$copyright" ]]; then
    echo "No license notice for bundled Tk library $library (package $pkg)" >&2
    exit 1
  fi
  install -D -m 0644 "$copyright" "$APPDIR/usr/share/doc/${pkg%%:*}/copyright"
  dpkg-query -W -f='${binary:Package} ${Version} source=${source:Package} (${source:Version})\n' "$pkg" >> "$manifest"
done
sort -u "$manifest" -o "$manifest"
install -m 0644 LICENSE "$APPDIR/usr/opt/SectorRec/LICENSE"
# Optical/read-only content verification in CI must ensure helper and engines
# were actually included; an AppImage without them is not a full edition.
for name in fls icat photorec testdisk ddrescue smartctl sectorrec-askpass; do
  test -x "$APPDIR/usr/bin/$name"
done
test -s "$APPDIR/usr/libexec/sectorrec-root.pyz"
mkdir -p dist
export ARCH
image="$PWD/dist/SectorRec-${VERSION}-Linux-${PUBLIC_ARCH}.AppImage"
"$APPIMAGETOOL_BIN" "$APPDIR" "$image"
# Test the EXACT packaged artifact: extract it to avoid FUSE, launch its frozen
# Python under a virtual screen and force-load *bundled* BLT and BLTlite.
# Failing this check prevents a broken AppImage from being released.
if command -v xvfb-run >/dev/null 2>&1; then
  smoke_dir="$(mktemp -d)"
  (cd "$smoke_dir" && "$image" --appimage-extract >/dev/null)
  xvfb-run -a "$smoke_dir/squashfs-root/AppRun" --check-tk-runtime
  # Also launch the frozen GUI as sudo does: via its separate askpass wrapper.
  # A plain Tk check does not catch child bootloader/BLT lookup failures.
  xvfb-run -a "$smoke_dir/squashfs-root/AppRun" --check-askpass-subprocess
  rm -rf "$smoke_dir"
else
  echo "WARNING: xvfb-run missing; cannot verify the frozen GUI on this host" >&2
fi
