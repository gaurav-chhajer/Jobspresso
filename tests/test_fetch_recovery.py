"""Recovery tests use a temporary database and the real Flask worker endpoints."""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

from scripts import fetch_recovery as recovery
from scripts import run_cloud_worker as worker
from tests import test_cloud_worker


class RecoveryTests(unittest.TestCase):
    setUp = test_cloud_worker.CloudEndpointTests.setUp
    call = test_cloud_worker.CloudEndpointTests.call

    def setup_recovery(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'recovery.db'
        self.company = self.call('companies').json['companies'][0]
        self.jobs = [dict(external_id='recovered', title='Software Engineer', raw_json={})]
        self.posts = []
        self.timeout_after_commit = False
        self.timeout_before_commit = False
        outer = self

        class Client:
            def call(self, route, data=None):
                if route == 'companies':
                    return {'companies': [outer.company]}
                if data is not None:
                    outer.posts.append(json.loads(json.dumps(data)))
                    if outer.timeout_before_commit:
                        outer.timeout_before_commit = False
                        raise requests.Timeout()
                response = outer.call(route, data)
                if response.status_code == 409:
                    raise worker.HostedInputsChanged()
                if response.status_code != 200:
                    raise worker.HostedOperationError(response.status_code)
                if data is not None and outer.timeout_after_commit:
                    outer.timeout_after_commit = False
                    raise requests.Timeout()
                return response.json

        self.hosted = Client()

    def run_round(self, now=1000):
        with patch.object(recovery.time, 'time', return_value=now), \
                patch.object(recovery.time, 'monotonic', return_value=0):
            recovery.run_round(self.hosted, 10000, threading.Event(), path=self.path)

    def state(self):
        with closing(recovery.connect(self.path)) as conn:
            return dict(conn.execute('SELECT * FROM feeds').fetchone())

    def test_committed_timeout_reconciles_after_restart_without_refetch_or_repost(self):
        self.setup_recovery()
        self.timeout_after_commit = True
        with patch.object(worker, '_download_company', return_value=self.jobs) as download:
            self.run_round()
            self.assertEqual(self.state()['status'], 'pending')
            self.assertIsNotNone(self.state()['request'])
            self.run_round(1059)
            self.assertEqual(len(self.posts), 1)
            self.run_round(1060)
            download.assert_called_once()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.state()['status'], 'saved')
        self.assertIsNone(self.state()['payload'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM jobs WHERE external_id='recovered'").fetchone()[0], 1)

    def test_uncommitted_timeout_replays_exact_request_and_does_not_refetch(self):
        self.setup_recovery()
        self.timeout_before_commit = True
        with patch.object(worker, '_download_company', return_value=self.jobs) as download:
            self.run_round()
            self.run_round(1060)
            download.assert_called_once()
        self.assertEqual(self.posts[0], self.posts[1])
        self.assertEqual(self.state()['status'], 'saved')

    def test_source_failure_backoff_and_later_success(self):
        self.setup_recovery()
        with patch.object(worker, '_download_company', side_effect=[requests.Timeout(), self.jobs]) as download:
            self.run_round()
            self.assertEqual(self.state()['due'], 1060)
            self.assertIsNone(self.state()['payload'])
            self.run_round(1030)
            self.assertEqual(download.call_count, 1)
            self.run_round(1060)
        self.assertEqual(self.state()['job_count'], 1)
        self.assertEqual(self.state()['last_success'], 1060)

    def test_failure_health_is_accepted_by_live_endpoint_contract(self):
        self.setup_recovery()
        self.timeout_before_commit = True
        def health(client, worker, state, company_id=0, job_count=0, error_code=''):
            response = self.call('health', dict(worker=worker, state=state, company_id=company_id,
                                               job_count=job_count, error_code=error_code))
            self.assertEqual(response.status_code, 200)
        with patch.object(worker, 'report_health', side_effect=health), \
                patch.object(worker, '_download_company', return_value=self.jobs):
            self.run_round()
        row = self.conn.execute("SELECT state,error_code FROM worker_health WHERE worker='fetch' AND company_id=?",
                                (self.company['id'],)).fetchone()
        self.assertEqual(tuple(row), ('failed', 'transfer_error'))

    def test_partial_source_is_not_counted_as_success(self):
        self.setup_recovery()
        self.company = dict(self.company, career_source='salesforce')
        with patch.object(recovery.career_sources, 'fetch', side_effect=ValueError()) as fetch:
            self.run_round()
        fetch.assert_called_once_with('salesforce', 9880, require_complete=True)
        self.assertEqual(self.state()['status'], 'pending')
        self.assertEqual(self.posts, [])

    def test_stale_spool_cannot_overwrite_newer_transfer(self):
        self.setup_recovery()
        self.timeout_before_commit = True
        with patch.object(worker, '_download_company', return_value=self.jobs):
            self.run_round()
            response = self.call('snapshot', dict(company_id=self.company['id'],
                fingerprint=self.company['fingerprint'], jobs=[]))
            self.assertEqual(response.status_code, 200)
            self.run_round(1060)
        self.assertEqual(self.state()['status'], 'pending')
        self.assertIsNone(self.state()['payload'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM jobs WHERE is_active=1 AND company_id=?',
                                         (self.company['id'],)).fetchone()[0], 0)

    def test_receipt_failure_rolls_back_job_changes(self):
        self.setup_recovery()
        self.conn.execute("""CREATE TRIGGER refuse_receipt BEFORE INSERT ON feed_transfer_receipts
            BEGIN SELECT RAISE(ABORT, 'test receipt failure'); END""")
        self.conn.commit()
        before = self.conn.execute('SELECT count(*) FROM jobs').fetchone()[0]
        response = self.call('snapshot', dict(company_id=self.company['id'], fingerprint=self.company['fingerprint'],
                                             jobs=self.jobs))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM jobs').fetchone()[0], before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM feed_transfer_receipts').fetchone()[0], 0)

    def test_catalog_change_discards_old_payload_but_preserves_last_success(self):
        self.setup_recovery()
        with closing(recovery.connect(self.path)) as conn:
            recovery.synchronize(conn, [self.company])
            with conn:
                conn.execute("UPDATE feeds SET payload='{}',request='{}',last_success=123")
            recovery.synchronize(conn, [dict(self.company, fingerprint='a' * 64)])
        self.assertIsNone(self.state()['payload'])
        self.assertEqual(self.state()['last_success'], 123)

    def test_empty_feed_is_confirmed_and_refresh_waits_thirty_minutes(self):
        self.setup_recovery()
        with patch.object(worker, '_download_company', return_value=[]) as download:
            self.run_round()
            self.assertEqual(self.state()['due'], 2800)
            self.run_round(2799)
            download.assert_called_once()
            self.run_round(2800)
            self.assertEqual(download.call_count, 2)
        self.assertEqual(self.state()['job_count'], 0)

    def test_saved_feed_with_old_hourly_deadline_refreshes_after_thirty_minutes(self):
        self.setup_recovery()
        with patch.object(worker, '_download_company', return_value=self.jobs) as download:
            self.run_round()
            with closing(recovery.connect(self.path)) as conn, conn:
                conn.execute('UPDATE feeds SET due=4600')
            self.run_round(2799)
            download.assert_called_once()
            self.run_round(2800)
            self.assertEqual(download.call_count, 2)
        self.assertEqual(self.state()['last_success'], 2800)
        self.assertEqual(self.state()['due'], 4600)

    def test_pending_failure_keeps_its_backoff_after_interval_change(self):
        self.setup_recovery()
        with closing(recovery.connect(self.path)) as conn:
            recovery.synchronize(conn, [self.company])
            with conn:
                conn.execute("UPDATE feeds SET status='pending',last_success=1000,due=4600")
        with patch.object(worker, '_download_company', return_value=self.jobs) as download:
            self.run_round(2800)
            download.assert_not_called()
            self.run_round(4600)
            download.assert_called_once()

    def test_receipt_content_mismatch_is_rejected(self):
        self.setup_recovery()
        request = dict(company_id=self.company['id'], fingerprint=self.company['fingerprint'],
                       jobs=self.jobs, transfer=dict(token='b' * 32, previous=None))
        first = self.call('snapshot', request)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.call('snapshot', request).json, first.json)
        self.assertEqual(self.call('snapshot', dict(request, jobs=[])).status_code, 409)

    def test_transfer_batches_resume_at_first_unconfirmed_batch(self):
        self.setup_recovery()
        self.jobs = [dict(external_id=str(i), title='Software Engineer', raw_json={}) for i in range(3)]
        self.timeout_after_commit = True
        with patch.object(worker, 'MAX_JOBS', 1), \
                patch.object(worker, '_download_company', return_value=self.jobs) as download:
            self.run_round()
            self.run_round(1060)
            download.assert_called_once()
        self.assertEqual(len(self.posts), 3)
        self.assertEqual(self.state()['job_count'], 3)
        self.assertEqual(self.state()['status'], 'saved')

    def test_incorrect_saved_count_keeps_payload_pending(self):
        self.setup_recovery()
        original = self.hosted.call
        def incorrect(route, data=None):
            result = original(route, data)
            return dict(result, saved=0) if data is not None else result
        with patch.object(self.hosted, 'call', side_effect=incorrect), \
                patch.object(worker, '_download_company', return_value=self.jobs):
            self.run_round()
        self.assertEqual(self.state()['status'], 'pending')
        self.assertIsNotNone(self.state()['payload'])
        self.assertIsNone(self.state()['last_success'])

    def test_stop_preserves_download_before_any_transfer(self):
        self.setup_recovery()
        stop = threading.Event()
        def download(*args):
            stop.set()
            return self.jobs
        with patch.object(worker, '_download_company', side_effect=download), \
                patch.object(recovery.time, 'monotonic', return_value=0):
            recovery.run_round(self.hosted, 10000, stop, path=self.path)
        self.assertEqual(self.state()['status'], 'transferring')
        self.assertEqual(self.posts, [])
        with patch.object(worker, '_download_company') as download:
            self.run_round()
            download.assert_not_called()
        self.assertEqual(self.state()['status'], 'saved')

    def test_download_saved_before_restart_is_fenced_against_newer_writes(self):
        self.setup_recovery()
        stop = threading.Event()
        def download(*args):
            stop.set()
            return self.jobs
        with patch.object(worker, '_download_company', side_effect=download), \
                patch.object(recovery.time, 'monotonic', return_value=0):
            recovery.run_round(self.hosted, 10000, stop, path=self.path)
        self.call('snapshot', dict(company_id=self.company['id'], fingerprint=self.company['fingerprint'], jobs=[]))
        self.run_round()
        self.assertEqual(self.state()['status'], 'pending')
        self.assertIsNone(self.state()['payload'])

    def test_spool_limit_is_reported_before_any_transfer(self):
        self.setup_recovery()
        with patch.object(worker, '_download_company', return_value=self.jobs), \
                patch.object(recovery, 'MAX_FEED_BYTES', 1):
            self.run_round()
        self.assertEqual(self.state()['status'], 'pending')
        self.assertEqual(self.posts, [])


class CompleteSourceTests(unittest.TestCase):
    def test_strict_fetch_rejects_partial_pages_and_closes_session(self):
        from backend import career_sources
        from unittest.mock import MagicMock
        feed = MagicMock()
        def partial(feed, rows):
            rows.append(dict(external_id='one', title='Engineer', raw_json={}))
            raise requests.Timeout()
        with patch.object(career_sources, 'Feed', return_value=feed), \
                patch.object(career_sources, '_listings', side_effect=partial):
            with self.assertRaises(requests.Timeout):
                career_sources.fetch('salesforce', require_complete=True)
        feed.close.assert_called_once()

    def test_strict_fetch_rejects_repeated_pages(self):
        from backend import career_sources
        from unittest.mock import MagicMock
        feed = MagicMock()
        def repeated(feed, rows):
            rows.extend([dict(external_id='one', title='Engineer', raw_json={})] * 2)
        with patch.object(career_sources, 'Feed', return_value=feed), \
                patch.object(career_sources, '_listings', side_effect=repeated):
            with self.assertRaisesRegex(ValueError, 'Repeated pagination'):
                career_sources.fetch('salesforce', require_complete=True)
        feed.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
