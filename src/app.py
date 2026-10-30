"""SectorRec entry point. MVC composition and frontend selection."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from src.controllers.controller import Controller
from src.infrastructure.windows_process import no_console_window
from src.views.view import PillowView
from src.infrastructure.platforms import current_platform
from src.services.source_assessment import assess_source

class UI(Controller):
    """Thin composition root: MVC controller owns exactly one Pillow view."""
    def __init__(self, demo=False, platform=None, desktop=False):
        super().__init__(demo=demo, platform=platform, desktop=desktop)
        self.view = PillowView(self)

    def render(self, height=170):
        return self.view.render(height=height)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--screenshot',choices=['welcome','devices','scan_ready','scanning','results','confirm','recovering','complete','signature_setup','signature_running','signature_summary','partition_ready','partition_scanning','partition_results','device_info'])
    ap.add_argument('--output',default='sectorrec-preview.png')
    ap.add_argument('--list-devices',action='store_true',help='Print detected source volumes without LCD access')
    ap.add_argument('--device-info',metavar='DEVICE',help='Read available passive device information as JSON')
    ap.add_argument('--check-source',metavar='DEVICE',help='Show quick device check and valid recovery methods as JSON')
    ap.add_argument('--known-partitions',metavar='DISK',
                    help='Read OS-known partition geometry without running TestDisk')
    ap.add_argument('--profile',action='store_true',help='Print input and render timing to stderr')
    ap.add_argument('--ui',choices=('auto','desktop','framebuffer'),default='auto',
                    help='Choose desktop window or Cardputer Zero framebuffer')
    ap.add_argument('--demo',action='store_true',help='Show demo data without accessing real disks')
    ap.add_argument('--resume-device',default=None,help=argparse.SUPPRESS)
    ap.add_argument('--selftest-ddrescue',action='store_true',
                    help='Verify bundled Windows ddrescue runtime without opening a disk')
    ap.add_argument('--height',type=int,default=170,help='Screenshot logical height (desktop layout)')
    args=ap.parse_args()
    if args.selftest_ddrescue:
        if sys.platform != 'win32':
            return 2
        from src.infrastructure.windows_ddrescue import runtime_dir, _env
        import subprocess
        try:
            folder = runtime_dir()
            for binary, arguments in (('ddrescue.exe', ['--version']),
                                      ('cygpath.exe', ['-u', '-a', str(Path.home())])):
                result = subprocess.run([str(folder / binary), *arguments],
                                        capture_output=True, timeout=15, env=_env(folder),
                                        **no_console_window())
                if result.returncode != 0:
                    return 2
            return 0
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return 2
    if args.list_devices:
        print(json.dumps(current_platform().devices(),indent=2))
        return
    if args.device_info:
        from dataclasses import asdict
        from src.services.device_diagnostics import inspect
        found=next((d for d in current_platform().devices() if d['path']==args.device_info),None)
        if not found:
            print('Device unavailable or protected',file=sys.stderr)
            return 2
        print(json.dumps(asdict(inspect(found)),indent=2))
        return 0
    if args.known_partitions:
        if not current_platform().raw_drives:
            print('Physical partition discovery requires the Linux device backend.', file=sys.stderr)
            return 2
        from dataclasses import asdict
        from src.services.partition import existing_partitions
        selected = next((d for d in current_platform().devices()
                         if d['path'] == args.known_partitions and d.get('partition_disk')), None)
        if selected is None:
            print('Select a visible, external WHOLE DISK (not a partition).',file=sys.stderr)
            return 2
        partitions = existing_partitions(selected['path'],selected.get('size',0))
        print(json.dumps({'source':selected['path'],
                          'size_bytes':selected.get('size',0),
                          'existing_partitions':[asdict(p) for p in partitions]},indent=2))
        return 0
    if args.check_source:
        match = next((d for d in current_platform().devices() if d['path'] == args.check_source), None)
        if match is None:
            print('Device unavailable or protected (system-disk recovery requires desktop confirmation)',file=sys.stderr)
            return 2
        check = assess_source(match,desktop=(args.ui != 'framebuffer'))
        print(json.dumps({'source':match, 'headline':check.headline,
                          'description':check.description,
                          'methods':[method.key for method in check.methods],
                          'mount_message':check.mount_message},indent=2))
        return 0
    if args.screenshot:
        ui=UI(demo=True, desktop=args.screenshot.startswith('signature_'))
        ui.stage=args.screenshot
        if args.screenshot in ('results','confirm','complete'):
            ui.build_result_filters()
        if args.screenshot in ('confirm','complete'):
            ui.message=('Recovered 2  • Partial 0\nUnverified 0  • Failed 0' if args.screenshot=='complete' else
                        '2 files • 50.4 MB known\nFree: 1.8 GB | reserve: 64 MB\nSaved in ~/Backups/SectorRec/')
            ui.status={'done':2,'total':2,'written':52533670,'failed':0,'recovered':2,'partial':0,'unverified':0,'outdir':str(Path.home()/'Backups'/'SectorRec'/'20261027_SanDisk')}
        if args.screenshot=='scan_ready':ui.selection=0
        if args.screenshot.startswith('signature_'):
            ui.engine='signature'
            ui.selection=0
            ui.signature_destination='/home/user/Recovered'
            ui.signature_status={'state':'running' if args.screenshot=='signature_running' else 'done',
                                 'files':452,'written':374003200,'free':2147483648,'started':time.monotonic()-64} 
        if args.screenshot.startswith('partition_'):
            from src.services.partition import PartitionCandidate
            ui.engine='partition'
            ui.selection=0
            ui.partition_status={'state':'done','started':time.monotonic()-42,
                  'candidates':[PartitionCandidate(2048,1000000,'FAT32'), PartitionCandidate(1002048,3200000,'MS Data (NTFS)')]}
        if args.screenshot=='scanning':ui.status={'count':421,'started':time.monotonic()-48}
        if args.screenshot=='recovering':ui.status={'done':1,'total':4,'written':4210673,'filename':'holiday_2025.jpg'}
        ui.render(height=args.height).save(args.output)
        print(args.output)
        return
    if args.ui == 'desktop' or (args.ui == 'auto' and (not sys.platform.startswith('linux') or os.environ.get('DISPLAY'))
                            and not os.environ.get('SECTORREC_FORCE_FB')):
        try:
            from src.views.desktop_native import DesktopWindow
            from src.infrastructure.windows_raw import windows_device_path
            if args.resume_device and (sys.platform != 'win32' or not windows_device_path(args.resume_device)):
                raise ValueError('Invalid Windows UAC source device')
            DesktopWindow(Controller(demo=args.demo, desktop=True),
                          resume_device=args.resume_device).run()
            return 0
        except (ImportError, OSError) as exc:
            print('SectorRec desktop UI:', exc, file=sys.stderr)
            return 1
    from src.views.cardputer.framebuffer_frontend import run_framebuffer
    return run_framebuffer(UI(demo=args.demo), profile=args.profile)

if __name__=='__main__':
    sys.exit(main())
