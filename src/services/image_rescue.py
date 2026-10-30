"""Desktop-only ddrescue disk-to-image workflow (no source writes).

The .map companion records progress and permits future resumption. This
module never shells out, and does not run on Cardputer Zero.
"""
from __future__ import annotations
import os
import json
import re
import shutil
import subprocess
from src.infrastructure.windows_process import no_console_window
import threading
import time
from datetime import datetime
from pathlib import Path
from src.services.signature import ensure_different_disk
from src.infrastructure.privileged import stop_proc, privileged_command, isolated_process_group
from src.infrastructure.appimage_environment import host_environment

RESERVE = 128 * 1024 * 1024


def _validate_source(source, source_info=None):
    if os.name == 'nt':
        from src.infrastructure.windows_raw import require_windows_device
        require_windows_device(source)
    elif not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', source):
        raise ValueError('Imaging requires a local Linux block device.')


def _allocated_size(image):
    """Allocated bytes, including sparse images; conservative if unknown."""
    stat = image.stat()
    if hasattr(stat, 'st_blocks'):
        return stat.st_blocks * 512
    if os.name == 'nt':
        import ctypes
        # NTFS sparse file allocation; never assume logical size == allocation.
        try:
            high = ctypes.c_uint32()
            fn = ctypes.WinDLL('kernel32', use_last_error=True).GetCompressedFileSizeW
            fn.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
            fn.restype = ctypes.c_uint32
            ctypes.set_last_error(0)
            low = fn(str(image), ctypes.byref(high))
            if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
                return 0
            return (high.value << 32) | low
        except (AttributeError, OSError, ValueError):
            return 0
    return stat.st_size



def progress_from_output(text):
    """Parse ddrescue's optional terminal status without assuming it is present."""
    percents=re.findall(r'pct rescued:\s*([\d.]+)%', text)
    rates=re.findall(r'current rate:\s*([^\r\n]+?)(?=\s{2,}|$)', text)
    return (float(percents[-1]) if percents else None,
            rates[-1].strip() if rates else None)


def rescued_percent_from_map(mapfile, source_bytes):
    """Read GNU ddrescue's map to measure successfully copied byte ranges.

    Return None for a missing or invalid map, rather than calling such an
    image complete. Map blocks use hexadecimal sizes and + for rescued data.
    """
    if source_bytes <= 0:
        return None
    copied=0
    seen=False
    try:
        with open(mapfile,'r',encoding='ascii',errors='replace') as handle:
            for line in handle:
                fields=line.split()
                if not fields or fields[0].startswith('#') or len(fields)!=3:
                    continue
                if fields[2] not in ('+','-','?','*','/'):
                    continue
                start=int(fields[0],0)
                length=int(fields[1],0)
                if start<0 or length<0 or start+length>source_bytes:
                    return None
                seen=True
                if fields[2]=='+':copied+=length
        return min(100.0,100.0*copied/source_bytes) if seen else None
    except (OSError,ValueError):
        return None


