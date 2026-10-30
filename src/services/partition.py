"""Read-only TestDisk partition discovery and offset-based file access.

TestDisk only discovers candidate partitions; Sleuth Kit lists/copies files
from their discovered offset. No partition table is written or repaired.
"""
from __future__ import annotations

import re
import json
import os
import sys
import shutil
import subprocess
from src.infrastructure.windows_process import no_console_window
from src.infrastructure.process_activity import process_read_bytes, record_read_activity
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from src.infrastructure.privileged import helper_command, stop_proc, isolated_process_group
from src.infrastructure.image_backend import find_tsk_binary

MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 128
# Never cut off a legitimate physical TestDisk search solely because a disk
# is small: badly damaged drives can take arbitrarily long. Keep the optional
# limit as a test/diagnostic override; it is disabled for production scans.
WINDOWS_SMALL_DISK_MAX_SECONDS = None
WINDOWS_SMALL_DISK_BYTES = 1024 * 1024 * 1024


def testdisk_report_phase(text):
    """Best-effort indication of where TestDisk last wrote a log line.

    A paused log is *not* evidence of a stalled scan: TestDisk can read for a
    long time without writing. Use only to help distinguish setup from search.
    """
    if re.search(r'search_part\s*\(', text, re.I):
        return 'Searching for partition signatures'
    if re.search(r'^\s*Current partition structure:', text, re.I | re.M):
        return 'Reading the current partition table'
    if re.search(r'Analyse Disk', text, re.I):
        return 'Analyzing disk layout'
    if re.search(r'Partition table type|Disk [/:\\]', text, re.I):
        return 'Opening and identifying the disk'
    return 'Starting the native TestDisk process'


def testdisk_binary():
    """Locate the actual TestDisk executable in the target OS package.

    On Windows, PhotoRec and TestDisk are shipped together with supporting
    runtime DLLs. PyInstaller extracts them below photorec-bin/. Never pass a
    Windows physical disk to this routine: callers authorize image files only.
    """
    name = 'testdisk_win.exe' if sys.platform == 'win32' else 'testdisk'
    locations = []
    bundled = getattr(sys, '_MEIPASS', None)
    if bundled:
        locations.append(Path(bundled) / 'photorec-bin')
    custom = os.environ.get('SECTORREC_TESTDISK_DIR')
    if custom:
        locations.append(Path(custom))
    for folder in locations:
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)

@dataclass(frozen=True)
class PartitionCandidate:
    start: int
    sectors: int
    label: str
    sector_size: int = 512
    origin: str = 'found'  # 'existing' from live partition table, or 'found' by TestDisk
    volume_path: str = ''  # Windows only: existing lettered partition's raw volume

    @property
    def size(self) -> int:
        return self.sectors * self.sector_size


