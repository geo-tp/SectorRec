#!/usr/bin/python3 -I
"""Privileged fixed-operation helper: external block source -> new regular .img only."""
import os
import fcntl
import json
import re
import signal
import subprocess
import sys
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, '/usr/share/sectorrec')
from src.helpers.reader import verify_device
from src.helpers.bundled_tools import binary, isolated_tool_env
from src.services.signature import ensure_different_disk
from src.services.image_rescue import source_identity


def fail(message):
    print('SectorRec imaging: '+message, file=sys.stderr)
    raise SystemExit(2)


def main():
    if os.geteuid()!=0:
        fail('Use the installed privileged helper with SOURCE IMAGE')
    resume = len(sys.argv)==4 and sys.argv[1]=='--resume'
    if not (len(sys.argv)==3 or resume):
        fail('Use SOURCE IMAGE or --resume SOURCE IMAGE')
    source,filename=sys.argv[-2:]
    source=verify_device(source, allow_unknown=True)
    image=Path(filename)
    if not image.is_absolute() or not re.fullmatch(r'SectorRec_[A-Za-z0-9_-]+(?:_\d+)?\.img',image.name):
        fail('Invalid output image name')
    if str(image) != str(image.resolve(strict=False)):
        fail('Output path must not contain symlinks')
    folder=image.parent
    try:
        if not folder.is_dir() or not folder.stat().st_mode & 0o200:
            fail('Output folder is not writable')
        if not os.path.isdir(folder):fail('Invalid directory')
        ensure_different_disk(source,folder)
        size=int(subprocess.check_output(['/usr/bin/lsblk','-bdn','-o','SIZE',source],text=True,timeout=5).strip())
        vfs=os.statvfs(folder)
        if not resume and vfs.f_bavail*vfs.f_frsize < size+128*1024**2:
            fail('Not enough free space for the full image')
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        fail('Destination validation failed: '+str(exc))
    mapfile=Path(str(image)+'.map')
    identity_file=Path(str(image)+'.source.json')
    identity=source_identity(source)
    if resume:
        if any(p.is_symlink() or not p.is_file() for p in (image,mapfile,identity_file)):
            fail('Resume requires regular .img, .map and .source.json files (no symlinks)')
        try:
            info=json.loads(identity_file.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            fail('Invalid source identity metadata')
        if not identity or info.get('schema')!=1 or info.get('identity')!=identity or info.get('size')!=size:
            fail('Original device identity or capacity does not match; refusing resume')
        if image.stat().st_size>size:
            fail('Image larger than source')
        vfs=os.statvfs(folder)
        available=vfs.f_bavail*vfs.f_frsize
        if available < max(0,size-image.stat().st_blocks*512)+128*1024**2:
            fail('Not enough free space to complete image')
    elif any(p.exists() or p.is_symlink() for p in (image,mapfile,identity_file)):
        fail('Output already exists; refusing to overwrite an existing image or mapfile')
    if not os.path.exists(binary('ddrescue')):
        fail('GNU ddrescue is not installed')
    # Source tracking prevents accidental reuse of a mapfile on a different disk.
    # -r3 retries bad areas on resume; previously rescued blocks are skipped.
    if not resume:
        fd=os.open(image,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        os.close(fd)
        meta_fd=os.open(identity_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(meta_fd,'w',encoding='utf-8') as handle:
            json.dump({'schema':1,'identity':identity,'size':size,'device':source},handle)
        # Make both files accessible even if the machine crashes mid-copy.
        # Without this, root-owned 0600 metadata would block UI resumption.
        uid=os.environ.get('SUDO_UID')
        gid=os.environ.get('SUDO_GID')
        if uid and gid:
            for item in (image, identity_file):
                os.chown(item,int(uid),int(gid),follow_symlinks=False)
    # Prevent two helpers from modifying the same image/map concurrently.
    # The lock is on the persistent identity file (no fourth sidecar file).
    identity_fd=os.open(identity_file,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        fcntl.flock(identity_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        fail('This disk image is already being copied. Stop the previous ddrescue job first.')
    # ddrescue creates the map later; make it readable after a hard crash,
    # while the privileged reader alone remains responsible for modifying it.
    os.umask(0o022)
    child=subprocess.Popen([binary('ddrescue'),'--mapfile-interval=5s,15s']+(['-r3'] if resume else [])+
                           [source,str(image),str(mapfile)],
                            start_new_session=True,stdout=sys.stdout,stderr=sys.stderr,
                            env=isolated_tool_env({'TERM': 'dumb'}))
    def stop(_signal,_frame):
        try:os.killpg(child.pid,signal.SIGINT)
        except ProcessLookupError:pass
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    result=child.wait()
    os.close(identity_fd)
    # Restore ownership to invoking user where possible (image and .map).
    uid=os.environ.get('SUDO_UID') or os.environ.get('PKEXEC_UID')
    gid=os.environ.get('SUDO_GID')
    if uid and not gid:
        import pwd
        gid=str(pwd.getpwuid(int(uid)).pw_gid)
    if uid and gid:
        for item in (image,mapfile,Path(str(mapfile)+'.bak'),identity_file):
            try:
                if item.is_file() and not item.is_symlink():os.chown(item,int(uid),int(gid))
            except OSError:pass
    return result

if __name__=='__main__':sys.exit(main())
