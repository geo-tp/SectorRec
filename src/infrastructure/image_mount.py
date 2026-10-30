"""Linux desktop: optional, user-approved, read-only mounts for file images.

No root helper, mount(2), or direct writes: UDisks2/Polkit controls privilege.
Only loop devices created by this session may be detached by this session.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PART = re.compile(r'/dev/loop\d+p\d+\Z')

class ImageMountError(RuntimeError):
    pass


def _run(args, timeout=35):
    try:
        result=subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImageMountError(str(exc)) from exc
    if result.returncode:
        raise ImageMountError((result.stderr or result.stdout or
                               'Command failed').strip()[:360])
    return result.stdout.strip()


def available():
    return sys.platform.startswith('linux') and bool(shutil.which('udisksctl')) and bool(shutil.which('lsblk'))


class ImageMountSession:
    def __init__(self, image, runner=_run):
        self.image=Path(image).expanduser().resolve(strict=True)
        if not self.image.is_file():raise ImageMountError('Select a regular disk image.')
        self.run=runner
        self.loop=None
        self.mounts=[]  # (block device, mount path)

    def mount(self):
        if self.loop:raise ImageMountError('Image is already mounted.')
        if not available():raise ImageMountError('This feature requires udisksctl and lsblk on Linux.')
        # UDisks2 creates a read-only loop. Mount flags ensure no filesystem writes
        # and prevent execution, devices, and privilege escalation on the image.
        output=self.run(['udisksctl','loop-setup','--read-only','--file',str(self.image)], timeout=90)
        match=re.search(r'/dev/loop\d+\b',output)
        if not match:raise ImageMountError('UDisks did not return the new loop device.')
        self.loop=match.group(0)
        try:
            # Give udev time to create partition nodes after loop setup.
            self.run(['udevadm','settle','--timeout=10'], timeout=15) if shutil.which('udevadm') else None
            data=json.loads(self.run(['lsblk','-J','-o','PATH,TYPE,FSTYPE,MOUNTPOINT',self.loop]))
            nodes=[]
            def walk(items):
                for item in items:
                    path=item.get('path','')
                    if (path==self.loop or PART.fullmatch(path)) and item.get('fstype') not in (None,'','swap'):
                        nodes.append(path)
                    walk(item.get('children') or [])
            walk(data.get('blockdevices') or [])
            if not nodes:raise ImageMountError('No mountable filesystem was detected in this image. Use Sleuth or TestDisk instead.')
            failures=[]
            for path in nodes:
                try:
                    result=self.run(['udisksctl','mount','--block-device',path,
                                     '--options','ro,nosuid,nodev,noexec'],timeout=90)
                    # udisksctl reports: Mounted /dev/loop0p1 at /media/user/...
                    match=re.search(r'\bat\s+(.+?)\.?\s*$',result)
                    if not match:raise ImageMountError('Could not determine the mounted folder.')
                    mountpoint=match.group(1).rstrip('.')
                    if not Path(mountpoint).is_dir():raise ImageMountError('Mount point does not exist: '+mountpoint)
                    self.mounts.append((path,mountpoint))
                except ImageMountError as exc:failures.append(f'{path}: {exc}')
            if not self.mounts:raise ImageMountError('Unable to mount an image filesystem. '+'; '.join(failures)[:360])
            return [mount for _,mount in self.mounts],failures
        except (ImageMountError,ValueError,KeyError) as exc:
            try:self.unmount()
            except ImageMountError:pass
            raise ImageMountError(str(exc)) from exc

    def unmount(self):
        errors=[]
        for device,_mount in reversed(self.mounts):
            try:self.run(['udisksctl','unmount','--block-device',device], timeout=90)
            except ImageMountError as exc:errors.append(f'{device}: {exc}')
            else:self.mounts.remove((device,_mount))
        if not self.mounts and self.loop:
            try:self.run(['udisksctl','loop-delete','--block-device',self.loop], timeout=90)
            except ImageMountError as exc:errors.append(str(exc))
            else:self.loop=None
        if errors:raise ImageMountError('; '.join(errors))
