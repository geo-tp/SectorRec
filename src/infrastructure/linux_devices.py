"""Linux-only drive discovery and cautious (never forced) unmounting."""
import json
import re
import shutil
import subprocess
import time

SUPPORTED_FS = frozenset(('vfat', 'fat', 'exfat', 'ntfs', 'ext2', 'ext3', 'ext4'))
# Anything under an active OS mount is never offered for recovery.  A user
# mount such as /media/pi/USB is visible, but marked as mounted and blocked.
SYSTEM_MOUNTS = frozenset(('/', '/boot', '/boot/firmware', '/boot/efi',
                           '/usr', '/var', '/home', '/etc'))


def mount_list(node):
    """lsblk MOUNTPOINTS may be null, a string, or a list containing nulls."""
    mounts = node.get('mountpoints') or []
    if isinstance(mounts, str):
        mounts = [mounts]
    return [str(m) for m in mounts if m]


def walk_devices(blocks):
    for b in blocks:
        yield b
        yield from walk_devices(b.get('children') or [])


def lsblk_tree():
    # No `-f` here: it would suppress useful *unformatted* removable media.
    output = subprocess.check_output([
        '/usr/bin/lsblk','-J','-b','-o',
        'NAME,PATH,TYPE,FSTYPE,SIZE,LABEL,MODEL,VENDOR,REV,ROTA,LOG-SEC,PHY-SEC,PTTYPE,TRAN,RM,MOUNTPOINTS'
    ], text=True, timeout=8)
    return json.loads(output).get('blockdevices', [])


def detect_devices(tree_supplier=lsblk_tree):
    """Show physical disks and volumes, marking active OS sources explicitly.

    A device being shown in the selector does NOT make it safe to scan.
    The privileged helper independently revalidates the path, filesystem
    type and mount state each time fls/icat is launched.
    """
    roots = tree_supplier()
    result = []
    for disk in roots:
        if disk.get('type') != 'disk':
            continue
        family = list(walk_devices([disk]))
        is_system = any(set(mount_list(n)) & SYSTEM_MOUNTS for n in family)
        # Active swap remains an unsupported source even on the desktop.
        transport = (disk.get('tran') or '').lower()
        transport = transport.upper() if transport else ('SD' if disk.get('name', '').startswith('mmcblk') else 'DISK')
        disk_label = disk.get('label') or (disk.get('model') or '').strip() or disk.get('name') or 'Drive'
        media = ('HDD' if disk.get('rota') is True or disk.get('rota') == 1
                 else 'SSD / flash' if disk.get('rota') is False or disk.get('rota') == 0
                 else 'Unknown')
        diag = {'disk_path': disk.get('path'), 'model': disk.get('model') or '',
                'vendor': disk.get('vendor') or '', 'revision': disk.get('rev') or '',
                'media_type': media, 'logical_sector':disk.get('log-sec'),
                'physical_sector':disk.get('phy-sec'),
                'partition_table':disk.get('pttype') or ''}
        partitions = [n for n in family[1:] if n.get('type') == 'part']
        # Partitioned media: list partitions; 'superfloppy'/unpartitioned
        # media: list the whole disk, as USBImager does.
        volumes = partitions or [disk]
        if partitions:
            # Offer the physical parent for TestDisk partition discovery;
            # metadata scans still target individual filesystem volumes.
            result.append({
                'path': disk['path'], 'label': disk_label + '  [WHOLE DISK]',
                'name': disk.get('name') or '', 'size': int(disk.get('size') or 0),
                'fs': 'DISK', 'supported': False, 'partition_disk': True,
                'transport': transport, 'is_system': is_system,
                **diag,
                'mounted': any(mount_list(n) for n in family),
                'child_filesystems': tuple((n.get('fstype') or '').upper() for n in [disk, *partitions]),
                'mountpoint': ', '.join(m for n in family for m in mount_list(n)),
            })
        for n in volumes:
            path = n.get('path')
            if not path or not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', path):
                continue
            fs = (n.get('fstype') or '').lower()
            # Linux already enumerates every unmounted filesystem/UNKNOWN
            # partition. Active swap must remain excluded (no file volume).
            if fs in ('swap', 'linux-swap', 'linux-swap(v1)'):
                continue
            mounts = mount_list(n)
            result.append({
                'path': path,
                'label': n.get('label') or disk_label,
                'name': n.get('name') or path.rsplit('/', 1)[-1],
                'size': int(n.get('size') or 0),
                'fs': fs.upper() or 'UNKNOWN',
                'supported': fs in SUPPORTED_FS,
                'partition_disk': (n is disk and not fs),
                'child_filesystems': (),
                'transport': transport, 'is_system': is_system,
                **diag,
                'mounted': bool(mounts),
                'mountpoint': ', '.join(mounts),
            })
    return sorted(result, key=lambda d: bool(d.get('is_system')))


