# Third-party notices — Windows GNU ddrescue

The Windows build includes **unmodified official Cygwin**
packages and their runtime dependencies. GNU ddrescue is distributed under
**GPL-3.0-or-later**. Cygwin DLL/runtime and libgcc/libstdc++ have their own
applicable licenses and exceptions; consult the package metadata and bundled
license files before distributing this build publicly.

Project and corresponding source information:

- GNU ddrescue: https://www.gnu.org/software/ddrescue/
- GNU ddrescue source: https://ftp.gnu.org/gnu/ddrescue/
- Official Cygwin binaries and source archives: https://cygwin.com/packages/summary/ddrescue.html
- Cygwin licensing: https://cygwin.com/licensing.html
- Cygwin setup and signed package metadata: https://cygwin.com/install.html

The native Windows build script invokes the official Cygwin installer with
`--include-source`, retaining downloaded source archives in its package cache
and creating `dist/SectorRec-Cygwin-GPL-sources.zip` when possible. Keep this
source bundle (and any additional required license notices) with the Release
assets. If source archives are unavailable, do **not** release the Windows
bundle publicly until the license obligations have been satisfied.

## Additional Windows upstream sources

The Windows build additionally downloads the matching official source archives
for **Sleuth Kit 4.15.0** and **TestDisk/PhotoRec 7.2**, and publishes them as
`SectorRec-Windows-thirdparty-sources.zip` alongside the Cygwin source bundle.
The archive contains a SHA-256 provenance manifest of the downloads. The source
archives and their license files must remain available with the release.

**Before public redistribution:** the source and license obligations of all
copied Cygwin DLLs, PyInstaller/Python dependencies and any other dynamically
or statically bundled code still need to be audited against the actual binary.
Simply uploading these two archives is not a substitute for that review.

## Release-time audit trail

A successful Windows build now creates `dist/THIRD_PARTY_NOTICES.txt` with
SHA-256 hashes of the bundled upstream executables and DLLs. It is packaged
inside the portable ZIP and installed alongside the EXE by the Setup installer.
Attach the same notice file and **both** source archives to the GitHub Release.

The build also verifies the two pinned upstream download checksums on each run,
even when previously unpacked binaries exist. The DLL source-coverage review
remains a required release sign-off: these checks do **not** guarantee every
transitive DLL has complete corresponding source.
