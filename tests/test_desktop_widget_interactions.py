"""Desktop regressions: avoid flashes and keep mouse actions reachable.

Run under X11/Xvfb for real Tk geometry. Tests skip in headless unit runs.
"""
import os
import unittest

from src.app import UI
from src.views.desktop_native import DesktopWindow


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'Tk needs a display')
class DesktopWidgetTests(unittest.TestCase):
    def setUp(self):
        self.state=UI(demo=True, desktop=True)
        self.window=DesktopWindow(self.state)
        self.window.root.update()

    def tearDown(self):
        self.window.confirm_action = lambda *args, **kwargs: True
        self.window.close()

    def test_selection_does_not_destroy_device_rows(self):
        first=self.window._device_rows[0][0]
        second=self.window._device_rows[1][0]
        deepest=second.winfo_children()[0].winfo_children()[0].winfo_children()[0]
        deepest.event_generate('<Button-1>')
        self.window.root.update()
        self.assertEqual(self.state.selection,1)
        self.assertTrue(first.winfo_exists())
        self.assertTrue(second.winfo_exists())
        self.window.dispatch('DOWN')
        self.window.root.update()
        self.assertTrue(first.winfo_exists())
        self.assertEqual(self.state.selection,2)

    def test_hotplug_refresh_does_not_rebuild_full_page(self):
        body_before=list(self.window.body.winfo_children())
        device_before=self.window.device_list
        self.state.devices.append(dict(self.state.devices[-1], path='/dev/sdz1',label='New USB'))
        self.window.root.after_cancel(self.window._tick_after_id)
        self.window._tick_after_id=None
        self.window.tick()
        self.window.root.update()
        self.assertIs(device_before,self.window.device_list)
        self.assertEqual(len(self.window._device_rows),4)
        self.assertEqual(body_before,list(self.window.body.winfo_children()))

    def test_no_refresh_button_or_full_view_reload(self):
        self.assertFalse(any('Refresh' in str(child.cget('text')) for child in
                         self.window.footer.winfo_children() if hasattr(child,'cget') and 'text' in child.keys()))
        row=self.window._device_rows[1][0]
        self.assertIn('<Double-Button-1>',row.bind())
        self.window.select_source(1,True)
        self.window.root.update()
        self.assertEqual(self.state.stage,'desktop_methods')

    def test_real_double_click_events_advance(self):
        device=self.window._device_rows[1][0]
        text=device.winfo_children()[0].winfo_children()[0].winfo_children()[0]
        text.event_generate('<ButtonPress-1>',x=10,y=10,time=1000)
        text.event_generate('<ButtonRelease-1>',x=10,y=10,time=1010)
        self.assertEqual(self.state.stage,'devices')
        # Tk synthesizes Double-Button-1 for the second press. The first
        # click must not destroy its target, otherwise this would fail.
        text.event_generate('<ButtonPress-1>',x=10,y=10,time=1070)
        self.window.root.update()
        self.assertEqual(self.state.stage,'desktop_methods')
        self.assertEqual(self.state.selection,1)

    def test_method_buttons_reachable_and_functional(self):
        self.window.select_source(1,True)
        self.window.root.update()
        controls={key:card for card,key,_ in self.window.method_cards}
        for control in (controls['metadata'],controls['signature']):
            self.assertTrue(control.winfo_ismapped())
            self.assertGreater(control.winfo_height(),25)
            self.assertLessEqual(control.winfo_rooty()+control.winfo_height(),
                                 self.window.root.winfo_rooty()+self.window.root.winfo_height())
        self.window.select_method('metadata')
        self.window._method_continue.invoke()
        self.assertEqual(self.state.stage,'scan_ready')
        self.window.back_to_methods()
        self.window.select_method('signature')
        self.window._method_continue.invoke()
        self.assertEqual(self.state.stage,'signature_setup')

    def test_cards_accept_click_on_their_text(self):
        self.window.select_source(0,True)
        self.window.root.update()
        left_card=self.window.method_cards[0][0]
        heading=left_card.winfo_children()[0].winfo_children()[0].winfo_children()[1].winfo_children()[1]
        heading.event_generate('<Button-1>')
        self.window.root.update()
        self.assertEqual(self.state.stage,'desktop_methods')
        self.assertEqual(self.window._method_selection,'metadata')
        self.window._method_continue.invoke()
        self.assertEqual(self.state.stage,'scan_ready')

    def test_buttons_remain_visible_in_small_window(self):
        self.window.root.geometry('810x570+0+0')
        self.window.root.update()
        self.window.select_source(0,True)
        self.window.root.update()
        controls={key:card for card,key,_ in self.window.method_cards}
        self.assertTrue(controls['metadata'].winfo_ismapped())
        self.assertTrue(controls['signature'].winfo_ismapped())
        self.assertTrue(self.window._method_continue.winfo_ismapped())
        self.assertLessEqual(self.window._method_continue.winfo_rooty()+self.window._method_continue.winfo_height(),
                             self.window.root.winfo_rooty()+self.window.root.winfo_height())

if __name__=='__main__':unittest.main()