def unmount_volume(device: str, detect_devices=detect_devices, helper_command=None) -> None:
    """Safely unmount a selected external volume before a read-only scan.

    Try UDisks2 as the normal user first (no password prompt); then use the
    already-installed, tightly checked sudo helper if policy denies the
    unmount or UDisks is unavailable. Never force/lazy unmount or write to it.
    """
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', device):
        raise ValueError('Invalid device path')

    # Guard the requested target against device reordering / system disk use;
    # the privileged helper independently validates again before the unmount.
    devices = detect_devices()
    match = next((d for d in devices if d['path'] == device), None)
    if match is None or match.get('partition_disk'):
        raise RuntimeError('Drive is missing or is a whole disk')
    if match.get('is_system'):
        raise RuntimeError('The active system disk must never be unmounted.')
    if not match['mounted']:
        return  # Already unmounted, probably by the OS or another program.

    udisksctl = shutil.which('udisksctl')
    udisks_error = ''
    if udisksctl:
        try:
            cp = subprocess.run([udisksctl, 'unmount', '--block-device', device,
                                 '--no-user-interaction'], capture_output=True,
                                text=True, timeout=10, check=False)
            if cp.returncode == 0:
                return
            udisks_error = (cp.stderr or cp.stdout or '').strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            udisks_error = str(exc)

    # If UDisks cannot release the source, use the validated privileged helper.
    # An omitted command builder must never leak an internal NoneType error.
    if helper_command is None:
        raise RuntimeError('Privileged recovery helper is unavailable for unmount')
    try:
        import os
        timeout = 300 if (os.environ.get('SECTORREC_APPIMAGE') == '1' or
                          os.environ.get('SECTORREC_DEBIAN_ARCHIVE') == '1') else 12
        cp = subprocess.run(helper_command('unmount', device), capture_output=True,
                            text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Unmount timed out or unavailable: '+str(exc)[:100]) from None
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or udisks_error or 'Unknown error').strip()
        if 'busy' in detail.lower():
            raise RuntimeError('Drive busy. Close any program using the USB drive.')
        raise RuntimeError(detail[:160])




def wait_for_unmounted_source(path: str, devices=detect_devices, *,
                              stable_seconds: float = 1.1, poll_seconds: float = 0.25,
                              clock=None, sleep=None, expected=None) -> None:
    """Wait for a *stable* unmounted device after udisks/umount completes.

    Automounters may mount the device immediately after a successful umount.
    Always re-check a physical Linux source, including one that was already
    unmounted when the user selected it. Called on a worker thread, never Tk.
    The privileged reader and desktop exclusive guard still check at open time;
    this settle check does not replace those final checks.
    """
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', path):
        raise ValueError('Invalid device path')
    deadline = clock() + stable_seconds
    while True:
        match = next((d for d in devices() if d.get('path') == path), None)
        if match is None:
            raise RuntimeError('The source disappeared while waiting for unmount; select it again.')
        if expected and int(expected.get('size') or 0) and int(match.get('size') or 0) != int(expected['size']):
            raise RuntimeError('The source size changed while preparing the scan; select the disk again.')
        if match.get('mounted'):
            raise RuntimeError('The source was mounted again by the system. Close the file manager and retry.')
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleep(min(poll_seconds, remaining))


def unmount_disk_children(path: str, *, helper_command=None) -> None:
    """Unmount external child volumes, including after TestDisk auto-remounts.

    Pass the exact same authenticated helper command builder used for individual
    filesystem volumes.  The per-volume helper rechecks device safety and the
    controller waits for a stable unmounted state before starting recovery.
    """
    roots = lsblk_tree()
    parent = next((d for d in roots if d.get('path') == path and d.get('type') == 'disk'), None)
    if not parent or any(set(mount_list(n)) & SYSTEM_MOUNTS for n in walk_devices([parent])):
        raise RuntimeError('Whole disk is unavailable or belongs to the system')
    children = list(walk_devices(parent.get('children') or []))
    for volume in children:
        if mount_list(volume):
            unmount_volume(volume['path'], detect_devices=detect_devices, helper_command=helper_command)
    # An ISO image can be mounted directly on /dev/sdX, with no partitions.
    if mount_list(parent):
        unmount_volume(path, detect_devices=detect_devices, helper_command=helper_command)
