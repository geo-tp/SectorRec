"""Short, factual scanning guidance for the desktop UI; no I/O or engine coupling."""

SCAN_TIPS = {
    "metadata": (
        "If metadata recovery misses your files, try PhotoRec signature extraction: it finds files without directory entries.",
        "Sleuth Kit reads filesystem records; it does not recover file contents during this scan.",
        "Deleted names and folders may survive even when their original data has been overwritten.",
        "Files with unknown extensions remain in Other. They may still be recoverable.",
        "No percentage is shown: filesystem metadata scans do not read every sector sequentially.",
        "An unusually large directory tree can make metadata searching take longer than expected.",
        "Scanning a damaged disk may be slow; repeated read errors are a reason to consider imaging first.",
        "After scanning, filter by file category and exact extension without rescanning the device.",
        "Some damaged or fragmented files can appear in results but fail to recover completely.",
        "SectorRec keeps partial scan results if you choose Stop early; the list may be incomplete.",
        "Recovery saves only the selected files. SectorRec never writes recovered data to its source.",
        "An old filename is not proof that the original file contents are still intact.",
        "On NTFS and FAT, deleted file records can be found even after the directory is no longer visible.",
        "A file without a recognizable extension will appear in Other, not necessarily as an error.",
        "Avoid writing to the source while searching: new writes can overwrite recoverable data.",
    ),
    "signature": (
        "If PhotoRec misses files or loses names, try Sleuth Kit metadata recovery for original filenames and folders.",
        "PhotoRec extracts as it scans; the destination must be on a different physical disk.",
        "Signature recovery can find deleted files when their original filesystem records are gone.",
        "Recovered names and original folder paths usually cannot be reconstructed from signatures.",
        "Some files from the current filesystem may be recovered too: not every result was deleted.",
        "PhotoRec can read the full selected address space; a large drive may take hours.",
        "PhotoRec recognizes data patterns; unknown or fragmented formats may remain unrecoverable.",
        "Progress depends on disk condition and USB/card-reader speed, not only capacity.",
        "Output is saved continuously. Stopping early keeps already written files in the destination.",
        "Available destination space is monitored while PhotoRec writes its recovery folders.",
        "A successful signature match does not guarantee a complete or undamaged file.",
        "The default PhotoRec format selection avoids some high false-positive signatures.",
        "An ISO installer image can yield files from the installer itself rather than older USB data.",
        "Never use the scanned device as the destination: recovered files may overwrite source data.",
        "Recoverable data volume is unknown until extraction finishes; free-space checks are estimates.",
    ),
    "partition": (
        "TestDisk searches for lost partition boundaries. It does not repair or write a partition table.",
        "An SD card with an intact partition table may have no *missing* partitions to report.",
        "To recover deleted files from a visible partition, select the partition instead of the whole disk.",
        "Bootable Linux installer USB drives may use hybrid ISO layouts that TestDisk cannot interpret.",
        "A found partition is only a candidate; its file records must still be readable to browse it.",
        "Partition searches inspect disk structures; large drives or damaged areas can take a long time.",
        "You can stop early and inspect locations already identified, but results will be incomplete.",
        "Reading the source can take a while. Avoid unplugging it during analysis.",
        "An apparently healthy FAT partition can be listed as Existing even when no new partitions are found.",
        "No candidates does not mean hardware damage or that your existing files are erased.",
        "Stop search cancels the running analysis; no repair of the source disk is performed.",
        "On unusual disk geometries, SectorRec may withhold uncertain results for safety.",
    ),
}

def scan_tip(method: str, elapsed: float, interval: float = 6.5) -> str:
    messages = SCAN_TIPS.get(method, ())
    if not messages:
        return ''
    return messages[max(0,int(elapsed // interval)) % len(messages)]
