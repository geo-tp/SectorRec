"""New Linux policy: reject a demonstrated overlap, not an unverified alias."""
import subprocess
from unittest.mock import patch
import pytest
from src.services import signature


def _run(argv, **kw):
    if argv[0] == 'findmnt':
        return subprocess.CompletedProcess(argv, 0, '/dev/root\n', '')
    raise AssertionError(argv)


def test_usb_to_documents_when_lvm_mount_is_unresolvable():
    with patch.object(signature, '_physical_disk_roots', side_effect=lambda p: {
            '/dev/sdb': frozenset({'/dev/sdb'})}.get(str(p))), \
         patch.object(signature, '_mounted_linux_block_device', return_value='/dev/dm-2'), \
         patch.object(signature.subprocess, 'run', side_effect=_run):
        signature.ensure_different_disk('/dev/sdb', '/home/user/Documents')


def test_same_disk_still_denied_across_partitions():
    roots = {'/dev/sdb1': frozenset({'/dev/sdb'}),
             '/dev/sdb2': frozenset({'/dev/sdb'})}
    with patch.object(signature, '_physical_disk_roots', side_effect=lambda p: roots.get(str(p))), \
         patch.object(signature, '_mounted_linux_block_device', return_value='/dev/sdb2'), \
         patch.object(signature.subprocess, 'run', side_effect=_run):
        with pytest.raises(ValueError, match='source disk'):
            signature.ensure_different_disk('/dev/sdb1', '/home/user/Documents')


def test_same_device_still_denied_even_without_physical_root():
    with patch.object(signature, '_physical_disk_roots', return_value=None), \
         patch.object(signature, '_mounted_linux_block_device', return_value='/dev/sdb1'), \
         patch.object(signature.subprocess, 'run', side_effect=_run):
        with pytest.raises(ValueError, match='source disk'):
            signature.ensure_different_disk('/dev/sdb1', '/home/user/Documents')
