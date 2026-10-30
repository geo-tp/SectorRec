"""Shared file categories, suffix classification and size labels.

Pure metadata helpers: no device access, database or recovery workers.
"""
from __future__ import annotations

from pathlib import Path

CATEGORIES = ('ALL', 'PHOTOS', 'VIDEOS', 'AUDIO', 'DOCS', 'ARCHIVES', 'OTHER')
EXTENSIONS = {
    'PHOTOS': {'.jpg','.jpeg','.png','.gif','.webp','.bmp','.tif','.tiff','.heic','.heif','.raw','.dng','.cr2','.nef','.arw','.orf','.rw2','.raf','.psd','.svg','.ico','.avif','.jfif','.jxl'},
    'VIDEOS': {'.mp4','.mkv','.mov','.avi','.webm','.wmv','.flv','.m4v','.mpg','.mpeg','.3gp','.mts','.m2ts','.ts','.vob','.ogv','.divx','.mxf'},
    'AUDIO': {'.mp3','.flac','.wav','.ogg','.aac','.m4a','.wma','.opus','.aiff','.aif','.alac','.mid','.midi','.amr','.ape','.m4b'},
    'DOCS': {'.pdf','.txt','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.odt','.rtf','.csv','.epub','.md','.json','.xml','.html','.htm','.ods','.odp','.pages','.numbers','.key','.log','.tex','.sqlite','.db','.eml','.msg' },
    'ARCHIVES': {'.zip','.7z','.rar','.tar','.gz','.xz','.bz2','.tgz','.iso','.zst','.cab','.img','.dmg','.apk','.deb','.rpm','.jar' },
}


def extension_of(filename: str) -> str:
    """Lower-case suffix, empty string means no extension."""
    return Path(filename).suffix.lower()


def category_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    for category, extensions in EXTENSIONS.items():
        if ext in extensions:
            return category
    return 'OTHER'


def human_size(n: int | None) -> str:
    if n is None or n < 0:
        return '?'
    value = float(n)
    for unit in ('B','KB','MB','GB','TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return '0 B'
