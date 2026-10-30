"""Integration-order regression checks for the alpha6-style partition scan."""
from unittest.mock import patch
from src.services import partition as part

REPORT = '''TestDisk 7.2\nDisk /dev/sdz - 10485760 bytes, sector size=512\nResults\n P Linux filesys 2048 8191 6144\ninterface_write()\n'''

class FinishedTestDisk:
    returncode = 0
    def __init__(self, argv, stdout=None, stderr=None, **kwargs):
        events.append('testdisk-start')
        stdout.write(REPORT.encode())
        stdout.flush()
    def poll(self):
        events.append('testdisk-finished')
        return 0
    def wait(self):
        events.append('testdisk-wait')
        return 0

events = []

def test_linux_lookup_happens_after_testdisk_completion():
    events.clear()
    def linux_lookup(source, size):
        events.append('linux-lookup')
        return [part.PartitionCandidate(2048, 6144, 'EXT4', origin='existing'),
                part.PartitionCandidate(8192, 2048, 'FAT32', origin='existing')]
    status = {}
    with patch.object(part.subprocess, 'Popen', FinishedTestDisk), \
         patch.object(part, 'existing_partitions', linux_lookup):
        worker = part.PartitionSearchWorker('/dev/sdz', 10_485_760, False, status)
        worker.run()
    assert status['state'] == 'done', status
    assert events.index('linux-lookup') > events.index('testdisk-wait')
    assert len(status['candidates']) == 2
    assert status['existing_count'] == 2
    assert status['found_count'] == 0
    assert status['testdisk_count'] == 1


def test_linux_failure_does_not_erase_testdisk_results():
    events.clear()
    def bad_linux(*args):
        raise OSError('no lsblk')
    status = {}
    with patch.object(part.subprocess, 'Popen', FinishedTestDisk), \
         patch.object(part, 'existing_partitions', bad_linux):
        part.PartitionSearchWorker('/dev/sdz', 10_485_760, False, status).run()
    assert status['state'] == 'done', status
    assert len(status['candidates']) == 1
    assert status['found_count'] == 1
    assert 'linux_scan_error' in status
