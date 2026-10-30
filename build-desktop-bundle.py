"""Native standalone desktop builds; run on the TARGET OS.

Windows: bundle official Sleuth Kit Windows `bin` folder under tsk-bin/.
Linux: the AppImage script handles all runtime/TSK libs separately.
"""
import os
import subprocess
import sys
from pathlib import Path


def bundle_command(base: Path):
    if sys.platform != 'win32':
        raise RuntimeError('Use build-appimage.sh for Linux desktop; this bundler targets Windows x64')
    mode = 'onefile'
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--'+mode, '--windowed', '--name', 'SectorRec',
           '--collect-data', 'src',
           '--exclude-module', 'PyQt5', '--exclude-module', 'PySide6']
    tsk_dir = Path(os.environ['TSK_BIN_DIR']) if os.environ.get('TSK_BIN_DIR') else base / 'tsk-bin'
    if tsk_dir.is_dir():
        for filename in ('fls.exe' if sys.platform == 'win32' else 'fls',
                         'icat.exe' if sys.platform == 'win32' else 'icat'):
            if not (tsk_dir / filename).is_file():
                raise RuntimeError(f'Missing bundled Sleuth Kit binary: {filename}')
        cmd += ['--add-binary', f'{tsk_dir}{os.pathsep}tsk-bin']
    else:
        raise RuntimeError('Portable release requires TSK_BIN_DIR with fls and icat')
    # PhotoRec comes from a different upstream project than Sleuth Kit. Keep
    # both binary bundles independent; never present the TSK package as if it
    # included PhotoRec. Its supporting DLLs belong beside PhotoRec.
    photo_dir = Path(os.environ['PHOTOREC_BIN_DIR']) if os.environ.get('PHOTOREC_BIN_DIR') else base/'photorec-bin'
    photo_name = 'photorec_win.exe' if sys.platform == 'win32' else 'photorec'
    testdisk_name = 'testdisk_win.exe' if sys.platform == 'win32' else 'testdisk'
    if not (photo_dir / testdisk_name).is_file():
        raise RuntimeError(f'Missing {testdisk_name}: partition search requires TestDisk')
    if not (photo_dir / photo_name).is_file():
        raise RuntimeError(f'Missing {photo_name}: supply PHOTOREC_BIN_DIR with PhotoRec and runtime dependencies')
    if (photo_dir / photo_name).is_file():
        cmd += ['--add-binary', f'{photo_dir}{os.pathsep}photorec-bin']
    # Optional passive SMART tool, supplied by the release builder on the
    # matching target OS. Missing SMART is reported as unavailable in the UI.
    smart_dir = Path(os.environ['SMARTCTL_BIN_DIR']) if os.environ.get('SMARTCTL_BIN_DIR') else base/'smartctl-bin'
    smart_name = 'smartctl.exe' if sys.platform=='win32' else 'smartctl'
    if (smart_dir/smart_name).is_file():
        cmd += ['--add-binary', f'{smart_dir}{os.pathsep}smartctl-bin']
    if sys.platform == 'win32':
        ddrescue_dir = base / 'ddrescue-bin'
        for required in ('ddrescue.exe', 'cygpath.exe', 'cygwin1.dll'):
            if not (ddrescue_dir / required).is_file():
                raise RuntimeError(f'Missing GNU ddrescue Cygwin runtime: {required}')
        cmd += ['--add-binary', f'{ddrescue_dir}{os.pathsep}ddrescue-bin']
    cmd += [str(base / 'launch_desktop.py')]
    return cmd


if __name__ == '__main__':
    base = Path(__file__).resolve().parent
    subprocess.check_call(bundle_command(base), cwd=base)