def plan_image(source, destination, size, *, preferred=None, source_info=None):
    """Plan a NEW raw image, never overwriting .img/.map/.source.json.

    A timestamp makes sessions recognizable. ``preferred`` is the file the UI
    already showed to the user, allowing a fresh preflight without generating
    a different filename when the wall clock advances before Start is pressed.
    Existing backups must use plan_resume(), not this function.
    """
    source = str(source)
    _validate_source(source, source_info)
    if os.name == 'nt':
        from src.infrastructure.windows_raw import require_admin
        from src.infrastructure.windows_ddrescue import imaging_disk
        require_admin()
        disk, part = imaging_disk(source, source_info)
        identity = source_identity(disk)
        if not identity:
            raise ValueError('Cannot obtain a stable disk serial or unique ID; imaging disabled for safety.')
        if part is not None:
            disk_size = int(json.loads(identity)['size'])
            if (size != part['size'] or part['offset'] + part['size'] > disk_size):
                raise ValueError('Selected partition geometry exceeds the verified disk capacity or has changed.')
    folder = Path(destination).expanduser().resolve(strict=True)
    if not folder.is_dir() or not os.access(folder, os.W_OK | os.X_OK):
        raise ValueError('Choose a writable destination folder.')
    ensure_different_disk(source, folder)
    if not isinstance(size, int) or size <= 0:
        raise ValueError('Source capacity is not known: cannot safely plan image.')
    available = shutil.disk_usage(folder).free
    if available < size + RESERVE:
        raise ValueError('Insufficient free space for the full raw image (plus 128 MiB reserve).')
    name = source.rsplit('\\', 1)[-1] if os.name == 'nt' else Path(source).name
    stem = 'SectorRec_' + re.sub(r'[^A-Za-z0-9_-]', '_', name)

    def unused(out):
        return not any(p.exists() or p.is_symlink() for p in
                       (out, Path(str(out) + '.map'), Path(str(out) + '.source.json')))

    if preferred is not None:
        out = Path(preferred).expanduser()
        # The UI must not authorize a new backup in another directory or
        # overwrite a file created after the destination was selected.
        expected = re.escape(stem) + r'_\d{6}-\d{6}(?:_\d+)?\.img'
        if out.parent.resolve(strict=True) != folder or not re.fullmatch(expected, out.name):
            raise ValueError('Selected image name or folder changed; choose a destination again.')
        if not unused(out):
            raise ValueError('A backup with this name already exists; choose a destination again.')
        return out

    timestamp = datetime.now().strftime('%y%m%d-%H%M%S')
    base = f'{stem}_{timestamp}'
    for i in range(1, 1000):
        out = folder / (base + ('' if i == 1 else f'_{i}') + '.img')
        if unused(out):
            return out
    raise ValueError('Too many existing image sessions in this folder.')


def source_identity(source):
    """Stable hardware identity required for automatic resume, never /dev/sdX alone."""
    source=str(source)
    if os.name == 'nt':
        from src.infrastructure.windows_ddrescue import source_identity as windows_identity
        return windows_identity(source)
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+',source):
        return None
    try:
        result=subprocess.run(['/usr/bin/lsblk','-dn','-o','SERIAL,WWN',source],
                              capture_output=True,text=True,timeout=5,check=True)
        parts=result.stdout.strip().split()
        if parts:
            return '|'.join(parts)
    except (OSError,subprocess.SubprocessError):
        pass
    try:
        result=subprocess.run(['/usr/bin/udevadm','info','--query=property','--name='+source],
                              capture_output=True,text=True,timeout=5,check=True)
        values={k:v for line in result.stdout.splitlines() if '=' in line
                for k,v in [line.split('=',1)]}
        return values.get('ID_WWN') or values.get('ID_SERIAL') or None
    except (OSError,subprocess.SubprocessError):
        return None


