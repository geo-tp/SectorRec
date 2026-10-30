"""Windows physical-device path validation and fail-closed output-disk checks.

No write capable handle is ever opened here. File-record and signature tools
consume only explicitly validated device paths and must run elevated.
"""
from __future__ import annotations
import base64
import ctypes
import os
import re
import subprocess
from src.infrastructure.windows_process import no_console_window
import sys

_DISK = re.compile(r'\\\\\.\\PhysicalDrive([0-9]+)\Z', re.I)
_VOLUME = re.compile(r'\\\\\.\\([A-Za-z]):\Z')
_PARTITION = re.compile(_DISK.pattern.replace(r'\Z', r'#Partition([1-9][0-9]*)\Z'), re.I)


def windows_device_path(path: str) -> bool:
    return isinstance(path,str) and bool(_DISK.fullmatch(path) or _VOLUME.fullmatch(path) or _PARTITION.fullmatch(path))


def require_windows_device(path: str) -> str:
    if not windows_device_path(path):
        raise ValueError('Invalid Windows physical drive or volume path')
    return path


def require_admin():
    if sys.platform != 'win32':
        raise RuntimeError('Raw Windows disk access is supported only on Windows')
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError) as exc:
        raise PermissionError('Cannot verify administrator privileges for raw disk access') from exc
    if not admin:
        raise PermissionError('Physical disk recovery requires SectorRec to run as Administrator')


def _native_volume_disk_number(directory):
    """Fast native Windows volume-to-physical-disk mapping (no PowerShell).

    GetVolumePathName follows volume mount points. IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS
    must report exactly one physical disk; spanned/dynamic volumes fail closed.
    If Windows refuses a query, the caller can use the legacy guarded lookup.
    """
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    root = ctypes.create_unicode_buffer(32768)
    get_root = kernel.GetVolumePathNameW
    get_root.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_root.restype = wintypes.BOOL
    if not get_root(str(directory), root, len(root)):
        return None
    guid = ctypes.create_unicode_buffer(100)
    get_guid = kernel.GetVolumeNameForVolumeMountPointW
    get_guid.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_guid.restype = wintypes.BOOL
    if not get_guid(root.value, guid, len(guid)):
        return None
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Zero desired access; no write handle is ever requested.
    handle = create(guid.value.rstrip('\\'), 0, 7, None, 3, 0, None)
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid:
        return None
    try:
        ioctl = kernel.DeviceIoControl
        ioctl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        ioctl.restype = wintypes.BOOL
        result = ctypes.create_string_buffer(4096)
        written = wintypes.DWORD(0)
        # IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = CTL_CODE(FILE_DEVICE_VOLUME, 0, METHOD_BUFFERED, ANY_ACCESS)
        if not ioctl(handle, 0x00560000, None, 0, result, len(result), ctypes.byref(written), None):
            return None
        raw = result.raw
        if written.value < 32:
            return None
        number_of_extents = int.from_bytes(raw[0:4], 'little')
        if number_of_extents != 1:
            raise ValueError('Destination volume uses multiple physical disks; refusing recovery.')
        return int.from_bytes(raw[8:12], 'little')
    finally:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)


def _disk_for_destination(destination, run=subprocess.run) -> int:
    """Get actual volume backing output directory (not guessed drive letter).

    Fail closed on junctions, unsupported volume types and ambiguous mappings.
    Get-Volume -FilePath resolves NTFS directory mount points as well.
    """
    path = os.path.realpath(os.fspath(destination))
    if run is subprocess.run:
        fast = _native_volume_disk_number(path)
        if fast is not None:
            return fast
    encoded=base64.b64encode(path.encode('utf-8')).decode('ascii')
    command=("$ErrorActionPreference='Stop';" 
             f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'));"
             "$volume=Get-Volume -FilePath $p -ErrorAction Stop;"
             "$parts=@(Get-Partition -Volume $volume -ErrorAction Stop);"
             "if($parts.Count -ne 1){exit 3};"
             "$parts[0].DiskNumber")
    try:
        result=run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
                   capture_output=True,text=True,timeout=12,check=False,
                   **no_console_window())
        if result.returncode:
            raise ValueError('Cannot uniquely identify the destination physical disk')
        value=result.stdout.strip()
        if not re.fullmatch(r'[0-9]+',value):
            raise ValueError('Destination is not on an identifiable single Windows disk')
        return int(value)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ValueError('Cannot verify the physical destination disk') from exc