def parse_testdisk_log(content: str, source_bytes: int = 0) -> list[PartitionCandidate]:
    """Parse finished TestDisk 7.2 reports, including the initial layout.

    TestDisk's scripted ``analyze,search`` may preserve a full-sized partition
    in ``Current partition structure`` yet show only a tiny signature in the
    later ``Results``. Both are *candidates*, never filesystem validation.
    The actual TestDisk execution/wait strategy is deliberately unchanged.
    """
    if not isinstance(content, str):
        raise TypeError('TestDisk report must be text')
    geometry = re.search(r'CHS\s+\d+\s+(\d+)\s+(\d+)', content)
    heads, track = (int(geometry[1]), int(geometry[2])) if geometry else (0, 0)
    sizes = re.findall(r'sector size\s*=\s*(\d+)', content, re.I)
    sector_size = int(sizes[0]) if sizes else 512
    if sector_size != 512:
        raise ValueError('Non-512-byte source sectors are not yet supported safely')

    # Only table rows inside explicit sections. Never interpret arbitrary log
    # messages, hints, CHS diagnostic lines, or the final interface_write().
    section_header = re.compile(r'^\s*(Current partition structure:|Results)\s*$', re.I | re.M)
    boundaries = re.compile(
        r'^\s*(?:search_part\(|interface_write\(|Disk /|Hint for|'
        r'Analyse Disk |TestDisk exited|Partition table type|Read error|'
        r'No partition|Structure:)', re.I)
    candidates: dict[tuple[int, int], PartitionCandidate] = {}
    matches = list(section_header.finditer(content))
    for index, header in enumerate(matches):
        origin = 'existing' if header.group(1).lower().startswith('current') else 'found'
        until = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        for line in content[header.end():until].splitlines():
            if boundaries.match(line):
                break
            row = re.match(r'^\s*(?:\d+\s+)?([*PELD])\s+(.*?)\s*$', line)
            if not row:
                continue
            desc = re.sub(r'\s*\[.*$', '', row.group(2))
            match = re.match(r'^(.*?)\s+((?:\d+\s+){2,6}\d+)\s*$', desc)
            if not match:
                continue
            label = match.group(1).strip()[:70]
            numbers = [int(v) for v in match.group(2).split()]
            if len(numbers) == 3:
                start, end, count = numbers
            elif len(numbers) == 7 and heads and track:
                c, h, sec, _ec, _eh, _es, count = numbers
                if not (0 <= h < heads and 1 <= sec <= track):
                    continue
                start = (c * heads + h) * track + sec - 1
                end = start + count - 1
            else:
                continue
            if not label or count <= 0 or start < 0 or start + count - 1 != end:
                continue
            if source_bytes and (start + count) * sector_size > source_bytes:
                continue
            key = (start, count)
            # Preserve the initial structure classification on duplicates.
            if key not in candidates or origin == 'existing':
                candidates[key] = PartitionCandidate(start, count, label, sector_size, origin)
            if len(candidates) >= MAX_CANDIDATES:
                break
    return sorted(candidates.values(), key=lambda p: (p.start, p.sectors))


def parse_existing_lsblk(tree: dict, source: str, source_bytes: int = 0) -> list[PartitionCandidate]:
    """Read kernel-known partitions independently of filesystem signatures.

    lsblk's FSTYPE can be absent even for a valid partition (e.g. a damaged
    boot sector). An entry in the partition table is NOT a TestDisk find;
    don't discard it just because blkid cannot identify a filesystem.
    """
    disks = tree.get('blockdevices', []) if isinstance(tree, dict) else []
    result = []
    for disk in disks:
        if disk.get('path') != source or disk.get('type') != 'disk':
            continue
        try:
            if int(disk.get('log-sec') or 512) != 512:
                return []
        except (TypeError, ValueError):
            return []
        for part in disk.get('children') or []:
            if part.get('type') != 'part':
                continue
            try:
                name = Path(str(part.get('path') or '')).name
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
                    continue
                sysfs = Path('/sys/class/block') / name
                raw_start = part.get('start')
                start = int(raw_start if raw_start not in (None, '') else
                            (sysfs / 'start').read_text().strip())
                raw_size = part.get('size')
                size = int(raw_size if raw_size not in (None, '') else
                           int((sysfs / 'size').read_text().strip()) * 512)
            except (TypeError, ValueError, OSError):
                continue
            if size <= 0 or size % 512 or start < 0:
                continue
            count = size // 512
            if source_bytes and (start + count) * 512 > source_bytes:
                continue
            label = str(part.get('fstype') or part.get('partlabel') or 'PARTITION').upper()
            result.append(PartitionCandidate(start, count, label, origin='existing'))
            if len(result) >= MAX_CANDIDATES:
                break
        break
    return result


def _existing_from_sysfs(source: str, source_bytes: int = 0,
                         sysfs_root: Path = Path('/sys/class/block')) -> list[PartitionCandidate]:
    """Linux's partition geometry: reliable fallback when lsblk/blkid fails.

    /sys/class/block/*/{start,size} are expressed in Linux 512-byte sectors.
    The parent is determined by the resolved sysfs hierarchy rather than by
    guessing names (works for sdX, mmcblkXpY, and nvmeXnYpZ).
    """
    name = Path(source).name
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        return []
    parent = sysfs_root / name
    try:
        parent_real = parent.resolve(strict=True)
        logical = int((parent / 'queue/logical_block_size').read_text().strip())
        if logical != 512:
            return []
        result = []
        for part in sysfs_root.iterdir():
            if not (part / 'partition').exists():
                continue
            if part.resolve().parent != parent_real:
                continue
            start = int((part / 'start').read_text().strip())
            count = int((part / 'size').read_text().strip())
            if start < 0 or count <= 0 or (source_bytes and
                (start + count) * 512 > source_bytes):
                continue
            result.append(PartitionCandidate(start, count, 'PARTITION', origin='existing'))
            if len(result) >= MAX_CANDIDATES:
                break
        return sorted(result, key=lambda p: p.start)
    except (OSError, ValueError):
        return []


