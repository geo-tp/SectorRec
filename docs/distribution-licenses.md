# Licenses and corresponding source — distribution checklist

SectorRec's original Python code and artwork claimed by its contributors is
licensed under MIT (see `LICENSE`). The Debian **archive** package installs
only SectorRec code and depends on Debian's existing packages for `sleuthkit`,
`testdisk` / PhotoRec, GNU `gddrescue`, `smartmontools` and Python libraries.
It must provide `debian/copyright` and accurately report ALL upstream work.

The **AppImage and Windows executables** are different: they redistribute
third-party executables and libraries. A MIT license for SectorRec **does not**
change third-party copyright or GPL obligations. Before publicly shipping them:

- Distribute exact applicable license/copyright notices with all bundled
  binaries, Python wheels, support libraries, and assets; record their versions.
- Make complete corresponding source **for the versions actually distributed**
  available (including required patches, build scripts, and permitted interfaces)
  or use another GPL-compliant distribution mechanism. A link to arbitrary
  upstream HEAD is not necessarily sufficient.
- Windows: separately audit the bundled Sleuth Kit / TestDisk / PhotoRec
  packages, Cygwin DLLs and GNU ddrescue. The current
  `SectorRec-Cygwin-GPL-sources.zip` checks for `cygwin` and `ddrescue` sources
  and the Windows build now also generates `SectorRec-Windows-thirdparty-sources.zip`
  for pinned Sleuth Kit and TestDisk/PhotoRec source releases. Neither archive
  alone proves full compliance for every copied DLL or Python dependency.
- AppImage: the build now requires and embeds the Linux distro's copyright
  notices for the four primary recovery packages, plus licenses/manifests for
  copied libraries. **This is necessary but not sufficient**: a matching
  corresponding-source bundle (and notices for PyInstaller, Python packages,
  and their dependencies) must be reviewed and published alongside the build.
- Do not label a binary release "license-compliant" without checking its exact
  contents and source links. Do not rely on the MIT file alone.

Upstream licensing links:
- https://www.sleuthkit.org/sleuthkit/download.php
- https://www.cgsecurity.org/wiki/TestDisk_Download
- https://www.gnu.org/software/ddrescue/
- https://cygwin.com/licensing.html

The official Debian archive package avoids the majority of these bundled-code
issues by using Debian packages installed and maintained by the distribution.
