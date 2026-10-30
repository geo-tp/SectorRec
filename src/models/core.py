"""Legacy metadata worker API and guarded recovery.

Catalogue storage, file metadata and TSK parsing live in focused modules.
Their public names remain available here for existing callers. Worker command
injection and recovery lifecycle retain their original compatibility surface.
"""
from __future__ import annotations
import datetime as dt
from collections import deque
import tempfile
import os
import re
import shutil
import subprocess
from src.infrastructure.windows_process import no_console_window
import threading
import time
from pathlib import Path

# Preserve the historical import surface while focused modules own these APIs.
from src.models.catalog import Catalog as Catalog
from src.models.files import (
    CATEGORIES as CATEGORIES, EXTENSIONS as EXTENSIONS,
    category_of as category_of, extension_of as extension_of,
    human_size as human_size,
)
from src.services.tsk_parser import (
    _META, parse_entry, parse_fls as parse_fls, trash_root, trash_content,
)
from src.infrastructure.privileged import (
    ROOT_HELPER as ROOT_HELPER, helper_command, stop_proc, isolated_process_group,
)

MIN_FREE = 64 * 1024 * 1024
MAX_RECORDS = 100_000  # Cardputer Zero: SQLite catalog; small fixed-size batches, not in-RAM file list
MAX_RECORDS_DESKTOP = 20_000_000  # Desktop only: streaming SQLite catalogue; Cardputer stays at 100k.
# Leave headroom on the volume hosting scan.sqlite (external for system-disk scans).
# The database and its rollback journal can grow to many gigabytes on large scans.
DESKTOP_CATALOG_FREE_RESERVE = 512 * 1024 * 1024
DESKTOP_CATALOG_FREE_CHECK_EVERY = 10_000


# Deprecated facade for callers of the v0.1.x model API. The actual Linux
# implementation now lives in linux_devices.py, while tests can still inject
# lsblk_tree / helper_command independently.
from src.infrastructure import linux_devices as _linux_devices
SUPPORTED_FS = _linux_devices.SUPPORTED_FS
SYSTEM_MOUNTS = _linux_devices.SYSTEM_MOUNTS
mount_list = _linux_devices.mount_list
walk_devices = _linux_devices.walk_devices
lsblk_tree = _linux_devices.lsblk_tree

def detect_devices():
    return _linux_devices.detect_devices(lsblk_tree)

def unmount_volume(device: str) -> None:
    return _linux_devices.unmount_volume(device, detect_devices, helper_command)


