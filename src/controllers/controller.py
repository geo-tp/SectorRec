"""View-agnostic controller: navigation, scan/recovery orchestration, state.

The 320x170 Pillow view and desktop view share this single controller.
"""
import time
import threading
import tempfile
import shutil
from pathlib import Path
from src.models.catalog import Catalog
from src.models.files import CATEGORIES, extension_of, human_size
from src.models.core import ScanWorker, RecoverWorker, free_bytes, MIN_FREE, MAX_RECORDS, MAX_RECORDS_DESKTOP
from src.infrastructure.platforms import current_platform
from src.infrastructure.image_backend import image_command
from src.services.source_assessment import assess_source
from src.services.device_diagnostics import inspect as inspect_drive

class Controller:
    def __init__(self, demo=False, platform=None, desktop=False):
        self._closing = threading.Event()
        self._close_lock = threading.Lock()
        self._close_reaper = None
        self.platform = platform or current_platform()
        self.desktop = bool(desktop)
        self.engine = 'metadata'  # PhotoRec is NOT a deeper Sleuth Kit scan.
        self.signature_destination = None
        self.metadata_destination = None
        self.signature = None
        self.signature_status = {}
        self.signature_device = None
        self.signature_space_approved = False
        self.signature_format = 'all'
        self.partition_search = None
        self.partition_status = {}
        self.partition_index = 0
        self.partition_selected = None
        self.partition_source = None
        self.images = []
        self.stage='welcome'
        self.message=''
        self.return_stage='devices'
        self.modal_action=None
        self.current=0
        self.devices=[]
        self.selection=(-1 if self.desktop else 0)
        self.category_options=['ALL']
        self.category_filter_index=0
        self.extension_options=['']
        self.extension_filter_index=0
        self.result_index=0
        self.exit_progress=0.0
        self.status={}
        self.scan=None
        self.unmount_thread=None
        self.unmount_status={}
        self.unmount_path=None
        self.recover=None
        self.frame=0
        self.scan_partial=False
        self.scan_stop_requested=False
        self.scan_guard=None  # desktop-only read-only kernel mount reservation
        self.scan_partial_reason=''
        self.unmount_next=None
        self.device_refresh=None
        self.device_refresh_ready=None
        self.next_device_refresh=0.0
        self.device_refresh_error=''
        self.device_refresh_completed=bool(demo)
        self.diagnostic_report=None
        self.diagnostic_key=None
        self.diagnostic_loading=False
        self.diagnostic_thread=None
        self.diagnostic_error=''
        self.diagnostic_scroll=0
        self.demo=demo
        self.cache_dir=Path.home()/'.cache'/'sectorrec'
        self.catalog=None if demo else Catalog(self.cache_dir/'scan.sqlite')
        self._external_catalog_dir = None
        self._external_catalog_destination = None
        if demo:
            self.devices=[
                {'label':'SanDisk Ultra','path':'/dev/sda1','name':'sda1','size':64*1024**3,'fs':'EXFAT','transport':'USB','mounted':False},
                {'label':'External HDD','path':'/dev/sdb1','name':'sdb1','size':500*1024**3,'fs':'NTFS','transport':'USB','mounted':False},
                {'label':'MicroSD','path':'/dev/mmcblk1p1','name':'mmcblk1p1','size':32*1024**3,'fs':'VFAT','transport':'SD','mounted':False},
            ]
            self.demo_results=[
                (1,'104-128-1','DCIM/holiday_2025.jpg',2434816,'PHOTOS'),
                (2,'105-128-1','Videos/clip_002.mp4',47185920,'VIDEOS'),
                (3,'106-128-1','Docs/project_notes.pdf',851968,'DOCS'),
                (4,'107-128-1','backup_photos.zip',158334976,'ARCHIVES'),
                (5,'108-128-1','Audio/recording.flac',18743296,'AUDIO'),
            ]

    def release_source(self):
        guard = self.scan_guard or self.status.get('_guard')
        self.scan_guard = None
        if guard:
            self.status.pop('_guard', None)
            guard.release()

    def close(self):
        """Request cancellation and release SQLite only after its clients exit.

        A worker may still be inside a blocking TSK process after the bounded
        join. A daemon reaper retains the controller/catalog/guard until it is
        safe to release them; never close an in-use SQLite connection.
        """
        with self._close_lock:
            if self._closing.is_set():
                return
            self._closing.set()
        workers = tuple(w for w in (
            self.scan, self.recover, self.signature, self.partition_search,
            self.unmount_thread, self.device_refresh, self.diagnostic_thread,
        ) if w is not None)
        for worker in (self.scan, self.recover, self.signature, self.partition_search):
            if worker is not None and worker.is_alive():
                worker.cancel()
        # A pending unmount/refresh cannot be safely interrupted while in a
        # system API; the reaper waits before releasing its source guard.
        for worker in workers:
            if worker.is_alive():
                worker.join(timeout=1.5 if worker in (self.scan, self.recover) else 0.25)
        if any(worker.is_alive() for worker in workers):
            self._close_reaper = threading.Thread(
                target=self._finish_close, args=(workers,),
                name='sectorrec-shutdown', daemon=True)
            self._close_reaper.start()
        else:
            self._finish_close(workers)

    def _finish_close(self, workers):
        for worker in workers:
            if worker.is_alive():
                worker.join()
        self.release_source()
        if self.catalog is not None:
            self.catalog.close()
            self.catalog = None
        if self._external_catalog_dir is not None:
            # Session indexes are temporary and contain private filenames.
            # Remove them after the final reader has released SQLite.
            shutil.rmtree(self._external_catalog_dir, ignore_errors=True)
            self._external_catalog_dir = None

    @property
    def scan_result_label(self):
        """Tell an incomplete scan from a completed scan with limited coverage."""
        if not self.scan_partial:
            return ''
        return 'LIMITED COVERAGE' if self.status.get('state') == 'done' else 'PARTIAL SCAN'

    def notice(self, msg, previous=None):
        self.message=msg
        self.return_stage=previous or self.stage
        self.stage='notice'

    def request_device_refresh(self, now=None):
        """Device discovery runs off the UI thread so it cannot freeze keys."""
        if self._closing.is_set():
            return
        if self.demo or (self.device_refresh and self.device_refresh.is_alive()):
            return
        interval = 3.0 if getattr(self.platform, 'name', '') == 'win32' else 1.6
        self.next_device_refresh=(now if now is not None else time.monotonic())+interval
        self.device_refresh_ready=None
        def task():
            try:
                found=self.platform.devices()
                if not self.desktop:
                    found=[d for d in found if not d.get('is_system')]
                self.device_refresh_ready=('ok', found)
            except Exception as exc:
                self.device_refresh_ready=('error', str(exc)[:140])
        self.device_refresh=threading.Thread(target=task,daemon=True)
        self.device_refresh.start()

    def set_signature_destination(self, path):
        import os
        if not self.device:
            raise ValueError('Select a source first')
        dest = Path(path).expanduser().resolve(strict=True)
        if not dest.is_dir() or not os.access(dest, os.W_OK | os.X_OK):
            raise ValueError('Choose a writable destination directory.')
        # Selection is not authorization to extract. The strict different-disk,
        # free-space and source checks remain in signature_preflight() and
        # are rechecked immediately before PhotoRec begins.
        self.signature_destination = str(dest)
        self.signature_space_approved = False
        # Reject Linux source-disk paths immediately on selection, not merely
        # at extraction start. Keep the selected path so the UI can show the
        # reason in red rather than silently leaving the previous path green.
        if not self.demo and self.platform.name == 'linux' and str(self.device['path']).startswith('/dev/'):
            from src.services.signature import validate_destination
            validate_destination(self.device['path'], dest)
        return dest

    def set_metadata_destination(self, path):
        """Record a chosen folder without launching Windows disk queries on each click.

        This is NOT authorization to recover: metadata_preflight() still
        verifies the physical disk before writing any recovered file.
        """
        import os
        if not self.device:
            raise ValueError('Select a source first.')
        folder = Path(path).expanduser().resolve(strict=True)
        if not folder.is_dir() or not os.access(folder, os.W_OK | os.X_OK):
            raise ValueError('Choose a writable destination directory.')
        from src.infrastructure.windows_raw import windows_device_path
        if self.device and not (self.platform.name == 'win32' and windows_device_path(self.device['path'])):
            from src.services.signature import validate_destination
            folder, _free = validate_destination(self.device['path'], folder)
        self.metadata_destination = str(folder)
        return folder

    def _prepare_system_catalog(self):
        """Keep *all scan indexing* off the live source physical disk.

        The destination has already passed the strict different-disk check.
        Reuse the same catalog for repeated scans of the selected source; the
        worker clears it at scan start. No arbitrary root-owned writes.
        """
        if not self.desktop or self.demo:
            return
        if not (self.device and self.device.get('is_system')):
            if self._external_catalog_dir is not None:
                if any(w is not None and w.is_alive() for w in (self.scan, self.recover)):
                    raise RuntimeError('Previous catalogue operation is still running.')
                restored = Catalog(self.cache_dir / 'scan.sqlite')
                old_catalog = self.catalog
                old_dir = self._external_catalog_dir
                self.catalog = restored
                self._external_catalog_dir = None
                self._external_catalog_destination = None
                if old_catalog is not None:
                    old_catalog.close()
                shutil.rmtree(old_dir, ignore_errors=True)
            return
        folder, _free = self.metadata_preflight()
        if self._external_catalog_destination == str(folder) and self._external_catalog_dir:
            return
        if any(w is not None and w.is_alive() for w in (self.scan,self.recover)):
            raise RuntimeError('Previous catalogue operation is still running.')
        directory = Path(tempfile.mkdtemp(prefix='.sectorrec-index-', dir=str(folder)))
        try:
            new_catalog = Catalog(directory / 'scan.sqlite')
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        old_catalog = self.catalog
        old_dir = self._external_catalog_dir
        self.catalog = new_catalog
        self._external_catalog_dir = directory
        self._external_catalog_destination = str(folder)
        if old_catalog is not None:
            old_catalog.close()
        if old_dir is not None:
            shutil.rmtree(old_dir, ignore_errors=True)

    def metadata_preflight(self):
        from src.services.signature import validate_destination
        if not self.metadata_destination:
            raise ValueError('Choose an extraction destination before the scan.')
        return validate_destination(self.device['path'], self.metadata_destination)

    def signature_preflight(self):
        from src.services.signature import validate_destination, photorec_binary, RESERVE_BYTES
        if not photorec_binary():
            raise RuntimeError('PhotoRec missing. Install testdisk, then restart.')
        if not self.device or not self.signature_destination:
            raise ValueError('Choose a destination before starting PhotoRec.')
        _dest, free = validate_destination(self.device['path'], self.signature_destination)
        # PhotoRec can write far more or less than source size; this is not a
        # prediction. It is a conservative disk-space WARNING, not a percentage.
        return {'free':free, 'source_bytes':self.device.get('size',0),
                'low':free - RESERVE_BYTES < self.device.get('size',0)}

    def begin_signature_scan(self):
        if self._closing.is_set():
            return
        from src.services.signature import PhotoRecWorker
        if not self.desktop or self.engine != 'signature':
            raise RuntimeError('PhotoRec is desktop-only')
        preflight = self.signature_preflight()
        if preflight['low'] and not self.signature_space_approved:
            raise ValueError('Free space may be insufficient. Confirm the warning before starting.')
        self.signature_space_approved = False
        if self.signature and self.signature.is_alive():
            raise RuntimeError('Previous PhotoRec extraction is still running')
        self.signature_device = dict(self.device)
        self.signature_status = {'state':'pending', 'started':time.monotonic()}
        self.stage = 'signature_running'
        if self.demo:
            self.signature_status.update(state='done',files=5,written=12344321,
                                         free=2048*1024**2,output=self.signature_destination)
            return
        self.signature = PhotoRecWorker(self.signature_device['path'],
                 self.signature_destination,self.signature_status, format_preset=self.signature_format,
                 source_info=self.signature_device)
        self.signature.start()

    def assess_selected_source(self):
        return assess_source(self.device, desktop=self.desktop)

    def request_diagnostics(self):
        if self._closing.is_set():
            return
        source = self.device
        if not source:
            return
        key = (source.get('disk_path') or source.get('path'), source.get('size'))
        if key == self.diagnostic_key:
            return
        self.diagnostic_key=key
        self.diagnostic_report=None
        self.diagnostic_error=''
        self.diagnostic_loading=True
        data=dict(source)
        def task():
            try:
                # No SMART on simulated or image sources. No subprocess in demo.
                report=inspect_drive(data, smart_reader=(lambda _s:(None,'Simulation: no SMART data.')) if self.demo else None)
                if self.diagnostic_key == key:
                    self.diagnostic_report=report
            except Exception as exc:
                if self.diagnostic_key == key:
                    self.diagnostic_error=str(exc)[:120]
            finally:
                if self.diagnostic_key == key:
                    self.diagnostic_loading=False
        self.diagnostic_thread=threading.Thread(target=task,daemon=True)
        self.diagnostic_thread.start()

    def desktop_select_method(self, engine):
        """PC-only workflow: select source, then method. Cardputer unchanged."""
        if not self.desktop or not self.device:
            raise ValueError('Select a source first')
        if engine not in ('metadata','signature','partition'):
            raise ValueError('Unknown recovery method')
        if not self.assess_selected_source().allows(engine):
            raise ValueError('This method does not apply to the selected source. Choose a different source.')
        from src.infrastructure.windows_raw import windows_device_path, require_admin
        if windows_device_path(self.device['path']):
            require_admin()
        # Sleuth Kit on a *partitioned image* needs an offset, but it does
        # not need TestDisk. Read existing MBR/GPT entries directly, then let
        # the user select a filesystem (or continue immediately for one).
        if engine == 'metadata' and self.device.get('is_image') and self.device.get('partition_disk'):
            from src.services.partition import image_table_partitions
            partitions=image_table_partitions(self.device['path'])
            if not partitions:
                raise ValueError('No valid MBR/GPT partitions were found in this image. '
                                 'Try TestDisk to locate missing partitions, or PhotoRec for raw recovery.')
            self.partition_source=dict(self.device)
            self.partition_selected=partitions[0] if len(partitions)==1 else None
            self.partition_index=0
            self.partition_status={'state':'done','source':'image_metadata',
                                   'candidates':partitions}
            self.engine='partition'  # Reuse safe offset-based fls/icat commands.
            self.signature_space_approved=False
            self.stage='scan_ready' if len(partitions)==1 else 'partition_results'
            return
        if engine=='partition':
            # TestDisk is a separate action from Sleuth on an existing image.
            self.partition_status={}
            self.partition_selected=None
        self.engine = engine
        self.signature_space_approved = False
        self.show_scan_ready()

    def show_devices(self):
        self.release_source()
        self.stage='devices'
        self.request_device_refresh()

    def apply_device_refresh(self):
        ready=self.device_refresh_ready
        if ready is None:
            return
        self.device_refresh_ready=None
        self.device_refresh_completed=True
        if ready[0]=='error':
            self.device_refresh_error=ready[1]
            return
        self.device_refresh_error=''
        path = self.device['path'] if self.device else None
        self.devices=list(reversed(self.images)) + sorted(
            ready[1] if self.desktop else [d for d in ready[1] if not d.get('is_system')],
            key=lambda d: bool(d.get('is_system')))
        matches=[i for i,item in enumerate(self.devices) if item['path']==path]
        if matches:
            self.selection=matches[0]
        elif self.desktop and path is None:
            # Never select a disk automatically on desktop. This matters for
            # safety as well as avoiding a misleading enabled Continue button.
            self.selection=-1
        else:
            self.selection=(-1 if self.desktop else 0) if not self.devices else (
                -1 if self.desktop and self.selection < 0 else min(self.selection,len(self.devices)-1))

    def begin_unmount(self, next_action=None):
        if self._closing.is_set():
            return
        if self.device and self.device.get('is_system'):
            raise RuntimeError('The active system disk must not be unmounted.')
        self.unmount_next=next_action
        self.unmount_path=self.device['path']
        self.unmount_status={'state':'working', 'error':''}
        self.stage='unmounting'

        def task():
            try:
                self.platform.unmount(self.unmount_path)
                if (not self.demo and self.platform.name.startswith('linux')
                        and self.unmount_path.startswith('/dev/')):
                    from src.infrastructure.linux_devices import wait_for_unmounted_source
                    wait_for_unmounted_source(self.unmount_path, self.platform.devices,
                                             expected=self.device)
                if (self.desktop and self.unmount_path.startswith('/dev/') and
                        (self.unmount_next in ('partition_files', 'signature_scan') or self.engine not in ('partition','signature'))):
                    from src.infrastructure.source_guard import SourceMountGuard
                    guard=SourceMountGuard.acquire(self.unmount_path)
                    self.scan_guard=guard
                self.unmount_status['state']='done'
            except Exception as exc:
                self.unmount_status.update(state='error', error=str(exc))
        self.unmount_thread=threading.Thread(target=task, daemon=True)
        self.unmount_thread.start()

    def add_image(self, path):
        """Select a *filesystem* image file as a read-only source, on any OS.

        Whole-disk images containing a partition table need a separate offset
        and are intentionally not silently interpreted as raw filesystems.
        """
        source=Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError('Choose a regular filesystem image file')
        volume={'path':str(source), 'label':source.name, 'name':source.name,
                'size':source.stat().st_size, 'fs':'IMAGE', 'transport':'IMAGE',
                'mounted':False, 'supported':True, 'is_image':True}
        # Classify a recognizable whole-disk image as such, not as a raw
        # filesystem. Never infer encryption from a failed raw-filesystem scan.
        with source.open('rb') as handle:
            head=handle.read(1024)
        mbr = len(head)>=512 and head[510:512]==b'\x55\xaa' and any(head[446+i*16+4] for i in range(4))
        gpt = head[512:520]==b'EFI PART'
        volume['partition_disk']=bool(mbr or gpt)
        volume['image_layout']='partitioned' if (mbr or gpt) else 'unknown or filesystem' 
        self.images=[d for d in self.images if d['path']!=str(source)]+[volume]
        self.devices=[volume]+[d for d in self.devices if d['path']!=str(source)]
        self.selection=0
        self.stage='devices'
        return volume

    @property
    def device(self):
        return self.devices[self.selection] if self.devices and 0 <= self.selection < len(self.devices) else None

    def active_filter(self):
        """Category + extension selectors are independent, both post-scan.

        TSK fls does not identify media signatures. An extension is only
        inferred from its filesystem filename, with no extension = 'NO EXT'.
        """
        category=self.category_options[self.category_filter_index]
        extension=self.extension_options[self.extension_filter_index]
        return (category, category, None if extension=='ALL' else extension)

    def extension_label(self):
        extension=self.extension_options[self.extension_filter_index]
        return ('ALL' if extension=='ALL' else extension.lstrip('.').upper() or 'NO EXT')

    def build_result_filters(self):
        """All deleted files were indexed; filters only query SQLite."""
        if self.demo:
            available={r[4] for r in self.demo_results}
        else:
            available=set(self.catalog.available_categories())
        self.category_options=['ALL']+[cat for cat in CATEGORIES[1:] if cat in available]
        self.category_filter_index=0
        self.rebuild_extensions()
        self.result_index=0

    def rebuild_extensions(self):
        cat=self.category_options[self.category_filter_index]
        if self.demo:
            counts={}
            for row in self.demo_results:
                if cat=='ALL' or row[4]==cat:
                    ext=extension_of(row[2]);counts[ext]=counts.get(ext,0)+1
            exts=sorted(counts,key=lambda ext:(-counts[ext],ext))
        else:
            exts=self.catalog.available_extensions(cat)
        # Empty string is reserved for ALL. Use None sentinel internally
        # for filenames without any extension, distinct from ALL.
        self.extension_options=['ALL'] + [ext for ext in exts]
        self.extension_filter_index=0

    def result_count(self):
        if self.demo:return len(self.visible_demo())
        _, cat, ext = self.active_filter()
        return self.catalog.summary(cat,ext)[0]

    def visible_demo(self):
        _, cat, ext = self.active_filter()
        return [r for r in self.demo_results if (cat=='ALL' or r[4]==cat)
                and (ext is None or extension_of(r[2])==ext)]

    def _sorted_demo(self, rows):
        order=getattr(self,'desktop_sort','id')
        if order=='id':return rows
        # Demo/test data are small. Live device files use SQLite ORDER BY.
        pos={'path':2,'type':2,'size':3}[order]
        return sorted(rows,key=lambda r:(r[pos] is None,
                      (extension_of(r[2]) if order=='type' else str(r[pos]).casefold() if pos==2 else r[pos] or 0),r[0]),
                      reverse=bool(getattr(self,'desktop_sort_desc',False)))

    def result_at(self,index):
        if self.demo:
            rows=self.visible_demo()
            return self._sorted_demo(rows)[index] if 0<=index<len(rows) else None
        _,cat,ext=self.active_filter()
        return self.catalog.at(index,cat,ext, getattr(self,'desktop_sort','id'), getattr(self,'desktop_sort_desc',False))

    def result_rows(self,start,count):
        if self.demo:return self._sorted_demo(self.visible_demo())[start:start+count]
        _,cat,ext=self.active_filter()
        return self.catalog.page(start,count,cat,ext, getattr(self,'desktop_sort','id'), getattr(self,'desktop_sort_desc',False))

    def found_total(self):
        if self.demo:return (len(self.visible_demo()), sum(x[3] or 0 for x in self.visible_demo()))
        _,cat,ext=self.active_filter()
        return self.catalog.summary(cat,ext)

    def found_details(self):
        if self.demo:
            rows = self.visible_demo()
            return len(rows), sum(x[3] for x in rows if x[3] is not None), sum(x[3] is None for x in rows)
        _,cat,ext=self.active_filter()
        return self.catalog.summary_details(cat,ext)

    def change_filter(self, delta=1):
        """TAB cycles broad file categories after a complete scan."""
        self.category_filter_index=(self.category_filter_index+delta)%len(self.category_options)
        self.rebuild_extensions()
        self.result_index=0

    def change_extension(self, delta=1):
        """E cycles only extensions seen in the current category."""
        self.extension_filter_index=(self.extension_filter_index+delta)%len(self.extension_options)
        self.result_index=0

    def show_scan_ready(self):
        """Confirmation before any unmount or scan; source stays untouched."""
        if not self.device:
            return
        if self.engine == 'partition' and not (self.device.get('partition_disk') or self.device.get('is_image')):
            self.notice('Choose a whole disk or whole-disk image for partition search.', 'devices')
            return
        if self.engine == 'metadata' and not self.device.get('supported', True):
            self.notice('Unsupported or unidentified filesystem ('+self.device['fs']+'). This scan requires FAT, exFAT, NTFS or ext2/3/4.', 'devices')
            return
        self.stage = ('signature_setup' if self.engine == 'signature' else
                      'partition_ready' if self.engine == 'partition' else 'scan_ready')

    def start_selected_scan(self):
        """Mount removal is deferred until explicit Start Scan confirmation."""
        if not self.device:
            return
        if self.device.get('is_system'):
            # Mounted OS volumes are intentionally NEVER unmounted or reserved.
            self.begin_scan()
            return
        # The mount state shown in the selector may already be stale. On
        # Linux always recheck after an unmount/settle window, even when the
        # source appeared unmounted at selection time (Cardputer included).
        if (not self.demo and self.platform.name.startswith('linux')
                and not self.device.get('is_image')
                and self.device['path'].startswith('/dev/')):
            self.begin_unmount()
        elif self.device.get('mounted'):
            self.begin_unmount()
        else:
            self.begin_scan()

    def start_signature_after_confirmation(self):
        """Called ONLY after the desktop user explicitly approves space risk."""
        self.signature_preflight()
        if self.device.get('is_system'):
            self.begin_signature_scan()  # Read-only; keep live system mounted.
        elif (self.desktop and not self.demo and not self.device.get('is_image') and
                self.device['path'].startswith('/dev/')):
            # Even an unmounted SD card can be re-mounted by the desktop
            # automounter before PhotoRec opens it. Guard before starting.
            if self.scan_guard and self.scan_guard.alive() and self.scan_guard.source == self.device['path']:
                self.begin_signature_scan()
            else:
                self.begin_unmount(next_action='signature_scan')
        elif self.device.get('mounted') and not self.device.get('is_image'):
            self.begin_unmount()
        else:
            self.begin_signature_scan()

    def start_partition_search(self):
        if self.device and self.device.get('is_system'):
            self.notice('Partition-table search on a live system disk is unavailable. '
                        'Select an individual filesystem volume for read-only recovery.', 'devices')
            return
        if not self.device or not (self.device.get('partition_disk') or self.device.get('is_image')):
            self.notice('Choose a physical disk or whole-disk image.', 'devices')
            return
        if (not self.demo and not self.device.get('is_image')
                and self.platform.name.startswith('linux')
                and self.device['path'].startswith('/dev/')):
            # TestDisk must open the physical disk itself: an exclusive guard
            # can interfere. Always settle and re-check mounts first, on both
            # Cardputer and desktop, even if the selector said "unmounted".
            self.release_source()
            self.begin_unmount(next_action='partition_search')
        elif self.device.get('mounted') and not self.device.get('is_image'):
            self.begin_unmount()
        else:
            self.begin_partition_search()

    def begin_partition_search(self):
        if self._closing.is_set():
            return
        if self.device and self.device.get('is_system'):
            raise RuntimeError('Searching partitions on the running system disk is disabled.')
        from src.services.partition import PartitionSearchWorker, PartitionCandidate
        if self.partition_search and self.partition_search.is_alive():
            self.notice('Previous partition search has not stopped.', 'devices')
            return
        self.partition_source = dict(self.device)
        self.partition_selected = None
        self.partition_index = 0
        self.partition_status = {'state':'pending','candidates':[], 'started':time.monotonic()}
        self.stage = 'partition_scanning'
        if self.demo:
            self.partition_status.update(state='done', candidates=[PartitionCandidate(2048, 524288, 'FAT32')])
            return
        self.partition_search = PartitionSearchWorker(
            self.partition_source['path'],self.partition_source.get('size',0),
            bool(self.partition_source.get('is_image')),self.partition_status)
        self.partition_search.start()

    def begin_partition_file_scan(self):
        candidates=self.partition_status.get('candidates',[])
        if not candidates:return
        candidate=candidates[self.partition_index]
        self.partition_selected=candidate
        self.engine='partition'
        if (not self.demo and not self.device.get('is_image')
                and self.platform.name.startswith('linux')
                and self.device['path'].startswith('/dev/')):
            if self.scan_guard and self.scan_guard.alive() and self.scan_guard.source == self.device['path']:
                self.begin_scan()
            else:
                # After TestDisk the system may remount a volume. Cardputer
                # needs the same read-only mount-state preflight as desktop.
                self.begin_unmount(next_action='partition_files')
        else:
            self.begin_scan()

    def partition_builder(self, action, source, inode=None):
        from src.services.partition import partition_command
        if not self.partition_selected:
            raise RuntimeError('Select a discovered partition')
        selected = self.partition_selected
        # A Windows partition with a drive letter is also available as a raw
        # volume (\\.\X:). It is more reliably readable by Windows TSK
        # builds than a filesystem offset into \\.\PhysicalDriveN.
        # A missing-letter or lost partition still uses the original disk
        # and its validated offset; no partition is mounted or modified.
        if (self.platform.name.startswith('win') and selected.origin == 'existing'
                and selected.volume_path and not self.partition_source.get('is_image')):
            # Check once per operation in the worker, not on every icat file.
            # Fail closed to the original physical disk offset if a removable
            # drive letter was reassigned between detection and extraction.
            if not getattr(self, '_partition_volume_checked', False):
                from src.services.partition import verify_windows_partition_volume
                self._partition_volume_checked = True
                self._partition_volume_path = (selected.volume_path
                    if verify_windows_partition_volume(source, selected) else '')
            if self._partition_volume_path == selected.volume_path:
                return partition_command(action, selected.volume_path, 0, inode, image=False)
        return partition_command(action, source, selected.start,
                inode, image=bool(self.partition_source.get('is_image')))

    def _metadata_command(self):
        if self.device.get('is_image'):
            return image_command
        from src.infrastructure.windows_raw import windows_device_path, raw_tsk_command, _PARTITION
        if _PARTITION.fullmatch(self.device['path']):
            # Retain an immutable selected partition snapshot. Never let a
            # synthetic partition path fall through into an unbounded raw scan.
            selected = dict(self.device)
            checked = None
            def bounded(action, source, inode=None):
                nonlocal checked
                if source != selected['path']:
                    raise ValueError('Selected partition changed during recovery.')
                if checked is None:
                    from src.infrastructure.windows_raw import photorec_partition_source
                    checked = photorec_partition_source(source, selected)
                return raw_tsk_command(action, source, inode, source_info=selected,
                                       verified_partition=checked)
            return bounded
        if windows_device_path(self.device['path']):
            return raw_tsk_command
        return None

    def begin_scan(self):
        if self._closing.is_set():
            return
        if not self.device:return
        if self.scan and self.scan.is_alive():
            self.notice('Previous scan is still stopping. Retry shortly.', 'devices')
            return
        # FLS always scans the same filesystem metadata; indexing ALL results
        # is necessary to allow category changes without rescanning.
        self.category_options=['ALL']
        self.category_filter_index=0
        self.extension_options=['ALL']
        self.extension_filter_index=0
        self.result_index=0
        self._partition_volume_checked = False
        self._partition_volume_path = ''
        self._prepare_system_catalog()
        self._prepare_system_catalog()
        self.status={'state':'pending','count':0,'limited':False,'started':time.monotonic()}
        self.scan_partial=False
        self.scan_partial_reason=''
        self.scan_stop_requested=False
        self.stage='scanning'
        if self.demo:
            self.status.update(state='done',count=len(self.demo_results))
            return
        self.scan=ScanWorker(self.catalog,self.device['path'],self.status,scope='ALL',
                             command_builder=self.partition_builder if self.engine=='partition' else
                                 self._metadata_command(),
                             include_allocated=self.engine=='partition',
                             protect_source=(self.desktop and not self.device.get('is_image') and
                                             not self.device.get('is_system') and
                                             self.platform.name.startswith('linux')),
                             source_guard=self.scan_guard,
                             max_records=(MAX_RECORDS_DESKTOP if self.desktop else MAX_RECORDS))
        self.scan.start()

    def begin_recover(self,selected=None):
        if self._closing.is_set():
            return
        if self.desktop and not self.demo:
            self.metadata_preflight()  # Recheck immediately before writing.
        self._partition_volume_checked = False
        self._partition_volume_path = ''
        self.status={'state':'pending','done':0,'total':0,'written':0,'failed':0,'partial':0,'recovered':0,'unverified':0}
        self.stage='recovering'
        if self.demo:
            self.status.update(state='done',total=1,done=1,written=2434816,recovered=1,outdir=str(Path.home()/'Backups'/'SectorRec'/'demo'))
            return
        _, category, extension = self.active_filter()
        self.recover=RecoverWorker(self.catalog,category,selected,
                self.device['path'],self.device['label'],Path.home(),self.status,extension=extension,
                destination_root=(self.metadata_destination if self.desktop else None),
                command_builder=self.partition_builder if self.engine=='partition' else
                    self._metadata_command(),
                product='SectorRec')
        self.recover.start()

    def handle(self,key):
        if key=='EXIT':
            self.stage='exit'
            return
        if self.stage=='welcome':
            if key=='OK':self.show_devices()
            elif key=='BACK':pass  # Only holding ESC for 3 s quits from welcome.
        elif self.stage=='devices':
            if key=='UP' and self.devices:self.selection=(self.selection-1)%len(self.devices) if self.selection>=0 else len(self.devices)-1
            elif key=='DOWN' and self.devices:self.selection=(self.selection+1)%len(self.devices) if self.selection>=0 else 0
            elif key in ('REFRESH',):self.request_device_refresh()
            elif key=='OK' and self.device:
                if self.desktop:
                    self.stage='desktop_methods'
                    self.request_diagnostics()
                else:
                    if not self.assess_selected_source().methods:
                        assessment=self.assess_selected_source()
                        if (not self.desktop and
                                ('ISO9660' in str(self.device.get('fs','')).upper() or
                                 'UDF' in str(self.device.get('fs','')).upper() or
                                 any(f in ('ISO9660','UDF') for f in self.device.get('child_filesystems',())))):
                            self.notice('Linux boot ISO detected.\nThis layout is not supported\nfor recovery on Cardputer.', 'devices')
                        else:
                            self.notice(assessment.description, 'devices')
                        return
                    if self.device.get('partition_disk'):
                        self.engine='partition'
                    else:
                        self.engine='metadata'
                    self.show_scan_ready()
            elif key=='RIGHT' and not self.desktop and self.device:
                self.diagnostic_scroll=0
                self.stage='device_info'
                self.request_diagnostics()
            elif key=='BACK':self.stage='devices' if self.desktop else 'welcome'
        elif self.stage=='device_info':
            if key in ('BACK','OK','LEFT'):self.stage='devices'
            elif key=='DOWN':self.diagnostic_scroll+=1
            elif key=='UP':self.diagnostic_scroll=max(0,self.diagnostic_scroll-1)
        elif self.stage=='desktop_methods':
            if key=='BACK':self.show_devices()
        elif self.stage=='desktop_image_setup':
            if key=='BACK':self.stage='desktop_methods'
        elif self.stage=='signature_setup':
            # Destination picker and space-warning confirmation are part of
            # desktop adapter; the controller still enforces preflight itself.
            if key=='OK':
                try:self.start_signature_after_confirmation()
                except (OSError,RuntimeError,ValueError) as exc:self.notice(str(exc),'signature_setup')
            elif key=='BACK':self.show_devices()
        elif self.stage=='signature_running':
            if key=='BACK' and self.signature:self.signature.cancel()
        elif self.stage=='signature_summary':
            if key=='OK' or key=='BACK':self.show_devices()
        elif self.stage=='partition_ready':
            if key=='OK':self.start_partition_search()
            elif key=='BACK': self.show_devices()
        elif self.stage=='partition_scanning':
            if key=='BACK' and self.partition_search:self.partition_search.cancel()
        elif self.stage=='partition_results':
            candidates=self.partition_status.get('candidates',[])
            if key in ('UP','LEFT') and candidates:self.partition_index=max(0,self.partition_index-1)
            elif key in ('DOWN','RIGHT') and candidates:self.partition_index=min(len(candidates)-1,self.partition_index+1)
            elif key=='OK' and candidates:self.begin_partition_file_scan()
            elif key=='BACK':self.show_devices()
        elif self.stage=='scan_ready':
            if key=='OK': self.start_selected_scan()
            elif key=='BACK': self.show_devices()
        elif self.stage=='unmounting':
            # Completion is handled in tick(); the running operation cannot
            # be interrupted safely by navigating away.
            pass
        elif self.stage=='scanning':
            if key=='BACK' and not self.scan_stop_requested:
                self.scan_stop_requested=True
                if self.scan:self.scan.cancel()
                # Stay here until the worker flushes its last batch to SQLite.
                # Its partial catalog can then be browsed and recovered.
                if self.demo:self.status['state']='cancelled'
        elif self.stage=='results':
            count=self.result_count()
            if key=='FILTER':self.change_filter()
            elif key=='EXTENSION':self.change_extension()
            elif key in ('UP','LEFT') and count:self.result_index=max(0,self.result_index-1)
            elif key in ('DOWN','RIGHT') and count:self.result_index=min(count-1,self.result_index+1)
            elif key=='OK' and count:self.confirm('ONE')
            elif key=='ALL' and count:self.confirm('ALL')
            elif key=='BACK':
                if not self.desktop:
                    # Cardputer has no persistent results browser once this
                    # screen is left. Confirm before losing the scan list.
                    self.stage='results_exit_confirm'
                elif self.engine=='partition':
                    self.stage='partition_results'
                else:
                    self.show_devices()
        elif self.stage=='results_exit_confirm':
            # A short ESC press keeps all filters, selection and scan results.
            # Only OK explicitly leaves the results screen.
            if key=='BACK':
                self.stage='results'
            elif key=='OK':
                if self.engine=='partition':
                    self.stage='partition_results'
                else:
                    self.show_devices()
        elif self.stage=='confirm':
            if key=='OK':
                action=self.modal_action
                self.modal_action=None
                if action=='ALL':self.begin_recover(None)
                elif action=='ONE':self.begin_recover(self.result_at(self.result_index))
            elif key=='BACK':self.stage='results'
        elif self.stage=='recovering':
            if key=='BACK':
                if self.recover:self.recover.cancel()
                self.stage='results'
        elif self.stage=='complete':
            if key=='OK':self.stage='results'
            elif key=='BACK':self.show_devices()
        elif self.stage=='notice':
            if key in ('BACK','OK'):self.stage=self.return_stage

    def confirm(self,action):
        count,total=self.found_total()
        if action=='ONE':
            selected=self.result_at(self.result_index)
            size=selected[3] if selected else None
            amount=1
        else:
            amount=count
            size=total
        root=Path.home()
        try: free=free_bytes(root)
        except OSError: free=0
        if size is not None and size>max(0,free-MIN_FREE):
            self.notice('Insufficient space. Free: '+human_size(free)+'. At least 64 MB is reserved.','results')
            return
        self.modal_action=action
        self.message=(f'{amount} file(s) • {human_size(size)} known\nFree: {human_size(free)} | reserve: 64 MB\n'
                      + 'Saved in ~/Backups/SectorRec/')
        self.stage='confirm'

    def tick(self):
        self.frame+=1
        if self.stage=='devices':
            self.apply_device_refresh()
            if not self.demo and time.monotonic() >= self.next_device_refresh:
                self.request_device_refresh()
        if self.stage=='signature_running':
            if self.signature_status.get('state') in ('done','cancelled','error'):
                self.release_source()
                self.stage='signature_summary'
        if self.stage=='unmounting':
            state=self.unmount_status.get('state')
            if state=='error':
                self.release_source()
                self.notice('Could not unmount: '+self.unmount_status.get('error',''), 'devices')
            elif state=='done':
                path=self.unmount_path
                if self.demo:
                    self.show_devices()
                else:
                    self.stage='devices'  # retain the desktop's guarded source FD
                if not self.demo:
                    # One synchronised refresh after unmount, since the next
                    # screen depends on its new mount state.
                    refreshed=self.platform.devices()
                    if not self.desktop:
                        refreshed=[d for d in refreshed if not d.get('is_system')]
                    self.devices=sorted(refreshed, key=lambda d: bool(d.get('is_system'))) + list(self.images)
                for idx,item in enumerate(self.devices):
                    if item['path']==path:
                        self.selection=idx
                        if item['mounted']:
                            self.release_source()
                            self.notice('Volume still mounted; close programs using it.', 'devices')
                        elif not item['supported'] and self.engine not in ('signature','partition'):
                            self.release_source()
                            self.notice('Unsupported filesystem after unmount.', 'devices')
                        elif self.unmount_next=='partition_files':
                            self.begin_scan()
                        elif self.unmount_next=='partition_search':
                            self.begin_partition_search()
                        elif self.unmount_next=='signature_scan':
                            try:self.begin_signature_scan()
                            except (OSError,ValueError,RuntimeError) as exc:
                                self.release_source()
                                self.notice(str(exc),'signature_setup')
                        elif self.engine=='partition':
                            self.begin_partition_search()
                        elif self.engine=='signature':
                            try:self.begin_signature_scan()
                            except (OSError,ValueError,RuntimeError) as exc:
                                self.notice(str(exc),'signature_setup')
                        else:
                            self.begin_scan()
                        break
                else:
                    self.release_source()
                    self.notice('The device disappeared after unmount.', 'devices')
                self.next_device_refresh=time.monotonic()+1.6
        if self.stage=='partition_scanning':
            state=self.partition_status.get('state')
            if state in ('done','error','cancelled'):
                if state=='error':
                    source=self.partition_source or {}
                    hint=(' Existing partitions are visible; try a volume directly for deleted-file recovery.'
                          if source.get('child_filesystems') else '')
                    if self.desktop:
                        self.notice('Partition scan not completed: '+self.partition_status.get('error','Layout not recognized')[:85]+'.'+hint+' This does not prove drive failure.', 'devices')
                    else:
                        source_fss=(str(source.get('fs','')).upper(), *(str(fs).upper() for fs in source.get('child_filesystems',())))
                        msg=('Linux boot ISO layout unsupported.\nNo disk damage implied.'
                             if any(fs in ('ISO9660','UDF') for fs in source_fss)
                             else 'Partition search could not finish.\nDisk may still be healthy.')
                        self.notice(msg,'devices')
                elif state=='cancelled':
                    if self.partition_status.get('candidates'):
                        self.partition_status['partial']=True
                        self.stage='partition_results'
                    else:self.show_devices()
                else:self.stage='partition_results'
        if self.stage=='scanning':
            if self.status.get('_guard') and self.scan_guard is None:
                self.scan_guard=self.status['_guard']
            state=self.status.get('state')
            if state in ('done','cancelled','error','limited'):
                self.scan_partial=(state!='done' or bool(self.status.get('limited_dirs')) or bool(self.status.get('limited_trash')) or bool(self.status.get('unreadable_dirs')) or bool(self.status.get('unreadable_trash')))
                reasons=[]
                # Sleuth Kit may describe a volume with an invalid offset or
                # unrecognized contents as 'possible encryption (high entropy)'.
                # High entropy is NOT proof of encryption: never misdiagnose it.
                raw_error = self.status.get('error', 'Unknown error')
                unrecognized_partition = (self.engine == 'partition' and state == 'error'
                    and any(term in raw_error.lower() for term in
                            ('high entropy', 'possible encryption', 'cannot determine file system',
                             'cannot determine filesystem', 'unsupported file system',
                             'unsupported filesystem')))
                if state=='cancelled': reasons.append('Stopped early; only results indexed so far are shown')
                elif unrecognized_partition:
                    reasons.append('Partition contents not readable at this offset. The partition may be damaged, '
                                   'overwritten or misidentified; encryption is not confirmed.')
                elif state=='error': reasons.append('Scan error: '+raw_error[:125])
                elif state=='limited':
                    if self.status.get('catalog_storage_low'):
                        free = self.status.get('catalog_storage_free', 0)
                        reasons.append('Scan paused to protect system storage: '
                                       f'only {human_size(free)} free for the SQLite catalogue. '
                                       'Free up disk space before scanning a larger volume.')
                    else:
                        reasons.append(f'Maximum catalog size reached ({MAX_RECORDS_DESKTOP if self.desktop else MAX_RECORDS:,} entries)')
                if self.status.get('limited_dirs'): reasons.append('Main metadata listing finished; some deleted folders were skipped (additional folder-search limit: 128)')
                if self.status.get('limited_trash'): reasons.append('Some recycle-bin locations were skipped (additional search limit: 16)')
                unreadable=self.status.get('unreadable_dirs',0)
                if unreadable: reasons.append(f'{unreadable} deleted folder(s) could not be read')
                unreadable=self.status.get('unreadable_trash',0)
                if unreadable: reasons.append(f'{unreadable} recycle-bin location(s) could not be read')
                self.scan_partial_reason='; '.join(reasons)
                self.build_result_filters()
                if state=='error' and self.result_count()==0:
                    if unrecognized_partition and not self.desktop:
                        self.notice('Partition files unreadable.\nOffset or filesystem may be wrong.\nEncryption not confirmed.', 'partition_results')
                    else:
                        self.notice(self.scan_partial_reason if unrecognized_partition
                                    else 'Scan error: '+raw_error[:100],
                                    'partition_results' if self.engine == 'partition' else 'devices')
                else:
                    self.stage='results'
        elif self.stage=='recovering':
            state=self.status.get('state')
            if state in ('done','error','cancelled'):
                self.message=(
                    f"Recovered {self.status.get('recovered',0)}  • Partial {self.status.get('partial',0)}\n"
                    f"Unverified {self.status.get('unverified',0)}  • Failed {self.status.get('failed',0)}"
                    + ('\n'+self.status.get('error','')[:85] if self.status.get('error') else '')
                )
                self.stage='complete'

