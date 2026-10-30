"""Environment for host utilities launched by the frozen AppImage.

PyInstaller changes LD_LIBRARY_PATH for its own runtime, while AppRun adds
bundled recovery-engine libraries. Neither path belongs in host sudo, UDisks,
Zenity or KDialog: their libraries must come from the installed OS.
"""
from __future__ import annotations

import os


def host_environment():
    """Preserve desktop/session variables but restore the original loader path.

    AppRun saves LD_LIBRARY_PATH *before* adding AppDir/usr/lib. The standard
    PyInstaller LD_LIBRARY_PATH_ORIG is insufficient in this case, because it
    has already captured AppDir/usr/lib. Keep SUDO_ASKPASS / DISPLAY / Wayland
    and the user's authentication agent environment untouched.
    """
    env = os.environ.copy()
    if env.get('SECTORREC_APPIMAGE') == '1':
        if env.get('SECTORREC_HOST_LD_LIBRARY_PATH_PRESENT') == '1':
            env['LD_LIBRARY_PATH'] = env.get('SECTORREC_HOST_LD_LIBRARY_PATH', '')
        else:
            env.pop('LD_LIBRARY_PATH', None)
        # A separately launched copy of the frozen GUI must boot from scratch.
        # The askpass wrapper also sets this, so it works with older callers.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    elif 'LD_LIBRARY_PATH_ORIG' in env and '_PYI_APPLICATION_HOME_DIR' in env:
        env['LD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH_ORIG']
    elif '_PYI_APPLICATION_HOME_DIR' in env:
        env.pop('LD_LIBRARY_PATH', None)
    return env
