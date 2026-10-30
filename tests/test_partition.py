"""Partition-specific tests: genuine TestDisk 7.2 log shapes + TSK stub files."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.services.partition import parse_testdisk_log, partition_command, PartitionCandidate
from src.models import core
from src.app import UI

GPT = '''Partition table type: EFI GPT
Analyse Disk /dev/sdd - 62 GB - CHS 59200 64 32, sector size=512
search_part()
Results
 P MS Data 2048 121176063 121174016
exFAT, blocksize=131072
 P EFI System 121176064 121241599 65536
FAT16, blocksize=512
interface_write()
 1 P EFI System 121176064 121241599 65536
'''
MBR = '''Disk /dev/sda - 750 GB - CHS 91201 255 63
Current partition structure:
 1 P Linux 0 32 33 12157 149 24 195309568
search_part()
Results
 1 P Linux 0 32 33 12157 149 24 195309568
 2 * HPFS - NTFS 12157 149 25 26746 195 9 234375168
interface_write()
'''

class PartitionTests(unittest.TestCase):
    def test_gpt_results_parsing_including_bounds_and_nonduplication(self):
        parts = parse_testdisk_log(GPT, source_bytes=70*1024**3)
        self.assertEqual([p.start for p in parts], [2048,121176064])
        self.assertEqual(parts[0].size,121174016*512)
        self.assertEqual(parse_testdisk_log(GPT,source_bytes=4096),[])

    def test_mbr_chs_conversion(self):
        parts=parse_testdisk_log(MBR,source_bytes=900*1024**3)
        self.assertEqual([p.start for p in parts],[2048,195311616])

    def test_log_without_result_is_not_a_fake_partition(self):
        current=parse_testdisk_log(MBR.split('Results')[0],1_000_000_000_000)
        self.assertEqual(len(current),1)
        self.assertEqual(current[0].origin,'existing')
        self.assertEqual(parse_testdisk_log('Results\nP UNKNOWN 9 8 7\ninterface_write()'),[])
        with self.assertRaises(ValueError):
            parse_testdisk_log(GPT.replace('sector size=512','sector size=4096'))

    def test_partition_commands_allow_read_only_verified_actions(self):
        with tempfile.TemporaryDirectory() as root:
            image=Path(root)/'drive.dd';image.write_bytes(b'\0'*8192)
            with patch('src.services.partition.find_tsk_binary',lambda binary:'/usr/bin/'+binary):
                self.assertEqual(partition_command('scan',str(image),8,image=True)[:5],
                                 ['/usr/bin/fls','-o','8','-r','-p'])
                self.assertIn('-r',partition_command('recover',str(image),8,'12',image=True))
                self.assertNotIn('-r',partition_command('recover_allocated',str(image),8,'12',image=True))
                with self.assertRaises(ValueError):partition_command('write',str(image),8,image=True)
                with self.assertRaises(ValueError):partition_command('recover',str(image),8,'1;rm',image=True)
                with self.assertRaises(ValueError):partition_command('scan',str(image),99,image=True)
        command=partition_command('recover','/dev/sdz',2048,'13-128-1')
        self.assertEqual(command[-3:],['partition_recover','/dev/sdz','2048:13-128-1'])

    def test_full_partition_scan_and_individual_allocated_file_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            db=core.Catalog(Path(folder)/'db.sqlite')
            listing=('r/r  10: docs/active.pdf\t1\t1\t1\t1\t4\t0\t0\n'
                     'r/r * 11: docs/deleted.jpg\t1\t1\t1\t1\t5\t0\t0\n')
            calls=[]
            def builder(action,src,inode=None):
                calls.append((action,inode))
                if action=='scan':return ['/usr/bin/python3','-c',f'import sys;sys.stdout.write({listing!r})']
                if action=='recover_allocated':return ['/usr/bin/python3','-c','import sys;sys.stdout.buffer.write(b"PDF!")']
                if action=='recover':return ['/usr/bin/python3','-c','import sys;sys.stdout.buffer.write(b"JPEG!")']
                return ['/usr/bin/python3','-c','pass']
            status={}
            scan=core.ScanWorker(db,'image.dd',status,command_builder=builder,include_allocated=True)
            scan.start();scan.join(4)
            self.assertEqual(status.get('state'),'done',status)
            self.assertEqual([r[5] for r in db.page(0,2)],['allocated','deleted'])
            saved={}
            job=core.RecoverWorker(db,'ALL',db.at(0),'image.dd','lost disk',Path(folder),saved,command_builder=builder)
            job.start();job.join(4)
            self.assertEqual(saved.get('recovered'),1,saved)
            self.assertIn(('recover_allocated','10'),calls)
            db.close()

    def test_cardputer_routes_disk_to_testdisk_without_photorec(self):
        ui=UI(demo=True,desktop=False)
        try:
            ui.devices=[{'label':'USB [WHOLE DISK]','path':'/dev/sda','size':10**9,
                     'fs':'DISK','mounted':False,'supported':False,'partition_disk':True}]
            ui.handle('OK');self.assertEqual(ui.stage,'devices')
            ui.handle('OK');self.assertEqual(ui.stage,'partition_ready')
            ui.handle('OK');self.assertEqual(ui.stage,'partition_scanning')
            ui.tick();self.assertEqual(ui.stage,'partition_results')
            ui.handle('OK');self.assertEqual(ui.stage,'scanning')
            ui.tick();self.assertEqual(ui.stage,'results')
            self.assertEqual(ui.engine,'partition')
            ui.stage='partition_results';self.assertEqual(ui.render().size,(320,170))
            with self.assertRaises(ValueError):ui.desktop_select_method('signature')
        finally:ui.close()

if __name__=='__main__':unittest.main()
