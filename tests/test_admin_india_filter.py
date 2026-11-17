import json
import unittest

from backend import locations, pipeline_skeleton as pipeline
from tests import test_cloud_worker, test_web_flows


class IndiaEligibilityTests(unittest.TestCase):
    def test_explicit_locations(self):
        for location in ('India', 'Bengaluru, India', 'Mumbai', 'Remote - India',
                         'Remote (Worldwide)', 'Remote - Global', 'Delhi; London, UK'):
            with self.subTest(location=location):
                self.assertTrue(locations.india_work_eligible(location))
        for location in (None, '', 'Remote', 'Remote - US', 'Remote - Canada',
                         'London, UK', 'Indianapolis, Indiana', 'Remote anywhere in Canada'):
            with self.subTest(location=location):
                self.assertFalse(locations.india_work_eligible(location))

    def test_description_requires_explicit_work_permission(self):
        cases = [
            ('This role is remote and open to candidates based in India.', True),
            ('Remote: worldwide.', True),
            ('Candidates can work remotely from anywhere in the world.', True),
            ('Our remote employees serve customers worldwide.', False),
            ('Our headquarters are in India.', False),
            ('Remote: worldwide except India.', False),
            ('This role is remote worldwide. Candidates must be based in Canada.', False),
            ('Remote: worldwide. Candidates in India are not eligible.', False),
            ('Remote: India only.', True),
            ('Remote: US only.', False),
            ('Remote: worldwide.\nWe cannot hire in India.', False),
            ('Remote: worldwide.\nMust be based in Canada.', False),
        ]
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(locations.india_work_eligible('Remote', description), expected)

    def test_invalid_inputs_and_foreign_location(self):
        for value in (123, [], 'x' * 4001):
            self.assertFalse(locations.india_work_eligible(value))
        self.assertFalse(locations.india_work_eligible('Remote', None))
        self.assertFalse(locations.india_work_eligible('Remote - Canada', 'Remote: worldwide.'))
        self.assertTrue(locations.india_work_eligible('', 'Job location: Bengaluru, India.'))


class AdminIndiaSettingsTests(unittest.TestCase):
    post = test_web_flows.WebFlowTests.post
    settings_data = test_web_flows.WebFlowTests.settings_data

    def setUp(self):
        test_web_flows.WebFlowTests.setUp(self)
        test_web_flows.WebFlowTests.seed_user(self)

    def preferences(self):
        return json.loads(self.conn.execute('SELECT preferences_json FROM users WHERE id=1').fetchone()[0])

    def test_regular_user_cannot_see_or_submit_toggle(self):
        with self.client.get('/settings') as page:
            self.assertNotIn(b'id="admin-india-only"', page.data)
        response = self.post('/settings', self.settings_data(
            admin_india_filter_present='1', admin_india_only='on'))
        self.assertEqual(response.status_code, 400)

    def test_admin_toggle_and_older_form_preservation(self):
        self.conn.execute("INSERT INTO users(id,name,email,resume_text,preferences_json) VALUES(3,'Other','other@example.test','Resume','{}')")
        pipeline.store_score(self.conn, 2, 3, dict(fit_score=75, reasoning='Fit', matched_skills=[], missing_requirements=[]))
        self.conn.execute('UPDATE accounts SET is_admin=1 WHERE user_id=1')
        self.conn.commit()
        with self.client.get('/settings') as page:
            self.assertIn(b'id="admin-india-only"', page.data)
        self.assertEqual(self.post('/settings', self.settings_data(
            admin_india_filter_present='1', admin_india_only='on')).status_code, 302)
        self.assertIs(self.preferences()['admin_india_only'], True)
        self.assertEqual(self.post('/settings', self.settings_data()).status_code, 302)
        self.assertIs(self.preferences()['admin_india_only'], True)
        self.assertEqual(self.post('/settings', self.settings_data(
            admin_india_filter_present='1')).status_code, 302)
        self.assertNotIn('admin_india_only', self.preferences())
        self.assertEqual(self.conn.execute('SELECT preferences_json FROM users WHERE id=3').fetchone()[0], '{}')
        self.assertEqual(self.conn.execute('SELECT is_stale FROM scores WHERE user_id=3').fetchone()[0], 0)

    def test_invalid_admin_checkbox_rejected(self):
        self.conn.execute('UPDATE accounts SET is_admin=1 WHERE user_id=1')
        self.conn.commit()
        response = self.post('/settings', self.settings_data(
            admin_india_filter_present='1', admin_india_only='yes'))
        self.assertEqual(response.status_code, 400)

    def test_manual_scoring_rejects_unclear_location_before_model_use(self):
        self.conn.execute('UPDATE accounts SET is_admin=1 WHERE user_id=1')
        self.conn.execute('UPDATE users SET preferences_json=? WHERE id=1',
                          (json.dumps(dict(admin_india_only=True)),))
        self.conn.commit()
        with self.client.session_transaction() as session:
            session['csrf'] = 'synthetic-csrf'
        with self.client.post('/score', json=dict(job_description='Python engineering role. Remote.'),
                              headers={'X-CSRF-Token': 'synthetic-csrf'}) as response:
            self.assertEqual(response.status_code, 400)
            self.assertIn('India', response.json['error'])