def existing_partitions(source: str, source_bytes: int = 0) -> list[PartitionCandidate]:
    """Read known partitions via lsblk and /sys, without device writes.

    A previously-undetected partition may still be found by TestDisk, but
    known partitions must not disappear because an lsblk invocation failed.
    """
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', source):
        return []
    command = ['/usr/bin/lsblk', '-J', '-b', '-o',
               'PATH,TYPE,FSTYPE,PARTLABEL,START,SIZE,LOG-SEC']
    result = []
    for args in (command + ['--', source], command):
        try:
            data = subprocess.check_output(args, timeout=7, text=True)
            result = parse_existing_lsblk(json.loads(data), source, source_bytes)
            if result:
                break
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    # Prefer labels supplied by lsblk, but do not depend on lsblk to provide
    # all children. Kernel partition geometry remains the final authority.
    kernel = _existing_from_sysfs(source, source_bytes)
    merged = {(p.start, p.sectors): p for p in kernel}
    merged.update({(p.start, p.sectors): p for p in result})
    return sorted(merged.values(), key=lambda p: p.start)[:MAX_CANDIDATES]



def existing_windows_partitions(source: str, source_bytes: int = 0,
                                run=subprocess.run) -> list[PartitionCandidate]:
    """Read the actual Windows partition table, including unlettered volumes.

    Do not infer partitions from filesystem labels or drive letters: a valid
    partition might be RAW, damaged or have no assigned letter. Get-Partition
    reports byte offsets and lengths; SectorRec/Sleuth expects 512-byte sectors.
    Unsupported logical sector sizes fail closed.
    """
    match = re.fullmatch(r'\\\\\.\\PhysicalDrive(\d+)', str(source), re.I)
    if not match:
        return []
    number = int(match.group(1))
    script = ("$ErrorActionPreference='Stop';"
              f"$d=Get-Disk -Number {number} -ErrorAction Stop;"
              "if([int]$d.LogicalSectorSize -ne 512){exit 4};"
              f"$p=@(Get-Partition -DiskNumber {number} -ErrorAction Stop | ForEach-Object {{"
              "[pscustomobject]@{Offset=[string]$_.Offset;Size=[string]$_.Size;"
              "Type=[string]$_.Type;Letter=[string]$_.DriveLetter}});"
              "ConvertTo-Json -InputObject $p -Depth 3 -Compress")
    try:
        result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                     capture_output=True, text=True, timeout=12, check=False,
                     **no_console_window())
        if result.returncode:
            return []
        partitions = json.loads(result.stdout)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return []
    if isinstance(partitions, dict):
        partitions = [partitions]
    if not isinstance(partitions, list):
        return []
    found = {}
    for partition in partitions:
        if not isinstance(partition, dict):
            continue
        try:
            offset = int(partition.get('Offset', ''))
            length = int(partition.get('Size', ''))
        except (TypeError, ValueError):
            continue
        if offset < 0 or length <= 0 or offset % 512 or length % 512:
            continue
        if source_bytes and offset + length > source_bytes:
            continue
        label = str(partition.get('Type') or 'PARTITION').strip()[:55] or 'PARTITION'
        letter = str(partition.get('Letter') or '').strip().upper()
        if re.fullmatch(r'[A-Z]', letter):
            label = f'{label} ({letter}:)'
        start, sectors = offset // 512, length // 512
        volume = '\\\\.\\' + letter + ':' if re.fullmatch(r'[A-Z]', letter) else ''
        found[(start, sectors)] = PartitionCandidate(start, sectors, label, origin='existing', volume_path=volume)
        if len(found) >= MAX_CANDIDATES:
            break
    return sorted(found.values(), key=lambda part: (part.start, part.sectors))



