"""OS access facade. Controllers never need to know how volumes are enumerated.

Linux uses its privileged reader; Windows can pass validated physical paths
to official read-only recovery tools when running as Administrator.
Other platforms currently support regular image files only.
"""
import sys
import os
from pathlib import Path
from src.infrastructure import linux_devices
from src.infrastructure.privileged import helper_command

class LinuxPlatform:
    name = 'linux'
    raw_drives = True
    def devices(self):
        return linux_devices.detect_devices()
    def unmount(self, path):
        device = next((x for x in linux_devices.detect_devices() if x['path']==path), None)
        if device and device.get('partition_disk'):
            return linux_devices.unmount_disk_children(path, helper_command=helper_command)
        return linux_devices.unmount_volume(path, linux_devices.detect_devices, helper_command)

class WindowsPlatform:
    name = 'win32'
    raw_drives = True
    def devices(self):
        from src.infrastructure.platform_disks import windows_inventory
        return windows_inventory()
    def unmount(self, path):
        # A Windows raw handle is opened read-only with sharing. Never
        # offline, dismount or modify a live Windows volume automatically.
        raise RuntimeError('SectorRec does not dismount Windows volumes')

class ImageOnlyPlatform:
    raw_drives = False
    def __init__(self, system):
        self.name = system
    def devices(self):
        # No physical drive inventory on unsupported platforms.
        return []
    def unmount(self, path):
        raise RuntimeError('Raw device access unavailable on this platform')

def current_platform():
    if sys.platform.startswith('linux'):
        if os.environ.get('SECTORREC_IMAGE_ONLY'):
            return ImageOnlyPlatform('linux-image')
        if os.environ.get('SECTORREC_APPIMAGE') == '1':
            # The portable app has its own guarded helpers. A root authorizer
            # is requested only for actions that actually need disk access.
            bundle = os.environ.get('SECTORREC_ROOT_BUNDLE', '')
            if not bundle or not Path(bundle).is_file():
                return ImageOnlyPlatform('linux-image')
            return LinuxPlatform()
        if getattr(sys, 'frozen', False) and not Path('/usr/lib/sectorrec/sectorrec-reader').exists():
            return ImageOnlyPlatform('linux-image')
        return LinuxPlatform()
    if sys.platform == 'win32':
        return WindowsPlatform()
    return ImageOnlyPlatform(sys.platform)
