"""Review boards resolve employer identity without merging unrelated names."""

import csv
import sqlite3
import unittest
from pathlib import Path

from backend.migrate import apply_migrations
from backend.pipeline_skeleton import SCHEMA


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'backend/migrations/066_verified_review_boards.sql'


class VerifiedReviewBoardsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript(SCHEMA)
        apply_migrations(self.conn)
        with (ROOT / 'data/boards/verified_review_boards.csv').open(encoding='utf-8', newline='') as source:
            self.boards = list(csv.DictReader(source))

    def replay(self):
        self.conn.commit()
        self.conn.executescript(MIGRATION.read_text(encoding='utf-8'))

    def test_all_twenty_one_boards_are_active_and_repeat_import_is_unchanged(self):
        self.assertEqual(len(self.boards), 21)
        self.assertEqual(len({(r['ats'], r['slug']) for r in self.boards}), 21)
        for row in self.boards:
            self.assertEqual(self.conn.execute(
                'SELECT name,active,discovery_only,career_source FROM companies WHERE ats_type=? AND board_slug=?',
                (row['ats'], row['slug'])).fetchall(), [(row['company'], 1, 0, None)])
        before = self.conn.execute('SELECT * FROM companies ORDER BY id').fetchall()
        self.replay()
        self.assertEqual(self.conn.execute('SELECT * FROM companies ORDER BY id').fetchall(), before)

    def test_unrelated_same_name_employers_are_preserved(self):
        originals = []
        for name, slug in (('Axiom', 'axiom'), ('Blue', 'blue'), ('Hazel', 'hazel-2'),
                           ('Nectar', 'nectar'), ('Vector', 'vector'), ('Porter', 'porter')):
            cursor = self.conn.execute(
                "INSERT INTO companies(name,ats_type,board_slug,discovery_only) VALUES(?,'greenhouse',?,1)",
                (name, 'yc-employer-' + slug))
            originals.append(self.conn.execute('SELECT * FROM companies WHERE id=?', (cursor.lastrowid,)).fetchone())
        self.replay()
        for original in originals:
            self.assertEqual(self.conn.execute('SELECT * FROM companies WHERE id=?', (original[0],)).fetchone(), original)
        self.assertEqual(self.conn.execute(
            "SELECT name FROM companies WHERE board_slug='porter' ORDER BY name").fetchall(),
            [('Porter Cares',), ('Porter Works',)])

    def test_verified_discovery_upgrades_keep_ids_jobs_and_subscriptions(self):
        identities = {}
        for row in self.boards:
            if row['legacy_slug']:
                company_id = self.conn.execute(
                    'SELECT id FROM companies WHERE ats_type=? AND board_slug=?',
                    (row['ats'], row['slug'])).fetchone()[0]
                identities[row['slug']] = company_id
                self.conn.execute(
                    "UPDATE companies SET ats_type='greenhouse',board_slug=?,discovery_only=1 WHERE id=?",
                    (row['legacy_slug'], company_id))
        self.assertEqual(len(identities), 6)
        company_id = identities['complete']
        self.conn.execute("INSERT INTO users(id,email,resume_text) VALUES(1,'review@example.invalid','Python')")
        self.conn.execute("INSERT INTO company_subscriptions(user_id,company_id,subscribed_at) VALUES(1,?,'2026-11-22')", (company_id,))
        self.conn.execute("INSERT INTO jobs(company_id,external_id,title,first_seen_at,last_seen_at) VALUES(?,'saved','Engineer','2026-11-22','2026-11-22')", (company_id,))
        self.replay()
        for slug, ident in identities.items():
            self.assertEqual(self.conn.execute(
                "SELECT id,active,discovery_only FROM companies WHERE ats_type='ashby' AND board_slug=?",
                (slug,)).fetchall(), [(ident, 1, 0)])
        self.assertEqual(self.conn.execute('SELECT company_id FROM jobs').fetchall(), [(company_id,)])
        self.assertEqual(self.conn.execute('SELECT company_id FROM company_subscriptions').fetchall(), [(company_id,)])
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_existing_porter_board_keeps_identity_when_label_is_disambiguated(self):
        ident = self.conn.execute("SELECT id FROM companies WHERE ats_type='lever' AND board_slug='porter'").fetchone()[0]
        self.conn.execute("UPDATE companies SET name='Porter' WHERE id=?", (ident,))
        self.replay()
        self.assertEqual(self.conn.execute("SELECT id,name FROM companies WHERE ats_type='lever' AND board_slug='porter'").fetchall(), [(ident, 'Porter Cares')])
