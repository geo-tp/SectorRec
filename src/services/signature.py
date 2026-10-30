"""PhotoRec signature carving — optional DESKTOP service, never used by the LCD UI.

Unlike TSK metadata listing, this worker **extracts while it scans**.  The
controller only sees a small status dict; there is no dependency on Tk, Pillow,
SQLite, or the Cardputer framebuffer.  The source is never passed for writing.
"""
from __future__ import annotations

import os
import json
import re
import shutil
import signal
import subprocess
from src.infrastructure.windows_process import no_console_window
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

RESERVE_BYTES = 128 * 1024 * 1024
LOW_SPACE_CHECK_SECONDS = 0.8
_SCAN_INTERVAL = 1.3


def photorec_binary():
    """Find PhotoRec from a native OS build, AppImage, or the system PATH."""
    exe = 'photorec_win.exe' if sys.platform == 'win32' else 'photorec'
    if getattr(sys, '_MEIPASS', None):
        for folder in ('photorec-bin', 'tsk-bin'):
            bundled = Path(sys._MEIPASS) / folder / exe
            if bundled.is_file():
                return str(bundled)
    return shutil.which(exe) or shutil.which('photorec')


def available():
    return photorec_binary() is not None


def free_space(directory):
    return shutil.disk_usage(directory).free


def _base_device(path):
    """Return the physical Linux block-device ancestor, or None if ambiguous.

    ``lsblk`` lists descendants by default. Querying a whole disk without
    ``--nodeps`` could therefore return one of its own partitions' PKNAME,
    leading to a loop (and previously ``maximum recursion depth exceeded``).
    Walking iteratively also protects against malformed/cyclic topology.
    """
    candidate = os.path.realpath(os.fspath(path))
    if not candidate.startswith('/dev/'):
        return None
    seen = set()
    for _ in range(32):
        if candidate in seen:
            return None
        seen.add(candidate)
        try:
            proc = subprocess.run(
                ['lsblk', '--nodeps', '--noheadings', '--raw',
                 '--output', 'PKNAME', '--paths', '--', candidate],
                capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode:
            return None
        parents = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if not parents:
            return candidate
        # One source block device should have at most one parent in this view.
        if len(parents) != 1:
            return None
        parent = parents[0]
        if not parent.startswith('/dev/'):
            parent = '/dev/' + parent
        parent = os.path.realpath(parent)
        if parent == candidate:
            return None
        candidate = parent
    return None




def _lsblk_physical_ancestors(device):
    """Independent topology fallback when sysfs cannot resolve a block node.

    lsblk's JSON *tree* can map a direct disk partition, USB or MMC device
    even when PKNAME or sysfs are incomplete. Layered DM/LUKS/LVM/MD volumes
    are deliberately excluded: their multi-disk ancestry cannot reliably be
    proven from the flattened tree alone. Unknown and virtual devices never
    count as proven physical media.
    """
    path = os.path.realpath(os.fspath(device or ''))
    if not path.startswith('/dev/'):
        return None
    try:
        result = subprocess.run(
            ['lsblk', '--json', '--paths', '--output', 'PATH,TYPE'],
            capture_output=True, text=True, timeout=4)
        if result.returncode:
            return None
        data = json.loads(result.stdout)
        devices = data.get('blockdevices')
        if not isinstance(devices, list):
            return None
        roots = set()
        for block in devices:
            if not isinstance(block, dict) or block.get('type') != 'disk':
                continue
            parent = os.path.realpath(str(block.get('path') or ''))
            if not parent.startswith('/dev/'):
                continue
            # Only a physical disk and its *direct* partitions are provably
            # one-disk devices via the lsblk tree. An LVM, crypt, md or other
            # stacked virtual node can have multiple backing disks and lsblk
            # may omit duplicate relationships. The sysfs `slaves` resolver
            # above remains the only authorized path for layered devices.
            members = [block] + [child for child in (block.get('children') or [])
                                 if isinstance(child, dict) and child.get('type') == 'part']
            for node in members:
                node_path = os.path.realpath(str(node.get('path') or ''))
                if node_path == path:
                    roots.add(parent)
        return frozenset(roots) if roots else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired):
        return None


