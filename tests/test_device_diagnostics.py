"""No live physical disk is accessed by these tests."""
import time
import unittest
from dataclasses import asdict
from src.services import device_diagnostics as dd
from src.controllers.controller import Controller

SOURCE={'path':'/dev/sdz1','disk_path':'/dev/sdz','label':'USB Test Disk',
        'size':1_000_000_000,'fs':'NTFS','transport':'USB','model':'Example',
        'revision':'1.2','logical_sector':512,'physical_sector':4096,
        'partition_table':'gpt'}

class DiagnosticsTests(unittest.TestCase):
    def test_unknown_is_not_good_health(self):
        r=dd.inspect(SOURCE,smart_reader=lambda _:({},''))
        self.assertEqual(r.health,'unavailable')
        self.assertNotIn('passed',r.note.lower())

    def test_no_smart_flash_drive(self):
        r=dd.inspect(SOURCE,smart_reader=lambda _:(None,'USB bridge has no SMART'))
        self.assertEqual(r.health,'unavailable')
        self.assertIn('USB bridge',r.note)

    def test_ata_pass_and_warning_for_bad_sectors(self):
        data={'smart_status':{'passed':True},
              'temperature':{'current':32},'power_on_time':{'hours':421},
              'ata_smart_attributes':{'table':[{'id':5,'raw':{'value':2}},
                                               {'id':197,'raw':{'value':1}},
                                               {'id':198,'raw':{'value':0}}]}}
        r=dd.interpret_smart(data,SOURCE)
        self.assertEqual((r.health,r.reallocated,r.pending,r.temperature_c),('warning',2,1,32))

    def test_ata_plain_pass(self):
        r=dd.interpret_smart({'smart_status':{'passed':True}, 'model_name':'Good Disk'},SOURCE)
        self.assertEqual(r.health,'passed')
        self.assertEqual(r.model,'Good Disk')

    def test_nvme_critical_warning(self):
        r=dd.interpret_smart({'nvme_smart_health_information_log':{'critical_warning':1,
                      'percentage_used':95,'media_errors':3,'power_on_hours':8000}},SOURCE)
        self.assertEqual(r.health,'failed')
        self.assertEqual(r.reported_errors,3)

    def test_nvme_endurance_warning(self):
        r=dd.interpret_smart({'smart_status':{'passed':True},
                'nvme_smart_health_information_log':{'percentage_used':102}},SOURCE)
        self.assertEqual(r.health,'warning')

    def test_smart_exit_bit_three_is_failure(self):
        r=dd.interpret_smart({'smartctl':{'exit_status':8}},SOURCE)
        self.assertEqual(r.health,'failed')

    def test_drive_image_does_not_call_smart(self):
        src={**SOURCE,'is_image':True}
        r=dd.inspect(src,smart_reader=lambda _ : self.fail('SMART should not be queried'))
        self.assertEqual(r.health,'unavailable')
        self.assertIn('image',r.note.lower())

    def test_fields_exclude_unavailable_numbers(self):
        lines=dict(dd.report_lines(dd.base_report(SOURCE)))
        self.assertEqual(lines['Health'],'SMART unavailable')
        self.assertNotIn('Temperature',lines)
        self.assertNotIn('Reallocated',lines)

    def test_no_tests_or_writes_in_smart_arguments(self):
        class FakeResult:
            stdout='{"smart_status":{"passed":true}}'
        calls=[]
        from unittest.mock import patch
        with patch.object(dd.shutil,'which',return_value='/usr/sbin/smartctl'),\
             patch.object(dd.subprocess,'run',side_effect=lambda cmd,**kw:(calls.append(cmd) or FakeResult())),\
             patch.object(dd.os,'geteuid',return_value=0):
            data,error=dd._read_smart(SOURCE)
        self.assertEqual(error,'')
        self.assertTrue(data['smart_status']['passed'])
        self.assertIn('-n',calls[0])
        self.assertNotIn('-t',calls[0]);self.assertNotIn('-s',calls[0]);self.assertNotIn('-x',calls[0])

    def test_controller_async_no_stale_result(self):
        from unittest.mock import patch
        ctrl=Controller(demo=True,desktop=True)
        ctrl.selection=0
        def fake(source,**kw):
            if source['path']=='/dev/sda1':time.sleep(0.05)
            return dd.base_report(source)
        with patch('src.controllers.controller.inspect_drive',side_effect=fake):
            ctrl.request_diagnostics()
            ctrl.selection=1
            ctrl.request_diagnostics()
            ctrl.diagnostic_thread.join(timeout=1)
            time.sleep(0.1)
        self.assertEqual(ctrl.diagnostic_report.model,'External HDD')
        ctrl.close()

if __name__=='__main__':unittest.main()

class SourceClassificationTests(unittest.TestCase):
    def test_bootable_iso_does_not_offer_testdisk(self):
        from src.services.source_assessment import assess_source
        for item in ({**SOURCE,'fs':'ISO9660','partition_disk':True},
                     {**SOURCE,'fs':'DISK','partition_disk':True,
                      'child_filesystems':('ISO9660','VFAT')}):
            check=assess_source(item,desktop=True)
            self.assertFalse(check.allows('partition'))
            self.assertFalse(check.allows('metadata'))
            self.assertIn('boot',check.headline.lower())

    def test_unrecognized_usb_smart_not_a_failure(self):
        from src.services.source_assessment import assess_source
        source={**SOURCE,'fs':'UNKNOWN','partition_disk':True,'supported':False}
        check=assess_source(source,desktop=True)
        self.assertTrue(check.allows('partition'))
        self.assertFalse(check.allows('metadata'))

    def test_diagnostic_only_inventory_cannot_recover(self):
        from src.services.source_assessment import assess_source
        report=assess_source({**SOURCE,'diagnostics_only':True},desktop=True)
        self.assertFalse(report.methods)
        self.assertIn('information',report.headline.lower())
