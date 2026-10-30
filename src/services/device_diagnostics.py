"""Passive drive identification/SMART snapshot. No tests, writes or benchmarks.

This optional adapter does not participate in recovery engine decisions. It only
reads device facts from the host and smartctl JSON. SMART absence is UNKNOWN.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from src.infrastructure.windows_process import no_console_window

MAX_OUTPUT = 512 * 1024
SMART_TIMEOUT = 7

@dataclass
class DriveReport:
    model: str = ''
    vendor: str = ''
    capacity: int = 0
    interface: str = ''
    media_type: str = ''
    filesystem: str = ''
    temperature_c: int | None = None
    power_on_hours: int | None = None
    ssd_used_percent: int | None = None
    reallocated: int | None = None
    pending: int | None = None
    uncorrectable: int | None = None
    reported_errors: int | None = None
    health: str = 'unavailable'  # passed / warning / failed / unavailable
    note: str = 'SMART information is not available for this device.'
    extras: dict = field(default_factory=dict)
    smart_data: dict = field(default_factory=dict, repr=False)


def _get(data, *path):
    for key in path:
        if not isinstance(data, dict): return None
        data = data.get(key)
    return data


def _integer(value):
    try:
        if isinstance(value, bool): return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _smart_integer(value):
    """ATA raw values can be numeric or a dict with value/string fields."""
    if isinstance(value, dict):
        value = value.get('value', value.get('string'))
    if isinstance(value, str):
        found = re.match(r'^\s*(\d+)', value)
        return int(found.group(1)) if found else None
    return _integer(value)


def base_report(source):
    return DriveReport(model=str(source.get('model') or source.get('label') or ''),
        vendor=str(source.get('vendor') or ''), capacity=_integer(source.get('size')) or 0,
        interface=str(source.get('transport') or ''), filesystem=str(source.get('fs') or ''),
        media_type=str(source.get('media_type') or ''))


def interpret_smart(data, source=None):
    """Do not interpret an absent SMART verdict as a PASS."""
    result = base_report(source or {})
    if not isinstance(data, dict): return result
    result.smart_data = data
    result.model = str(data.get('model_name') or data.get('product') or result.model)
    result.vendor = str(data.get('vendor') or result.vendor)
    result.capacity = _integer(_get(data, 'user_capacity', 'bytes')) or result.capacity
    result.interface = str(_get(data,'interface_type','name') or result.interface)
    result.temperature_c = _integer(_get(data,'temperature','current'))
    result.power_on_hours = _integer(_get(data,'power_on_time','hours'))
    nvme = data.get('nvme_smart_health_information_log') or {}
    if result.temperature_c is None:
        result.temperature_c = _integer(nvme.get('temperature'))
    if result.power_on_hours is None:
        result.power_on_hours = _integer(nvme.get('power_on_hours'))
    result.ssd_used_percent = _integer(nvme.get('percentage_used'))
    ata={}
    for item in _get(data,'ata_smart_attributes','table') or []:
        identifier = _integer(item.get('id')) if isinstance(item, dict) else None
        if identifier is not None: ata[identifier] = _smart_integer(item.get('raw'))
    result.reallocated=ata.get(5)
    result.pending=ata.get(197)
    result.uncorrectable=ata.get(198)
    if result.temperature_c is None:
        result.temperature_c=ata.get(194,ata.get(190))
    result.reported_errors = _integer(nvme.get('media_errors'))
    if result.reported_errors is None:
        result.reported_errors = ata.get(187)
    # A nonempty error log has varying semantics and isn't equal to bad sectors.
    if result.reported_errors is None:
        result.reported_errors = _integer(_get(data,'ata_smart_error_log','summary','count'))
    verdict=_get(data,'smart_status','passed')
    critical=_integer(nvme.get('critical_warning'))
    smart_available = _get(data, 'smart_support', 'available')
    if verdict is False or (critical is not None and critical != 0):
        result.health='failed'
        result.note='The device reports a SMART health warning. Prioritize copying important data.'
    elif verdict is True:
        result.health='passed'
        result.note='SMART self-assessment passed; this does not guarantee recoverability.'
    elif smart_available is False:
        result.note='SMART is not supported or is hidden by this interface.'
    # Retired/pending sectors matter even when the global device verdict passes.
    flagged=[label for label,value in (('reallocated sectors',result.reallocated),
              ('unstable sectors',result.pending),('uncorrectable sectors',result.uncorrectable))
              if value is not None and value>0]
    if flagged:
        if result.health != 'failed': result.health='warning'
        result.note='Non-zero ' + ', '.join(flagged) + '; handle the device cautiously.'
    if result.ssd_used_percent is not None and result.ssd_used_percent >= 100:
        if result.health != 'failed': result.health='warning'
        result.note='SSD endurance indicator reached or exceeded 100%.'
    result.extras = {
        'firmware': str(data.get('firmware_version') or ''),
        'protocol': str(data.get('device', {}).get('protocol') or ''),
        'form_factor': str(_get(data,'form_factor','name') or ''),
        'nvme_unsafe_shutdowns': _integer(nvme.get('unsafe_shutdowns')),
        'nvme_available_spare': _integer(nvme.get('available_spare')),
        'nvme_data_units_written': _integer(nvme.get('data_units_written')),
        'smart_exit_status': _integer(_get(data,'smartctl','exit_status')),
    }
    # smartctl bit 3 means the SMART status itself failed. Other bits are
    # potentially historical/errors and not equivalent to passed status.
    code=result.extras['smart_exit_status']
    if code is not None and code & 8:
        result.health='failed'
        result.note='SMART reported a failing health status.'
    return result


def _physical_source(source):
    if source.get('is_image'): return None
    raw=str(source.get('disk_path') or source.get('path') or '')
    if sys.platform.startswith('linux'):
        if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+',raw): return None
    elif sys.platform.startswith('win'):
        if not re.fullmatch(r'\\\\\.\\PhysicalDrive\d+',raw,re.I): return None
    else: return None
    return raw


def _read_smart(source, timeout=SMART_TIMEOUT, extended=False):
    disk = _physical_source(source)
    if not disk: return None, 'SMART is not available for disk image files.'
    binary=shutil.which('smartctl')
    if not binary and getattr(sys,'_MEIPASS',None):
        name='smartctl.exe' if sys.platform.startswith('win') else 'smartctl'
        candidate=Path(sys._MEIPASS)/'smartctl-bin'/name
        if candidate.is_file():binary=str(candidate)
    if not binary: return None, 'smartmontools is not installed.'
    # Never launch -t (self-test), -s (SMART enable) or any writing operation.
    # -n standby avoids waking a sleeping drive when the device supports it.
    cmd=([binary,'-j','-x','-n','standby',disk] if extended
         else [binary,'-j','-i','-H','-A','-n','standby',disk])
    # The automatic SMART summary runs when the user selects a drive. It must
    # NEVER open an authentication dialog or start a privileged background job.
    # First probe as the logged-in user; smartctl may already be allowed by
    # udev permissions or the device may simply not offer SMART through USB.
    try:
        completed=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,check=False,
                                **no_console_window())
    except subprocess.TimeoutExpired:
        return None, 'SMART query timed out. You can continue recovery.'
    except (OSError, ValueError):
        return None, 'SMART is not accessible on this device.'
    # Only an explicit click on "Full SMART report" may ask for Polkit.
    # Do not needlessly elevate a query that succeeded without root.
    if (extended and sys.platform.startswith('linux') and os.geteuid()!=0 and
            source.get('disk_path') and
            (Path('/usr/lib/sectorrec/sectorrec-reader').is_file() or
             os.environ.get('SECTORREC_APPIMAGE') == '1')):
        # smartctl exit codes can signal drive health issues, not permissions.
        # Escalate only when permissions actually prevented a useful reading.
        # Its JSON output can still contain a `device` object when an access
        # check failed, so do not use that field to infer authorization.
        permission_error = ('permission denied' in (completed.stderr or '').lower() or
                            'operation not permitted' in (completed.stderr or '').lower() or
                            'permission denied' in (completed.stdout or '').lower())
        if permission_error:
            from src.infrastructure.privileged import helper_command
            cmd=helper_command('smart_full', disk)
            # Polkit's password box must not be cut off after 11 seconds.
            auth_timeout = 300 if os.environ.get('SECTORREC_DEBIAN_ARCHIVE') == '1' else 180
            try:
                completed=subprocess.run(cmd,capture_output=True,text=True,
                                         timeout=max(timeout, auth_timeout),check=False,
                                         **no_console_window())
            except subprocess.TimeoutExpired:
                return None, 'SMART authorization/query timed out.'
            except (OSError, ValueError, RuntimeError) as exc:
                return None, 'SMART is not accessible: '+str(exc)[:100]
    if len(completed.stdout)>(MAX_OUTPUT * (4 if extended else 1)):
        return None, 'SMART response is unexpectedly large.'
    try:
        data=json.loads(completed.stdout)
    except ValueError:
        return None, 'SMART response unavailable (permissions or unsupported interface).'
    if not isinstance(data,dict):
        return None, 'SMART response unavailable.'
    return data, ''


def inspect(source, smart_reader=None):
    """Synchronous provider; ALWAYS invoke from a worker, not the UI thread."""
    report=base_report(source)
    if source.get('is_image'):
        report.note='Disk image: device health information cannot be measured.'
        return report
    read = smart_reader or _read_smart
    try:
        data,error=read(source)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        data,error=None,str(exc)[:140]
    if data:
        report=interpret_smart(data,source)
        if not any((report.health!='unavailable',report.temperature_c is not None,
                    report.power_on_hours is not None)):
            report.note=error or 'SMART information not exposed by this USB bridge or device.'
    else:
        report.note=error or report.note
    report.extras.update({
        'revision':str(source.get('revision') or ''),
        'logical_sector':_integer(source.get('logical_sector')),
        'physical_sector':_integer(source.get('physical_sector')),
        'partition_table':str(source.get('partition_table') or ''),
    })
    return report


def report_lines(report):
    from src.models.files import human_size
    fields=[('Health', {'passed':'SMART passed', 'failed':'SMART warning',
                      'warning':'Attention needed','unavailable':'SMART unavailable'}.get(report.health,'SMART unavailable')),
            ('Model',report.model), ('Vendor',report.vendor),
            ('Capacity',human_size(report.capacity) if report.capacity else ''),
            ('Connection',report.interface),('Type',report.media_type),
            ('File system',report.filesystem),
            ('Temperature',f'{report.temperature_c} °C' if report.temperature_c is not None else ''),
            ('Power-on',f'{report.power_on_hours:,} h' if report.power_on_hours is not None else ''),
            ('SSD used',f'{report.ssd_used_percent}%' if report.ssd_used_percent is not None else ''),
            ('Reallocated',str(report.reallocated) if report.reallocated is not None else ''),
            ('Pending sectors',str(report.pending) if report.pending is not None else ''),
            ('Uncorrectable',str(report.uncorrectable) if report.uncorrectable is not None else ''),
            ('Reported errors',str(report.reported_errors) if report.reported_errors is not None else '')]
    fields.extend((str(key).replace('_',' ').title(), str(value)) for key,value in report.extras.items()
                  if value is not None and value != '')
    return [(name,value) for name,value in fields if value]


def detailed_smart(source, timeout=11):
    """Optional passive extended report. Call from a worker; do not block recovery/UI.

    -x fetches logs, NOT self-tests (-t) or surface scans. 
    If unsupported (USB bridge), return an explicit error instead of a false OK.
    """
    return _read_smart(source, timeout=timeout, extended=True)


def smart_attribute_lines(report):
    """All ATA attributes exposed by the standard passive SMART query."""
    data=report.smart_data or {}
    rows=[]
    for item in (data.get('ata_smart_attributes') or {}).get('table', ()):
        if not isinstance(item,dict):continue
        name=str(item.get('name') or 'Attribute')
        identifier=item.get('id')
        label=f"{identifier} · {name}" if identifier is not None else name
        raw=item.get('raw') or {}
        value=(raw.get('string',raw.get('value')) if isinstance(raw,dict) else raw)
        parts=[]
        if value is not None: parts.append(f'Raw: {value}')
        for key in ('value','worst','thresh','when_failed'):
            v=item.get(key)
            if v is not None and v!='':parts.append(f'{key}: {v}')
        rows.append((label,'   ·   '.join(parts) or 'Not reported'))
    return rows


def smart_operational_lines(report):
    """Readable values from all *passively returned* NVMe/SCSI information.

    Key names are device-provided. We don't infer zeroes for absent attributes.
    The complete on-demand -x JSON remains available for vendor-specific data.
    """
    data=report.smart_data or {}
    rows=[]
    for section,heading in (('nvme_smart_health_information_log','NVMe'),
                            ('scsi_error_counter_log','SCSI errors'),
                            ('scsi_grown_defect_list','SCSI defects')):
        part=data.get(section)
        if not isinstance(part,dict):continue
        for name,value in part.items():
            if isinstance(value,dict):
                for subkey,subvalue in value.items():
                    if isinstance(subvalue,(str,int,float,bool)):
                        rows.append((f'{heading} · {name} · {subkey}',str(subvalue)))
            elif isinstance(value,(str,int,float,bool)):
                rows.append((f'{heading} · {name}',str(value)))
            if len(rows)>=200:break
    for key in ('serial_number','rotation_rate','logical_block_size','physical_block_size'):
        value=data.get(key)
        if isinstance(value,(str,int,float)):
            rows.append((key.replace('_',' ').title(),str(value)))
    return rows[:200]