class ScanWorker(threading.Thread):
    """Bounded, cancellable, streaming TSK scan with optional targeted passes."""
    MAX_DELETED_DIRS = 128   # avoid runaway rescans on complex/corrupt images
    MAX_TRASH_ROOTS = 16
    MAX_DEPTH = 8

    def __init__(self, catalog, device, status, scope='ALL', command_builder=None, include_allocated=False,
                 protect_source=False, source_guard=None, max_records=MAX_RECORDS):
        super().__init__(daemon=True)
        self.catalog, self.device, self.status = catalog, device, status
        self.scope = scope
        self.command_builder = command_builder
        self.include_allocated = include_allocated
        self.protect_source = protect_source
        self.source_guard = source_guard
        self.cancel_event = threading.Event()
        self.proc = None
        self.pending = []
        self.dir_seen = set()
        self.deleted_dirs = deque()
        self.total_seen = 0
        self.max_records = max_records
        # Fewer SQLite transactions on desktop without retaining entire scans.
        self.batch_size = 500 if max_records is None or max_records > MAX_RECORDS else 100

    def cancel(self):
        self.cancel_event.set()
        if self.proc is not None:
            threading.Thread(target=stop_proc, args=(self.proc,), daemon=True).start()

    def flush(self):
        if self.pending:
            self.catalog.insert_batch(self.pending)
            self.pending.clear()
        self.status['count'] = self.catalog.indexed_count

    def accept(self, record, origin='deleted'):
        if not record or self.cancel_event.is_set():
            return
        if self.scope != 'ALL' and record[3] != self.scope:
            return
        self.pending.append((*record,origin))
        self.total_seen += 1
        self.status['count'] = self.total_seen
        # Desktop catalog preview reads committed SQLite rows. Previously
        # the first 499 discoveries existed only in this in-memory batch:
        # the counter showed e.g. "98 entries found" but the preview stayed
        # blank until the full scan completed. Publish the first entry and
        # small early batches, then revert to the efficient 500-row batches.
        warmup = (self.max_records is None or self.max_records > MAX_RECORDS)
        publish_early = warmup and (self.total_seen == 1 or
                                    (self.total_seen <= 100 and self.total_seen % 25 == 0))
        if publish_early or len(self.pending) >= self.batch_size:
            self.flush()
        # A high desktop cap is only useful if indexing cannot fill the system
        # volume. Check occasionally rather than per row (the catalogue lives
        # on disk, not in a Python list). This never runs on Cardputer Zero.
        if (self.max_records is not None and self.max_records > MAX_RECORDS and
                self.total_seen % DESKTOP_CATALOG_FREE_CHECK_EVERY == 0):
            catalog_path = getattr(self.catalog, 'path', None)
            if catalog_path:
                try:
                    free = shutil.disk_usage(str(Path(catalog_path).parent)).free
                except OSError as exc:
                    raise RuntimeError(f'Cannot check catalog disk space: {exc}') from exc
                if free < DESKTOP_CATALOG_FREE_RESERVE:
                    self.status['catalog_storage_low'] = True
                    self.status['catalog_storage_free'] = free
                    self.status['limited'] = True
        if ((self.max_records is not None and self.total_seen >= self.max_records)
                or self.status.get('catalog_storage_low')):
            self.status['limited'] = True
            self.cancel_event.set()
            # Stop the producer, not the UI; preserve already indexed matches.
            if self.proc:
                threading.Thread(target=stop_proc, args=(self.proc,), daemon=True).start()

    def stream(self, action, inode=None):
        """Yield TSK stdout, draining stderr to disk so pipes cannot deadlock."""
        if self.cancel_event.is_set():
            return
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen((self.command_builder or helper_command)(action, self.device, inode),
                stdout=subprocess.PIPE, stderr=errors, bufsize=1,
                text=True, errors='replace', **isolated_process_group(),
                **no_console_window())
            self.proc = proc
            fully_read = False
            try:
                for line in proc.stdout:
                    if self.cancel_event.is_set():
                        break
                    yield line
                else:
                    fully_read = True
            finally:
                # A caller may stop after collecting enough directory roots.
                # Kill the producer before closing stdout to prevent deadlock.
                if self.cancel_event.is_set() or not fully_read:
                    stop_proc(proc)
                proc.stdout.close()
                code = proc.wait()
                errors.seek(0)
                details = errors.read(1000).decode('utf-8','replace').strip()
                self.proc = None
                if code != 0 and not self.cancel_event.is_set() and fully_read:
                    raise RuntimeError(details or f'{action}: fls returned {code}')

    def scan_deleted_directories(self):
        """fls -r does not follow deleted directories; enumerate bounded roots."""
        # Directory entries were collected in the initial deleted metadata
        # walk. No second full-volume fls -r pass is necessary.
        queue = self.deleted_dirs
        seen = 0
        while queue and seen < self.MAX_DELETED_DIRS and not self.cancel_event.is_set():
            inode, prefix, depth = queue.popleft()
            seen += 1
            try:
                for line in self.stream('deleted_children', inode):
                    record = parse_entry(line,'file')
                    if record:
                        i, path, size, cat = record
                        self.accept((i, prefix.rstrip('/')+'/'+path, size, category_of(path)))
                    elif depth < self.MAX_DEPTH:
                        subdir = parse_entry(line,'dir')
                        if subdir and subdir[0] not in self.dir_seen:
                            if len(self.dir_seen) >= self.MAX_DELETED_DIRS:
                                self.status['limited_dirs'] = True
                                continue
                            self.dir_seen.add(subdir[0])
                            queue.append((subdir[0],prefix.rstrip('/')+'/'+subdir[1],depth+1))
            except RuntimeError:
                # Deleted inode may have been reused / its contents wiped.
                # Other candidate recovery must remain available.
                self.status['unreadable_dirs'] = self.status.get('unreadable_dirs',0)+1
        if queue:
            self.status['limited_dirs'] = True

    def scan_trash(self):
        """Search allocated files only inside discovered root trash directories."""
        roots = []
        try:
            for line in self.stream('trash_roots'):
                record = parse_entry(line,'dir')
                if record and trash_root(record[1]):
                    roots.append((record[0],record[1]))
                if len(roots) >= self.MAX_TRASH_ROOTS:
                    self.status['limited_trash'] = True
                    break
        except RuntimeError:
            self.status['unreadable_trash'] = self.status.get('unreadable_trash',0)+1
        for inode, name in roots:
            if self.cancel_event.is_set():
                break
            try:
                for line in self.stream('trash_scan', inode):
                    record = parse_fls(line)
                    if record:
                        ino,path,size,cat = record
                        full = name + '/' + path
                        if trash_content(full):
                            self.accept((ino, full, size, category_of(path)), 'trash')
            except RuntimeError:
                self.status['unreadable_trash'] = self.status.get('unreadable_trash',0)+1

    def run(self):
        self.status.update(state='running',count=0,error='',started=time.monotonic(),elapsed=0.0)
        final_state = 'error'
        try:
            if self.protect_source and self.device.startswith('/dev/'):
                # The kernel guard prevents a desktop automounter re-mounting
                # between TestDisk and TSK or during a long fls scan.
                if not self.source_guard or not self.source_guard.alive():
                    from src.infrastructure.source_guard import SourceMountGuard
                    self.source_guard=SourceMountGuard.acquire(self.device)
                self.status['_guard']=self.source_guard
            self.catalog.clear()
            self.status['phase'] = 'Partition files' if self.include_allocated else 'Deleted metadata'
            for line in self.stream('scan'):
                record = parse_fls(line)
                if record:
                    allocated = self.include_allocated and not bool(_META.match(line.split('\t',1)[0]) and _META.match(line.split('\t',1)[0]).group(2))
                    self.accept(record, 'allocated' if allocated else 'deleted')
                else:
                    folder = parse_entry(line,'dir')
                    if folder and folder[0] not in self.dir_seen and (
                            not self.include_allocated or bool(_META.match(line.split('\t',1)[0]) and
                            _META.match(line.split('\t',1)[0]).group(2))):
                        if len(self.dir_seen) < self.MAX_DELETED_DIRS:
                            self.dir_seen.add(folder[0])
                            self.deleted_dirs.append((folder[0],folder[1],0))
                        else:
                            self.status['limited_dirs'] = True
            if not self.cancel_event.is_set():
                self.status['phase'] = 'Deleted folders'
                self.scan_deleted_directories()
            if not self.cancel_event.is_set() and not self.include_allocated:
                self.status['phase'] = 'Recycle bins'
                self.scan_trash()
            final_state = ('limited' if self.status.get('limited') else
                           'cancelled' if self.cancel_event.is_set() else 'done')
        except Exception as exc:
            self.status['error'] = str(exc)[:250]
            final_state = 'cancelled' if self.cancel_event.is_set() else 'error'
        finally:
            # Publish a terminal state only AFTER the last SQLite batch is
            # durable. Otherwise the UI could open an incomplete list on error.
            try:
                self.flush()
            except Exception as exc:
                self.status['error'] = ('Catalog write: '+str(exc))[:250]
                final_state = 'error'
            stop_proc(self.proc)
            self.status['state'] = final_state


