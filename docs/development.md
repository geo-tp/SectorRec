# Development, architecture and builds

SectorRec 1.0 retains the existing GUI and recovery flows while hardening shutdown behavior, SQLite worker cleanup and privileged command boundaries. Internal Python modules still use the `src` package name for compatibility.

## Project layout

| Area | Purpose |
| --- | --- |
| `src/models/files.py` | File categories, suffix classification and shared size labels |
| `src/models/catalog.py` | Thread-safe SQLite catalogue and result queries |
| `src/models/core.py` | Metadata/recovery workers and historical API compatibility |
| `src/controllers/controller.py` | Navigation and scan orchestration |
| `src/controllers/imaging.py` | Cancellable image preparation, worker and source-guard lifecycle |
| `src/services/tsk_parser.py` | Pure parsing of Sleuth Kit listings |
| `src/services/` | Sleuth Kit, TestDisk, PhotoRec, ddrescue and diagnostics |
| `src/infrastructure/` | OS adapters, device discovery, image access, process validation, source protection |
| `src/views/desktop_native.py` | Native Tkinter desktop UI |
| `src/views/` | Presentation helpers and Pillow rendering |
| `src/views/cardputer/` | Cardputer framebuffer, keyboard and backlight |
| `src/helpers/` | Privileged Linux entry points |
| `src/app.py`, `launch_desktop.py` | CLI and composition root |

The controller has no Tk, framebuffer or evdev dependency. Desktop composes a Controller with Tk; Cardputer composes a Controller with its Pillow/framebuffer UI. Recovery engines use command builders and guarded platform-specific readers. Assets in `src/assets/` are included in packages.

Use the focused modules for new imports. `models.core` still re-exports the
catalogue, metadata helpers and parser functions so older callers and worker
command injection remain compatible. Linux platform adapters use
`infrastructure.linux_devices` directly; views and diagnostics import file
metadata without loading the metadata recovery workers.

`ImagingJob` owns the preparation thread, cancellation event, image worker and
source guard. The desktop registers the job before starting it, observes its
status and waits for it to finish on close. Cancellation during unmount or
guard acquisition must prevent a later copy from starting; guard release is
part of the job lifetime. Destination preflight and native recovery commands
retain their existing validation paths.

## Run and test from source

