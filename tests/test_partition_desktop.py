"""New TestDisk GUI controls, exercised with real Tk events under Xvfb."""
import os
import unittest
from src.app import UI
from src.views.desktop_native import DesktopWindow

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Requires an X display')
class PartitionDesktopTests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.ui.devices.insert(0,{'label':'SanDisk [WHOLE DISK]','path':'/dev/sda',
               'name':'sda','size':4*1024**3,'fs':'DISK','transport':'USB',
               'mounted':False,'supported':False,'partition_disk':True})
        self.window=DesktopWindow(self.ui)
        self.window.root.update()

    def tearDown(self):
        self.window.confirm_action = lambda *args, **kwargs: True
        self.window.close()

    def test_method_availability_matches_selected_source(self):
        self.window.select_source(0, True)
        self.window.root.update()
        self.assertEqual({key for _,key,_ in self.window.method_cards}, {'partition','signature'})
        self.window.select_method('partition')
        self.window._method_continue.invoke()
        self.assertEqual(self.ui.stage,'partition_ready')

    def test_full_disk_partition_flow_is_mouse_navigable(self):
        self.window.select_source(0, True)
        self.window.select_method('partition')
        self.window._method_continue.invoke()
        self.window.root.update()
        self.window.dispatch('OK')
        self.window.root.update()
        self.assertEqual(self.ui.stage,'partition_scanning')
        self.ui.tick()
        self.window.show(force=True)
        self.assertEqual(self.ui.stage,'partition_results')
        self.assertEqual(len(self.window.partition_tree.get_children()),1)
        self.window.open_partition()
        self.window.root.update()
        self.assertEqual(self.ui.stage,'scan_ready')
        self.assertIsNotNone(self.ui.partition_selected)

    def test_partition_method_disabled_on_individual_partition(self):
        self.window.select_source(1,True)
        self.window.root.update()
        self.assertEqual({key for _,key,_ in self.window.method_cards}, {'metadata','signature'})
        self.window.select_method('partition')
        self.assertIsNone(self.window._method_selection)
        self.assertEqual(str(self.window._method_continue['state']),'disabled')

if __name__=='__main__':unittest.main()