def safe_filename(name: str):
    # Never treat metadata names as a destination directory path.
    name = Path(name.replace('\\', '/')).name
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+','_',name).strip(' .')[:85]
    return name if name not in ('','.','..') else 'unnamed.bin'



def safe_relative_path(source_path: str) -> Path:
    """Restore folders without allowing traversal, absolute paths or symlinks.

    Discard drive roots and neutralize unsafe components; reject overdeep and
    nonsensical input to keep pathological disk metadata from creating huge
    directory structures on the small system drive.
    """
    if not isinstance(source_path, str) or not source_path or '\x00' in source_path:
        raise ValueError('Unsafe recovered pathname')
    normalized = source_path.replace('\\','/')
    normalized = re.sub(r'^[a-zA-Z]:', '', normalized).lstrip('/')
    parts = normalized.split('/')
    if len(parts) > 24:
        raise ValueError('Path nesting exceeds safe limit')
    clean = []
    for part in parts:
        if part in ('', '.'):
            continue
        if part == '..':
            raise ValueError('Parent traversal is not permitted')
        clean.append(safe_filename(part))
    if not clean:
        raise ValueError('Empty recovered pathname')
    return Path(*clean)


def safe_target_path(outdir: Path, relative: Path, inode: str) -> Path:
    """Create safe recovery folders, disallow symlink components / collisions."""
    import stat
    destination = outdir
    for component in relative.parts[:-1]:
        destination = destination / component
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError:
            if not stat.S_ISDIR(destination.lstat().st_mode):
                raise ValueError('Unsafe directory or symbolic link in destination')
    # Use plain original name where possible, and deterministic collision IDs.
    requested = destination / relative.name
    if requested.is_symlink() or requested.exists():
        stem = Path(relative.name).stem[:64]
        suffix = Path(relative.name).suffix[:16]
        requested = destination / (stem + '_' + inode.replace('-','_') + suffix)
    return requested


