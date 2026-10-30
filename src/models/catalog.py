"""Thread-safe SQLite catalogue shared by scan workers and result views."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from src.models.files import extension_of


class Catalog:
    def __init__(self, cache_path: Path):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(cache_path)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, inode TEXT, path TEXT, size INTEGER, category TEXT, extension TEXT NOT NULL DEFAULT \'\', origin TEXT NOT NULL DEFAULT \'deleted\', UNIQUE(inode,path))')
        # Migrate existing scan cache rather than failing on a version upgrade.
        if 'origin' not in {x[1] for x in self.db.execute('PRAGMA table_info(entries)')}:
            self.db.execute("ALTER TABLE entries ADD COLUMN origin TEXT NOT NULL DEFAULT 'deleted'")
        self.db.execute('CREATE INDEX IF NOT EXISTS cat_idx ON entries(category,extension,id)')
        self.db.commit()
        # Maintain the UI count incrementally. Computing SUM(size) on the
        # complete SQLite catalog at every 100-row batch becomes prohibitively
        # expensive when desktop scans reach hundreds of thousands of files.
        self.indexed_count = self.db.execute('SELECT count(*) FROM entries').fetchone()[0]

    def clear(self):
        with self.lock:
            self.db.execute('DELETE FROM entries')
            self.db.commit()
            self.indexed_count = 0

    def insert_batch(self, rows):
        if not rows:
            return
        with self.lock:
            data = [(row[0],row[1],row[2],row[3],extension_of(row[1]),
                     row[4] if len(row)>4 else 'deleted') for row in rows]
            before = self.db.total_changes
            self.db.executemany('INSERT OR IGNORE INTO entries(inode,path,size,category,extension,origin) VALUES (?,?,?,?,?,?)', data)
            self.db.commit()
            self.indexed_count += self.db.total_changes - before

    @staticmethod
    def where(category='ALL', extension=None):
        clauses = []
        params = []
        if category != 'ALL':
            clauses.append('category=?')
            params.append(category)
        if extension is not None:
            clauses.append('extension=?')
            params.append(extension)
        return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), tuple(params)

    def summary(self, category='ALL', extension=None):
        where, params = self.where(category, extension)
        with self.lock:
            count, total = self.db.execute('SELECT count(*),sum(size) FROM entries' + where, params).fetchone()
        return count or 0, total or 0

    def summary_details(self, category='ALL', extension=None):
        where, params = self.where(category, extension)
        sql = 'SELECT count(*),sum(size),sum(CASE WHEN size IS NULL THEN 1 ELSE 0 END) FROM entries' + where
        with self.lock:
            count, total, unknown = self.db.execute(sql, params).fetchone()
        return count or 0, total or 0, unknown or 0

    def available_extensions(self, category):
        where, params = self.where(category)
        sql = 'SELECT extension,count(*) FROM entries' + where + ' GROUP BY extension ORDER BY count(*) DESC, extension ASC'
        with self.lock:
            return [ext for ext, count in self.db.execute(sql, params).fetchall()]

    def available_categories(self):
        with self.lock:
            return [cat for (cat,) in self.db.execute(
                'SELECT category FROM entries GROUP BY category ORDER BY count(*) DESC')]

    @staticmethod
    def _sort_clause(sort='id', descending=False):
        # Only allow known SQL identifiers; column headings never become SQL.
        expressions = {'id':'id', 'path':'path COLLATE NOCASE',
                       'type':'extension COLLATE NOCASE', 'size':'size'}
        column = expressions.get(sort, 'id')
        direction = 'DESC' if descending else 'ASC'
        # Consistent order across pages; keep files with unknown size last.
        unknown = 'size IS NULL ASC, ' if sort=='size' else ''
        return ' ORDER BY ' + unknown + column + ' ' + direction + ', id ' + direction

    def at(self, position, category='ALL', extension=None, sort='id', descending=False):
        where, params = self.where(category, extension)
        with self.lock:
            return self.db.execute('SELECT id,inode,path,size,category,origin FROM entries' + where +
                                   self._sort_clause(sort,descending) + ' LIMIT 1 OFFSET ?', params+(position,)).fetchone()

    def page(self, start, count, category='ALL', extension=None, sort='id', descending=False):
        where, params = self.where(category, extension)
        with self.lock:
            return self.db.execute('SELECT id,inode,path,size,category,origin FROM entries' + where +
                                   self._sort_clause(sort,descending) + ' LIMIT ? OFFSET ?', params+(count,start)).fetchall()

    def live_page(self, offset=0, limit=100, term='', category='ALL', extension='', sort='id', descending=True):
        """Query the entire committed catalog, never only its newest entries.

        Called from a desktop background thread, not from the Tk event loop.
        The scan writer remains the sole writer. This method only reads SQLite.
        """
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        where, params = self.where(category)
        clauses = [where[7:]] if where else []
        args = list(params)
        if extension:
            clauses.append("ltrim(extension, '.') LIKE ? ESCAPE '\\'")
            args.append(extension.lower().lstrip('.') .replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%')
        if term:
            clauses.append("path LIKE ? ESCAPE '\\'")
            args.append('%' + term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%')
        condition = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        # Separate read connection avoids blocking the UI on the scan's Python lock.
        con = sqlite3.connect(Path(self.path).resolve().as_uri() + '?mode=ro', uri=True, timeout=0.4)
        try:
            con.execute('PRAGMA query_only=ON')
            total = con.execute('SELECT COUNT(*) FROM entries' + condition, args).fetchone()[0]
            records = con.execute(
                'SELECT id,path,extension,size FROM entries' + condition +
                self._sort_clause(sort,descending) + ' LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
            return total, records
        finally:
            con.close()

    def all_rows(self, category='ALL', extension=None):
        # Stream from a separate connection, not one Python list of 50k rows.
        where, params = self.where(category, extension)
        con = sqlite3.connect(self.path)
        try:
            yield from con.execute('SELECT id,inode,path,size,category,origin FROM entries' + where + ' ORDER BY id', params)
        finally:
            con.close()

    def close(self):
        with self.lock:
            self.db.close()
