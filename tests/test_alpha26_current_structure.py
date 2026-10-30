from src.services.partition import parse_testdisk_log, merge_partition_candidates, PartitionCandidate

REPORT = """Using locale 'fr_FR.UTF-8'.
Disk /dev/sdc - 519 MB / 495 MiB - CHS 1022 16 62, sector size=512
Partition table type: None
Analyse Disk /dev/sdc - 519 MB / 495 MiB - CHS 1022 16 62
Current partition structure:
   P Sys=0D                   0   0  1  1022  15 30    1014784

search_part()
Disk /dev/sdc - 519 MB / 495 MiB - CHS 1022 16 62
     Sys=0D                   0   0  1     0   0  4          4
     FATX, 2048 B
Results
   P Sys=0D                   0   0  1     0   0  4          4
     FATX, 2048 B
interface_write()
search_part()
Disk /dev/sdc - 519 MB / 495 MiB - CHS 1022 16 62
Results
   P Sys=0D                   0   0  1     0   0  4          4
interface_write()
TestDisk exited normally.
"""

def test_actual_usb_512mb_report_preserves_full_size_initial_candidate():
    got = parse_testdisk_log(REPORT, 1014784 * 512)
    assert [(p.start,p.sectors,p.origin) for p in got] == [
        (0,4,'found'), (0,1014784,'existing')]

def test_current_only_returns_existing():
    got = parse_testdisk_log(REPORT.split('search_part()')[0],1014784*512)
    assert len(got)==1 and got[0].sectors==1014784

def test_out_of_bounds_initial_candidate_is_discarded():
    got = parse_testdisk_log(REPORT, 3*512)
    assert got == []

def test_kernel_duplicate_merged():
    got=parse_testdisk_log(REPORT,1014784*512)
    merged=merge_partition_candidates([PartitionCandidate(0,1014784,'KERNEL',origin='existing')],got)
    assert len(merged)==2 and merged[1].label=='KERNEL'