class AdminIndiaWorkerTests(unittest.TestCase):
    call = test_cloud_worker.CloudEndpointTests.call

    def setUp(self):
        test_cloud_worker.CloudEndpointTests.setUp(self)

    def configure(self, admin, enabled):
        self.conn.execute('UPDATE accounts SET is_admin=? WHERE user_id=1', (admin,))
        self.conn.execute('UPDATE users SET preferences_json=? WHERE id=1',
                          (json.dumps(dict(admin_india_only=enabled)),))
        self.conn.execute("UPDATE jobs SET location=CASE id WHEN 1 THEN 'India' ELSE 'Remote - US' END")
        self.conn.commit()

    def test_admin_enabled_filters_work_without_changing_user_payload(self):
        self.configure(1, True)
        response = self.call('work')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['job']['id'] for item in response.json['items']], [1])
        self.assertEqual(set(response.json['items'][0]['user']), {'id', 'resume_text', 'preferences_json'})

    def test_regular_forged_preference_and_admin_default_off_keep_global_jobs(self):
        for admin, enabled in ((0, True), (1, False)):
            self.configure(admin, enabled)
            with self.subTest(admin=admin):
                response = self.call('work')
                self.assertEqual({item['job']['id'] for item in response.json['items']}, {1, 2})

    def test_trusted_gate_overrides_forged_internal_marker(self):
        self.configure(0, True)
        user = dict(self.conn.execute('SELECT * FROM users WHERE id=1').fetchone())
        user['_admin_india_only'] = True
        self.assertFalse(pipeline.account_filtering_user(self.conn, user)['_admin_india_only'])

    def test_score_submission_rechecks_admin_location_policy(self):
        self.configure(1, False)
        item = next(item for item in self.call('work').json['items'] if item['job']['id'] == 2)
        self.configure(1, True)
        user = self.conn.execute('SELECT id,resume_text,preferences_json FROM users WHERE id=1').fetchone()
        job = self.conn.execute('SELECT * FROM jobs WHERE id=2').fetchone()
        fingerprint = test_cloud_worker.cloud_worker.fingerprint(
            test_cloud_worker.cloud_worker.work_identity(user, job))
        response = self.call('score', dict(user_id=1, job_id=item['job']['id'], fingerprint=fingerprint, result={}))
        self.assertEqual(response.status_code, 409)
        self.assertIn('preferences', response.json['error'])
        self.assertEqual(self.conn.execute('SELECT is_stale FROM scores WHERE job_id=2').fetchone()[0], 1)

    def test_html_description_can_confirm_remote_india(self):
        self.configure(1, True)
        self.conn.execute("UPDATE jobs SET location='Remote',raw_json=? WHERE id=2",
                          (json.dumps(dict(content='<p>This role is remote and open to candidates based in India.</p>')),))
        self.conn.commit()
        response = self.call('work')
        self.assertEqual({item['job']['id'] for item in response.json['items']}, {1, 2})

    def test_similarity_queue_uses_same_admin_filter(self):
        self.configure(1, True)
        response = self.call('similarity/work')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['job']['id'] for item in response.json['items']], [1])

    def test_resolved_description_rechecks_location(self):
        self.configure(1, True)
        self.conn.execute("UPDATE companies SET career_source='hotstar' WHERE id=1")
        self.conn.execute('UPDATE jobs SET raw_json=? WHERE id=1',
                          (json.dumps(dict(career_source='hotstar')),))
        self.conn.commit()
        item = self.call('work').json['items'][0]
        response = self.call('description', dict(user_id=1, job_id=1, fingerprint=item['fingerprint'],
            description='<p>Python and SQL; complete JD tail.</p>', title=item['job']['title'],
            location='Remote - US'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['eligible'])
        self.assertEqual(self.conn.execute('SELECT is_active FROM jobs WHERE id=1').fetchone()[0], 1)
