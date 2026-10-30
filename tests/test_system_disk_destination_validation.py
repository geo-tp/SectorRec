"""System-drive destination safety: Windows UI must not mark unknown as safe."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from src.controllers.controller import Controller
from src.views.desktop_native import DesktopWindow

INITIAL_DISPLAY = os.environ.get("DISPLAY")

class SystemDestinationValidation(unittest.TestCase):
    def test_metadata_screen_waits_for_strict_windows_disk_check(self):
        with tempfile.TemporaryDirectory() as td:
            ui=Controller(demo=True,desktop=True)
            ui.devices=[{'path':r'\\.\C:', 'is_system': True, 'supported':True, 'label':'System', 'size':1024}]
            ui.selection=0; ui.metadata_destination=td; ui.stage='scan_ready'
            gate=threading.Event(); entered=threading.Event()
            window=None
            def strict(source, folder):
                entered.set(); gate.wait(2)
                raise ValueError('Destination belongs to the source physical disk.')
            try:
                with patch.dict(os.environ, {'DISPLAY': INITIAL_DISPLAY} if INITIAL_DISPLAY else {}), \
                     patch('src.views.desktop_native.sys.platform','win32'), \
                     patch('src.infrastructure.windows_raw.quick_same_windows_disk',return_value=None), \
                     patch('src.services.signature.validate_destination',side_effect=strict):
                    window=DesktopWindow(ui)
                    ui.stage='scan_ready'; window.show(force=True)
                    self.assertTrue(entered.wait(2))
                    state=window._destination_validation
                    self.assertIsNotNone(state)
                    self.assertFalse(state['done'])
                    # Forbidden until checked, even when native lookup is unknown.
                    self.assertFalse(window._windows_destination_validation('metadata',td)[0])
                    with patch.object(window, 'show_modal_notice') as warning, \
                         patch.object(ui, 'metadata_preflight') as strict_start:
                        window.start_metadata_scan()
                        warning.assert_called_once()
                        strict_start.assert_not_called()
                    gate.set()
                    for _ in range(25):
                        window.root.update(); import time; time.sleep(.015)
                        if state['done']: break
                    self.assertTrue(state['done'])
                    self.assertFalse(window._windows_destination_validation('metadata',td)[0])
                    self.assertIn('source physical disk', state['error'])
            finally:
                gate.set()
                if window: window.root.destroy()
                ui.close()

    def test_photo_same_disk_is_rejected_immediately(self):
        with tempfile.TemporaryDirectory() as td:
            ui=Controller(demo=True,desktop=True)
            ui.devices=[{'path':r'\\.\PhysicalDrive0', 'is_system': True, 'supported':True, 'label':'System', 'size':1024}]
            ui.selection=0; ui.signature_destination=td; ui.stage='signature_setup'
            window=None
            try:
                with patch.dict(os.environ, {'DISPLAY': INITIAL_DISPLAY} if INITIAL_DISPLAY else {}), \
                     patch('src.views.desktop_native.sys.platform','win32'), \
                     patch('src.infrastructure.windows_raw.quick_same_windows_disk',return_value=True):
                    window=DesktopWindow(ui)
                    self.assertEqual(window._windows_destination_validation('signature',td)[0],False)
                    self.assertTrue(window._destination_validation['done'])
                    self.assertIn('source physical disk',window._destination_validation['error'])
            finally:
                if window: window.root.destroy()
                ui.close()
