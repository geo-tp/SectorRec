"""Read-only Windows physical disk and partition enumeration."""
import json
import re
import subprocess
from src.infrastructure.windows_process import no_console_window


def windows_transport(interface, pnp_id, native_bus=None):
    """Normalize Win32_DiskDrive's sometimes empty/ambiguous SD-reader bus.

    Built-in SD readers may report MMC/SD (or no interface at all) depending
    on their Windows driver. Only infer an SD/MMC bus from a characteristic
    PnP enumerator; a generic SCSI/USB disk must retain its reported bus.
    """
    bus = str(interface or '').strip().upper()
    pnp = str(pnp_id or '').strip().upper()
    native = str(native_bus or '').strip().upper()
    # Get-Disk.BusType distinguishes internal SD/MMC controllers more reliably
    # than the old Win32_DiskDrive.InterfaceType, which can be blank or SCSI.
    if native in ('SD', 'MMC', 'SDCARD', 'SDSTOR'):
        return 'MMC'
    if bus in ('SD', 'MMC', 'SDCARD', 'SDSTOR'):
        return 'MMC'
    if pnp.startswith(('SD\\', 'SDSTOR\\', 'MMC\\')):
        return 'MMC'
    return bus or (native if native not in ('UNKNOWN', 'UNSPECIFIED') else '') or 'DISK'