def _disk_for_source(source, run=subprocess.run) -> int:
    require_windows_device(source)
    match=_DISK.fullmatch(source)
    if match:
        return int(match.group(1))
    unlettered = _PARTITION.fullmatch(source)
    if unlettered:
        return int(unlettered.group(1))
    letter=_VOLUME.fullmatch(source).group(1).upper()
    if run is subprocess.run:
        fast = _native_volume_disk_number(letter + ':\\')
        if fast is not None:
            return fast
    cmd=(f"$p=Get-Partition -DriveLetter {letter} -ErrorAction Stop;"
         "$p.DiskNumber")
    try:
        proc=run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],
                 capture_output=True,text=True,timeout=10,check=False,
                  **no_console_window())
        result=proc.stdout.strip()
        if proc.returncode or not re.fullmatch(r'[0-9]+',result):
            raise ValueError('Source volume does not map to one physical disk')
        return int(result)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ValueError('Cannot identify source physical disk') from exc



def photorec_partition_source(source, selected, run=subprocess.run):
    """Resolve a selected Windows volume to PhotoRec's physical disk/partition.

    Native PhotoRec enumerates whole Windows physical devices. Its /cmd
    partition selection must use the real partition number, *not* a raw
    \\\\.\\X: volume path. Refuse a changed/reassigned letter or partition
    layout instead of silently scanning another partition or whole disk.
    """
    require_windows_device(source)
    match = _VOLUME.fullmatch(source)
    unlettered = _PARTITION.fullmatch(source)
    if not (match or unlettered) or not isinstance(selected, dict):
        raise ValueError('PhotoRec partition selection requires a Windows partition')
    expected_disk = selected.get('disk_path')
    expected_number = selected.get('partition_number')
    expected_offset = selected.get('partition_offset')
    expected_size = selected.get('size')
    expected_style = str(selected.get('partition_style') or '').upper()
    if not (_DISK.fullmatch(str(expected_disk or '')) and
            isinstance(expected_number, int) and expected_number >= 1 and
            isinstance(expected_offset, int) and expected_offset >= 0 and
            isinstance(expected_size, int) and expected_size > 0 and
            expected_style in ('GPT', 'MBR')):
        raise ValueError('Partition identity was not captured. Refresh the device list before scanning.')
    if unlettered:
        if (int(unlettered.group(1)) != int(_DISK.fullmatch(expected_disk).group(1)) or
                int(unlettered.group(2)) != expected_number):
            raise ValueError('Windows partition identity changed. Refresh devices; refusing to scan another area.')
        selector = (f'Get-Partition -DiskNumber {unlettered.group(1)} '
                    f'-PartitionNumber {unlettered.group(2)} -ErrorAction Stop')
    else:
        selector = f'Get-Partition -DriveLetter {match.group(1).upper()} -ErrorAction Stop'
    script = ("$ErrorActionPreference='Stop';"
              f"$p={selector};"
              "if(@($p).Count -ne 1){exit 4};"
              "$d=Get-Disk -Number $p.DiskNumber -ErrorAction Stop;"
              "[pscustomobject]@{Disk=[string]$p.DiskNumber;"
              "Number=[string]$p.PartitionNumber;Offset=[string]$p.Offset;"
              "Size=[string]$p.Size;Style=[string]$d.PartitionStyle}"
              "|ConvertTo-Json -Compress")
    try:
        result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                     capture_output=True, text=True, timeout=12, check=False,
                     **no_console_window())
        if result.returncode:
            raise ValueError('Cannot verify selected Windows partition')
        import json
        data = json.loads(result.stdout)
        actual_disk = int(data['Disk'])
        actual_number = int(data['Number'])
        actual_offset = int(data['Offset'])
        actual_size = int(data['Size'])
        actual_style = str(data['Style']).upper()
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError,
            TypeError) as exc:
        raise ValueError('Unable to verify the Windows volume/partition mapping. Reconnect and refresh.') from exc
    expected_disk_number = int(_DISK.fullmatch(expected_disk).group(1))
    if (actual_disk != expected_disk_number or actual_number != expected_number or
            actual_offset != expected_offset or actual_size != expected_size or
            actual_style != expected_style):
        raise ValueError('Windows partition identity changed. Refresh devices; refusing to scan another area.')
    return expected_disk, actual_number, ('partition_gpt' if actual_style == 'GPT' else 'partition_i386')


