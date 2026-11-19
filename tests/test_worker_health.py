import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.worker_health import save, observations


class WorkerHealthTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(Path('backend/migrations/059_worker_health.sql').read_text())

    def test_failure_preserves_last_success_and_stale_is_visible(self):
        value = dict(worker='fetch', company_id=1, state='success', job_count=4, error_code='')
        save(self.conn, value)
        original = observations(self.conn, datetime.now(timezone.utc))[('fetch', 1)]
        save(self.conn, dict(value, state='failed', error_code='transfer_error'))
        current = observations(self.conn, datetime.now(timezone.utc) + timedelta(minutes=16))[('fetch', 1)]
        self.assertEqual(current['last_success_at'], original['last_success_at'])
        self.assertEqual(current['display_state'], 'Failed')
        self.assertEqual(original['display_state'], 'Success')

    def test_private_error_text_and_invalid_counts_rejected(self):
        value = dict(worker='score', company_id=0, state='idle', job_count=0, error_code='')
        for invalid in (dict(value, error_code='private response'), dict(value, job_count=-1), dict(value, worker='unknown')):
            with self.assertRaises(ValueError):
                save(self.conn, invalid)

    def test_active_company_reports_keep_running_fetch_fresh(self):
        value = dict(worker='fetch', company_id=0, state='running', job_count=0, error_code='')
        save(self.conn, value)
        old = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
        self.conn.execute('UPDATE worker_health SET updated_at=?', (old,))
        save(self.conn, dict(value, company_id=1, state='success', job_count=3))
        current = observations(self.conn, datetime.now(timezone.utc))[('fetch', 0)]
        self.assertEqual(current['display_state'], 'Running')
        self.assertGreater(current['updated_at'], old)
