"""Regression coverage for legible scan timers and PhotoRec recency preview."""
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from src.views.elapsed import format_elapsed
from src.views.partition_activity import partition_activity_display
from src.services.signature import extraction_totals

DISPLAY_AT_START = os.environ.get('DISPLAY')


@pytest.mark.parametrize('seconds,desktop,compact', [
    (0, '0s', '0s'),
    (59.9, '59s', '59s'),
    (60, '1m 00s', '1m00s'),
    (203, '3m 23s', '3m23s'),
    (3599, '59m 59s', '59m59s'),
    (3600, '1h 00m 00s', '1h00m00s'),
    (43205, '12h 00m 05s', '12h00m05s'),
    (-100, '0s', '0s'),
])
def test_elapsed_formats(seconds, desktop, compact):
    assert format_elapsed(seconds) == desktop
    assert format_elapsed(seconds, compact=True) == compact


def test_testdisk_clock_and_log_age_use_human_time():
    status = {'testdisk_io_bytes': 1000, 'testdisk_io_idle_seconds': 0,
              'testdisk_log_bytes': 567, 'testdisk_log_idle_seconds': 203}
    display = partition_activity_display(status, 43205, 'win32')
    assert display['clock'] == '12h 00m 05s ELAPSED'
    assert 'reads still active' in display['log_detail']
    assert not display['log_warn']  # Reads have not stopped.
    paused = partition_activity_display({'testdisk_log_bytes': 567,
                                         'testdisk_log_idle_seconds': 100}, 100, 'win32')
    assert '1m 40s' in paused['log_detail']


def test_recent_files_source_is_timestamped_without_following_symlinks(tmp_path):
    output = tmp_path / 'SectorRec_ABC.1'
    output.mkdir()
    older = output / 'old.jpg'
    newer = output / 'new.jpg'
    older.write_bytes(b'old')
    newer.write_bytes(b'new')
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(5_000_000_000, 5_000_000_000))
    latest = []
    assert extraction_totals(tmp_path, tmp_path / 'SectorRec_ABC', latest) == (2, 6)
    assert sorted(latest, key=lambda row: (row[3], row[2]), reverse=True)[0][0] == 'new.jpg'


@pytest.mark.skipif(not DISPLAY_AT_START and os.name != 'nt', reason='Tk display required')
def test_photorec_live_preview_newest_first_and_column_sort_still_works():
    from src.app import UI
    from src.views.desktop_native import DesktopWindow

    with mock.patch.dict(os.environ, {'DISPLAY': DISPLAY_AT_START} if DISPLAY_AT_START else {}):
        ui = UI(demo=True, desktop=True)
        view = DesktopWindow(ui)
        try:
            ui.stage = 'signature_running'
            ui.signature_format = 'all'
            # Deliberately shuffled: GUI should not depend on filesystem traversal order.
            ui.signature_status = {
                'state': 'running', 'started': time.monotonic()-66,
                'files': 3, 'written': 63, 'free': 123456789,
                'recent_files': (
                    ('older.jpg', 20, '/tmp/older.jpg', 10),
                    ('newest.jpg', 30, '/tmp/newest.jpg', 30),
                    ('middle.jpg', 13, '/tmp/middle.jpg', 20),
                ),
            }
            view.show(force=True)
            view._refresh_live(force=True)
            def names():
                return [view.live_tree.item(i, 'values')[0] for i in view.live_tree.get_children()]
            assert names() == ['newest.jpg', 'middle.jpg', 'older.jpg']
            assert '1m 06s' in view.dynamic['secondary'].cget('text')
            view.sort_table('name', live=True)
            assert names() == ['middle.jpg', 'newest.jpg', 'older.jpg']
            # A different scan must default back to newest-first, not inherit the old sort.
            ui.stage = 'devices'
            view.show(force=True)
            ui.stage = 'signature_running'
            view.show(force=True)
            view._refresh_live(force=True)
            assert names() == ['newest.jpg', 'middle.jpg', 'older.jpg']
        finally:
            ui.stage = 'devices'  # Suppress the real-operation exit confirmation.
            view.close()
            ui.close()
