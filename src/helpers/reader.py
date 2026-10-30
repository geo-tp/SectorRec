#!/usr/bin/python3 -I
"""Privileged, read-only TSK launcher. Never writes to a source or destination disk.

This helper MUST be owned by root and not writable by normal users. It only
executes fls/icat on verified block devices, including mounted OS volumes
only for read-only file recovery. Never unmounts or reserves active OS volumes.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import signal
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, '/usr/share/sectorrec')
from src.helpers.bundled_tools import binary as tool_binary, isolated_tool_env


def fail(message):
    print('SectorRec: ' + message, file=sys.stderr)
    sys.exit(2)


def descendants(node):
    yield node
    for child in node.get('children', []):
        yield from descendants(child)


def verify_device(device, allow_mounted=False, allow_unknown=False, allow_system_read=False):
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+',device):
        fail('Invalid device path')
    actual=os.path.realpath(device)
    if actual != device or not actual.startswith('/dev/'):
        fail('Device aliases and symlinks not permitted')
    try:
        if not stat.S_ISBLK(os.stat(actual).st_mode):
            fail('Not a block device')
    except OSError:
        fail('Block device unavailable')
    try:
        trees=json.loads(subprocess.check_output([
            '/usr/bin/lsblk','-J','-b','-o',
            'NAME,PATH,TYPE,FSTYPE,SIZE,LABEL,TRAN,RM,MOUNTPOINTS'
        ], timeout=8, text=True, env={'PATH':'/usr/bin:/bin','LC_ALL':'C'}))['blockdevices']
    except (OSError,ValueError,subprocess.SubprocessError):
        fail('Cannot verify mount state')
    # lsblk MOUNTPOINTS does not flag active swap. Reading a disk while the
    # kernel is writing swap pages can produce inconsistent recovery results.
    try:
        with open('/proc/swaps', encoding='utf-8') as swaps:
            active_swap={line.split()[0] for line in swaps.read().splitlines()[1:] if line.split()}
    except OSError:
        fail('Cannot verify active swap usage')
    found=None
    for disk in trees:
        family=list(descendants(disk))
        if any(n.get('path') in active_swap for n in family):
            continue  # active swap must never be scanned
        system_disk = any(set(m for m in (item.get('mountpoints') or []) if m) &
               {'/', '/boot', '/boot/firmware', '/boot/efi', '/usr', '/var', '/home', '/etc'}
               for item in family)
        if system_disk and not allow_system_read:
            continue
        for item in family:
            if item.get('path')==device and item.get('type') in ('part','disk'):
                found=item
                break
        if found:
            break
    if not found:
        fail('Device is unknown or belongs to the system disk')
    mounted = [p for p in (found.get('mountpoints') or []) if p]
    # A whole disk cannot be scanned while any of its partitions are mounted.
    if found.get('type') == 'disk':
        mounted += [p for item in family for p in (item.get('mountpoints') or []) if p]
    if mounted and not allow_mounted and not (allow_system_read and system_disk):
        fail('Source is mounted. Unmount it before scanning/recovering')
    fs=(found.get('fstype') or '').lower()
    if not allow_unknown and fs not in ('vfat','fat','ntfs','exfat','ext2','ext3','ext4'):
        fail('Unsupported or unidentified filesystem')
    return actual


def main():
    # Reject malformed device arguments *before* requesting elevation.  The
    # portable root archive is invoked via sudo, but it can also be executed
    # without privileges (e.g. during Debian package tests).  Invalid paths
    # must never be masked by a generic sudo error.  verify_device() still
    # performs the full privileged stat/lsblk/mount checks below.
    if len(sys.argv) not in (3,4):
        fail('Usage: reader scan DEVICE | recover DEVICE INODE | unmount DEVICE')
    action,device=sys.argv[1:3]
    if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', device):
        fail('Invalid device path')
    if os.geteuid()!=0:
        # Debian users with pre-existing read access to a device do not need
        # root for metadata/recovery reads. Explicitly keep unmount and SMART
        # privileged. Full verify_device() checks are still mandatory below.
        if (action in ('unmount', 'smart', 'smart_full') or
                not os.access(device, os.R_OK)):
            fail('Needs sudo elevation for raw disk access')
    if action in ('smart','smart_full') and len(sys.argv) == 3:
        # No self-tests or configuration changes. Only trusted system binary.
        verified=verify_device(device, allow_mounted=True, allow_unknown=True)
        physical=subprocess.check_output(['/usr/bin/lsblk','-dn','-o','TYPE',verified],
                                         timeout=4,text=True).strip()
        if physical!='disk':fail('SMART diagnostics require a physical disk')
        if not os.path.isfile(tool_binary('smartctl')):
            fail('smartmontools is not installed')
        try:
            args=([tool_binary('smartctl'),'-j','-x','-n','standby',verified]
                  if action=='smart_full' else
                  [tool_binary('smartctl'),'-j','-i','-H','-A','-n','standby',verified])
            reading=subprocess.run(args,capture_output=True,
                                    timeout=11 if action=='smart_full' else 7,check=False,
                                    env=isolated_tool_env())
        except subprocess.TimeoutExpired:
            fail('SMART information request timed out')
        sys.stdout.buffer.write(reading.stdout[:2097152 if action=='smart_full' else 524288])
        # smartctl uses nonzero bits for warnings, not only command failures.
        return
    if action == 'hold_source' and len(sys.argv) == 3:
        # Linux's O_RDONLY|O_EXCL open on a block device fails if already
        # mounted and prevents the desktop automounter from re-mounting it.
        # The FD is held for the lifetime of this one parent-owned session.
        path = verify_device(device, allow_unknown=True)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0))
        except OSError as exc:
            fail('Cannot reserve unmounted source (another process may have remounted it): '+str(exc))
        try:
            print('READY', flush=True)
            # The UI owns stdin; exiting/crashing closes the pipe and
            # automatically releases the kernel's exclusive open.
            while sys.stdin.buffer.read(512):
                pass
        finally:
            os.close(fd)
        return
    if action == 'partition_search' and len(sys.argv) == 3:
        path = verify_device(device, allow_unknown=True)
        kind = subprocess.check_output(['/usr/bin/lsblk','-dn','-o','TYPE', path],
                 text=True, timeout=4).strip()
        if kind != 'disk':
            fail('Partition search requires a whole non-system disk')
        logical = int(subprocess.check_output(['/usr/bin/lsblk','-dn','-o','LOG-SEC',path], text=True,timeout=4).strip())
        if logical != 512:fail('This preview supports only 512-byte sectors')
        if not os.path.isfile(tool_binary('testdisk')):
            fail('TestDisk is not installed')
        with tempfile.TemporaryDirectory(prefix='sectorrec-partitions-') as work:
            report = str(Path(work) / 'testdisk.log')
            command = [tool_binary('testdisk'),'/log','/logname',report,
                       '/cmd', path, 'analyze,search']
            os.environ.clear()
            os.environ.update({'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':work})
            child = subprocess.Popen(command, cwd=work, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True)
            def cancel(_sig, _frame):
                try: os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                raise SystemExit(143)
            signal.signal(signal.SIGTERM, cancel)
            signal.signal(signal.SIGINT, cancel)
            # Read-only, numeric progress sent to the parent UI via stderr.
            # /proc/<pid>/io:rchar is cumulative process read I/O, not bytes
            # uniquely scanned and never a completion percentage.
            while True:
                try:
                    code = child.wait(timeout=2.0)
                    break
                except subprocess.TimeoutExpired:
                    try:
                        with open(f'/proc/{child.pid}/io', encoding='ascii') as io:
                            counter = next((line.split(':',1)[1].strip() for line in io
                                            if line.startswith('rchar:')), None)
                        if counter is not None and counter.isdecimal():
                            print('SECTORREC_IO_READ_BYTES=' + counter,
                                  file=sys.stderr, flush=True)
                    except (OSError, ValueError):
                        pass
            if os.path.isfile(report):
                with open(report,'rb') as handle:
                    header=handle.read(4096)
                    handle.seek(0,os.SEEK_END)
                    length=handle.tell()
                    handle.seek(max(4096,length-(2*1024*1024-4096)))
                    sys.stdout.buffer.write(header+b'\n'+handle.read(2*1024*1024-4096))
            sys.exit(code)
    if action in ('partition_scan','partition_recover','partition_recover_allocated','partition_deleted_children') and len(sys.argv) in (4,):
        path = verify_device(device, allow_unknown=True)
        kind = subprocess.check_output(['/usr/bin/lsblk','-dn','-o','TYPE', path],
                 text=True, timeout=4).strip()
        if kind != 'disk':
            fail('Expected whole non-system disk')
        token = sys.argv[3]
        if action == 'partition_scan':
            if not re.fullmatch(r'\d{1,16}',token):fail('Invalid partition offset')
            sector = int(token)
        else:
            match = re.fullmatch(r'(\d{1,16}):(\d+(?:-\d+){0,2})',token)
            if not match:fail('Invalid partition offset / inode')
            sector = int(match[1])
        size = int(subprocess.check_output(['/usr/bin/lsblk','-bdn','-o','SIZE',path],
                   text=True,timeout=4).strip())
        logical = int(subprocess.check_output(['/usr/bin/lsblk','-dn','-o','LOG-SEC',path], text=True,timeout=4).strip())
        if logical != 512:fail('This preview supports only 512-byte sectors')
        if sector * 512 >= size:
            fail('Partition starts beyond disk capacity')
        if action in ('partition_scan','partition_deleted_children'):
            binary=tool_binary('fls')
            args=([binary,'-o',str(sector),'-r','-p','-l',path] if action=='partition_scan'
                  else [binary,'-o',str(sector),'-d','-l','-p',path,match[2]])
        else:
            binary=tool_binary('icat')
            args=[binary,'-o',str(sector)]
            if action == 'partition_recover':args.append('-r')
            args += [path,match[2]]
        os.environ.clear()
        os.environ.update(isolated_tool_env())
        os.execv(binary,args)
    if action=='unmount' and len(sys.argv)==3:
        path=verify_device(device, allow_mounted=True, allow_unknown=True)
        # Recheck immediately before the operation. umount is non-forced:
        # a busy volume fails instead of killing processes or losing data.
        try:
            cp=subprocess.run(['/usr/bin/umount', '--', path], capture_output=True,
                              text=True, timeout=12, check=False,
                              env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        except (OSError, subprocess.TimeoutExpired):
            fail('Unmount failed or timed out')
        if cp.returncode!=0:
            fail((cp.stderr or cp.stdout or 'Unmount rejected').strip()[:160])
        return
    elif action in ('scan','trash_roots') and len(sys.argv)==3:
        path = verify_device(device, allow_system_read=True)
        binary = tool_binary('fls')
        options = {
            'scan': ['-r','-d','-p','-l'],
            'trash_roots': ['-u','-D','-l'],
        }
        args = [binary,*options[action],path]
    elif action in ('recover','recover_allocated','deleted_children','trash_scan') and len(sys.argv)==4:
        inode = sys.argv[3]
        if not re.fullmatch(r'\d+(?:-\d+){0,2}', inode):
            fail('Invalid metadata address')
        path = verify_device(device, allow_system_read=True)
        if action in ('recover','recover_allocated'):
            binary = tool_binary('icat')
            args = [binary] + (['-r'] if action=='recover' else []) + [path,inode]
        else:
            binary = tool_binary('fls')
            args = ([binary,'-d','-l','-p',path,inode] if action=='deleted_children'
                    else [binary,'-u','-r','-p','-l','-F',path,inode])
    else:
        fail('Unknown operation')
    if not os.path.isfile(binary):
        fail('Sleuth Kit is not installed')
    os.environ.clear()
    os.environ.update({'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    os.execv(binary,args)


if __name__=='__main__':
    main()