def _physical_disk_roots(device):
    """All underlying Linux *physical* disks, including DM/LUKS/LVM/MD.

    lsblk PKNAME only follows a partition's parent reliably; a device-mapper
    filesystem can have no PKNAME at all despite being backed by the source
    disk. Walk kernel ``slaves`` links *before* accepting any leaf. If a layer
    is unverifiable (loop device, missing sysfs for virtual devices, a cycle),
    return None: extraction may only proceed with proven disk separation.
    """
    path = os.path.realpath(os.fspath(device or ''))
    if not path.startswith('/dev/'):
        return None
    seen = set()

    def roots(current, depth):
        current = os.path.realpath(current)
        if depth >= 32 or current in seen or not current.startswith('/dev/'):
            return None
        seen.add(current)
        try:
            name = Path(current).name
            sys_block = Path('/sys/class/block') / name
            if sys_block.is_dir():
                try:
                    slaves = sorted((sys_block / 'slaves').iterdir())
                except OSError:
                    return None
                if slaves:
                    result = set()
                    for slave in slaves:
                        found = roots('/dev/' + slave.name, depth + 1)
                        if not found:
                            return None
                        result.update(found)
                    return frozenset(result) if result else None
            # The kernel's partition hierarchy is a more reliable source of
            # parent identity than lsblk PKNAME on some SD/MMC reader drivers.
            # Follow it before falling back to lsblk. Device-mapper / MD
            # ancestry still goes through the slaves traversal above.
            sys_parent = None
            try:
                if (sys_block / 'partition').is_file():
                    ancestor = sys_block.resolve(strict=True).parent.name
                    if (re.fullmatch(r'[A-Za-z0-9_.-]+', ancestor) and
                            ancestor != name and
                            (Path('/sys/class/block') / ancestor).is_dir()):
                        sys_parent = '/dev/' + ancestor
            except (OSError, RuntimeError):
                pass
            # A partition's PKNAME can itself be another virtual device.
            # Resolve that ancestor instead of considering it a separate disk.
            if sys_parent:
                return roots(sys_parent, depth + 1)
            if name.startswith(('dm-', 'md', 'loop', 'zram', 'nbd', 'rbd', 'zd')):
                # A logical disk without provable physical backing is unsafe.
                return None
            # The kernel itself identifies these *whole disks*. Previously we
            # still required `lsblk -o PKNAME` to succeed for an actual leaf
            # disk. If lsblk could not access the node (or did not return its
            # parent field), every unrelated destination was rejected. A
            # non-virtual sysfs block device with an authentic major:minor is
            # stronger evidence than PKNAME and needs no userspace subprocess.
            # Do NOT apply this shortcut to partitions, DM, MD, loop or other
            # virtual devices: these must be followed to the underlying disk.
            if sys_block.is_dir():
                try:
                    resolved = sys_block.resolve(strict=True)
                    device_number = (sys_block / 'dev').read_text().strip()
                    if '/devices/virtual/' in str(resolved):
                        return None
                    if (not (sys_block / 'partition').is_file()
                            and str(resolved).startswith('/sys/devices/')
                            and re.fullmatch(r'\d+:\d+', device_number)):
                        return frozenset(('/dev/' + name,))
                except (OSError, RuntimeError):
                    pass
            # Compatible fallback for older kernels with incomplete sysfs.
            # A failed lsblk lookup is NOT proof that a device is safe.
            parent = _base_device(current)
            if not parent:
                return None
            if os.path.realpath(parent) != current:
                return roots(parent, depth + 1)
            return frozenset((current,))
        finally:
            seen.discard(current)

    kernel_roots = roots(path, 0)
    return kernel_roots if kernel_roots else _lsblk_physical_ancestors(path)


