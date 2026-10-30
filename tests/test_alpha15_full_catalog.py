from pathlib import Path
from src.models.core import Catalog


def test_entire_catalog_pagination_and_filters(tmp_path):
    catalog = Catalog(tmp_path / 'catalog.sqlite')
    try:
        rows = [(str(i), f'folder/file_{i}.jpg' if i % 2 else f'other/report_{i}.pdf', i, 'PHOTOS' if i % 2 else 'DOCS') for i in range(500)]
        catalog.insert_batch(rows)
        count, first = catalog.live_page(0, 100)
        assert count == 500 and len(first) == 100 and first[0][0] == 500
        count, last = catalog.live_page(400, 100)
        assert count == 500 and len(last) == 100 and last[-1][0] == 1
        count, jpg = catalog.live_page(0, 200, category='PHOTOS', extension='jp')
        assert count == 250 and len(jpg) == 200
        count, match = catalog.live_page(0, 100, term='file_127')
        assert count == 1 and 'file_127' in match[0][1]
        count, no_match = catalog.live_page(0, 100, term='not_a_real_file')
        assert (count, no_match) == (0, [])
    finally:
        catalog.close()
