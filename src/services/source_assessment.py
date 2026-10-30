"""Fast, read-only assessment of already enumerated source metadata.

No disk scan happens here: this is capability routing, never a claim that files
or missing partitions were found. The adapter supplies lsblk/OS metadata.
"""
from dataclasses import dataclass

BOOT_FS = frozenset({'ISO9660', 'UDF', 'SQUASHFS'})
RECOVERY_FS = frozenset({'VFAT', 'FAT', 'FAT12', 'FAT16', 'FAT32', 'EXFAT',
                         'NTFS', 'EXT2', 'EXT3', 'EXT4'})

@dataclass(frozen=True)
class Method:
    key: str
    title: str
    caption: str
    benefit: str
    limitation: str
    recommended: bool = False

@dataclass(frozen=True)
class Assessment:
    headline: str
    description: str
    methods: tuple[Method, ...]
    unavailable: tuple[str, ...]
    mount_message: str

    def allows(self, engine):
        return any(method.key == engine for method in self.methods)


def assess_source(source, desktop=True):
    """Map reliable source facts into actionable methods, not guesses.

    System sources remain visible on desktop with restricted recovery methods.
    A non-destructive status check must never run TestDisk automatically.
    """
    if not source:
        return Assessment('Select a source', 'Choose a device first.', (), (), '')
    if source.get('diagnostics_only'):
        return Assessment('Device information available',
                          'This platform supports passive disk information here. '
                          'Raw-disk recovery is not available in this build; '
                          'open a filesystem image for recovery.', (), (), '')
    fs = str(source.get('fs') or 'UNKNOWN').upper()
    image = bool(source.get('is_image'))
    whole = bool(source.get('partition_disk'))
    supported = bool(source.get('supported', fs in RECOVERY_FS or image)) and fs not in BOOT_FS
    boot = fs in BOOT_FS or any(str(f).upper() in BOOT_FS for f in source.get('child_filesystems', ()))
    children = source.get('child_filesystems', ())
    system_source = bool(source.get('is_system'))
    mount_message = ('Active system disk: never unmounted; read-only scans may be inconsistent.'
                     if system_source else
                     'Mounted: SectorRec can unmount the source automatically after you start.'
                     if source.get('mounted') else 'Source is not mounted. Scans only read source data.')

    metadata = Method('metadata', 'Find deleted files', 'SLEUTH KIT · File records',
                      'Review filenames, sizes and folders. Save only what you select.',
                      'Works only when recoverable filesystem records remain.', True)
    signature = Method('signature', 'Recover deleted files by signatures', 'PHOTOREC · Alternative recovery',
                       'A second way to find deleted files when file records are missing. Saves matches immediately.',
                       'Names/folders are usually lost; may also extract files that are not deleted.')
    partition = Method('partition', 'Find missing partitions', 'TESTDISK · Partitions',
                       'Look for missing partition boundaries on the whole disk, then browse readable files.',
                       'For missing/damaged partition tables; cannot recover every layout.', True)

    methods = []
    unavailable = []
    if boot:
        headline = 'Bootable image detected'
        description = ('This appears to be a bootable ISO/UDF image (common on Linux installer USB drives). '
                       'An unusual or hybrid partition table is expected, not proof of disk damage. '
                       'Partition recovery is not useful for this layout.')
        unavailable.append('File-record recovery and partition search are not offered for this boot-image layout.')
        if desktop:
            methods.append(signature)
            description += ' Signature recovery is optional and may also find files belonging to the image.'
    elif image:
        headline = 'Disk image selected'
        description = ('SectorRec can inspect file records in an image of a filesystem, '
                       'or search for partitions if this is a complete disk image.')
        methods.append(Method('metadata', metadata.title, metadata.caption,
                              metadata.benefit, 'Needs a readable filesystem image; whole-disk images may need partition search.'))
        methods.append(partition)
        if desktop:
            methods.append(signature)
    elif supported:
        headline = 'Filesystem recognized'
        description = f'{fs} can be inspected for deleted-file records without extracting everything.'
        methods.append(metadata)
        if desktop:
            methods.append(signature)
        if whole and children:
            # Display parent only if selected explicitly; a whole disk with an
            # existing filesystem is not generally a missing-partition case.
            unavailable.append('For missing partitions, select the whole disk rather than an individual volume.')
    elif whole:
        headline = 'Disk layout needs inspection'
        description = ('No supported filesystem was identified at this level. '
                       'If a partition is missing, search its boundaries without repairing the disk.')
        methods.append(partition)
        if desktop:
            methods.append(signature)
        unavailable.append('Selective deleted-file recovery requires a readable FAT, exFAT, NTFS or ext volume.')
        if children:
            headline = 'Whole disk selected'
            description = ('Existing partitions are already visible. For deleted files, select a partition '
                           'in the device list. Search for missing partitions only if one has disappeared.')
            methods[0] = Method(partition.key, partition.title, partition.caption,
                                partition.benefit, partition.limitation, False)
    else:
        headline = 'Unsupported filesystem'
        description = f'Unsupported filesystem: {fs}. SectorRec cannot browse its deleted-file records.'
        if desktop:
            methods.append(signature)
        unavailable.append('Metadata Recovery requires FAT, exFAT, NTFS or ext2/3/4.')

    if system_source:
        # Never offer a potentially destructive workflow against a mounted OS disk.
        # Individual filesystem volumes may still be inspected read-only.
        methods = [m for m in methods if m.key != 'partition']
        if whole:
            unavailable.append('System-disk imaging and lost-partition search are disabled while the OS is running.')
        description += (' ACTIVE SYSTEM DISK: filesystem activity may overwrite deleted data. '
                        'Results may be inconsistent, and recovery must be saved to another physical disk. '
                        'For the best chance of recovery, use a different computer or a bootable OS.')
        if not whole:
            unavailable.append('No automatic unmount: live system volumes remain mounted throughout scanning.')
    if not methods:
        description += ' There is no compatible recovery action for this source.'
    # Keep recommended action first. A signature-only recommendation is not a
    # guarantee that remnants of previously deleted data remain on the disk.
    return Assessment(headline, description, tuple(methods), tuple(unavailable), mount_message)


def imaging_option_available(device, platform_name):
    """Display the imaging choice for real Linux devices and Windows volumes.

    Windows partition numbers are *not* a UI capability flag: a lettered
    volume is a valid selectable source even when its metadata is incomplete.
    Imaging still requires a fresh, verified disk/partition identity and
    geometry at preflight, before ddrescue is started.
    """
    if not device or device.get('is_image') or device.get('is_system') or device.get('diagnostics_only'):
        return False
    path = str(device.get('path') or '')
    if platform_name == 'linux':
        return path.startswith('/dev/')
    if platform_name == 'win32':
        from src.infrastructure.windows_raw import windows_device_path
        return windows_device_path(path)
    return False
