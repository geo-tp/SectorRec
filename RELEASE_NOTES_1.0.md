# SectorRec 1.0

SectorRec provides guided recovery of deleted files (Sleuth Kit), lost partitions (TestDisk), signature-based files (PhotoRec), and disk-image creation/resume (GNU ddrescue).

## Downloads

- `SectorRec-1.0-Linux-amd64.deb` — standard Linux PC (AMD64) with Tkinter desktop UI.
- `SectorRec-1.0-Linux-arm64.deb` — standard Linux PC (ARM64) with the **same full desktop UI and dependencies**.
- `SectorRec-1.0-Cardputer-Zero-arm64.deb` — lightweight Cardputer Zero edition with APPLaunch integration and framebuffer display.
- `SectorRec-1.0-Linux-amd64.AppImage` / `SectorRec-1.0-Linux-arm64.AppImage` — portable Linux builds with bundled recovery engines and isolated privileged helpers. Physical disk operations request interactive sudo (or Polkit); no Debian installation is required.
- `SectorRec-1.0-Windows-x64-Setup.exe` / `SectorRec-1.0-Windows-x64-Portable.zip` — full Windows x64 desktop edition built and smoke-tested on Windows by GitHub Actions. Physical disk access uses UAC when needed; source device and destination checks remain enforced. Hardware-dependent disk access may vary between Windows systems.

All Debian packages now use the same standard Debhelper/pybuild packaging pipeline (system recovery utilities, not bundled copies). Both desktop and Cardputer use restricted Polkit actions for fixed-purpose helpers. Desktop asks for administrator approval only when a privileged disk operation needs it, then reuses one restricted broker until the application closes. A new application session requires authorization again; no permanent user permissions are installed. The Cardputer policy authorizes its restricted helpers without a password.

The two ARM64 Debian editions are alternatives, not interchangeable. The `sectorrec-cardputer` package replaces the older ARM64 Cardputer-focused `sectorrec` package name; installing it may require removing the previous `sectorrec` package. The normal Linux desktop ARM64 package is now `sectorrec`.

## Desktop update

After disk imaging finishes, **Choose another source** appears on the **left** side of the footer; stopping an active image remains on the right. Existing ddrescue output/map resume behavior is unchanged.

All recovery commands remain read-only against the source where applicable. Always save recovered files and disk images to a different physical drive.

The Linux .deb download filenames show the application version **1.0**. Debian
package metadata retains its required distribution revision (for example
`1.0-9`); `dpkg` and APT use that internal version for upgrades.

**Release automation:** pushing the `v1.0` tag triggers the Linux and Windows
builds and publishes the assets only after both jobs succeed. This file is
used as the GitHub Release description; it does not trigger builds by itself.
