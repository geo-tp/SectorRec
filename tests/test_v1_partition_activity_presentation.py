"""Presentation regressions: TestDisk activity and concise same-disk warnings."""
import os
import unittest
from unittest.mock import patch

from src.views.partition_activity import (
    partition_activity_display, LOG_PAUSED_SECONDS, POSSIBLE_IDLE_SECONDS,
)
from src.views.desktop_native import DesktopWindow, TEAL, DANGER
_INITIAL_DISPLAY = os.environ.get('DISPLAY')
from src.app import UI


class PartitionActivityPresentationTests(unittest.TestCase):
    def test_active_reading_over_capacity_is_not_a_progress_percent_or_stall(self):
        data = dict(testdisk_io_bytes=895_000_000, testdisk_io_idle_seconds=0,
                    testdisk_log_bytes=567, testdisk_log_idle_seconds=150,
                    testdisk_phase='Analyzing source')
        result = partition_activity_display(data, 155, 'win32')
        self.assertIn('853.5',result['read_value'])
        self.assertIn('not scan progress',result['read_detail'])
        self.assertFalse(result['log_warn'])
        self.assertFalse(result['caution'])
        self.assertIn('reads still active',result['log_detail'])

    def test_log_warning_is_delayed_and_requires_no_activity(self):
        status = dict(testdisk_io_bytes=4096, testdisk_io_idle_seconds=160,
                      testdisk_log_bytes=567, testdisk_log_idle_seconds=160)
        early = partition_activity_display(status, 70, 'win32')
        self.assertFalse(early['log_warn'])
        self.assertFalse(early['caution'])
        mid = partition_activity_display({**status,'testdisk_log_idle_seconds':90}, 90, 'win32')
        self.assertIn('Log unchanged', mid['log_detail'])
        self.assertFalse(mid['log_warn'])
        late = partition_activity_display(status, 160, 'win32')
        self.assertTrue(late['log_warn'])
        self.assertIn('may be waiting', late['caution'])

    def test_other_platforms_do_not_claim_log_or_progress(self):
        status = {'testdisk_io_bytes':123456}
        for plat in ('linux', 'unsupported'):
            result = partition_activity_display(status, 30, plat)
            self.assertEqual(result['log_value'],'Running')
            self.assertNotIn('%', result['read_value'])
            self.assertEqual(result['caution'],'')
        self.assertTrue(LOG_PAUSED_SECONDS >= 90)
        self.assertTrue(POSSIBLE_IDLE_SECONDS >= LOG_PAUSED_SECONDS)

    def test_same_disk_message_short_and_clear(self):
        text = DesktopWindow._explain_destination_error('Destination belongs to the source physical disk.')
        self.assertIn('SAME PHYSICAL DISK', text)
        self.assertIn('another partition', text)
        self.assertIn('overwrite', text)
        self.assertLess(len(text), 250)


@unittest.skipUnless(os.name == 'nt' or os.environ.get('DISPLAY'), 'Tk display required')
class PartitionActivityDesktopWidgetTests(unittest.TestCase):
    def setUp(self):
        self._display_patch = patch.dict(os.environ, {'DISPLAY': _INITIAL_DISPLAY} if _INITIAL_DISPLAY else {})
        self._display_patch.start()
        self.addCleanup(self._display_patch.stop)
        self.ui=UI(demo=True,desktop=True)
        self.window=DesktopWindow(self.ui)
        self.window.root.update()

    def tearDown(self):
        self.ui.stage = 'devices'
        self.window.close()

    def test_tiles_update_without_recreating_widgets(self):
        self.ui.stage='partition_scanning'
        self.ui.partition_status={'started':__import__('time').monotonic()-160,
                                  'testdisk_io_bytes':895_000_000,
                                  'testdisk_io_idle_seconds':0,
                                  'testdisk_log_bytes':567,
                                  'testdisk_log_idle_seconds':155}
        with patch('src.views.desktop_native.sys.platform','win32'):
            self.window.show(force=True)
            self.window.root.update_idletasks()
            tile=self.window.dynamic['partition_read']
            self.assertIn('853.5',tile.cget('text'))
            self.assertEqual(self.window.dynamic['partition_caution'].cget('text'),'')
            self.ui.partition_status['testdisk_io_idle_seconds']=155
            self.window.update_dynamic()
            self.assertIn('may be waiting',self.window.dynamic['partition_caution'].cget('text'))
            self.assertIs(tile,self.window.dynamic['partition_read'])
