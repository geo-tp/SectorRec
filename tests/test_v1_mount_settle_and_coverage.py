"""Safe Linux mount-state settle and consistent result labels; no real disks."""
import unittest
from unittest.mock import Mock, patch

from src.controllers.controller import Controller
from src.infrastructure.linux_devices import wait_for_unmounted_source
from src.views.view import PillowView
from PIL import Image, ImageDraw


class MountSettleTests(unittest.TestCase):
    @staticmethod
    def simulate(states, *, size=1024):
        clock = [0.0]
        calls = [0]
        def devices():
            idx = min(calls[0], len(states)-1)
            calls[0] += 1
            return [{'path':'/dev/sdz1', 'size':size, 'mounted':states[idx]}]
        def advance(dt):
            clock[0] += dt
        return devices, lambda: clock[0], advance, calls

    def test_requires_multiple_unmounted_observations_over_one_second(self):
        supplier, now, sleep, calls = self.simulate([False]*10)
        wait_for_unmounted_source('/dev/sdz1', supplier,
                                  clock=now, sleep=sleep, expected={'size':1024})
        self.assertGreaterEqual(calls[0], 5)
        self.assertGreaterEqual(now(), 1.1)

    def test_remount_mid_settle_aborts_scan(self):
        supplier, now, sleep, _ = self.simulate([False,False,True,False])
        with self.assertRaisesRegex(RuntimeError,'mounted again'):
            wait_for_unmounted_source('/dev/sdz1', supplier, clock=now, sleep=sleep)

    def test_disappearing_device_aborts_scan(self):
        with self.assertRaisesRegex(RuntimeError, 'disappeared'):
            wait_for_unmounted_source('/dev/sdz1', lambda: [], clock=lambda:0, sleep=lambda _:None)

    def test_changed_device_size_aborts_scan(self):
        supplier, now, sleep, _ = self.simulate([False], size=2048)
        with self.assertRaisesRegex(RuntimeError, 'size changed'):
            wait_for_unmounted_source('/dev/sdz1', supplier, clock=now, sleep=sleep,
                                      expected={'size':1024})

    def test_cardputer_preflights_even_when_device_looks_unmounted(self):
        platform=Mock(name='LinuxPlatform', name_attr='linux')
        platform.name='linux'
        ui=Controller(demo=False, desktop=False, platform=platform)
        try:
            ui.devices=[{'path':'/dev/sdz1','size':1024,'mounted':False,'supported':True,'fs':'VFAT'}]
            ui.selection=0
            with patch.object(ui, 'begin_unmount') as prepare, patch.object(ui, 'begin_scan') as scan:
                ui.start_selected_scan()
                prepare.assert_called_once_with()
                scan.assert_not_called()
        finally:
            ui.close()

    def test_cardputer_partition_search_preflights_unmounted_device(self):
        platform=Mock(name='LinuxPlatform')
        platform.name='linux'
        ui=Controller(demo=False, desktop=False, platform=platform)
        try:
            ui.devices=[{'path':'/dev/sdz','size':1024,'mounted':False,
                         'partition_disk':True, 'supported':False,'fs':'DISK'}]
            ui.selection=0
            with patch.object(ui, 'begin_unmount') as prepare, patch.object(ui, 'begin_partition_search') as search:
                ui.start_partition_search()
                prepare.assert_called_once_with(next_action='partition_search')
                search.assert_not_called()
        finally:
            ui.close()


class OutcomeLabelTests(unittest.TestCase):
    def test_cardputer_done_with_skipped_directories_is_limited_coverage(self):
        ui=Controller(demo=True, desktop=False)
        try:
            ui.stage='scanning'
            ui.status={'state':'done', 'count':1234,'limited_dirs':True}
            ui.tick()
            self.assertTrue(ui.scan_partial)
            self.assertEqual(ui.scan_result_label,'LIMITED COVERAGE')
            self.assertIn('128',ui.scan_partial_reason)
            view=PillowView(ui)
            calls=[]
            with patch.object(view,'label',side_effect=lambda _canvas, pos, val, *args, **kwargs: calls.append(str(val))):
                view.render()  # Render badge in the Cardputer view
            self.assertIn('LIMITED COVERAGE', calls)
            self.assertNotIn('PARTIAL SCAN', calls)
        finally:
            ui.close()

    def test_cardputer_cancelled_or_entry_limited_is_partial(self):
        for state in ('cancelled','limited','error'):
            ui=Controller(demo=True, desktop=False)
            try:
                ui.stage='scanning'
                ui.status={'state':state, 'count':500, 'limited':state == 'limited'}
                ui.tick()
                self.assertEqual(ui.scan_result_label,'PARTIAL SCAN')
            finally:
                ui.close()


if __name__ == '__main__':
    unittest.main()
