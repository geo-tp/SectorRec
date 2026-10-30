"""Unprivileged TSK command builder for filesystem image files.

Image sources never execute mount/umount and never use the privileged reader.
"""
import re
import shutil
import os
import sys
from pathlib import Path

INODE = re.compile(r'\d+(?:-\d+){0,2}\Z')

def find_tsk_binary(name):
    """Use bundled binaries first; fall back to host TSK for source/.deb."""
    basename=name+'.exe' if sys.platform=='win32' else name
    roots=[]
    frozen=getattr(sys,'_MEIPASS',None)
    if frozen:
        roots.extend((Path(frozen)/'tsk-bin',Path(frozen)/'src'/'tsk-bin'))
    env=os.environ.get('SECTORREC_TSK_DIR')
    if env:roots.append(Path(env))
    for directory in roots:
        binary=directory/basename
        if binary.is_file():return str(binary)
    return shutil.which(basename)


def image_command(action, image_path, inode=None):
    source=Path(image_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError('The source must be a readable filesystem image')
    if action == 'scan':
        program, args = 'fls', ['-r','-d','-p','-l',str(source)]
    elif action == 'trash_roots':
        program, args = 'fls', ['-u','-D','-l',str(source)]
    elif action in ('deleted_children','trash_scan','recover','recover_allocated'):
        if inode is None or not INODE.fullmatch(str(inode)):
            raise ValueError('Invalid metadata address')
        if action == 'recover':
            program, args = 'icat', ['-r',str(source),str(inode)]
        elif action == 'recover_allocated':
            program, args = 'icat', [str(source),str(inode)]
        elif action == 'deleted_children':
            program, args = 'fls', ['-d','-l','-p',str(source),str(inode)]
        else:
            program, args = 'fls', ['-u','-r','-p','-l','-F',str(source),str(inode)]
    else:
        raise ValueError('Unsupported read-only command')
    binary = find_tsk_binary(program)
    if not binary:
        raise RuntimeError('Sleuth Kit not found: install fls and icat and add them to PATH')
    return [binary,*args]