def verify_windows_partition_volume(source: str, candidate: PartitionCandidate,
                                    run=subprocess.run) -> bool:
    """Prove that a lettered volume still belongs to the selected partition.

    Drive letters can be reassigned when USB media are removed. Fall back to
    PhysicalDrive + offset if the current mapping cannot be established.
    This check is intended once per scan/recovery operation, not per file.
    """
    disk = re.fullmatch(r'\\\\\.\\PhysicalDrive(\d+)', str(source), re.I)
    volume = re.fullmatch(r'\\\\\.\\([A-Za-z]):', candidate.volume_path)
    if not disk or not volume or candidate.origin != 'existing':
        return False
    letter = volume.group(1).upper()
    script = ("$ErrorActionPreference='Stop';"
              f"$p=Get-Partition -DriveLetter {letter} -ErrorAction Stop;"
              "[pscustomobject]@{Disk=[string]$p.DiskNumber;"
              "Offset=[string]$p.Offset;Size=[string]$p.Size}"
              "|ConvertTo-Json -Compress")
    try:
        result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                     capture_output=True, text=True, timeout=12, check=False,
                     **no_console_window())
        if result.returncode:
            return False
        record = json.loads(result.stdout)
        return (isinstance(record, dict) and
                int(record['Disk']) == int(disk.group(1)) and
                int(record['Offset']) == candidate.start * candidate.sector_size and
                int(record['Size']) == candidate.size)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return False


def merge_partition_candidates(current, discovered):
    """Merge OS-visible partitions with TestDisk results by disk sector range."""
    mapped = {(p.start, p.sectors): p for p in discovered}
    for p in current:
        mapped[(p.start, p.sectors)] = p
    return sorted(mapped.values(), key=lambda p: (p.start, p.sectors))[:MAX_CANDIDATES]

def partition_command(action, source, offset, inode=None, image=False):
    """Return strictly read-only command without shell or user-supplied flags."""
    if not isinstance(offset, int) or offset < 0:
        raise ValueError('Invalid partition offset')
    if action not in ('scan', 'recover', 'recover_allocated', 'deleted_children'):
        raise ValueError('Partition mode does not support this action')
    if action != 'scan' and (inode is None or not re.fullmatch(r'\d+(?:-\d+){0,2}', str(inode))):
        raise ValueError('Invalid metadata address')
    from src.infrastructure.windows_raw import windows_device_path, require_admin
    raw_windows = sys.platform == 'win32' and windows_device_path(source)
    if image or raw_windows:
        if raw_windows:
            require_admin()
            src = source
        else:
            src = Path(source).expanduser().resolve(strict=True)
            if not src.is_file():
                raise ValueError('Expected a readable disk image')
            if (offset + 1) * 512 > src.stat().st_size:
                raise ValueError('Partition offset is outside image')
        binary = find_tsk_binary('fls' if action in ('scan','deleted_children') else 'icat')
        if not binary:
            raise RuntimeError('Sleuth Kit binaries missing')
        args = ([binary, '-o', str(offset), '-r', '-p', '-l', str(src)] if action == 'scan'
                else [binary, '-o', str(offset), '-d', '-l', '-p', str(src), str(inode)] if action=='deleted_children'
                else [binary, '-o', str(offset)] + (['-r'] if action == 'recover' else []) + [str(src), str(inode)])
        return args
    return helper_command('partition_' + action, source, str(offset) if inode is None else f'{offset}:{inode}')


def discover_command(source, image=False):
    """Subprocess syntax for read-only partition analysis; no 'write' token."""
    if image:
        src = Path(source).expanduser().resolve(strict=True)
        if not src.is_file():
            raise ValueError('Source must be a regular file')
        binary = testdisk_binary() or ('testdisk' if sys.platform != 'win32' else None)
        if not binary:
            raise RuntimeError('TestDisk not found: provide the official TestDisk executable for this OS')
        return [binary, '/log', '/cmd', str(src), 'analyze,search']
    from src.infrastructure.windows_raw import windows_device_path, require_admin
    if sys.platform == 'win32' and windows_device_path(source):
        require_admin()
        binary = testdisk_binary()
        if not binary:
            raise RuntimeError('TestDisk Windows executable missing')
        return [binary, '/log', '/cmd', source, 'analyze,search']
    return helper_command('partition_search', source)


