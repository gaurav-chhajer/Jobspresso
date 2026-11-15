"""Temporary database fixtures; never insert synthetic postings into hosted data."""

import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import MagicMock, patch

from backend import pipeline_skeleton as pipeline, role_titles
from scripts import diagnose_groq_queue as diagnostic, run_cloud_worker as runner
from tests import test_cloud_worker, test_evaluation


class QueueDiagnosticTests(unittest.TestCase):
    def test_parallel_downloads_overlap_and_bound_inflight_work(self):
        barrier = threading.Barrier(runner.MAX_FETCH_WORKERS)
        lock = threading.Lock()
        counts = {'active': 0, 'peak': 0}
        def download(company, deadline, stop):
            with lock:
                counts['active'] += 1
                counts['peak'] = max(counts['peak'], counts['active'])
            try:
                barrier.wait(timeout=5)
                return [company['id']]
            finally:
                with lock:
                    counts['active'] -= 1
        companies = [dict(id=index) for index in range(runner.MAX_FETCH_WORKERS * 2)]
        with patch.object(runner, '_download_company', side_effect=download):
            with runner.parallel_downloads(companies, runner.time.monotonic()+3000, None) as downloads:
                results = [future.result(timeout=6) for company, future in downloads]
        self.assertEqual(results, [[index] for index in range(len(companies))])
        self.assertEqual(counts['peak'], runner.MAX_FETCH_WORKERS)

    def test_stale_reasons_follow_current_filters_and_keep_history(self):
        from backend.queue_diagnostics import stale_page
        self.assertEqual({r['reason'] for r in stale_page(self.conn, 30, 0)}, {'Eligible for rescoring'})
        self.conn.execute('UPDATE jobs SET is_active=0 WHERE id=1')
        self.preferences(roles=['Unrelated role'])
        reasons = {r['job_id']: r['reason'] for r in stale_page(self.conn, 30, 0)}
        self.assertEqual(reasons[1], 'Job inactive')
        self.assertEqual(reasons[2], 'Selected role mismatch')
        self.assertEqual(len(stale_page(self.conn, 1, 1)), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM scores WHERE is_stale=1').fetchone()[0], 2)

    def test_worker_acknowledges_empty_queue_and_endpoint_requires_authentication(self):
        from backend import matching_tasks
        from datetime import datetime, timezone
        self.preferences(roles=['Unrelated role'])
        matching_tasks.enqueue(self.conn, 1, 'backlog', datetime.now(timezone.utc))
        self.conn.commit()
        client = MagicMock()
        client.call.side_effect = self.call
        pool = MagicMock()
        self.assertEqual(runner._score_pending(client, runner.time.monotonic() + 3000, pool), 0)
        self.assertTrue(any(call.args[0] == 'complete-empty' for call in client.call.call_args_list))
        pool.score.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT status FROM matching_tasks WHERE user_id=1').fetchone()[0], 'complete')
        with self.client.post('/internal/worker/complete-empty', json={}) as response:
            self.assertEqual(response.status_code, 401)

    def test_empty_ack_rechecks_revision_preferences_and_new_jobs(self):
        from backend import matching_tasks
        from datetime import datetime, timezone
        self.preferences(roles=['Unrelated role'])
        matching_tasks.enqueue(self.conn, 1, 'backlog', datetime.now(timezone.utc))
        self.conn.commit()
        acknowledgement = self.call('work')['empty_task']
        matching_tasks.enqueue(self.conn, 1, 'postings', datetime.now(timezone.utc))
        self.conn.commit()
        self.assertFalse(self.call('complete-empty', acknowledgement)['completed'])
        acknowledgement = self.call('work')['empty_task']
        self.preferences()
        self.assertFalse(self.call('complete-empty', acknowledgement)['completed'])
        self.preferences(roles=['Unrelated role'])
        acknowledgement = self.call('work')['empty_task']
        self.conn.execute("UPDATE jobs SET title='Unrelated role' WHERE id=1")
        user = self.conn.execute('SELECT * FROM users WHERE id=1').fetchone()
        from tests.test_cloud_worker import title_profile
        role_titles.save(self.conn, user, title_profile(['Unrelated role', 'Backend Engineer', 'Frontend Engineer', 'Data Analyst', 'QA Engineer']))
        self.conn.commit()
        self.assertFalse(self.call('complete-empty', acknowledgement)['completed'])
        self.conn.execute('UPDATE jobs SET is_active=0')
        self.conn.commit()
        acknowledgement = self.call('work')['empty_task']
        self.assertTrue(self.call('complete-empty', acknowledgement)['completed'])
        self.assertEqual(self.conn.execute('SELECT status FROM matching_tasks WHERE user_id=1').fetchone()[0], 'complete')

    def setUp(self):
        test_cloud_worker.CloudEndpointTests.setUp(self)

    def call(self, route, data=None):
        response = test_cloud_worker.CloudEndpointTests.call(self, route, data)
        self.assertEqual(response.status_code, 200)
        return response.json

    def inspect(self):
        report = diagnostic.inspect_queue(self.conn)
        self.assertEqual(report['scoreable_jobs'], len(self.call('work')['items']))
        return report['accounts'][0]

    def preferences(self, **values):
        self.conn.execute('UPDATE users SET preferences_json=? WHERE id=1', (json.dumps(values),))
        self.conn.commit()

    def test_ready_jobs_and_existing_scores_explain_expected_empty_queue(self):
        self.assertEqual(self.inspect()['reason'], 'ready_for_scoring')
        self.conn.execute('UPDATE scores SET is_stale=0')
        self.conn.commit()
        report = self.inspect()
        self.assertEqual(report['reason'], 'all_active_jobs_already_scored')
        self.assertEqual(report['excluded']['unscored'], 2)

    def test_subscription_and_inactive_job_gates(self):
        self.conn.execute('INSERT INTO user_settings(user_id,subscriptions_configured) VALUES(1,1)')
        self.conn.commit()
        report = self.inspect()
        self.assertEqual(report['excluded']['subscribed'], 2)
        self.assertEqual(report['reason'], 'no_unscored_jobs_in_subscribed_companies')
        self.conn.execute('UPDATE jobs SET is_active=0')
        self.conn.commit()
        self.assertEqual(self.inspect()['reason'], 'no_active_jobs')

    def test_each_preference_gate_matches_the_actual_work_endpoint(self):
        for preferences, stage in (({'roles': ['Unrelated role']}, 'role_preferences'),
                                   ({'locations': ['Tokyo']}, 'location_preferences'),
                                   ({'employment_types': ['internship']}, 'employment_and_seniority'),
                                   ({'min_stipend': 20000}, 'stipend')):
            with self.subTest(stage=stage):
                self.preferences(**preferences)
                self.conn.execute("UPDATE jobs SET location='Pune, India',raw_json=?",
                                  (json.dumps({'content': 'Salary INR 10000', 'employmentType': 'FullTime'}),))
                self.conn.commit()
                self.assertEqual(self.inspect()['excluded'][stage], 2)

    def test_seniority_and_unrelated_family_can_empty_the_queue(self):
        self.conn.execute("UPDATE jobs SET title='Senior Software Engineer'")
        self.conn.commit()
        self.assertEqual(self.inspect()['excluded']['employment_and_seniority'], 2)
        self.conn.execute("UPDATE jobs SET title='Software Engineer, Security'")
        self.conn.commit()
        self.assertEqual(self.inspect()['remaining']['role_family'], 2)
        self.conn.execute("UPDATE jobs SET title='Data Scientist'")
        self.conn.commit()
        report = self.inspect()
        self.assertEqual(report['remaining']['stipend'], 2)
        self.assertEqual(report['excluded']['role_family'], 2)
        self.assertEqual(report['reason'], 'no_matching_role_family_aliases')

    def test_missing_and_insufficient_profiles_are_distinguished(self):
        self.conn.execute('DELETE FROM resume_role_titles')
        self.conn.commit()
        report = diagnostic.inspect_queue(self.conn)['accounts'][0]
        self.assertEqual(report['reason'], 'role_profile_generation_required')
        self.assertEqual(report['profiles_needed'], 2)
        self.assertIn('role_profile_required', self.call('work'))
        user = self.conn.execute('SELECT * FROM users WHERE id=1').fetchone()
        role_titles.save(self.conn, user, dict(status='insufficient_evidence', reason='Synthetic fixture', families=[]))
        self.conn.commit()
        self.assertEqual(self.inspect()['reason'], 'resume_profile_has_insufficient_evidence')

    def test_temporary_fetched_job_is_queued_scored_and_then_correctly_absent(self):
        self.conn.execute('UPDATE scores SET is_stale=0')
        self.conn.commit()
        self.assertEqual(self.inspect()['reason'], 'all_active_jobs_already_scored')
        fixture = dict(external_id='diagnostic-fixture', title='Software Engineer', location='Remote',
                       department=None, url='https://example.test/diagnostic-fixture',
                       raw_json={'descriptionHtml': 'Python and SQL; student projects accepted.'})
        client = MagicMock()
        client.call.side_effect = self.call
        with patch.object(runner.pipeline, 'fetch_jobs_for_company',
                          side_effect=lambda company: [fixture] if company['id'] == 1 else []), \
                patch.dict(runner.os.environ, TAVILY_API_KEY=''):
            self.assertEqual(runner._fetch_and_score(client, runner.time.monotonic() + 3000, True, None), 0)
        self.assertEqual(self.inspect()['remaining']['role_family'], 1)
        pool = MagicMock(reporting_failed=False)
        pool.score.return_value = test_evaluation.sample_result()
        with patch.object(runner, 'GroqKeyPool', return_value=pool):
            self.assertEqual(runner.score_pending(client, runner.time.monotonic() + 3000, threading.Event()), 0)
        pool.score.assert_called_once()
        self.assertEqual(self.inspect()['reason'], 'all_active_jobs_already_scored')

    def test_paging_limits_privacy_and_read_only_database(self):
        before = Path(self.database).read_bytes()
        with patch.object(diagnostic, 'PAGE_SIZE', 1):
            report = diagnostic.report_database(self.database)
        self.assertEqual(report['scoreable_jobs'], 2)
        self.assertEqual(Path(self.database).read_bytes(), before)
        text = json.dumps(report)
        for private in ('Python resume', 'owner@example.test', 'preferences_json', 'resume_text'):
            self.assertNotIn(private, text)
        with patch.object(diagnostic, 'MAX_PAGES', 1), patch.object(diagnostic, 'PAGE_SIZE', 1):
            with self.assertRaisesRegex(ValueError, 'limit exceeded'):
                diagnostic.inspect_queue(self.conn)
        missing = Path(self.directory.name) / 'not-created.db'
        with self.assertRaises(FileNotFoundError):
            diagnostic.report_database(missing)
        self.assertFalse(missing.exists())

    def test_no_eligible_accounts_is_reported_without_private_data(self):
        self.conn.execute('UPDATE accounts SET email_verified=0')
        self.conn.commit()
        self.assertEqual(diagnostic.inspect_queue(self.conn),
                         dict(eligible_accounts=0, accounts=[], scoreable_jobs=0))
