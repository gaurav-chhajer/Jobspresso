"""Verified discovery boards retain identity and become fetchable offline."""

import csv
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from backend.migrate import apply_migrations
from backend.pipeline_skeleton import SCHEMA


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'backend/migrations/065_verified_discovery_boards.sql'


class VerifiedDiscoveryBoardsTests(unittest.TestCase):
    def test_catalog_matches_csv_and_reapplication_preserves_rows(self):
        with (ROOT / 'verified_boards_discovery.csv').open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript(SCHEMA)
            apply_migrations(conn)
            for row in rows:
                self.assertEqual(conn.execute(
                    'SELECT active,discovery_only FROM companies WHERE ats_type=? AND board_slug=?',
                    (row['ats'], row['slug'])).fetchall(), [(1, 0)], row['company'])
            before = conn.execute('SELECT * FROM companies ORDER BY id').fetchall()
            conn.executescript(MIGRATION.read_text(encoding='utf-8'))
            self.assertEqual(conn.execute('SELECT * FROM companies ORDER BY id').fetchall(), before)

    def test_upgrade_preserves_identity_links_and_unrelated_disabled_rows(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript(SCHEMA)
            apply_migrations(conn)
            conn.execute("UPDATE companies SET ats_type='greenhouse',board_slug='yc-employer-aiprise',discovery_only=1 WHERE name='AiPrise'")
            company_id = conn.execute("SELECT id FROM companies WHERE name='AiPrise'").fetchone()[0]
            conn.execute("INSERT INTO users(id,email,resume_text) VALUES(1,'test@example.invalid','Python')")
            conn.execute("INSERT INTO company_subscriptions(user_id,company_id,subscribed_at) VALUES(1,?,'2026-11-22T00:00:00+00:00')", (company_id,))
            conn.execute("INSERT INTO jobs(company_id,external_id,title,first_seen_at,last_seen_at) VALUES(?,'old-job','Engineer','2026-11-22','2026-11-22')", (company_id,))
            conn.execute("INSERT INTO companies(name,ats_type,board_slug,active) VALUES('Unrelated','lever','unrelated-disabled',0)")
            conn.commit()
            conn.executescript(MIGRATION.read_text(encoding='utf-8'))
            self.assertEqual(conn.execute("SELECT id,ats_type,board_slug,active,discovery_only FROM companies WHERE name='AiPrise'").fetchall(), [(company_id, 'ashby', 'aiprise', 1, 0)])
            self.assertEqual(conn.execute('SELECT company_id FROM company_subscriptions').fetchall(), [(company_id,)])
            self.assertEqual(conn.execute("SELECT company_id FROM jobs WHERE external_id='old-job'").fetchall(), [(company_id,)])
            self.assertEqual(conn.execute("SELECT active FROM companies WHERE name='Unrelated'").fetchone(), (0,))
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