def _kernel_btrfs_single_device(directory, source):
    """Check Btrfs backing device against the kernel's mounted-FSID listing.

    For a Btrfs subvolume stat().st_dev is often a virtual major:minor, and
    lsblk may list the mountpoint only on another subvolume or not at all.
    The sysfs Btrfs device list is kernel-maintained and enumerates *all*
    physical/logical members, not just the one shown by findmnt. We only
    approve a single-device filesystem matching findmnt's canonical source.
    Return None if the kernel interface cannot be consulted.
    """
    try:
        result = subprocess.run(
            ['findmnt', '-n', '-o', 'UUID', '-T', str(directory)],
            capture_output=True, text=True, timeout=3)
        if result.returncode:
            return None
        fsid = result.stdout.strip()
        if not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}', fsid):
            return None
        devices_dir = Path('/sys/fs/btrfs') / fsid / 'devices'
        if not devices_dir.is_dir():
            return None
        members = list(devices_dir.iterdir())
        # A missing/extra device means no safe single-disk proof.
        if len(members) != 1:
            return False
        member = members[0]
        if not member.is_symlink():
            return False
        return os.path.realpath('/dev/' + member.name) == os.path.realpath(source)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None


def _mounted_linux_block_device(directory):
    """Identify the kernel block device actually holding a destination folder.

    ``findmnt SOURCE`` is useful, but alone it is not proof: bind mounts,
    subvolumes and mount aliases can conceal the underlying block device.
    Linux's stat(2) st_dev refers to the filesystem containing this exact
    directory; /sys/dev/block maps that identity back to the kernel device.
    If it cannot be mapped, do not assume the destination is safe.
    """
    try:
        identity = os.stat(directory).st_dev
        sys_link = Path('/sys/dev/block') / f'{os.major(identity)}:{os.minor(identity)}'
        if not sys_link.is_dir():
            return None
        name = sys_link.resolve(strict=True).name
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            return None
        return '/dev/' + name
    except (OSError, ValueError, RuntimeError):
        return None



def _mount_device_from_kernel_number(directory):
    """Resolve a mount with an opaque SOURCE (e.g. /dev/root) by MAJ:MIN.

    Only accept the kernel mount identifier when it agrees with stat() of
    the selected directory. Never treat anonymous FUSE/Btrfs device numbers
    as block devices, and never use this to override a conflicting SOURCE.
    """
    try:
        result = subprocess.run(
            ['findmnt', '-n', '-o', 'MAJ:MIN', '-T', str(directory)],
            capture_output=True, text=True, timeout=3)
        numbers = result.stdout.strip().splitlines() if result.returncode == 0 else []
        if len(numbers) != 1 or not re.fullmatch(r'\d+:\d+', numbers[0].strip()):
            return None
        major, minor = (int(part) for part in numbers[0].strip().split(':'))
        stat_dev = os.stat(directory).st_dev
        if (os.major(stat_dev), os.minor(stat_dev)) != (major, minor):
            return None
        sys_link = Path('/sys/dev/block') / f'{major}:{minor}'
        if not sys_link.is_dir():
            return None
        name = sys_link.resolve(strict=True).name
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            return None
        return '/dev/' + name
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return None


