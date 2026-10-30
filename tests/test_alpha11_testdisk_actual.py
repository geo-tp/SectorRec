"""TestDisk log fixture from a *real* (published) testdisk.log.

The original log: https://gist.github.com/tlvince/1961186
The test intentionally keeps the real section ordering and sector arithmetic.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.services.partition import (parse_testdisk_log, parse_existing_lsblk,
                                   existing_partitions, _existing_from_sysfs)

REAL_LOG_EXCERPT = '''Disk /dev/sdb - 1000 GB / 931 GiB - CHS 121601 255 63, sector size=512
Partition table type (auto): EFI GPT
Disk /dev/sdb - 1000 GB / 931 GiB - Mass Storage Device
Partition table type: EFI GPT
Analyse Disk /dev/sdb - 1000 GB / 931 GiB - CHS 121601 255 63
hdr_size=92
check_part_gpt failed for partition
1 P MS Data 2048 1953524958 1953522911
Current partition structure:
No FAT, NTFS, ext2, JFS, Reiser, cramfs or XFS marker
1 P MS Data 2048 1953524958 1953522911
1 P MS Data 2048 1953524958 1953522911
search_part()
Disk /dev/sdb - 1000 GB / 931 GiB - CHS 121601 255 63
recover_EXT2: s_blocksize=4096
MS Data 2049 1953524952 1953522904 [ears]
Results
P MS Data 2049 1953524952 1953522904 [ears]
EXT4 Large file Sparse superblock
interface_write()
1 P MS Data 2049 1953524952 1953522904 [ears]
'''

class ActualTestdiskLogTests(unittest.TestCase):
    def test_real_testdisk_log_current_and_result(self):
        parts=parse_testdisk_log(REAL_LOG_EXCERPT, 1_001_000_000_000)
        self.assertEqual([(p.start,p.sectors,p.origin) for p in parts],
                         [(2048,1953522911,'existing'),(2049,1953522904,'found')])
    def test_numbered_chs_partition_record(self):
        report='''Disk /dev/sdz - 4 GB - CHS 486 255 63, sector size=512
Current partition structure:
1 * FAT32 LBA 0 32 33 480 10 5 7700000 [SDCARD]
'''
        parts=parse_testdisk_log(report,4_000_000_000)
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0].start,2048)
        self.assertEqual(parts[0].origin,'existing')

class KernelPartitionFallbackTests(unittest.TestCase):
    def test_no_filesystem_signature_does_not_hide_partition(self):
        sample={'blockdevices':[{'path':'/dev/sdz','type':'disk','log-sec':512,
               'children':[{'path':'/dev/sdz1','type':'part','fstype':None,
                            'start':2048,'size':3_000_000_000}]}]}
        got=parse_existing_lsblk(sample,'/dev/sdz',4_000_000_000)
        self.assertEqual(len(got),1)
        self.assertEqual(got[0].label,'PARTITION')

    def test_realistic_sysfs_adapter_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); block=root/'block'; block.mkdir()
            disk=block/'sdb'; disk.mkdir()
            (disk/'queue').mkdir()
            (disk/'queue/logical_block_size').write_text('512\n')
            part=disk/'sdb1'; part.mkdir()
            (part/'partition').write_text('1\n')
            (part/'start').write_text('2048\n')
            (part/'size').write_text('7800000\n')
            listing=root/'class'; listing.mkdir()
            (listing/'sdb').symlink_to(disk,target_is_directory=True)
            (listing/'sdb1').symlink_to(part,target_is_directory=True)
            found=_existing_from_sysfs('/dev/sdb',4_100_000_000,listing)
            self.assertEqual(len(found),1)
            self.assertEqual((found[0].start,found[0].sectors),(2048,7800000))

    def test_sysfs_fallback_after_two_empty_lsblk_responses(self):
        empty={'blockdevices':[{'path':'/dev/sdz','type':'disk','log-sec':512}]}
        known=[type('Fake',(),{'start':2048,'sectors':7000000,'sector_size':512,
                               'origin':'existing','label':'FAT32'})()]
        with patch('src.services.partition.subprocess.check_output',return_value=json.dumps(empty)), \
             patch('src.services.partition._existing_from_sysfs',return_value=known):
            result=existing_partitions('/dev/sdz',4_000_000_000)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0].start,2048)
