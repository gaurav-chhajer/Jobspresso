"""Offline fixtures for the requested remote-employer feeds and safe activation."""

import io
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

from backend import career_sources as sources
from backend import remote_career_sources as remote
from backend.migrate import apply_migrations
from backend.job_ingestion import mode_for_source
from backend.pipeline_skeleton import SCHEMA
from scripts.run_local_fetch import seed_companies

IDENTIFIER = '0118abfd-9378-431a-af25-1e7bf1a5cb76'


class RemoteCareerTests(unittest.TestCase):
    def candidate(self, key, identifier, title, url):
        result = sources.job(key, identifier, title, 'India', url)
        result['raw_json'] = json.dumps(result['raw_json'])
        return result

    def test_remote_listing_and_full_advert_sections_preserve_tail(self):
        item = dict(public_id=IDENTIFIER, title='Engineer', location='India',
                    accepting_applications=True, employment_type='Full-time')
        with patch.object(sources.Feed, 'json', return_value={'jobs': [item]}):
            jobs = sources.fetch('remote')
        self.assertEqual(jobs[0]['url'], 'https://apply.remote.com/jobs/' + IDENTIFIER)
        item['advert_sections'] = [dict(doc=dict(type='doc', content=[
            dict(type='paragraph', content=[dict(text='Python & SQL FINAL REQUIREMENT')])]))]
        with patch.object(sources.Feed, 'json', return_value={'job': item}):
            result = sources.prepare_job(self.candidate('remote', IDENTIFIER, 'Engineer', jobs[0]['url']))
        self.assertIn('SQL FINAL REQUIREMENT', json.loads(result['raw_json'])['descriptionHtml'])
        item['public_id'] = 'wrong'
        with patch.object(sources.Feed, 'json', return_value={'job': item}):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                sources.prepare_job(self.candidate('remote', IDENTIFIER, 'Engineer', jobs[0]['url']))

    def test_atlassian_composition_and_full_rich_text_are_data_only(self):
        item = dict(fields=dict(id='123', title='Engineer', locations=['Bengaluru', 'Remote'],
                                category='Engineering', type='Full-Time'))
        composition = dict(type='careersGallery', parameters=dict(data=dict(value=[item])))
        page = 'window._uniformPreloadedComposition = ' + json.dumps(composition) + ';'
        with patch.object(sources.Feed, 'request', return_value=page):
            jobs = sources.fetch('atlassian')
        self.assertEqual(jobs[0]['location'], 'Bengaluru; Remote')
        item['fields']['locations'] = None
        with patch.object(sources.Feed, 'request', return_value='window._uniformPreloadedComposition=' + json.dumps(composition)):
            self.assertIsNone(sources.fetch('atlassian')[0]['location'])
        body = dict(nodeType='document', content=[dict(nodeType='paragraph', content=[
            dict(nodeType='text', value='<Python> FINAL REQUIREMENT')])])
        detail = dict(content=[dict(type='heading', parameters=dict(text=dict(value='Engineer'))),
            dict(type='bodyContent', parameters=dict(body=dict(value=body)))])
        with patch.object(sources.Feed, 'request', return_value='window._uniformPreloadedComposition=' + json.dumps(detail)):
            result = sources.prepare_job(self.candidate('atlassian', '123', 'Engineer', jobs[0]['url']))
        self.assertIn('&lt;Python&gt; FINAL REQUIREMENT', json.loads(result['raw_json'])['descriptionHtml'])
        with self.assertRaises(ValueError):
            remote.composition('window._uniformPreloadedComposition=runUntrustedCode()')

    def test_shopify_listing_and_nested_microdata_description(self):
        url = 'https://www.shopify.com/careers/engineer_' + IDENTIFIER
        listing = '<a href="/careers/engineer_' + IDENTIFIER + '" data-component-extra-job-posting-id="' + IDENTIFIER + '"><h3>Engineer</h3><p>Remote - India</p></a>'
        with patch.object(sources.Feed, 'request', return_value=listing):
            jobs = sources.fetch('shopify')
        self.assertEqual(jobs[0]['url'], url)
        detail = '<article data-component-extra-job-posting-id="' + IDENTIFIER + '"><span itemprop="title" content="Engineer"></span><div itemprop="description"><p>Python</p><ul><li>FINAL REQUIREMENT &amp; SQL</li></ul></div></article>'
        with patch.object(sources.Feed, 'request', return_value=detail):
            result = sources.prepare_job(self.candidate('shopify', IDENTIFIER, 'Engineer', url))
        self.assertIn('<li>FINAL REQUIREMENT &amp; SQL</li>', json.loads(result['raw_json'])['descriptionHtml'])
        with self.assertRaises(ValueError):
            remote.Element('<div itemprop="description"><p>truncated', 'itemprop', 'description')

    def test_deel_listing_uses_published_application_identity(self):
        url = 'https://jobs.deel.com/deel/job-details/' + IDENTIFIER + '/appl'
        item = dict(attributes=dict(ashby_id=IDENTIFIER, title='Engineer', external_link=url, is_listed=True,
                                   locations_list=[dict(location='India')], employment_type='Full-time'))
        payload = '1:' + json.dumps(dict(jobs=[item])) + '\n'
        page = '<script>self.__next_f.push(' + json.dumps([1, payload]) + ')</script>'
        with patch.object(sources.Feed, 'request', return_value=page):
            jobs = sources.fetch('deel')
        self.assertEqual(jobs[0]['url'], url)
        posting = dict(**{'@type': 'JobPosting'}, title='Engineer', description='Python FINAL REQUIREMENT')
        detail = '<script type="application/ld+json">' + json.dumps(posting) + '</script>' + '\\"id\\":\\"' + IDENTIFIER + '\\"'
        with patch.object(sources.Feed, 'request', return_value=detail):
            result = sources.prepare_job(self.candidate('deel', IDENTIFIER, 'Engineer', url))
        self.assertIn('FINAL REQUIREMENT', json.loads(result['raw_json'])['descriptionHtml'])
        item['attributes']['external_link'] = 'https://untrusted.example/job'
        payload = '1:' + json.dumps(dict(jobs=[item])) + '\n'
        page = '<script>self.__next_f.push(' + json.dumps([1, payload]) + ')</script>'
        with patch.object(sources.Feed, 'request', return_value=page):
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                sources.fetch('deel')

    def test_missing_custom_listings_fail_explicitly(self):
        for key in ('shopify', 'atlassian', 'deel'):
            with self.subTest(key=key), patch.object(sources.Feed, 'request', return_value='<html>No feed</html>'):
                with self.assertRaises(ValueError):
                    sources.fetch(key)

    def test_new_sources_reject_untrusted_hosts_without_making_requests(self):
        for key in ('deel', 'remote', 'shopify', 'atlassian'):
            with self.subTest(key=key), patch.object(sources.requests, 'Session') as session:
                feed = sources.Feed(key, sources.time.monotonic() + 30)
                try:
                    with self.assertRaisesRegex(ValueError, 'allowlisted host'):
                        feed.request('https://untrusted.example/job')
                    session.return_value.request.assert_not_called()
                finally:
                    feed.close()

    def test_source_activation_is_idempotent_and_keeps_existing_subscriptions(self):
        keys = ('highlevel', 'gitlab', 'grafana', 'wikimedia', 'remote', 'atlassian', 'toptal',
                'supabase', 'deel', 'elastic', 'zapier', 'automattic', 'shopify', 'github', 'duckduckgo', 'canonical')
        for key in keys:
            self.assertEqual(mode_for_source(key),
                             'observed' if key in ('remote', 'atlassian', 'deel', 'shopify', 'github') else 'snapshot')
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript(SCHEMA)
            apply_migrations(conn)
            conn.execute("INSERT INTO companies(id,name,ats_type,board_slug,career_source,active) VALUES(7,'DuckDuckGo','greenhouse','career-site-duckduckgo','duckduckgo',0)")
            conn.execute("INSERT INTO users(id,name,email,resume_text) VALUES(1,'Fixture','fixture@example.test','Python')")
            conn.execute("INSERT INTO company_subscriptions(user_id,company_id,subscribed_at) VALUES(1,7,'2026-11-15')")
            with redirect_stdout(io.StringIO()):
                seed_companies(conn)
                first = conn.execute('SELECT id,name,career_source,active FROM companies ORDER BY id').fetchall()
                seed_companies(conn)
            self.assertEqual(first, conn.execute('SELECT id,name,career_source,active FROM companies ORDER BY id').fetchall())
            states = dict(conn.execute('SELECT career_source,active FROM companies'))
            self.assertTrue(all(states[key] == 1 for key in keys))
            self.assertEqual(conn.execute('SELECT company_id FROM company_subscriptions').fetchall(), [(7,)])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO companies(name,ats_type,board_slug,career_source) VALUES('Invalid','greenhouse','invalid','unverified-source')")