def windows_inventory(run=subprocess.run):
    """List every physical Windows partition, including unlettered EFI/MSR/recovery.

    Lettered sources retain the existing raw-volume path. Unlettered rows get
    an internal *selection key*, never passed directly to external programs:
    their raw reads are routed through verified PhysicalDrive + sector offset.
    """
    script = ("$ErrorActionPreference='Stop';"
              "$osdrive=[string](Get-CimInstance Win32_OperatingSystem).SystemDrive;"
              "$out=@(Get-CimInstance Win32_DiskDrive | ForEach-Object {"
              "$d=$_;$native=Get-Disk -Number $d.Index -ErrorAction SilentlyContinue;"
              "$verifiedSize=if($null -ne $native){[string]$native.Size}else{''};"
              "$verifiedSerial=if($null -ne $native){[string]$native.SerialNumber}else{''};"
              "$verifiedUnique=if($null -ne $native){[string]$native.UniqueId}else{''};"
              "$verifiedBus=if($null -ne $native){[string]$native.BusType}else{''};"
              "$parts=@(Get-Partition -DiskNumber $d.Index -ErrorAction SilentlyContinue | ForEach-Object {"
              "$p=$_; $v=Get-Volume -Partition $p -ErrorAction SilentlyContinue;"
              "[pscustomobject]@{Number=$p.PartitionNumber;Offset=$p.Offset;Size=$p.Size;"
              "Letter=[string]$p.DriveLetter;Fs=[string]$v.FileSystem;Label=[string]$v.FileSystemLabel;"
              "GptType=[string]$p.GptType;MbrType=[string]$p.MbrType;PartitionType=[string]$p.Type;"
              "IsSystem=([bool]$p.IsSystem -or [bool]$p.IsBoot -or ($osdrive -and ([string]$p.DriveLetter+':') -eq $osdrive))}"
              "});[pscustomobject]@{DeviceID=$d.DeviceID;Model=$d.Model;"
              "Manufacturer=$d.Manufacturer;Size=$d.Size;DiskSize=$verifiedSize;"
              "DiskSerial=$verifiedSerial;DiskUnique=$verifiedUnique;DiskPnP=[string]$d.PNPDeviceID;InterfaceType=$d.InterfaceType;NativeBus=$verifiedBus;LogicalSector=[string]$native.LogicalSectorSize;"
              "MediaType=$d.MediaType;PartitionStyle=[string]$native.PartitionStyle;Partitions=$parts}});"
              "ConvertTo-Json -InputObject $out -Depth 6 -Compress")
    try:
        proc=run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                 capture_output=True,text=True,timeout=15,check=False,
                 **no_console_window())
        if proc.returncode: return []
        parsed=json.loads(proc.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return []
    if isinstance(parsed,dict): parsed=[parsed]
    output=[]
    for disk in parsed if isinstance(parsed,list) else []:
        if not isinstance(disk,dict):continue
        path=str(disk.get('DeviceID') or '')
        if not re.fullmatch(r'\\\\\.\\PhysicalDrive\d+',path,re.I):continue
        try:size=int(disk.get('DiskSize') or disk.get('Size') or 0)
        except (ValueError,TypeError):continue
        if size<=0:continue
        # Capture the same Get-Disk identity used by the ddrescue worker.
        # A USB drive swapped for another disk of identical capacity must
        # never be treated as the source originally selected in the UI.
        from src.infrastructure.windows_ddrescue import canonical_source_identity
        identity = (canonical_source_identity(disk.get('DiskSerial'), disk.get('DiskUnique'),
                                                      size, disk.get('DiskPnP'))
                    if disk.get('DiskSize') else None)
        parts=disk.get('Partitions') or []
        if isinstance(parts,dict):parts=[parts]
        is_system=any(bool(p.get('IsSystem')) for p in parts if isinstance(p,dict))
        common={'disk_path':path,'is_system':is_system,'model':disk.get('Model') or '',
                'vendor':disk.get('Manufacturer') or '',
                'transport':windows_transport(disk.get('InterfaceType'), disk.get('DiskPnP'), disk.get('NativeBus')),
                'media_type':disk.get('MediaType') or ''}
        output.append({'path':path,'label':(disk.get('Model') or path)+'  [WHOLE DISK]',
                       'name':path.rsplit('\\',1)[-1],'size':size,
                       'fs':'DISK','supported':False,'partition_disk':True,
                       'imaging_size_verified':bool(disk.get('DiskSize')),
                       'imaging_identity':identity,
                       'child_filesystems':(), 'mounted':False,**common})
        for part in parts:
            if not isinstance(part,dict):
                continue
            try:
                psize = int(part.get('Size') or 0)
                number = int(part.get('Number') or 0)
                offset = int(part.get('Offset') or 0)
            except (ValueError, TypeError):
                continue
            if psize <= 0:
                continue
            letter = str(part.get('Letter') or '').strip().upper()
            has_letter = bool(re.fullmatch('[A-Z]', letter))
            # Never produce a synthetic selectable key without the exact
            # partition address; otherwise a tool might read the whole disk.
            if not has_letter and (number < 1 or offset < 0):
                continue
            filesystem = str(part.get('Fs') or 'UNKNOWN').upper()
            supported = filesystem in ('NTFS', 'VFAT', 'FAT', 'FAT12', 'FAT16',
                                       'FAT32', 'EXFAT', 'EXT2', 'EXT3', 'EXT4')
            gpt_type = str(part.get('GptType') or '').strip().upper().strip('{}')
            mbr_type = str(part.get('MbrType') or '').strip().upper()
            partition_type = str(part.get('PartitionType') or '').strip()
            # GPT partition type GUIDs are stable even when there is no
            # filesystem, mount point, or drive letter.
            role = ({
                'C12A7328-F81F-11D2-BA4B-00A0C93EC93B': 'EFI SYSTEM',
                'E3C9E316-0B5C-4DB8-817D-F92DF00215AE': 'MSR',
                'DE94BBA4-06D1-4D40-A16A-BFD50179D6AC': 'RECOVERY',
                '0FC63DAF-8483-4772-8E79-3D69D8477DE4': 'LINUX FILESYSTEM',
                '0657FD6D-A4AB-43C4-84E5-0933C84B4F4F': 'LINUX SWAP',
            }.get(gpt_type) or ({'0XEF': 'EFI SYSTEM', '0X27': 'RECOVERY',
                                 '0X82': 'LINUX SWAP'}.get(mbr_type)) or '')
            if not role and partition_type and partition_type.lower() not in ('basic', 'unknown'):
                role = partition_type.upper()[:40]
            label = str(part.get('Label') or '').strip()
            if has_letter:
                source_path = '\\\\.\\' + letter + ':'
                label = (label or letter + ':') + ' (' + letter + ':)'
            else:
                source_path = path + '#Partition' + str(number)
                label = 'Partition ' + str(number) + ((' · ' + role) if role else '') + ' (NO LETTER)'
            try:
                logical_sector = int(disk.get('LogicalSector') or 512)
            except (ValueError, TypeError):
                logical_sector = 512
            output.append({'path':source_path, 'label':label,
                           'name':letter+':' if has_letter else 'Partition '+str(number),
                           'size':psize, 'fs':filesystem, 'supported':supported,
                           'partition_disk':False, 'mounted':False,
                           'mountpoint':letter+':\\' if has_letter else '',
                           'has_drive_letter':has_letter, 'partition_role':role,
                           'child_filesystems':(), 'partition_number':number,
                           'partition_offset':offset,
                           'partition_style':str(disk.get('PartitionStyle') or '').upper(),
                           'logical_sector':logical_sector,
                           'imaging_size_verified':bool(disk.get('DiskSize')),
                           'imaging_identity':identity, **common})
    return sorted(output, key=lambda d: bool(d.get('is_system')))

