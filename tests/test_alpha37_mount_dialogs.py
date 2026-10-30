"""Alpha37: no stock dialogs in the desktop image mounting workflow."""
import os
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from tkinter import ttk

from src.app import UI
from src.views.desktop_native import DesktopWindow, BACKGROUND, SURFACE, AMBER, TEAL


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk display needed')
class MountDialogs(unittest.TestCase):
    def setUp(self):
        self.win = DesktopWindow(UI(demo=True, desktop=True))
        self.win.root.update_idletasks()

    def tearDown(self):
        # Closing would trigger a confirmation if a mocked mount is still present.
        self.win._image_mount = None
        self.win.root.destroy()

    @staticmethod
    def descendants(widget):
        children = widget.winfo_children()
        return children + [nested for child in children for nested in MountDialogs.descendants(child)]

    def _assert_notice(self, title, text, *, warning):
        observations = {}

        def check_and_close():
            dialogs = [w for w in self.win.root.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertEqual(len(dialogs), 1)
            dialog = dialogs[0]
            self.assertEqual(dialog.cget('bg'), BACKGROUND)
            self.assertEqual(dialog.title(), title)
            children = self.descendants(dialog)
            bodies = [w for w in children if isinstance(w, tk.Text)]
            self.assertEqual(len(bodies), 1)
            self.assertEqual(bodies[0].cget('bg'), SURFACE)
            self.assertEqual(bodies[0].cget('state'), 'disabled')
            self.assertEqual(bodies[0].get('1.0', 'end-1c'), text)
            headings = [w for w in children if isinstance(w, tk.Label) and w.cget('text') == title]
            self.assertEqual(len(headings), 1)
            self.assertEqual(headings[0].cget('fg'), AMBER if warning else TEAL)
            ok = [w for w in children if isinstance(w, tk.Button) and w.cget('text') == 'OK']
            self.assertEqual(len(ok), 1)
            observations['tested'] = True
            ok[0].invoke()

        self.win.root.after(30, check_and_close)
        self.win.show_modal_notice(title, text, warning=warning)
        self.assertTrue(observations.get('tested'))
        self.assertIsNone(self.win.root.grab_current())

    def test_warning_notice_matches_app(self):
        self._assert_notice('Mount not available', 'Unrecognized filesystem.', warning=True)

    def test_success_notice_matches_app(self):
        self._assert_notice('Image mounted', 'Mounted read-only:\n/media/test volume', warning=False)

    def test_long_diagnostics_scroll_and_escape(self):
        message='\n'.join(f'Partition {n}: cannot mount /media/path-{n}' for n in range(32))
        observations = {}
        def inspect():
            dialog = next(w for w in self.win.root.winfo_children() if isinstance(w, tk.Toplevel))
            children = self.descendants(dialog)
            observations['scrollbar'] = bool([w for w in children if isinstance(w, ttk.Scrollbar)])
            observations['height'] = dialog.winfo_height()
            dialog.event_generate('<Escape>')
        self.win.root.after(30, inspect)
        self.win.show_modal_notice('Mount not available', message, warning=True)
        self.assertTrue(observations['scrollbar'])
        self.assertLess(observations['height'], self.win.root.winfo_screenheight())

    def test_mount_success_and_failure_use_notice_not_messagebox(self):
        from src.infrastructure.image_mount import ImageMountError
        class Session:
            def __init__(self, _path): self.image = __import__('pathlib').Path(_path).resolve(); self.loop = None
            def mount(self): return ['/tmp/destination'], []
        self.win.ui.devices = [{'path': '/tmp/test.iso', 'is_image': True}]
        self.win.ui.selection = 0
        self.win.ui.stage = 'desktop_methods'
        for exception,expected in [(None,('Image mounted',False)),(ImageMountError('Bad image'),('Mount not available',True))]:
            class FakeSession(Session):
                def mount(self):
                    if exception: raise exception
                    return super().mount()
            calls=[]
            self.win._image_mount=None
            class InlineThread:
                def __init__(self, target, **kwargs): self.target = target
                def start(self): self.target()
            with patch('src.views.desktop_native.threading.Thread',InlineThread), \
                 patch('src.infrastructure.image_mount.ImageMountSession',FakeSession), \
                 patch.object(self.win,'confirm_action',return_value=True), \
                 patch.object(self.win,'show_modal_notice',side_effect=lambda title,message,warning=False:calls.append((title,warning))), \
                 patch.object(self.win,'_open_folder',return_value=None), \
                 patch('tkinter.messagebox.showinfo',side_effect=AssertionError('stock info dialog')), \
                 patch('tkinter.messagebox.showwarning',side_effect=AssertionError('stock warning dialog')):
                self.win.toggle_image_mount()
                deadline=time.monotonic()+4
                while self.win._image_mount_busy and time.monotonic()<deadline:
                    self.win.root.update(); time.sleep(0.01)
                self.assertFalse(self.win._image_mount_busy)
                self.assertEqual(calls[0],expected)

if __name__ == '__main__': unittest.main()
