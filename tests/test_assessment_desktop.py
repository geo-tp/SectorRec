"""Real Tk guided-method choices; must not trigger invalid TestDisk analysis."""
import os
import unittest
from src.app import UI
from src.views.desktop_native import DesktopWindow

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Requires X11 test display')
class AssessmentDesktopTests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.window=DesktopWindow(self.ui)
        self.window.root.update()
    def tearDown(self):
        self.window.confirm_action = lambda *args, **kwargs: True
        self.window.close()

    def choose(self,source):
        self.ui.devices=[source]
        self.ui.selection=0
        self.ui.stage='desktop_methods'
        self.window.show(force=True)
        self.window.root.update()

    def test_bootable_iso_does_not_offer_invalid_testdisk_action(self):
        self.choose(dict(path='/dev/sdb',label='Tiny Core Linux',size=1024**3,
                         fs='ISO9660',mounted=True,supported=False,partition_disk=False))
        self.assertEqual([key for _,key,_ in self.window.method_cards],['signature'])
        self.assertNotIn('partition',self.window._method_available)
        self.assertEqual(self.ui.stage,'desktop_methods')
        self.window.select_method('signature',advance=True)
        self.assertEqual(self.ui.stage,'signature_setup')

    def test_ntfs_prompts_auto_unmount_only_after_start(self):
        self.choose(dict(path='/dev/sdb1',label='USB',size=2*1024**3,
                         fs='NTFS',mounted=True,supported=True,partition_disk=False))
        self.assertEqual([key for _,key,_ in self.window.method_cards],['metadata','signature'])
        self.window.select_method('metadata',advance=True)
        self.window.root.update()
        self.assertEqual(self.ui.stage,'scan_ready')
        self.assertEqual(str(self.window.footer.winfo_children()[-1]['text']),'Start metadata scan  →')
        self.assertEqual(self.ui.unmount_status,{})

    def test_unknown_entire_disk_recommends_partition_search(self):
        self.choose(dict(path='/dev/sdb',label='Damaged USB',size=2*1024**3,
                         fs='UNKNOWN',mounted=False,supported=False,partition_disk=True))
        self.assertEqual([key for _,key,_ in self.window.method_cards],['partition','signature'])
        self.window.select_method('partition',advance=True)
        self.assertEqual(self.ui.stage,'partition_ready')

if __name__=='__main__':unittest.main()
