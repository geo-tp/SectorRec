"""Parse Sleuth Kit file listings without starting processes or writing files."""
from __future__ import annotations

import re

from src.models.files import category_of


# TSK -l accepts both the colon-led output seen on Debian and tabbed fields.
# Crucially, NTFS orphan records often have -/r (unknown name type / regular
# metadata type); rejecting these silently loses deleted NTFS files.
_META = re.compile(r'^\s*([a-z-]/[a-z-])\s+(\*)?\s*(\d+(?:-\d+){0,2})(?:\(realloc\))?:\s*(.*)$', re.I)
_INODE = re.compile(r'^\d+(?:-\d+){0,2}$')
_MAX_PATH = 4096


def parse_entry(line: str, wanted='file', include_realloc=False):
    """Parse one fls -l record, including NTFS -/r and nested paths.

    Returns (inode, path, size, category). File system source flags are
    provided by the calling scan phase and never trusted from the filename.
    """
    line = line.rstrip('\r\n')
    if not line or ('(realloc)' in line and not include_realloc):
        return None
    parts = line.split('\t')
    match = _META.match(parts[0])
    if match:
        ftype, _deleted, inode, path = match.groups()
        if not path and len(parts) >= 2:
            path = parts[1]
    elif len(parts) >= 3:
        ftype = parts[0].strip().rstrip('*').strip()
        inode = parts[1].strip().removesuffix(':').replace('(realloc)','')
        path = parts[2]
    else:
        return None
    if not _INODE.fullmatch(inode) or '/' not in ftype:
        return None
    metadata_type = ftype.split('/', 1)[1].lower()
    if metadata_type != ('r' if wanted == 'file' else 'd'):
        return None
    path = path.strip().lstrip('/')
    if not path or len(path) > _MAX_PATH or path in ('.','..'):
        return None
    # Long format: modified, accessed, changed, created, SIZE, uid, gid.
    # Never mistake timestamps / IDs for a size in a truncated record.
    size = None
    if len(parts) >= 8:
        try:
            candidate = int(parts[-3].strip())
            if 0 <= candidate <= 2**63 - 1:
                size = candidate
        except ValueError:
            pass
    return inode, path, size, category_of(path)


def parse_fls(line: str):
    return parse_entry(line, 'file')


def trash_root(name):
    """Only known root-level trash containers, never arbitrary live files."""
    name = name.strip('/').replace('\\', '/')
    if '/' in name or name in ('', '.', '..'):
        return False
    return (name.casefold() in ('$recycle.bin', '.trashes', '.trash') or
            re.fullmatch(r'\.Trash-[0-9]+', name, re.I) is not None)


def trash_content(path):
    segments = path.replace('\\', '/').split('/')
    # Metadata sidecars from Freedesktop Trash and NTFS recycle info files
    # should not masquerade as user recoverable documents.
    if 'info' in segments[:-1] or 'expunged' in segments[:-1]:
        return False
    if segments[-1].startswith('$I') and '$RECYCLE.BIN' in segments:
        return False
    return True