def _kernel_btrfs_destination_roots(directory, mount_source=None):
    """Resolve a Btrfs destination without depending on findmnt's UUID column.

    `findmnt -o UUID` can be unavailable/blank for subvolumes, even though
    `findmnt -o SOURCE` names the real backing block device. Previously that
    caused ALL destinations on those systems to be rejected. Cross-check the
    destination's actual mount number against stat(), and the mounted block
    source against the kernel's Btrfs device inventory instead.

    A Btrfs filesystem with multiple members is never approved here: a source
    disk might be hidden among those members. Nothing is written or mounted.
    """
    try:
        mount = subprocess.run(
            ['findmnt', '-n', '-o', 'FSTYPE,UUID,MAJ:MIN,SOURCE',
             '-T', str(directory)],
            capture_output=True, text=True, timeout=3)
        if mount.returncode or len(mount.stdout.strip().splitlines()) != 1:
            return None
        parts = mount.stdout.strip().split(maxsplit=3)
        # util-linux prints an empty UUID as an omitted column, so the
        # four-column output can contain 3 tokens instead of 4.
        if len(parts) == 4:
            fstype, fsid, devnum, found_source = parts
        elif len(parts) == 3:
            fstype, devnum, found_source = parts
            fsid = ''
        else:
            return None
        if fstype.lower() != 'btrfs' or not re.fullmatch(r'\d+:\d+', devnum):
            return None
        major, minor = [int(x) for x in devnum.split(':')]
        stat_dev = os.stat(directory).st_dev
        if (os.major(stat_dev), os.minor(stat_dev)) != (major, minor):
            return None
        found_source = found_source.split('[', 1)[0]
        canonical = os.path.realpath(found_source)
        # Never take a source supplied by a caller instead of the actual
        # mounted filesystem reported by findmnt.
        if mount_source and os.path.realpath(mount_source) != canonical:
            return None
        if not canonical.startswith('/dev/') or not _physical_disk_roots(canonical):
            return None
        registry = Path('/sys/fs/btrfs')
        if not registry.is_dir():
            return None
        valid_fsid = bool(re.fullmatch(
            r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', fsid))
        matches = []
        for filesystem in registry.iterdir():
            if valid_fsid and filesystem.name.lower() != fsid.lower():
                continue
            members_dir = filesystem / 'devices'
            if not members_dir.is_dir():
                continue
            members = list(members_dir.iterdir())
            # A multi-device filesystem is not provably separate via SOURCE.
            if len(members) != 1 or not members[0].is_symlink():
                continue
            member = os.path.realpath('/dev/' + members[0].name)
            if member != canonical:
                continue
            roots = _physical_disk_roots(member)
            if roots:
                matches.append(roots)
        # Do not infer from an unrelated Btrfs mount or ambiguous registry.
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return None


def _mount_source_corroborated(directory, source):
    """Confirm a mounted Linux block-device source when stat.st_dev is virtual.

    FUSE drivers (notably NTFS-3G / exFAT) and Btrfs can expose an anonymous
    filesystem device ID, so /sys/dev/block cannot map it to a physical disk.
    In this special case require *both* findmnt's authoritative mount record
    and lsblk's independent mountpoint inventory to name the same source.
    Never approve non-block/network sources, unmounted sources, or mismatches.
    """
    source = os.path.realpath(os.fspath(source))
    if not source.startswith('/dev/') or not _physical_disk_roots(source):
        return False
    try:
        mount = subprocess.run(
            ['findmnt', '--json', '-T', str(directory), '-o', 'SOURCE,TARGET,FSTYPE'],
            capture_output=True, text=True, timeout=3)
        if mount.returncode:
            return False
        entries = json.loads(mount.stdout).get('filesystems', [])
        if len(entries) != 1:
            return False
        record = entries[0]
        reported_source = str(record.get('source', '')).split('[', 1)[0]
        if os.path.realpath(reported_source) != source:
            return False
        mountpoint = str(record.get('target') or '')
        if not mountpoint.startswith('/'):
            return False
        folder = Path(directory).resolve(strict=True)
        root = Path(mountpoint).resolve(strict=True)
        if folder != root and root not in folder.parents:
            return False
        fstype = str(record.get('fstype') or '').lower()
        # Multi-device Btrfs is different from single-device block mounts:
        # findmnt may report only one member while another member is the
        # source disk. Prove the filesystem has exactly one member or refuse.
        if fstype == 'btrfs':
            # The kernel lists every device of the mounted Btrfs filesystem;
            # this also works when the optional btrfs-progs CLI is absent and
            # when lsblk has no MOUNTPOINTS for a subvolume.
            kernel_match = _kernel_btrfs_single_device(root, source)
            if kernel_match is not None:
                return kernel_match
            # Older kernels: preserve the original conservative fallback.
            btrfs = subprocess.run(['btrfs', 'filesystem', 'show', '--raw', str(root)],
                                   capture_output=True, text=True, timeout=4)
            if btrfs.returncode or not re.search(r'Total devices\s+1(?:\s|$)', btrfs.stdout):
                return False
        disks = subprocess.run(
            ['lsblk', '--json', '--paths', '-o', 'PATH,TYPE,MOUNTPOINTS'],
            capture_output=True, text=True, timeout=4)
        if disks.returncode:
            return False
        nodes = list(disks.stdout and json.loads(disks.stdout).get('blockdevices', []) or [])
        matches = set()
        while nodes:
            node = nodes.pop()
            nodes.extend(node.get('children') or [])
            names = node.get('mountpoints') or []
            if isinstance(names, str):
                names = [names]
            # A mountpoint may contain a trailing slash or a block-device
            # alias; normalize both rather than rejecting a valid local disk.
            if any(os.path.normpath(str(m)) == os.path.normpath(mountpoint)
                   for m in names if m):
                matches.add(os.path.realpath(str(node.get('path') or '')))
        # No competing block source may expose that mountpoint. In particular
        # do not incorrectly approve a multi-device Btrfs or layered mount.
        return matches == {source}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired, RuntimeError):
        return False


def ensure_different_disk(device, destination):
    """Linux: block only when source and destination share a physical disk.

    The previous implementation treated any missing or conflicting secondary
    observation as an automatic rejection. This made ordinary destinations
    inaccessible on some Linux systems, especially behind LUKS/LVM.

    Check every physical-disk identity we *can* obtain. A proven overlap is
    rejected; an unavailable identity no longer vetoes the scan. This is a
    deliberate best-effort policy: an unidentifiable destination cannot be
    guaranteed to be separate, so users should only choose a disk they know
    is distinct from the source.
    """
    if sys.platform == 'win32' and str(device).startswith('\\\\.\\'):
        from src.infrastructure.windows_raw import ensure_different_windows_disk
        ensure_different_windows_disk(device, destination)
        return
    if not sys.platform.startswith('linux') or not str(device).startswith('/dev/'):
        return

    source_disks = _physical_disk_roots(device)

    # First try the kernel's device number of the destination filesystem,
    # which also works for ext4 over dm-crypt/LVM.
    observed = []
    mounted_device = _mounted_linux_block_device(destination)
    if mounted_device:
        observed.append(mounted_device)

    # Mount source may be an alias (/dev/root, /dev/mapper/...) or a normal
    # partition. Use it only when it resolves to a physical disk.
    mount_source = ''
    try:
        proc = subprocess.run(
            ['findmnt', '-n', '-o', 'SOURCE', '-T', str(destination)],
            capture_output=True, text=True, timeout=3)
        if proc.returncode == 0 and proc.stdout.strip():
            mount_source = proc.stdout.strip().splitlines()[0].split('[', 1)[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    if mount_source.startswith('/dev/'):
        observed.append(mount_source)

    # Some filesystems have no /sys/dev/block mapping for stat.st_dev.
    # A matching kernel mount major:minor is another optional observation.
    if not mounted_device:
        mount_device = _mount_device_from_kernel_number(destination)
        if mount_device:
            observed.append(mount_device)
        if mount_source:
            btrfs_disks = _kernel_btrfs_destination_roots(destination, mount_source)
            if btrfs_disks and source_disks and source_disks.intersection(btrfs_disks):
                raise ValueError('Destination is on the source disk. Choose a different disk.')

    for destination_device in dict.fromkeys(observed):
        # Exact same kernel node is unambiguously unsafe even if lsblk/sysfs
        # cannot determine its parent disk.
        if os.path.realpath(str(device)) == os.path.realpath(destination_device):
            raise ValueError('Destination is on the source disk. Choose a different disk.')
        destination_disks = _physical_disk_roots(destination_device)
        if (source_disks and destination_disks
                and source_disks.intersection(destination_disks)):
            raise ValueError('Destination is on the source disk. Choose a different disk.')

    # Unknown or contradictory auxiliary identities do not establish that the
    # disks are the same, so they no longer block recovery automatically.

def validate_destination(source, destination):
    """Preflight only: do not create output or mutate source during selection."""
    target = Path(destination).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError('Choose a destination directory.')
    if not os.access(target, os.W_OK | os.X_OK):
        raise PermissionError('Destination is not writable.')
    from src.infrastructure.windows_raw import windows_device_path
    if not os.path.isfile(source) and not str(source).startswith('/dev/') and not windows_device_path(source):
        raise ValueError('Unsupported source path.')
    if os.path.isfile(source):
        src = Path(source).expanduser().resolve(strict=True)
        if target == src:
            raise ValueError('Source and destination cannot be the same path.')
    ensure_different_disk(source, target)
    free = free_space(target)
    if free <= RESERVE_BYTES:
        raise ValueError('Not enough free space. At least 128 MB must remain.')
    return target, free


# PhotoRec fileopt identifiers denote SIGNATURE FAMILIES, not necessarily an
# exact filename extension (e.g. ZIP covers modern Office documents).
# Keep ALL as the recommended/default setting: narrow masks can harm carving.
FORMAT_PRESETS = {
    'all': (),
    'photos': ('jpg', 'png', 'gif', 'tif', 'bmp'),
    'video': ('mov', 'riff'),
    'documents': ('pdf', 'zip'),
    'audio': ('mp3', 'riff', 'flac'),
    'archives': ('zip', '7z', 'rar', 'gz'),
}


def photorec_command(binary, source, output_prefix, format_preset='all', *, partition=None):
    """Construct the documented PhotoRec scripted file-option selection."""
    if format_preset not in FORMAT_PRESETS:
        raise ValueError('Unsupported PhotoRec file family selection')
    selected = FORMAT_PRESETS[format_preset]
    # Treat the explicitly chosen source as one raw address space.
    # partition_none avoids PhotoRec's automatic partition selection when a
    # whole disk (including adapters with a normal partition table) is chosen.
    if partition is None:
        # Original whole-disk and image mode, unchanged.
        start = ['partition_none']
    else:
        # Documented PhotoRec /cmd selection on a physical device.
        # (partition table type, then the 1-based partition number).
        style, number = partition
        if style not in ('partition_i386', 'partition_gpt') or not isinstance(number, int) or number < 1:
            raise ValueError('Unsupported PhotoRec partition selector')
        start = [style, str(number)]
    options = start + ['fileopt', 'everything', 'enable' if not selected else 'disable']
    for family in selected:
        options.extend((family, 'enable'))
    options.extend(('wholespace', 'search'))
    return [binary, '/d', str(output_prefix), '/cmd', str(source), ','.join(options)]


def extraction_totals(parent, prefix, recent=None):
    """Count currently extracted files without reading their content.

    Never traverse symlinks. PhotoRec writes PREFIX.1, PREFIX.2, ... .
    """
    total, number = 0, 0
    if recent is not None: recent.clear()
    name = prefix.name
    try:
        roots = [p for p in parent.iterdir()
                 if p.is_dir() and not p.is_symlink() and re.fullmatch(re.escape(name) + r'\.\d+', p.name)]
    except OSError:
        return 0, 0
    todo = roots[:]
    walked = 0
    while todo and walked < 300_000:
        folder = todo.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    walked += 1
                    if walked >= 300_000:
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            todo.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            # PhotoRec creates its own metadata; never count it as a recovered file.
                            if entry.name.lower() in ('report.xml', 'photorec.log', 'photorec.ses'):
                                continue
                            number += 1
                            stat = entry.stat(follow_symlinks=False)
                            total += stat.st_size
                            if recent is not None:
                                # Bounded, zero-content-read snapshot. Do not open output files.
                                record = (entry.name, stat.st_size, str(entry.path), stat.st_mtime_ns)
                                if len(recent) < 120: recent.append(record)
                                else:
                                    oldest = min(range(len(recent)), key=lambda i: (recent[i][3], recent[i][2]))
                                    if (record[3], record[2]) > (recent[oldest][3], recent[oldest][2]):
                                        recent[oldest] = record
                    except OSError:
                        continue
        except OSError:
            continue
    return number, total


class PhotoRecWorker(threading.Thread):
    """Run one signature scan and monitor available destination capacity.

    `status` is intentionally just the small existing controller status dict.
    Process and output file ownership depend on the OS's disk privileges; we
    do not run a bundled setuid helper or grant arbitrary root write access.
    """
    def __init__(self, source, destination, status, binary=None, launcher=None,
                 reserve=RESERVE_BYTES, interval=LOW_SPACE_CHECK_SECONDS, format_preset='all', source_info=None):
        super().__init__(daemon=True)
        self.source = str(source)
        self.source_info = source_info
        self.destination = Path(destination)
        self.status = status
        self.binary = binary or photorec_binary()
        self.launcher = launcher  # injectable (unit tests); default direct / optional pkexec
        self.reserve = reserve
        self.interval = interval
        self.stop_event = threading.Event()
        self.process = None
        self._stop_reason = ''
        self._elevated = False
        if format_preset not in FORMAT_PRESETS:
            raise ValueError('Unknown PhotoRec file family')
        self.format_preset = format_preset

    def cancel(self):
        self._stop_reason = self._stop_reason or 'Stopped early by user.'
        self.stop_event.set()

    def _stop_process(self):
        process = self.process
        if not process or process.poll() is not None:
            return
        if self._elevated:
            # Only the root-owned launcher can signal an elevated PhotoRec.
            # Send a control message rather than attempting to kill a root PID.
            try:
                process.stdin.write(b'STOP\n')
                process.stdin.flush()
                process.wait(timeout=9)
                return
            except (OSError,subprocess.TimeoutExpired) as exc:
                raise RuntimeError('Elevated PhotoRec did not acknowledge STOP') from exc
        if os.name != 'posix':
            # Windows has no SIGKILL. Terminate the native PhotoRec process
            # then escalate through Popen.kill if it fails to exit.
            try:
                process.terminate()
                process.wait(timeout=3)
                return
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=3)
                return
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            if process.poll() is not None:return
            try:
                os.killpg(process.pid,sig)
                process.wait(timeout=3)
                return
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                continue
        if process.poll() is None:
            raise RuntimeError('PhotoRec did not terminate after STOP')

    def _commands(self, prefix):
        if not self.binary:
            raise RuntimeError('PhotoRec is not installed (Linux package: testdisk).')
        source = self.source
        partition = None
        if sys.platform == 'win32':
            from src.infrastructure.windows_raw import (windows_device_path, require_admin,
                                                         photorec_partition_source)
            if windows_device_path(self.source):
                require_admin()
                if (self.source[-1:] == ':' and len(self.source) == 6) or '#Partition' in self.source:
                    # PhotoRec /cmd uses a physical disk and a partition
                    # selector; never treat a lettered source as WHOLE DISK.
                    source, number, style = photorec_partition_source(self.source, self.source_info)
                    partition = (style, number)
        args = photorec_command(self.binary, source, prefix, self.format_preset, partition=partition)
        if self.launcher is not None:
            return self.launcher(args)
        if sys.platform.startswith('linux') and self.source.startswith('/dev/') and not os.access(self.source, os.R_OK):
            from src.infrastructure.privileged import privileged_command
            self._elevated = True
            self.status['elevation'] = True
            return privileged_command('photo', self.source, str(prefix), self.format_preset)
        return args

    @staticmethod
    def _failure_detail(logfile, code):
        """Use a short, safe diagnostic from the PhotoRec/helper output log."""
        try:
            tail = Path(logfile).read_bytes()[-8192:].decode('utf-8', errors='replace')
        except OSError:
            return f'PhotoRec stopped with exit code {code}. Log: {logfile}'
        lines = [''.join(ch for ch in line if ch.isprintable()).strip()
                 for line in tail.splitlines()]
        helper = next((line for line in reversed(lines)
                       if line.startswith('SectorRec PhotoRec: ')), None)
        if helper:
            return ('PhotoRec could not start: ' + helper[len('SectorRec PhotoRec: '):])[:240]
        # Avoid presenting arbitrary raw terminal output as a verified diagnosis.
        return f'PhotoRec stopped with exit code {code}. Log: {logfile}'

    def run(self):
        st = self.status
        st.update(state='pending', files=0, written=0, free=0, started=time.monotonic(),
                  output='', error='', stopped_reason='', progress_known=False)
        try:
            dest, initial_free = validate_destination(self.source, self.destination)
            stamp = datetime.now().strftime('%y%m%d')
            # Keep folder names human-readable. PhotoRec will create e.g.
            # SectorRec_261027.1; later sessions use _2, _3, etc.
            # The hidden session directory is also an atomic reservation,
            # preventing two runs selecting the same prefix concurrently.
            for number in range(1, 1000):
                suffix = '' if number == 1 else f'_{number}'
                prefix = dest / ('SectorRec_' + stamp + suffix)
                session = dest / ('.sectorrec_' + stamp + suffix)
                if list(dest.glob(prefix.name + '.[0-9]*')):
                    continue
                try:
                    session.mkdir(mode=0o700)
                    break
                except FileExistsError:
                    continue
            else:
                raise RuntimeError('Cannot reserve a unique recovery session folder')
            # PhotoRec writes PREFIX.1 etc. We never use this directory as input.
            st.update(output=str(prefix), free=initial_free, state='running')
            args = self._commands(prefix)
            st['session_dir'] = str(session)
            with (session / 'photorec-output.log').open('wb') as log:
                from src.infrastructure.privileged import isolated_process_group
                self.process = subprocess.Popen(args, cwd=session, stdout=log,
                                                stdin=subprocess.PIPE if self._elevated else subprocess.DEVNULL,
                                                stderr=subprocess.STDOUT,
                                                **(isolated_process_group() if os.name == 'posix' else {}),
                                                **no_console_window())
                next_scan = 0.
                while self.process.poll() is None:
                    if self.stop_event.is_set():
                        self._stop_process()
                        break
                    remaining = free_space(dest)
                    st['free'] = remaining
                    if remaining <= self.reserve:
                        self._stop_reason = 'Stopped: destination is running out of space.'
                        self.stop_event.set()
                        self._stop_process()
                        break
                    if time.monotonic() >= next_scan:
                        latest=[]
                        st['files'], st['written'] = extraction_totals(dest, prefix, latest)
                        st['recent_files'] = tuple(sorted(latest, key=lambda row: (row[3], row[2]), reverse=True))
                        next_scan = time.monotonic() + _SCAN_INTERVAL
                    self.stop_event.wait(self.interval)
                code = self.process.wait(timeout=8)
            st['files'], st['written'] = extraction_totals(dest, prefix)
            st['free'] = free_space(dest)
            st['exit_code'] = code
            if self.stop_event.is_set():
                st.update(state='cancelled', stopped_reason=self._stop_reason or 'Stopped early.')
            elif code != 0:
                st.update(state='error', error=self._failure_detail(session / 'photorec-output.log', code))
            else:
                st['state'] = 'done'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            st.update(state='error', error=str(exc)[:220])
        finally:
            if self.process is not None and self.process.poll() is None:
                try:self._stop_process()
                except (OSError,RuntimeError) as exc:
                    st.update(state='error', error=f'Failed to stop PhotoRec: {exc}')


def _diagnose_linux_disks(source, destination):
    """Read-only troubleshooting: report identity evidence without changing it."""
    print('SectorRec Linux disk identity diagnostics (read-only)')
    print('Source:', source)
    print('Destination:', destination)
    print('Source roots:', _physical_disk_roots(source))
    print('Destination stat device:', _mounted_linux_block_device(destination))
    print('Destination mount MAJ:MIN device:', _mount_device_from_kernel_number(destination))
    print('Destination Btrfs kernel roots:', _kernel_btrfs_destination_roots(destination))
    for args in (
        ['findmnt', '-T', str(destination), '-o', 'SOURCE,TARGET,FSTYPE,UUID,MAJ:MIN'],
        ['lsblk', '-p', '-o', 'NAME,TYPE,PKNAME,FSTYPE,TRAN,MOUNTPOINTS'],
    ):
        print('\n$', ' '.join(args))
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=7)
            print((proc.stdout or proc.stderr or '(empty output)').strip())
            if proc.returncode:
                print('Exit code:', proc.returncode)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print('Unavailable:', exc)
    try:
        ensure_different_disk(source, destination)
        print('\nResult: physical disks verified as different')
    except (ValueError, OSError) as exc:
        print('\nResult: not authorized:', str(exc))
    print('No files or disks were changed.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='SectorRec Linux destination diagnostics (read-only)')
    parser.add_argument('--diagnose', action='store_true', required=True)
    parser.add_argument('source', help='Source block device, for example /dev/mmcblk0')
    parser.add_argument('destination', help='Destination folder')
    args = parser.parse_args()
    _diagnose_linux_disks(args.source, args.destination)
