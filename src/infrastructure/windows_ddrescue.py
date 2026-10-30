"""Windows/Cygwin GNU ddrescue adapter (READ-ONLY source).

Cygwin can render a disk as Disk{GUID} rather than PhysicalDriveN.
For aliases, compare the native Windows storage device numbers before imaging.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import hashlib
import unicodedata
from src.infrastructure.windows_process import no_console_window
import sys
from pathlib import Path

from src.infrastructure.windows_raw import require_admin, require_windows_device

_WHOLE_DISK = re.compile(r'\\\\\.\\PhysicalDrive(\d+)\Z', re.I)
_CYGWIN_DISK_ALIAS = re.compile(r'\\\\\.\\Disk\{[0-9a-f-]{36}\}\Z', re.I)


def require_whole_disk(source):
    require_windows_device(str(source))
    match = _WHOLE_DISK.fullmatch(str(source))
    if not match:
        raise ValueError('Disk imaging requires a whole PhysicalDrive, not a drive-letter volume.')
    return int(match.group(1))


def _letters(index):
    result = ''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(97 + remainder) + result
    return result


def runtime_dir():
    """Find the PyInstaller bundle or locally staged Cygwin runtime."""
    roots = [Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])),
             Path(__file__).resolve().parents[2]]
    override = os.environ.get('SECTORREC_DDRESCUE_BIN_DIR')
    if override:
        roots.insert(0, Path(override).parent)
    for root in roots:
        folder = Path(override) if override else root / 'ddrescue-bin'
        if all((folder / item).is_file() for item in
               ('ddrescue.exe', 'cygpath.exe', 'cygwin1.dll')):
            return folder
    raise RuntimeError('GNU ddrescue Cygwin runtime is missing from this Windows build.')


def _env(folder):
    env = os.environ.copy()
    env['PATH'] = str(folder) + os.pathsep + env.get('PATH', '')
    env['LC_ALL'] = 'C'
    env['TERM'] = 'dumb'
    return env


def _native_disk_identity(path):
    """Identify a whole disk using Windows' read-only IOCTLs.

    This is a verification query, not a new imaging path. Cygwin might return
    a Disk{GUID} alias, which must NEVER be trusted by string comparison.
    Never guess or accept a mapping when the native query is unavailable.
    """
    if sys.platform != 'win32':
        raise OSError('Native disk verification requires Windows')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    open_device = kernel.CreateFileW
    open_device.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]
    open_device.restype = wintypes.HANDLE
    # No write access requested; share access with Windows and other readers.
    handle = open_device(str(path), 0, 7, None, 3, 0, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'Cannot open disk to verify its native identity')
    try:
        ioctl = kernel.DeviceIoControl
        ioctl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        ioctl.restype = wintypes.BOOL
        output = ctypes.create_string_buffer(12)
        written = wintypes.DWORD(0)
        # IOCTL_STORAGE_GET_DEVICE_NUMBER: device type, number, partition no.
        if not ioctl(handle, 0x002D1080, None, 0, output, len(output),
                     ctypes.byref(written), None) or written.value != 12:
            raise OSError(ctypes.get_last_error(), 'Cannot identify Windows disk device number')
        import struct
        disk_type, disk_number, partition_number = struct.unpack('<III', output.raw)
        if disk_type != 7 or partition_number not in (0, 0xFFFFFFFF):
            raise ValueError('Cygwin device is not a whole physical disk')
        # Use the length if Windows exposes it; all matching numbers are still
        # mandatory even when the optional length is unavailable.
        size = ctypes.create_string_buffer(8)
        size_written = wintypes.DWORD(0)
        disk_bytes = None
        if ioctl(handle, 0x0007405C, None, 0, size, len(size),
                 ctypes.byref(size_written), None) and size_written.value == 8:
            disk_bytes = struct.unpack('<Q', size.raw)[0]
        return disk_type, disk_number, disk_bytes
    finally:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)


def mapped_source(source, folder=None, run=subprocess.run):
    """Verify Cygwin device using native disk identity when it uses an alias."""
    number = require_whole_disk(source)
    folder = Path(folder) if folder else runtime_dir()
    cyg_device = '/dev/sd' + _letters(number)
    try:
        result = run([str(folder / 'cygpath.exe'), '-w', cyg_device],
                     capture_output=True, text=True, timeout=10, check=False,
                     env=_env(folder), **no_console_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Cannot verify the Cygwin-to-Windows disk mapping.') from exc
    actual = (result.stdout or '').strip().replace('/', '\\').rstrip('\\').casefold()
    expected = str(source).rstrip('\\').casefold()
    if result.returncode != 0:
        raise ValueError(f'Cygwin cannot resolve {cyg_device}: {(result.stderr or "").strip()[:200]}')
    if actual == expected:
        return cyg_device
    # Cygwin on current Windows can resolve /dev/sdX to a Disk{GUID} alias.
    # Compare real device numbers on BOTH handles; never equate names/indices
    # by assumption or accept a volume/partition in place of a whole disk.
    if _CYGWIN_DISK_ALIAS.fullmatch(actual):
        try:
            selected = _native_disk_identity(source)
            mapped = _native_disk_identity(actual)
        except (OSError, ValueError) as exc:
            raise ValueError(f'Cygwin disk alias {actual!r} could not be verified '
                             f'against {source}. Refusing imaging: {exc}') from exc
        if (selected[:2] == mapped[:2] and selected[1] == number and
                (selected[2] is None or mapped[2] is None or selected[2] == mapped[2])):
            return cyg_device
    raise ValueError(f'Cygwin disk mapping could not be verified for {source}; '
                     f'{cyg_device} resolved to {result.stdout.strip()!r}. Refusing imaging.')


def cygwin_file_path(path, folder=None, run=subprocess.run):
    folder = Path(folder) if folder else runtime_dir()
    path = Path(path).resolve()
    try:
        result = run([str(folder / 'cygpath.exe'), '-u', '-a', str(path)],
                     capture_output=True, text=True, timeout=10, check=False,
                     env=_env(folder), **no_console_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Cannot convert the Windows destination to a Cygwin path.') from exc
    converted = (result.stdout or '').strip()
    if result.returncode != 0 or not converted.startswith('/') or '\n' in converted:
        raise ValueError('Cannot convert the output path for Cygwin ddrescue.')
    return converted



def _stable_id(value):
    """Normalize Windows storage IDs identically in inventory and preflight.

    PowerShell/CIM can vary Unicode representation, casing and trailing NULs
    for the same Windows device. Do not remove meaningful internal characters.
    """
    return unicodedata.normalize('NFC', str(value or '').rstrip('\x00').strip())


def canonical_source_identity(serial, unique, size, pnp=None):
    """Identity snapshot for an *individual* Windows disk device.

    The optional PnP device instance ID is independently obtained from
    Win32_DiskDrive. It gives us an additional way of verifying a USB device
    when Get-Disk.SerialNumber is unstable across two PowerShell queries.
    Legacy sidecars intentionally remain readable without a PnP field.
    """
    try:
        capacity = int(str(size).strip())
    except (TypeError, ValueError):
        return None
    serial, unique, pnp = _stable_id(serial), _stable_id(unique), _stable_id(pnp)
    if capacity <= 0 or not (serial or unique):
        return None
    fields = {'serial': serial, 'unique': unique, 'size': capacity}
    if pnp:
        fields['pnp'] = pnp
    return json.dumps(fields, sort_keys=True, separators=(',', ':'))


def _identity_fields(identity):
    try:
        raw = json.loads(identity)
        if not isinstance(raw, dict):
            return None
        canonical = canonical_source_identity(raw.get('serial'), raw.get('unique'),
                                              raw.get('size'), raw.get('pnp'))
        return json.loads(canonical) if canonical else None
    except (TypeError, ValueError):
        return None


def _valid_unique(value):
    """Do not treat placeholder/empty disk IDs as a second identity factor."""
    value = _stable_id(value).casefold()
    # USB readers sometimes return missing identifiers or a zero disk ID.
    return bool(value and value not in ('unknown', 'none', 'null', 'n/a')
                and any(ch not in '0-{} ' for ch in value))


def identity_difference(selected, observed):
    """Compare identities without false rejection of an unstable USB serial.

    Require the same *capacity* and UniqueId. A serial mismatch is tolerated
    only when two other, non-empty and independent identifiers (Windows
    UniqueId AND the PnP device-instance ID) match exactly. This is intentionally
    stricter for older sidecars without a PnP identity: never guess which disk
    was used to create an existing backup. No PnP downgrade is permitted.
    """
    first, second = _identity_fields(selected), _identity_fields(observed)
    if not first or not second:
        return 'One snapshot does not contain a verifiable disk serial/unique ID.'

    def normalized(field, data):
        return _stable_id(data.get(field)).casefold()

    def fingerprint(value):
        value = _stable_id(value)
        return hashlib.sha256(value.casefold().encode('utf-8')).hexdigest()[:12] if value else 'missing'

    differences = []
    if first['size'] != second['size']:
        differences.append(f'disk capacity: selected {first["size"]} bytes, now {second["size"]} bytes')
    unique_matches = normalized('unique', first) == normalized('unique', second)
    if not unique_matches:
        differences.append('unique ID: selected ' + fingerprint(first['unique'])
                           + ', now ' + fingerprint(second['unique']) + ' (SHA-256 prefix)')
    prior_pnp = normalized('pnp', first)
    new_pnp = normalized('pnp', second)
    if prior_pnp and not new_pnp:
        differences.append('PnP device identity: unavailable during verification; refusing unverified downgrade')
    elif prior_pnp and new_pnp and prior_pnp != new_pnp:
        differences.append('PnP device identity: selected ' + fingerprint(first['pnp'])
                           + ', now ' + fingerprint(second['pnp']) + ' (SHA-256 prefix)')

    serial_matches = normalized('serial', first) == normalized('serial', second)
    # This is NOT a bypass based on size, disk index, or UniqueId alone.
    # Only allow a USB serial variation if PnP and a meaningful UniqueId
    # independently identify the same device at both observations.
    strong_alternative = (unique_matches and _valid_unique(first['unique'])
                          and bool(prior_pnp and new_pnp and prior_pnp == new_pnp))
    if not serial_matches and not strong_alternative:
        differences.append('serial number: selected ' + fingerprint(first['serial'])
                           + ', now ' + fingerprint(second['serial']) + ' (SHA-256 prefix)')
    return '; '.join(differences)


def source_identity(source, run=subprocess.run):
    """Fresh Windows identity from Get-Disk plus independent CIM PnP ID.

    CIM failure does not invent a PnP identity: the comparator then falls back
    to a strict serial/unique/size match, which is safe but may refuse copying.
    """
    number = require_whole_disk(source)
    script = ("$ErrorActionPreference='Stop';"
              f"$d=Get-Disk -Number {number} -ErrorAction Stop;"
              f"$w=Get-CimInstance Win32_DiskDrive -Filter 'Index = {number}' "
              "-ErrorAction SilentlyContinue;"
              "$pnp=if($null -ne $w){[string]$w.PNPDeviceID}else{''};"
              "[pscustomobject]@{Serial=[string]$d.SerialNumber;"
              "Unique=[string]$d.UniqueId;Size=[string]$d.Size;PnP=$pnp}"
              "|ConvertTo-Json -Compress")
    try:
        result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                     capture_output=True, text=True, timeout=12, check=False,
                     **no_console_window())
        if result.returncode:
            return None
        info = json.loads(result.stdout)
        return canonical_source_identity(info.get('Serial'), info.get('Unique'),
                                         info.get('Size'), info.get('PnP'))
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None



def imaging_partition(source, selected):
    """Verify the chosen lettered partition before reading its physical disk.

    Never derive an imaging range from an unverified UI offset or from a drive
    letter alone. Windows confirms the current disk, partition, offset, size
    and table style through the same independent preflight used by PhotoRec.
    """
    from src.infrastructure.windows_raw import photorec_partition_source
    if not isinstance(selected, dict):
        raise ValueError('Select the Windows partition again before imaging.')
    disk, number, style = photorec_partition_source(source, selected)
    offset, size = selected['partition_offset'], selected['size']
    if (offset <= 0 or size <= 0 or number < 1):
        raise ValueError('Selected Windows partition has invalid geometry.')
    return {'disk': disk, 'number': number,
            'style': 'GPT' if style == 'partition_gpt' else 'MBR',
            'offset': offset, 'size': size}


def imaging_disk(source, selected=None):
    """Physical disk for imaging; returns (disk, optional verified partition)."""
    from src.infrastructure.windows_raw import require_windows_device
    require_windows_device(source)
    if _WHOLE_DISK.fullmatch(source):
        if selected is not None and not selected.get('partition_disk'):
            raise ValueError('Selected partition does not match this whole-disk source.')
        return source, None
    part = imaging_partition(source, selected)
    return part['disk'], part


def command(source, image, resume=False, folder=None, run=subprocess.run, source_info=None):
    require_admin()
    disk, partition = imaging_disk(source, source_info)
    folder = Path(folder) if folder else runtime_dir()
    device = mapped_source(disk, folder=folder, run=run)
    output = cygwin_file_path(image, folder=folder, run=run)
    mapfile = cygwin_file_path(str(image) + '.map', folder=folder, run=run)
    cmd = [str(folder / 'ddrescue.exe'), '--mapfile-interval=5s,15s']
    if resume:
        cmd += ['-r3']
    if partition is not None:
        # ddrescue reads only [offset, offset+size), placing the resulting
        # partition image at byte 0. Its .map uses output-relative positions.
        cmd += [f"--input-position={partition['offset']}",
                '--output-position=0', f"--size={partition['size']}"]
    cmd += [device, output, mapfile]
    return cmd, _env(folder)


from contextlib import contextmanager


@contextmanager
def image_lock(identity_file):
    """Exclusive OS file lock; released automatically after a GUI crash."""
    import msvcrt
    with open(identity_file, 'r+b') as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise ValueError('Another SectorRec process is already imaging this file.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def assert_image_unlocked(identity_file):
    with image_lock(identity_file):
        pass
