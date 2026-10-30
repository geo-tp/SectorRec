"""Desktop-scale catalogue capacity without changing Cardputer or recovery logic."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.controllers.controller import Controller
from src.models.core import (
    Catalog,
    ScanWorker,
    MAX_RECORDS,
    MAX_RECORDS_DESKTOP,
    DESKTOP_CATALOG_FREE_CHECK_EVERY,
    DESKTOP_CATALOG_FREE_RESERVE,
)


class DesktopCatalogCapacityTests(unittest.TestCase):
    def test_large_desktop_limit_and_unchanged_cardputer_limit(self):
        self.assertEqual(MAX_RECORDS_DESKTOP, 20_000_000)
        self.assertEqual(MAX_RECORDS, 100_000)
        for desktop, expected in ((True, MAX_RECORDS_DESKTOP), (False, MAX_RECORDS)):
            with tempfile.TemporaryDirectory() as folder:
                with patch('pathlib.Path.home', return_value=Path(folder)):
                    ui = Controller(demo=False, desktop=desktop)
                try:
                    ui.devices = [{'path': '/dev/sdz1', 'size': 10_000, 'fs': 'VFAT',
                                   'supported': True, 'label': 'test', 'mounted': False}]
                    ui.selection = 0
                    with patch('src.controllers.controller.ScanWorker') as worker:
                        ui.begin_scan()
                    self.assertEqual(worker.call_args.kwargs['max_records'], expected)
                finally:
                    ui.close()

    def test_low_disk_space_stops_desktop_before_exhaustion(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Catalog(Path(folder) / 'scan.sqlite')
            try:
                status = {}
                worker = ScanWorker(catalog, '/dev/example', status, max_records=MAX_RECORDS_DESKTOP)
                worker.total_seen = DESKTOP_CATALOG_FREE_CHECK_EVERY - 1
                with patch('src.models.core.shutil.disk_usage', return_value=SimpleNamespace(free=128*1024*1024)) as usage:
                    worker.accept(('17', 'found.pdf', 1024, 'DOCS'))
                self.assertTrue(usage.called)
                self.assertTrue(status.get('catalog_storage_low'))
                self.assertEqual(status.get('catalog_storage_free'), 128*1024*1024)
                self.assertTrue(status.get('limited'))
                self.assertTrue(worker.cancel_event.is_set())
                worker.flush()
                self.assertEqual(catalog.indexed_count, 1)
            finally:
                catalog.close()

    def test_above_reserve_continues_and_still_stops_at_record_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Catalog(Path(folder) / 'scan.sqlite')
            try:
                status = {}
                worker = ScanWorker(catalog, '/dev/example', status, max_records=MAX_RECORDS_DESKTOP)
                worker.total_seen = DESKTOP_CATALOG_FREE_CHECK_EVERY - 1
                with patch('src.models.core.shutil.disk_usage', return_value=SimpleNamespace(free=DESKTOP_CATALOG_FREE_RESERVE + 1024)):
                    worker.accept(('17', 'file.txt', 42, 'DOCS'))
                self.assertFalse(worker.cancel_event.is_set())
                self.assertNotIn('catalog_storage_low', status)
                worker.total_seen = MAX_RECORDS_DESKTOP - 1
                with patch('src.models.core.shutil.disk_usage', return_value=SimpleNamespace(free=DESKTOP_CATALOG_FREE_RESERVE + 1024)):
                    worker.accept(('18', 'another.txt', 42, 'DOCS'))
                self.assertTrue(worker.cancel_event.is_set())
                self.assertTrue(status.get('limited'))
                self.assertFalse(status.get('catalog_storage_low', False))
            finally:
                catalog.close()

    def test_cardputer_does_not_run_desktop_disk_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Catalog(Path(folder) / 'scan.sqlite')
            try:
                worker = ScanWorker(catalog, '/dev/example', {}, max_records=MAX_RECORDS)
                worker.total_seen = DESKTOP_CATALOG_FREE_CHECK_EVERY - 1
                with patch('src.models.core.shutil.disk_usage', side_effect=AssertionError('desktop-only check')):
                    worker.accept(('17', 'file.txt', 42, 'DOCS'))
                self.assertFalse(worker.cancel_event.is_set())
            finally:
                catalog.close()

    def test_ui_explains_storage_limit(self):
        from src.app import UI
        ui = UI(demo=True, desktop=True)
        try:
            ui.stage = 'scanning'
            ui.status = {'state': 'limited', 'count': 14_000, 'catalog_storage_low': True,
                         'catalog_storage_free': 180*1024*1024}
            ui.tick()
            self.assertEqual(ui.stage, 'results')
            self.assertIn('storage', ui.scan_partial_reason.lower())
            self.assertIn('180', ui.scan_partial_reason)
            self.assertNotIn('20,000,000', ui.scan_partial_reason)
        finally:
            ui.close()
