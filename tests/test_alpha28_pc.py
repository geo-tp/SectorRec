import os
import tempfile
import unittest
from pathlib import Path

from src.services.signature import extraction_totals
from src.models.core import create_output_directory

class PhotoRecMetadataTests(unittest.TestCase):
    def test_internal_xml_not_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            prefix=root/'SectorRec_20261027'
            output=root/'SectorRec_20261027.1'
            output.mkdir()
            (output/'report.xml').write_bytes(b'metadata')
            (output/'f001234.jpg').write_bytes(b'abc')
            recent=[]
            self.assertEqual(extraction_totals(root,prefix,recent),(1,3))
            self.assertEqual([item[0] for item in recent],['f001234.jpg'])

    def test_metadata_output_uses_chosen_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            folder=create_output_directory(Path.home(),'USB', destination_root=base)
            self.assertEqual(folder.parent, base)
            self.assertTrue(folder.exists())

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk needs a display')
class DesktopAlpha28Tests(unittest.TestCase):
    def test_methods_require_selection(self):
        from src.app import UI
        from src.views.desktop_native import DesktopWindow
        ui=UI(demo=True,desktop=True)
        window=DesktopWindow(ui)
        try:
            window.select_source(1,True)
            self.assertEqual(ui.stage,'desktop_methods')
            self.assertEqual(window._method_continue.cget('state'),'disabled')
            available=window._method_available
            selected=next(key for key in ('metadata','signature','partition') if key in available)
            window.select_method(selected)
            self.assertEqual(ui.stage,'desktop_methods')
            self.assertEqual(window._method_continue.cget('state'),'normal')
            window.continue_method()
            self.assertNotEqual(ui.stage,'desktop_methods')
        finally: window.close()

if __name__=='__main__':unittest.main()