Python **3.10+** and Pillow are required. Desktop mode needs Tkinter (`python3-tk` on Debian/Ubuntu); actual recovery additionally needs the native Sleuth Kit, TestDisk and/or PhotoRec binaries appropriate for the OS.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m src.app --ui desktop --demo
python -m src.app --screenshot results --output preview.png
# Only on a compatible Cardputer framebuffer device:
python -m src.app --ui framebuffer
```

```bash
python -m pytest -q
# Linux desktop/headless CI, with xvfb and xauth installed:
xvfb-run -a ./run-tests.sh -q
```

Use `PYTHON=/path/to/python ./run-tests.sh` to select a Python interpreter. The test suite includes simulated engines, temporary images, UI events and cross-platform adapters; automated tests do **not** replace validation with real media. Windows-specific tests are in `tests/test_windows_bundle.py` and related files.

Tests that simulate Linux disk topology must isolate both subprocess output
and sysfs. Otherwise a host device with the same name as a synthetic source
(for example `nvme0n1`) can bypass the simulated `lsblk` ancestry.

## Build packages

The application version is `src.__version__` (`src/__init__.py`). Packaging scripts place results in `dist/`.

| Target | Build command | Expected result |
| --- | --- | --- |
| Debian/Ubuntu x86-64 desktop | `./build-deb.sh amd64 desktop` | `SectorRec-1.0-Linux-amd64.deb` |
| Debian/Ubuntu ARM64 desktop | `./build-deb.sh arm64 desktop` | `SectorRec-1.0-Linux-arm64.deb` |
| Cardputer Zero ARM64 | `./build-deb.sh arm64 cardputer` | `SectorRec-1.0-Cardputer-Zero-arm64.deb` |
| Linux portable desktop | `./build-appimage-local.sh` | `SectorRec-1.0-Linux-amd64.AppImage` / `SectorRec-1.0-Linux-arm64.AppImage` (build on matching architecture) |
| Windows x64 | `powershell -ExecutionPolicy Bypass -File .\build-windows.ps1` | `SectorRec-1.0-Windows-x64-Portable.zip` (installer via `build-windows-installer.ps1`) |

Debian packaging uses the standard Debhelper/pybuild source package under `debian/`. Build on a native Debian-compatible AMD64 or ARM64 machine using `dpkg-buildpackage` (or `./build-deb.sh`). On ARM64, the same source build also compiles the native AArch64 pixel565 framebuffer library. Both desktop `.deb` files install Tkinter, Sleuth Kit, TestDisk (including PhotoRec), and GNU ddrescue as dependencies. The Cardputer interface does not offer PhotoRec or ddrescue and does not require Tk; it includes the native framebuffer blitter and APPLaunch registration. Its TestDisk dependency also supplies the PhotoRec binary.

The two ARM64 packages are **alternative editions**, not interchangeable: `sectorrec` is the desktop application, while `sectorrec-cardputer` is the Cardputer edition. They declare Debian conflicts to prevent overlapping installation files. The former ARM64 `sectorrec` package was Cardputer-focused; existing Cardputer users should replace it with `sectorrec-cardputer`. The default `./build-deb.sh arm64` produces **both** mutually exclusive ARM64 editions from the same Debian source package.

The AppImage build needs PyInstaller, Pillow, Tkinter, Sleuth Kit, TestDisk/PhotoRec, GNU ddrescue, smartmontools and `appimagetool` (configurable via `APPIMAGETOOL_BIN`). The AppImage bundles its own restricted root helpers. Physical disk access requests interactive sudo (or Polkit), while the GUI stays unprivileged. The .deb and AppImage are separate distribution formats; no .deb installation is needed for the AppImage.

Windows executables must be assembled on Windows (PyInstaller is not a cross-compiler). The Windows build runs `test_win*.py` plus shared packaging, source-assessment and catalogue tests; the Linux jobs run the full suite, including Linux-only sysfs, IPC and Debian packaging tests. The bundler looks in `tsk-bin/` and `photorec-bin/`, or honors `TSK_BIN_DIR` and `PHOTOREC_BIN_DIR`. Optional SMART tools can be supplied via `SMARTCTL_BIN_DIR`. The Windows PowerShell downloads pinned Sleuth Kit and TestDisk/PhotoRec archives (SHA-256 checked) and installs signed Cygwin GNU ddrescue packages into a build-only directory; the bundled ddrescue.exe, cygpath.exe and Cygwin DLL dependencies are packed into the Windows executable. See [Windows documentation](windows.md) for prerequisites and physical-disk safety restrictions.

The main `.github/workflows/packages.yml` runs desktop AMD64, desktop ARM64 and Cardputer ARM64 Debian builds (plus Windows builds), and publishes all three required Linux packages on version tags; the separate `.github/workflows/windows-preflight.yml` allows Windows-only tests without creating a Release. Windows binaries are unsigned.

## Release notes

See [SectorRec 1.0 release notes](../RELEASE_NOTES_1.0.md) for the specific lifecycle and packaging changes in this release.

## Debian archive packaging

All GitHub Debian packages and the Debian archive submission candidate now use the **same Debhelper source package** in `debian/`. They install system recovery dependencies through APT rather than embedding tools. Desktop and Cardputer Zero use Polkit for their fixed-purpose root helpers, never a root GUI or a package-installed sudoers rule. On desktop, a privileged operation asks for approval only once per app session; later operations reuse a constrained root broker without new prompts or persistent per-user policies. Cardputer authorizes its fixed helpers without prompting. See [Debian submission checklist](debian-submission.md).
