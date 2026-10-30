"""Alpha32 hover feedback / fade tests; desktop only."""
import os
import unittest
from unittest.mock import patch

from src.views.desktop_native import DesktopWindow, _HoverTip

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk GUI tests require DISPLAY')
class DesktopAlpha32HoverTests(unittest.TestCase):
    def setUp(self):
        from src.app import UI
        self.win=DesktopWindow(UI(demo=True,desktop=True))
        self.win.root.update()

    def tearDown(self):
        self.win.confirm_action = lambda *args, **kwargs: True
        self.win.close()

    def test_hover_delayed_shows_popup_and_destroys_on_leave(self):
        btn=self.win.button(self.win.body,'Browse…',lambda:None,
                            emphasis=True,tooltip='Choose a recovery destination.')
        btn.pack()
        self.win.root.update()
        tip=btn._sector_hover_tip
        tip._schedule()
        self.assertIsNotNone(tip._pending)
        self.assertIsNone(tip._popup)
        tip._show()
        self.win.root.update()
        self.assertIsNotNone(tip._popup)
        self.assertTrue(tip._popup.winfo_exists())
        tip.hide()
        self.assertIsNone(tip._popup)
        self.assertIsNone(tip._pending)
        btn.destroy()

    def test_empty_destination_label_has_tooltip_and_click(self):
        import tkinter as tk
        calls=[]
        label=self.win.destination_hint(self.win.body,'Choose a folder…',lambda:calls.append(1))
        label.pack()
        self.win.root.update()
        tip=label._sector_hover_tip
        tip._schedule()
        self.assertIsNotNone(tip._pending)
        tip.hide()
        label.event_generate('<Button-1>')
        self.assertEqual(calls,[1])

    def test_disabled_button_has_no_hover_and_emphasized_button_is_distinct(self):
        normal=self.win.button(self.win.body,'Normal',lambda:None)
        emph=self.win.button(self.win.body,'Browse…',lambda:None,
                              emphasis=True,tooltip='Choose directory')
        self.assertNotEqual(normal.cget('background'),emph.cget('background'))
        self.assertEqual(emph.cget('relief'),'solid')
        emph.configure(state='disabled')
        emph._sector_hover_tip._schedule()
        self.assertIsNone(emph._sector_hover_tip._pending)
        self.win.root.update()
        normal.destroy(); emph.destroy()

    def test_transition_stays_opaque_without_fade_timers(self):
        self.win._soft_page_transition()
        self.assertEqual(self.win._fade_after_ids,[])
        self.win.root.update()
        self.assertEqual(float(self.win.root.attributes('-alpha')),1.0)
        self.win.root.after(230,self.win.root.quit)
        self.win.root.mainloop()
        self.assertAlmostEqual(float(self.win.root.attributes('-alpha')),1.0,places=2)

    def test_page_recreation_cancels_tooltip(self):
        tip_holder=self.win.button(self.win.body,'Browse…',lambda:None,tooltip='Some information')
        tip_holder.pack()
        tip=tip_holder._sector_hover_tip
        tip._schedule()
        self.win.show(force=True)
        self.win.root.update()
        self.assertIsNone(tip._pending)
        self.assertIsNone(tip._popup)

if __name__ == '__main__': unittest.main()