def quick_same_windows_disk(source, destination):
    """Fast UI-only check, avoiding PowerShell on every folder selection.

    Returns True only for a proven same-disk match, False only for a proven
    different-disk match, otherwise None. This must NEVER authorize writing:
    ensure_different_windows_disk() performs the mandatory full preflight.
    """
    if sys.platform != 'win32' or not windows_device_path(source):
        return None
    match = _DISK.fullmatch(source)
    try:
        part = _PARTITION.fullmatch(source)
        source_number = (int(match.group(1)) if match else
                         int(part.group(1)) if part else
                         _native_volume_disk_number(_VOLUME.fullmatch(source).group(1) + ':\\'))
        dest_number = _native_volume_disk_number(os.fspath(destination))
    except (OSError, ValueError, AttributeError):
        return None
    if source_number is None or dest_number is None:
        return None
    return source_number == dest_number


def ensure_different_windows_disk(source, destination, run=subprocess.run):
    """No output to the source disk, including a different partition of it."""
    require_windows_device(source)
    source_disk=_disk_for_source(source,run)
    dest_disk=_disk_for_destination(destination,run)
    if source_disk == dest_disk:
        raise ValueError('Destination belongs to the source physical disk. Choose a folder on a different physical disk, not another partition or drive letter on the same device.')
    return source_disk,dest_disk


def raw_tsk_command(action, source, inode=None, *, source_info=None, verified_partition=None):
    """Only allow the same fixed read-only fls/icat subset used for image scans."""
    require_windows_device(source)
    require_admin()
    from src.infrastructure.image_backend import find_tsk_binary, INODE
    unlettered = _PARTITION.fullmatch(source)
    if action == 'scan':
        program, flags = 'fls',['-r','-d','-p','-l']
    elif action == 'trash_roots':
        program, flags = 'fls',['-u','-D','-l']
    elif action in ('deleted_children','trash_scan','recover','recover_allocated'):
        if inode is None or not INODE.fullmatch(str(inode)):
            raise ValueError('Invalid metadata address')
        if action == 'recover':
            program,flags='icat',['-r']
        elif action == 'recover_allocated':
            program,flags='icat',[]
        elif action == 'deleted_children':
            program,flags='fls',['-d','-l','-p']
        else:
            program,flags='fls',['-u','-r','-p','-l','-F']
    else:
        raise ValueError('Unsupported raw read-only operation')
    binary=find_tsk_binary(program)
    if binary is None:
        raise RuntimeError('Sleuth Kit Windows executables missing')
    if unlettered:
        # A synthetic selection key must never become an external program's
        # source. Validate fresh geometry at the start of each scan/recovery
        # operation; the controller caches that verified scope for the batch.
        if verified_partition is None:
            disk, number, _style = photorec_partition_source(source, source_info)
        else:
            # Controller creates this snapshot only after a fresh OS lookup,
            # at the beginning of each independent scan or extraction batch.
            disk, number, _style = verified_partition
            expected = _PARTITION.fullmatch(source)
            if (not isinstance(source_info, dict) or not expected or
                    disk.lower() != source_info.get('disk_path', '').lower() or
                    int(expected.group(2)) != number or
                    number != source_info.get('partition_number')):
                raise ValueError('Unverified Windows partition read scope.')
        logical = int(source_info.get('logical_sector') or 512)
        offset = int(source_info['partition_offset'])
        if logical not in (512, 1024, 2048, 4096) or offset % logical:
            raise ValueError('Unsupported Windows partition sector alignment.')
        # -b is the native TSK sector size, -o is a sector *count*.
        flags = [*flags, '-b', str(logical), '-o', str(offset // logical)]
        return [binary, *flags, disk] + ([str(inode)] if inode is not None else [])
    return [binary,*flags,source]+([str(inode)] if inode is not None else [])