class PartitionSearchWorker(threading.Thread):
    """Bounded-memory TestDisk search; generated logs stay off source media."""
    def __init__(self, source, source_size, image, status):
        super().__init__(daemon=True)
        self.source, self.source_size, self.image, self.status = source, source_size, image, status
        self.proc = None
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()
        # The worker thread owns termination and wait(), avoiding a race where
        # the UI sees completion while the privileged helper is still alive.


    def run(self):
        self.status.update(state='running', started=time.monotonic(), candidates=[], error='')
        for stale in ('testdisk_io_bytes','testdisk_io_last_change','testdisk_io_idle_seconds',
                      'testdisk_phase','testdisk_log_bytes','testdisk_log_idle_seconds'):
            self.status.pop(stale, None)
        try:
            with tempfile.TemporaryDirectory(prefix='sectorrec-partitions-') as tmp:
                from src.infrastructure.windows_raw import windows_device_path, require_admin
                win_raw = sys.platform == 'win32' and windows_device_path(self.source)
                timed_out = False
                if self.image or win_raw:
                    if win_raw: require_admin()
                    # /logname is supported by TestDisk 7.2. File creation is
                    # confined to the private temporary directory.
                    log = Path(tmp) / 'testdisk.log'
                    binary = testdisk_binary()
                    if not binary:
                        raise RuntimeError('TestDisk executable unavailable for this platform')
                    cmd = [binary, '/log', '/logname', str(log), '/cmd',
                           (self.source if win_raw else str(Path(self.source).resolve(strict=True))), 'analyze,search']
                    # Optional detailed native diagnostics. Keep off by
                    # default: verbose logs can grow very large on big disks.
                    # The regular status already tracks process I/O and phase.
                    if win_raw and os.environ.get('SECTORREC_TESTDISK_DEBUG') == '1':
                        cmd.insert(1, '/debug')
                    # Never leave TestDisk stderr attached to an unread PIPE.
                    # On Windows the child could block forever once the pipe
                    # fills, even though the USB scan itself has finished.
                    with tempfile.TemporaryFile() as errors:
                        self.proc = subprocess.Popen(cmd, cwd=tmp, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=errors, **isolated_process_group(),
                                **no_console_window())
                        self.status['testdisk_started'] = time.monotonic()
                        timed_out = False
                        last_log_probe = 0.0
                        last_log_size = 0
                        log_last_changed = time.monotonic()
                        small_windows_disk = (win_raw and 0 < self.source_size <= WINDOWS_SMALL_DISK_BYTES)
                        while self.proc.poll() is None and not self.stop.wait(0.15):
                            elapsed = time.monotonic() - self.status['testdisk_started']
                            if win_raw and elapsed - last_log_probe >= 2:
                                last_log_probe = elapsed
                                try:
                                    current_log_size = log.stat().st_size
                                    if current_log_size != last_log_size:
                                        log_last_changed = time.monotonic()
                                        last_log_size = current_log_size
                                        # Inspect only a bounded tail: debug
                                        # logs can exceed many megabytes.
                                        with log.open('rb') as report:
                                            report.seek(max(0,current_log_size-16384))
                                            tail = report.read(16384).decode('utf-8','replace')
                                        self.status['testdisk_phase'] = testdisk_report_phase(tail)
                                except OSError:
                                    pass
                                self.status['testdisk_log_bytes'] = last_log_size
                                self.status['testdisk_log_idle_seconds'] = max(
                                    0, int(time.monotonic() - log_last_changed))
                                # Read activity is more meaningful than an
                                # unchanged log: TestDisk often writes no new
                                # log lines while performing legitimate I/O.
                                record_read_activity(self.status, process_read_bytes(self.proc), time.monotonic())
                            if not win_raw and elapsed - last_log_probe >= 2:
                                # Direct image scans have no privileged wrapper.
                                last_log_probe = elapsed
                                record_read_activity(self.status, process_read_bytes(self.proc), time.monotonic())
                            if (small_windows_disk and WINDOWS_SMALL_DISK_MAX_SECONDS is not None
                                    and elapsed >= WINDOWS_SMALL_DISK_MAX_SECONDS):
                                timed_out = True
                                self.status['testdisk_timed_out'] = True
                                break
                        if self.stop.is_set() or timed_out:
                            stop_proc(self.proc)
                        self.proc.wait()
                        self.status['testdisk_seconds'] = round(time.monotonic() - self.status['testdisk_started'], 2)
                        try:
                            self.status['testdisk_log_bytes'] = log.stat().st_size
                        except OSError:
                            self.status['testdisk_log_bytes'] = 0
                        errors.seek(0)
                        err = errors.read(300).decode('utf-8', 'replace')
                    if log.exists():
                        with open(log,'rb') as fh:
                            head=fh.read(4096)
                            fh.seek(0,2)
                            fh.seek(max(4096,fh.tell()-(MAX_LOG_BYTES-4096)))
                            result=(head+b'\n'+fh.read(MAX_LOG_BYTES-4096)).decode('utf-8','replace')
                    else:
                        result=''
                else:
                    # Privileged helper independently validates the disk and
                    # writes the TestDisk report to stdout on completion.
                    # Spool output to temporary files: never let a pipe fill
                    # and stall a long privileged partition search.
                    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                        self.proc = subprocess.Popen(discover_command(self.source),
                                stdout=output, stderr=errors, **isolated_process_group(),
                                **no_console_window())
                        last_probe = 0.0
                        while self.proc.poll() is None and not self.stop.wait(0.15):
                            now = time.monotonic()
                            if now - last_probe >= 2.0:
                                last_probe = now
                                # The privileged helper reports only a numeric
                                # counter for its TestDisk child. os.pread does
                                # not disturb the helper's stderr file offset.
                                try:
                                    end = os.fstat(errors.fileno()).st_size
                                    tail = os.pread(errors.fileno(), min(2048, end), max(0, end - 2048))
                                    matches = re.findall(rb'SECTORREC_IO_READ_BYTES=(\d+)', tail)
                                    if matches:
                                        record_read_activity(self.status, int(matches[-1]), now)
                                except (OSError, ValueError):
                                    pass
                        if self.stop.is_set(): stop_proc(self.proc)
                        self.proc.wait()
                        output.seek(0); errors.seek(0)
                        result = output.read(MAX_LOG_BYTES).decode('utf-8','replace')
                        err = '\n'.join(line for line in errors.read(4096).decode('utf-8','replace').splitlines()
                                        if not line.startswith('SECTORREC_IO_READ_BYTES='))[:300]
                if self.stop.is_set():
                    # "Stop early" must preserve useful validated partitions,
                    # especially now that large Windows disks are not cut off
                    # by a hard deadline. Do not claim Windows-known entries
                    # were discovered by TestDisk.
                    if win_raw:
                        try:
                            candidates = parse_testdisk_log(result, self.source_size) if result.strip() else []
                        except (ValueError,TypeError):
                            candidates = []  # an interrupted log can be incomplete
                        self.status['testdisk_count'] = len(candidates)
                        try:
                            known = existing_windows_partitions(self.source,self.source_size)
                            candidates = merge_partition_candidates(known,candidates)
                        except (OSError,ValueError) as lookup_error:
                            self.status['existing_scan_error'] = str(lookup_error)[:200]
                        self.status['candidates'] = candidates
                        self.status['existing_count'] = sum(p.origin == 'existing' for p in candidates)
                        self.status['found_count'] = sum(p.origin == 'found' for p in candidates)
                        self.status['partial'] = True
                        self.status['note'] = ('Scan stopped early. Existing partitions may be reported by Windows; '
                                               'only additional entries are TestDisk candidates. '
                                               'Lost partitions may still be missing.')
                    self.status['state'] = 'cancelled'
                else:
                    # Some TestDisk releases return a non-zero status when a
                    # scripted search cannot progress further. Do not throw
                    # away already validated candidates from a real report.
                    candidates = parse_testdisk_log(result, self.source_size) if result.strip() else []
                    # Preserve the known-good alpha6 sequence: let TestDisk run
                    # untouched until it exits, parse its finished report, THEN
                    # ask Linux about currently present partitions. No lsblk,
                    # sysfs probing, or report polling takes place during scan.
                    self.status['testdisk_count'] = len(candidates)
                    if not self.image:
                        self.status['state'] = 'merging'
                        try:
                            existing = (existing_windows_partitions(self.source, self.source_size)
                                        if win_raw else existing_partitions(self.source, self.source_size))
                            candidates = merge_partition_candidates(existing, candidates)
                        except Exception as lookup_error:
                            self.status['existing_scan_error'] = str(lookup_error)[:200]
                            if not win_raw:
                                self.status['linux_scan_error'] = str(lookup_error)[:200]
                    self.status['candidates'] = candidates
                    self.status['existing_count'] = sum(p.origin == 'existing' for p in candidates)
                    self.status['found_count'] = sum(p.origin == 'found' for p in candidates)
                    if timed_out and not candidates:
                        raise RuntimeError('Windows TestDisk did not finish the small-disk scan within '
                                           f'{WINDOWS_SMALL_DISK_MAX_SECONDS}s. This is a stalled or '
                                           'exceptionally slow native scan, not proof of disk damage. '
                                           'Try scanning a disk image instead.')
                    if timed_out:
                        self.status['partial'] = True
                        if self.status['testdisk_count'] == 0 and self.status['existing_count']:
                            self.status['note'] = (
                                f'TestDisk did not finish its physical-device scan after '
                                f'{WINDOWS_SMALL_DISK_MAX_SECONDS}s. The existing partition(s) '
                                'shown were reported by Windows, NOT discovered by TestDisk. '
                                'The lost-partition search remains incomplete; try scanning a disk image.')
                        else:
                            self.status['note'] = (
                                f'TestDisk did not finish its physical-device scan after '
                                f'{WINDOWS_SMALL_DISK_MAX_SECONDS}s. These are partial results; '
                                'additional lost partitions may be missing. Try scanning a disk image.')
                    if self.proc.returncode != 0 and not candidates:
                        detail = (err.strip() or 'No usable partition locations were reported.')
                        raise RuntimeError('TestDisk did not provide usable partition results. '
                                           'Possible causes include unsupported layouts or hybrid ISO images. '
                                           + detail[:100])
                    if not result.strip():
                        if not (win_raw and candidates):
                            raise RuntimeError('TestDisk produced no analysis report')
                        self.status['partial'] = True
                        if not timed_out:
                            self.status['note'] = ('Windows reports the existing partitions, but TestDisk '
                                               'did not generate an analysis report. Lost partitions '
                                               'could not be confirmed in this scan.')
                    self.status['state'] = 'done'
                    if self.proc.returncode != 0:
                        self.status['partial'] = True
                        self.status.setdefault('note',
                            'Search stopped early or reported a problem. '
                            'Only the validated candidates shown can be explored.')
                    elif not candidates:
                        if not re.search(r'^\s*Results\s*$',result,re.I|re.M):
                            self.status['note'] = ('TestDisk did not include a recognized partition table. '
                                                   'The report may have a different format; no partition '
                                                   'was confirmed by SectorRec.')
                        else:
                            self.status['note'] = ('No validated partition candidates were reported. '
                                                   'This does not mean the disk is damaged.')
        except Exception as exc:
            self.status.update(state='cancelled' if self.stop.is_set() else 'error', error=str(exc)[:200])
        finally:
            if self.proc is not None:
                try:
                    if self.proc.poll() is None:
                        stop_proc(self.proc)
                    self.proc.wait()
                except (OSError, RuntimeError) as exc:
                    self.status.update(state='error', error=str(exc)[:200])
                self.proc = None