def create_output_directory(home: Path, label: str, product='SectorRec', destination_root=None):
    path = target_root(home,label,product=product,compact=(destination_root is not None))
    if destination_root is not None:
        root = Path(destination_root).expanduser().resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise ValueError('Extraction destination is no longer a safe directory.')
        path = root / path.name
    for attempt in range(100):
        candidate = path if attempt == 0 else path.with_name(path.name+f'_{attempt}')
        try:
            candidate.mkdir(parents=True,exist_ok=False,mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError('No free recovery directory name')

def free_bytes(directory: Path):
    return shutil.disk_usage(str(directory)).free


def target_root(home: Path, label: str, when=None, product='SectorRec', compact=False):
    moment = when or dt.datetime.now()
    slug = re.sub(r'[^a-zA-Z0-9_-]+','-',label).strip('-')[:22] or 'device'
    if product == 'SectorRec':
        # Desktop: descriptive but compact; collisions get _1, _2, ...
        date = moment.strftime('%y%m%d' if compact else '%Y%m%d')
        return home / 'Backups' / product / f'{slug[:18]}_{date}'
    return home / 'Backups' / product / f'{moment:%Y%m%d-%H%M%S}_{slug}'


class RecoverWorker(threading.Thread):
    def __init__(self, catalog, category, selected, device, label, home, status, extension=None, command_builder=None, product='SectorRec', destination_root=None):
        super().__init__(daemon=True)
        self.catalog, self.category, self.selected = catalog, category, selected
        self.extension = extension
        self.device, self.label, self.home, self.status = device, label, home, status
        self.command_builder = command_builder
        self.product = product
        self.destination_root = destination_root
        self.cancel_event = threading.Event()
        self.proc = None

    def cancel(self):
        self.cancel_event.set()
        proc = self.proc
        if proc is not None:
            threading.Thread(target=stop_proc,args=(proc,),daemon=True).start()

    def run(self):
        self.status.update(state='running', done=0,total=0,error='',written=0,
                           failed=0,partial=0,recovered=0,unverified=0)
        try:
            outdir = create_output_directory(self.home,self.label,self.product, self.destination_root)
            self.status['outdir'] = str(outdir)
            if self.selected is None:
                iterable = self.catalog.all_rows(self.category,self.extension)
                count,known_size = self.catalog.summary(self.category,self.extension)
            else:
                iterable = [self.selected]
                count,known_size = 1, (self.selected[3] or 0)
            self.status['total'] = count
            if count == 0:
                self.status['state'] = 'done'
                return
            if known_size > max(0,free_bytes(outdir)-MIN_FREE):
                raise RuntimeError('Not enough free space for known file sizes')
            for idx,item in enumerate(iterable,1):
                if self.cancel_event.is_set():
                    break
                _, inode, name, expected, _, *tail = item
                origin = tail[0] if tail else 'deleted'
                self.status.update(filename=safe_filename(name),done=idx-1)
                saved = 0
                code = 255
                error = ''
                output_path = None
                try:
                    if free_bytes(outdir) < MIN_FREE + (expected or 0):
                        raise RuntimeError('Disk space safety limit reached')
                    rel = safe_relative_path(name)
                    output_path = safe_target_path(outdir,rel,inode)
                    action = 'recover_allocated' if origin in ('trash','allocated') else 'recover'
                    with tempfile.TemporaryFile() as errors:
                        self.proc = subprocess.Popen((self.command_builder or helper_command)(action,self.device,inode),
                            stdout=subprocess.PIPE,stderr=errors,**isolated_process_group(),
                            **no_console_window())
                        try:
                            # O_EXCL and O_NOFOLLOW prevent metadata-driven overwrite
                            # or redirection through a pre-existing final symlink.
                            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0)
                            with os.fdopen(os.open(output_path,flags,0o600),'wb') as output:
                                while not self.cancel_event.is_set():
                                    chunk = self.proc.stdout.read(65536)
                                    if not chunk:
                                        break
                                    if expected is not None and saved+len(chunk) > expected:
                                        raise RuntimeError('Data exceeds file size reported by TSK')
                                    if free_bytes(outdir) < MIN_FREE+len(chunk):
                                        raise RuntimeError('Disk space safety limit reached')
                                    output.write(chunk)
                                    saved += len(chunk)
                        finally:
                            if self.cancel_event.is_set():
                                stop_proc(self.proc)
                            elif self.proc.poll() is None and sys_exc_raised():
                                stop_proc(self.proc)
                            if self.proc.stdout and not self.proc.stdout.closed:
                                self.proc.stdout.close()
                            code = self.proc.wait()
                            errors.seek(0)
                            error = errors.read(1200).decode('utf-8','replace').strip()
                except Exception as exc:
                    error = str(exc)
                    stop_proc(self.proc)
                finally:
                    self.proc = None
                if self.cancel_event.is_set():
                    if output_path:
                        output_path.unlink(missing_ok=True)
                    break
                if code == 0 and expected == saved:
                    outcome = 'recovered'
                elif saved > 0 and (code != 0 or (expected is not None and saved != expected)):
                    outcome = 'partial'
                elif code == 0 and expected is None and saved > 0:
                    outcome = 'unverified'  # No size metadata; do not claim verified.
                else:
                    outcome = 'failed'
                if output_path and outcome == 'failed':
                    output_path.unlink(missing_ok=True)
                elif output_path and outcome == 'partial':
                    try:
                        renamed = output_path.with_name(output_path.name+'.partial')
                        if renamed.exists() or renamed.is_symlink():
                            renamed = output_path.with_name(output_path.name+'_'+inode.replace('-','_')+'.partial')
                        output_path.rename(renamed)
                    except OSError:
                        output_path.unlink(missing_ok=True)
                        outcome='failed'
                self.status[outcome] += 1
                if outcome != 'failed':
                    self.status['written'] += saved
                else:
                    self.status['error'] = (error or 'Empty or unreadable file')[:170]
                self.status['done'] = idx
                if 'Disk space' in error:
                    raise RuntimeError(error)
            self.status['state'] = 'cancelled' if self.cancel_event.is_set() else 'done'
        except Exception as exc:
            self.status.update(state='cancelled' if self.cancel_event.is_set() else 'error',error=str(exc)[:200])
        finally:
            stop_proc(self.proc)


def sys_exc_raised():
    """Whether a pending Python exception is unwinding the output loop."""
    import sys
    return sys.exc_info()[0] is not None
