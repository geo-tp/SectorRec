"""Alpha4 visual and diagnostic regressions; no real device needed."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import device_diagnostics as diagnostics
from src.app import UI
from src.views.desktop_native import DesktopWindow, BACKGROUND


class PassiveSmartTests(unittest.TestCase):
    def test_all_vendor_ata_attributes_remain_visible(self):
        data={'smart_status':{'passed':True},'ata_smart_attributes':{'table':[
            {'id':5,'name':'Reallocated_Sector_Ct','value':100,'worst':90,'thresh':36,'raw':{'value':2}},
            {'id':199,'name':'UDMA_CRC_Error_Count','raw':{'string':'7'}}]}}
        report=diagnostics.interpret_smart(data,{'size':42})
        rows=dict(diagnostics.smart_attribute_lines(report))
        self.assertIn('Raw: 2',rows['5 · Reallocated_Sector_Ct'])
        self.assertIn('Raw: 7',rows['199 · UDMA_CRC_Error_Count'])
        self.assertEqual(report.smart_data,data)

    def test_nvme_fields_are_not_silently_omitted(self):
        data={'nvme_smart_health_information_log':{'percentage_used':33,
               'available_spare':98,'media_errors':2,'unsafe_shutdowns':12}}
        report=diagnostics.interpret_smart(data)
        lines=dict(diagnostics.smart_operational_lines(report))
        self.assertEqual(lines['NVMe · media_errors'],'2')
        self.assertEqual(lines['NVMe · available_spare'],'98')

    def test_extended_query_is_passive(self):
        source={'disk_path':'/dev/sdb','path':'/dev/sdb1'}
        class Response:
            stdout='{"ata_smart_attributes":{"table":[]}}'
        with patch.object(diagnostics.shutil,'which',return_value='/usr/sbin/smartctl'), \
             patch.object(diagnostics.os,'geteuid',return_value=0), \
             patch.object(diagnostics.subprocess,'run',return_value=Response()) as run:
            data,error=diagnostics.detailed_smart(source)
        self.assertIsInstance(data,dict)
        cmd=run.call_args.args[0]
        self.assertIn('-x',cmd)
        self.assertIn('-n',cmd)
        for forbidden in ('-t','-s','--test','-C','-X'):
            self.assertNotIn(forbidden,cmd)
        self.assertEqual(cmd[-1],'/dev/sdb')

@unittest.skipUnless(os.environ.get('DISPLAY'), 'Tk needs X')
class DesktopPolishTests(unittest.TestCase):
    def setUp(self):
        self.ui=UI(demo=True,desktop=True)
        self.window=DesktopWindow(self.ui)
        self.window.root.update()
    def tearDown(self):
        self.window.confirm_action = lambda *args, **kwargs: True
        self.window.close()
    def test_logo_is_high_res_transparent(self):
        self.assertTrue(hasattr(self.window,'_brand_image'))
        self.assertEqual(self.window._brand_image.width(),64)
    def test_no_recommendation_in_method_selection(self):
        self.window.select_source(1,True)
        self.window.root.update()
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        from tkinter import Label
        texts=[item.cget('text') for item in children(self.window.body) if isinstance(item,Label)]
        self.assertNotIn('RECOMMENDED',' '.join(texts).upper())
        cards={key:card for card,key,_ in self.window.method_cards}
        self.assertEqual(cards['metadata'].cget('bg'), cards['signature'].cget('bg'))
    def test_mouse_wheel_inside_device_row_scrolls(self):
        self.ui.devices.extend([dict(self.ui.devices[0],path=f'/dev/sd{i}',label=f'Disk {i}') for i in range(15)])
        self.window._render_devices()
        self.window.root.update()
        child=self.window._device_rows[0][1]
        before=self.window.device_canvas.yview()[0]
        child.event_generate('<Button-5>',x=8,y=8)
        self.window.root.update()
        self.assertGreater(self.window.device_canvas.yview()[0],before)
    def test_recovery_report_shows_four_statuses(self):
        self.ui.status={'recovered':3,'partial':1,'unverified':2,'failed':1,'outdir':'/tmp/out'}
        self.ui.stage='complete'
        self.window.show(force=True)
        self.window.root.update()
        from tkinter import Label
        def walk(root):
            for child in root.winfo_children():
                if isinstance(child,Label): yield child.cget('text')
                yield from walk(child)
        full=' '.join(walk(self.window.body))
        for term in ('Recovered','Incomplete','Unverified','Failed','✓','✕'):
            self.assertIn(term,full)

if __name__=='__main__': unittest.main()