def image_table_partitions(image_path: str) -> list[PartitionCandidate]:
    """Enumerate *existing* MBR/GPT partitions in a regular disk image.

    This is NOT TestDisk. Only bounded, read-only sector reads are performed;
    a corrupt table is not treated as evidence of missing/deleted partitions.
    The existing fls/icat -o path consumes the same sector offsets as TestDisk.
    Supports 512-byte-sector images, including an MBR extended EBR chain.
    """
    import struct
    source=Path(image_path).resolve(strict=True)
    if not source.is_file():
        raise ValueError('Select a regular disk image')
    total=source.stat().st_size
    if total<512:
        return []
    max_lba=total//512
    def valid(start, count):
        return start>0 and count>0 and start<max_lba and count<=max_lba-start
    result=[]
    with source.open('rb') as f:
        def sector(lba, count=1):
            if lba<0 or count<1 or count>256 or not valid(lba,count):
                return b''
            f.seek(lba*512)
            return f.read(count*512)
        f.seek(0)
        boot=f.read(512)
        if boot[510:512]!=b'\x55\xaa':
            return []
        entries=[]
        for n in range(4):
            entry=boot[446+n*16:462+n*16]
            code=entry[4]
            start,count=struct.unpack_from('<II',entry,8)
            if code and valid(start,count):
                entries.append((code,start,count))
        # GPT: protective MBR (0xEE), followed by a GPT header at LBA 1.
        if any(code==0xee for code,_,_ in entries):
            header=sector(1)
            if (header[:8]==b'EFI PART' and len(header)==512 and
                    92<=int.from_bytes(header[12:16],'little')<=512):
                table_lba=struct.unpack_from('<Q',header,72)[0]
                entry_count=struct.unpack_from('<I',header,80)[0]
                entry_size=struct.unpack_from('<I',header,84)[0]
                if (entry_size>=128 and entry_size<=1024 and entry_size%8==0 and
                        entry_count<=16384 and table_lba>1 and table_lba<max_lba):
                    # Never allocate/read the entire untrusted partition array.
                    for n in range(min(entry_count, MAX_CANDIDATES * 16)):
                        offset=table_lba*512+n*entry_size
                        if offset+entry_size>total:
                            break
                        f.seek(offset)
                        entry=f.read(entry_size)
                        if len(entry)!=entry_size or entry[:16]==bytes(16):
                            continue
                        first,last=struct.unpack_from('<QQ',entry,32)
                        if first>last or not valid(first,last-first+1):
                            continue
                        name=entry[56:min(128,entry_size)].decode('utf-16-le','replace').split('\x00')[0].strip()
                        name=''.join(c for c in name if c.isprintable())[:64]
                        result.append(PartitionCandidate(first,last-first+1,name or f'GPT partition {n+1}',origin='existing'))
                        if len(result)>=MAX_CANDIDATES:
                            break
                    return sorted(result,key=lambda part:part.start)
            # Invalid GPT: don't mistake its protective MBR for a filesystem.
            return []
        types={0x01:'FAT12',0x04:'FAT16',0x06:'FAT16',0x07:'NTFS / exFAT',
               0x0b:'FAT32',0x0c:'FAT32',0x0e:'FAT16',0x83:'Linux',0xaf:'HFS+',
               0x82:'Linux swap',0xa5:'FreeBSD',0x0f:'Extended',0x05:'Extended',0x85:'Extended'}
        extensions=[]
        for code,start,count in entries:
            if code in (0x05,0x0f,0x85):
                extensions.append(start)
            else:
                result.append(PartitionCandidate(start,count,types.get(code, f'MBR type 0x{code:02X}'),origin='existing'))
        # Logical partitions: EBR relative start; 'next EBR' relative to the
        # primary extended partition's base. Stop on loops/corrupted chains.
        for base in extensions:
            ebr=base
            seen=set()
            while ebr not in seen and len(seen)<MAX_CANDIDATES and len(result)<MAX_CANDIDATES:
                seen.add(ebr)
                block=sector(ebr)
                if len(block)!=512 or block[510:512]!=b'\x55\xaa':
                    break
                first=block[446:462]
                code=first[4]
                relative,count=struct.unpack_from('<II',first,8)
                if code and code not in (0x05,0x0f,0x85) and valid(ebr+relative,count):
                    result.append(PartitionCandidate(ebr+relative,count,
                                  types.get(code,f'MBR type 0x{code:02X}'),origin='existing'))
                follow=block[462:478]
                next_rel=struct.unpack_from('<I',follow,8)[0]
                if follow[4] not in (0x05,0x0f,0x85) or not next_rel or not valid(base+next_rel,1):
                    break
                ebr=base+next_rel
    return sorted(result,key=lambda part:part.start)[:MAX_CANDIDATES]
