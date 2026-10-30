# Windows x64 — Desktop release

The Windows build uses the native desktop interface with Sleuth Kit, TestDisk,
PhotoRec and **GNU ddrescue (via the official Cygwin package)**.

## Build

Use native Windows 10/11 x64 with Python 3.12 or 3.13:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

The script downloads verified official Sleuth Kit and TestDisk/PhotoRec archives,
installs Cygwin ddrescue into `.windows-build/`, copies its DLL dependencies
into the PyInstaller `SectorRec.exe`, and runs smoke tests without reading any
physical drive. Internet access to a Cygwin mirror is required at build time.
The primary output is `dist/SectorRec.exe`.

Windows 10/11 GUI details: external recovery tools (fls/icat/TestDisk/PhotoRec/
ddrescue) are launched without console windows. Device discovery refreshes every
3 seconds on the selection screen. Choosing a destination is instant: for a
physical disk the strict disk-identity/destination comparison is performed
before extraction; it uses the native Windows volume API where available, with
a guarded PowerShell fallback. Some unsupported or ambiguous Windows volumes
are rejected rather than bypassing the source/destination check.

## Disk imaging

**Start SectorRec normally.** When you choose a physical disk recovery method or
Create a disk image, SectorRec asks for consent and requests Windows UAC elevation.
It then restarts with the same verified source selected. If UAC is denied, no
raw disk operation starts. Regular filesystem images do not require elevation.
Choose the WHOLE physical disk from the
device list, then **Create a disk image**. The image must be written to a folder
on a **different physical disk** with adequate free space. Both the source disk
identity and the mapping between Windows `PhysicalDriveN` and Cygwin `/dev/sdX`
are checked before copying; if either cannot be verified, imaging is refused.
Only read operations are requested on the source; no partition table is edited.

SectorRec saves `SectorRec_PhysicalDriveN.img`, its `.map` and `.source.json`.
Use **Resume an incomplete image** with the **same original physical disk** after
an interruption; ddrescue skips ranges marked recovered. Never remove or mix
any of these three files. The Cygwin runtime is included in the executable;
users need not install Cygwin separately.

**Windows-specific cautions:** Cygwin GNU ddrescue can report misleading
`Permission denied` errors for physical media. An image of a live system disk
is not guaranteed consistent; prefer offline media. Do not attempt imaging
on an actively failing original if it can be handled by a Linux rescue USB;
GNU ddrescue's upstream manual recommends POSIX platforms for better error
handling. Raw disk and imaging paths received initial Windows 10 device testing, but broader hardware compatibility is not guaranteed.

Mounting images is not supported on Windows. Linux-only
sudo helper and UDisks2 source-unmount machinery are not used on Windows.

## Binary licenses

See `windows-ddrescue-licenses.md` and the Cygwin GPL source archive generated
by the Windows packaging script. The Cygwin installer verifies package
repository signatures. Do not redistribute an executable without reviewing
third-party licenses and corresponding-source obligations.