def plan_resume(source, image, size, *, source_info=None):
    """Validate a pre-existing, separately tracked ddrescue image for retries.

    Old raw images without recorded device identity must not be auto-resumed:
    using a mapfile against another disk can silently corrupt the backup.
    """
    source=str(source)
    _validate_source(source, source_info)
    partition = None
    identity_source = source
    if os.name == 'nt':
        from src.infrastructure.windows_raw import require_admin
        from src.infrastructure.windows_ddrescue import imaging_disk
        require_admin()
        identity_source, partition = imaging_disk(source, source_info)
    image=Path(image).expanduser()
    if image.is_symlink() or not image.is_file() or image.suffix!='.img':
        raise ValueError('Select a previously created regular .img file.')
    image=image.resolve(strict=True)
    mapfile=Path(str(image)+'.map')
    identity_file=Path(str(image)+'.source.json')
    if any(p.is_symlink() or not p.is_file() for p in (mapfile,identity_file)):
        raise ValueError('Resume requires the matching .map and .source.json files. Older images cannot be resumed automatically.')
    try:
        info=json.loads(identity_file.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise ValueError('Cannot read the image source identity.') from exc
    identity=source_identity(identity_source)
    if os.name == 'nt':
        # Windows storage IDs can differ in casing/representation across
        # Get-Disk snapshots. Apply the SAME conservative comparison used by
        # the initial imaging preflight, including legacy .source.json files.
        from src.infrastructure.windows_ddrescue import identity_difference
        same_identity = identity is not None and not identity_difference(info.get('identity'), identity)
    else:
        same_identity = identity is not None and info.get('identity') == identity
    if info.get('schema') not in (1, 2):
        raise ValueError('Unrecognized .source.json format; cannot safely resume this image.')
    if partition is None:
        if info.get('schema') != 1 or info.get('partition') is not None:
            raise ValueError('This backup belongs to a partition, not a whole disk.')
    else:
        if info.get('schema') != 2 or info.get('partition') != partition:
            raise ValueError('Partition geometry or identity differs from the original image. Refusing resume.')
    if not same_identity:
        if os.name == 'nt':
            detail = identity_difference(info.get('identity'), identity)
            raise ValueError('The source disk does not match this image: ' + detail + '. Refusing resume.')
        raise ValueError('The source disk identity does not match this image. Refusing resume.')
    try:
        recorded_size = int(info['size'])
        selected_size = int(size)
        live_size = int(json.loads(identity)['size']) if os.name == 'nt' else selected_size
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Invalid source capacity in image metadata; refusing resume.') from exc
    if partition is not None:
        if (recorded_size <= 0 or recorded_size != partition['size'] or
                partition['offset'] + partition['size'] > live_size or
                selected_size != recorded_size):
            raise ValueError('Partition geometry or size changed since imaging. Refusing resume.')
        live_size = recorded_size  # the image contains only this partition
    elif recorded_size <= 0 or recorded_size != live_size:
        raise ValueError('Source identity or size mismatch: capacity changed since imaging: saved '
                         f'{recorded_size} bytes, current {live_size} bytes. Refusing resume.')
    if selected_size != live_size and os.name != 'nt':
        raise ValueError('Selected source capacity does not match this image: '
                         f'{selected_size} vs {recorded_size} bytes.')
    # On Windows an old device-list snapshot can be stale even when both the
    # saved image identity AND a fresh Get-Disk query agree on exact capacity.
    # Only in that proven case, trust the live size rather than the cached UI.
    size = live_size
    if image.stat().st_size > size:
        raise ValueError(f'Image size ({image.stat().st_size} bytes) exceeds '
                         f'the verified source capacity ({size} bytes). Refusing resume.')
    if os.name=='posix':
        import fcntl
        # A crashed GUI can leave its helper copying; never launch a second
        # ddrescue against the same .map while the original is still active.
        with open(identity_file,'rb') as handle:
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('This image is still being copied by another ddrescue process.') from exc
            finally:
                try:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
                except OSError:pass
    if os.name == 'nt':
        from src.infrastructure.windows_ddrescue import assert_image_unlocked
        assert_image_unlocked(identity_file)
    folder=image.parent
    ensure_different_disk(source,folder)
    allocated=_allocated_size(image)
    if shutil.disk_usage(folder).free < max(0,size-allocated)+RESERVE:
        raise ValueError('Insufficient free space to complete the image safely.')
    return image


class ImageWorker(threading.Thread):
    def __init__(self, source, output, source_bytes, status, resume=False, expected_identity=None, source_info=None):
        super().__init__(daemon=True)
        self.source, self.output, self.source_bytes, self.status = source, Path(output), source_bytes, status
        self.resume = resume
        self.expected_identity = expected_identity
        self.source_info = source_info
        self.proc = None
        self.stop_event = threading.Event()

    def cancel(self):
        self.stop_event.set()
        if self.proc:
            threading.Thread(target=stop_proc, args=(self.proc,), daemon=True).start()

    def run(self):
        if os.name == 'nt':
            return self._run_windows()
        if self.stop_event.is_set():
            self.status['state']='cancelled'
            return
        self.status.update(state='running', started=time.monotonic(), output=str(self.output),
                           mapfile=str(self.output)+'.map', error='', size=self.source_bytes)
        try:
            cmd=privileged_command('image', *(['--resume'] if self.resume else []),
                                   self.source, str(self.output))
            # Cancellation may have arrived during setup; never start an
            # external privileged process after a requested stop.
            if self.stop_event.is_set():
                self.status['state']='cancelled'
                return
            # Use file-backed output so an unread pipe cannot stall ddrescue.
            import tempfile
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                self.proc=subprocess.Popen(cmd, stdout=output, stderr=errors,
                                           env=host_environment(), **isolated_process_group())
                while self.proc.poll() is None:
                    if self.stop_event.wait(0.35):
                        stop_proc(self.proc)
                        break
                    try:
                        length=os.fstat(output.fileno()).st_size
                        tail=os.pread(output.fileno(),min(8192,length),max(0,length-8192)).decode('utf-8','replace')
                        percent, rate=progress_from_output(tail)
                        if percent is not None:self.status['percent']=max(float(self.status.get('percent') or 0), min(100.0,percent))
                        if rate:self.status['rate']=rate
                    except (OSError,ValueError):pass
                # The final status may have arrived after the last polling tick.
                try:
                    length=os.fstat(output.fileno()).st_size
                    tail=os.pread(output.fileno(),min(16384,length),max(0,length-16384)).decode('utf-8','replace')
                    percent,rate=progress_from_output(tail)
                    if percent is not None:self.status['percent']=percent
                    if rate:self.status['rate']=rate
                except (OSError,ValueError):pass
                errors.seek(0)
                detail=errors.read(1000).decode('utf-8','replace').strip()
                self.proc.wait()
                map_percent=rescued_percent_from_map(str(self.output)+'.map',self.source_bytes)
                if map_percent is not None:self.status['percent']=map_percent
                if self.stop_event.is_set():self.status['state']='cancelled'
                elif self.proc.returncode == 0:
                    pct=self.status.get('percent')
                    self.status['state']='incomplete' if pct is not None and pct<99.999 else 'done'
                else:self.status.update(state='error',error=detail or f'ddrescue exited with code {self.proc.returncode}')
        except (OSError,ValueError) as exc:
            self.status.update(state='error',error=str(exc))
        finally:
            self.proc = None

    def _run_windows(self):
        """Run the staged Cygwin ddrescue with Windows disk/path validation."""
        from src.infrastructure.windows_ddrescue import (command, image_lock, imaging_disk,
            source_identity as win_identity, identity_difference)
        from src.infrastructure.windows_raw import ensure_different_windows_disk
        if self.stop_event.is_set():
            self.status['state'] = 'cancelled'
            return
        self.status.update(state='running', started=time.monotonic(),
                           output=str(self.output),mapfile=str(self.output)+'.map',
                           error='', size=self.source_bytes)
        image = self.output
        meta = Path(str(image)+'.source.json')
        mapfile = Path(str(image)+'.map')
        created = False
        started = False
        step = 'destination and source verification'
        try:
            ensure_different_windows_disk(self.source, image.parent)
            disk, partition = imaging_disk(self.source, self.source_info)
            identity = win_identity(disk)
            if not identity:
                raise ValueError('Cannot verify a stable source disk identity. Reconnect the disk and select it again; imaging refused.')
            current_size = int(json.loads(identity)['size'])
            if partition is not None and (self.source_bytes != partition['size'] or
                    partition['offset'] + partition['size'] > current_size):
                raise ValueError('Partition exceeds the original physical disk or its geometry has changed.')
            if self.resume:
                # The existing .source.json is the authoritative original
                # snapshot. plan_resume checks it against a *fresh* native
                # Get-Disk identity before we use any cached UI values.
                if partition is not None:
                    plan_resume(self.source, image, self.source_bytes, source_info=self.source_info)
                else:
                    plan_resume(self.source, image, self.source_bytes)
                if self.expected_identity:
                    # A stale capacity from the selection list alone must not
                    # block a proven match of stored + current disk identity.
                    old = identity_difference(self.expected_identity, identity)
                    if old and ('serial number:' in old or 'unique ID:' in old or
                                'does not contain' in old):
                        raise ValueError('The selected source identity changed since selection: '
                                         + old + '. Refusing resume.')
                self.source_bytes = partition['size'] if partition is not None else current_size
                self.status['size'] = self.source_bytes
            else:
                if self.expected_identity:
                    difference = identity_difference(self.expected_identity, identity)
                    if difference:
                        raise ValueError('The physical disk has changed since selection, or Windows returned inconsistent identifiers. '
                                         f'Difference: {difference}. Refresh the device list and reselect the original disk; '
                                         'imaging refused.')
                if (partition is None and current_size != self.source_bytes):
                    raise ValueError('Windows disk capacity differs from the selected source '
                                     f'({self.source_bytes} vs {current_size} bytes). '
                                     'Refresh the device list and reselect the disk; imaging refused.')
            if not self.resume:
                # Exclusive creation: never overwrite an existing backup/map.
                if any(p.exists() or p.is_symlink() for p in (image,mapfile,meta)):
                    raise ValueError('Output already exists; refusing to overwrite an image or mapfile.')
                with image.open('xb'):
                    pass
                try:
                    with meta.open('x',encoding='utf-8') as stream:
                        json.dump({'schema':2 if partition is not None else 1,
                                   'identity':identity,'size':self.source_bytes,
                                   'device':self.source,
                                   **({'partition':partition} if partition is not None else {})},stream)
                    created = True
                except BaseException:
                    image.unlink(missing_ok=True)
                    raise
            with image_lock(meta):
                step = 'Cygwin disk mapping or output-path conversion'
                if partition is not None:
                    # Verify the selected letter's current geometry once again,
                    # immediately before constructing the physical-disk read.
                    now_disk, now_part = imaging_disk(self.source, self.source_info)
                    if now_disk != disk or now_part != partition:
                        raise ValueError('Windows partition changed during imaging setup.')
                cmd,env = (command(self.source,image,resume=self.resume,source_info=self.source_info)
                           if partition is not None else command(self.source,image,resume=self.resume))
                if self.stop_event.is_set():
                    self.status['state']='cancelled'
                    return
                import tempfile
                with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                    step = 'starting GNU ddrescue'
                    self.proc=subprocess.Popen(cmd,stdout=output,stderr=errors,env=env,
                                               creationflags=(getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0) |
                                                              no_console_window().get('creationflags',0)))
                    started = True
                    step = 'reading the selected partition with GNU ddrescue' if partition is not None else 'reading the physical disk with GNU ddrescue'
                    while self.proc.poll() is None:
                        if self.stop_event.wait(0.35):
                            stop_proc(self.proc)
                            break
                        try:
                            output.seek(0,os.SEEK_END)
                            output.seek(max(0,output.tell()-8192))
                            percent,rate=progress_from_output(output.read().decode('utf-8','replace'))
                            if percent is not None:
                                self.status['percent']=percent
                            if rate:self.status['rate']=rate
                        except (OSError,ValueError):pass
                    self.proc.wait()
                    output.seek(0,os.SEEK_END)
                    output.seek(max(0,output.tell()-16384))
                    percent,rate=progress_from_output(output.read().decode('utf-8','replace'))
                    if percent is not None:self.status['percent']=percent
                    if rate:self.status['rate']=rate
                    errors.seek(0)
                    detail=errors.read(3000).decode('utf-8','replace').strip()
                    # Cygwin ddrescue can print fatal I/O errors to stdout,
                    # not stderr. Keep its last lines when stderr is empty.
                    if not detail:
                        output.seek(0, os.SEEK_END)
                        output.seek(max(0, output.tell() - 3000))
                        detail = output.read().decode('utf-8', 'replace').strip()
                    # Retain actionable diagnostics, but strip terminal escapes.
                    detail = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', detail).strip()[-1500:]
                    map_percent=rescued_percent_from_map(mapfile,self.source_bytes)
                    if map_percent is not None:self.status['percent']=map_percent
                    if self.stop_event.is_set():
                        self.status['state']='cancelled'
                    elif self.proc.returncode==0:
                        pct=self.status.get('percent')
                        self.status['state']='incomplete' if pct is not None and pct<99.999 else 'done'
                    else:
                        self.status.update(state='error', error=(
                            f'GNU ddrescue exited with code {self.proc.returncode}. '
                            + (detail or 'No error details were returned by the process.')
                        )[:1800])
        except (OSError,ValueError,RuntimeError) as exc:
            self.status.update(state='cancelled' if self.stop_event.is_set() else 'error',
                               error=f'Windows image copy — {step}: {exc}'[:1800])
        finally:
            # If validation/launch failed before ddrescue ever ran, allow a
            # clean retry instead of stranding an empty .img/.source.json.
            if created and not started and not mapfile.exists():
                try: meta.unlink(missing_ok=True)
                except OSError: pass
                try: image.unlink(missing_ok=True)
                except OSError: pass
            self.proc=None
