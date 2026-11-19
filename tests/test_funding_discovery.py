"""Funding discovery registers only name-verified boards and stays bounded."""

import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import funding_discovery as fd
from backend.app import create_app
from backend.migrate import apply_migrations
from backend.pipeline_skeleton import SCHEMA

NOW = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
MIGRATION = Path(__file__).parents[1] / 'backend/migrations/058_funded_companies.sql'


def database():
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(SCHEMA)
    conn.executescript(MIGRATION.read_text(encoding='utf-8'))
    return conn


def entry(title, days_ago=1, link='https://entrackr.com/2026/11/post'):
    return dict(title=title, link=link, published=NOW - timedelta(days=days_ago))


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Acme Pay raises $12M in Series A</title><link>https://entrackr.com/a</link>
<pubDate>Wed, 18 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>Evil raises $1M</title><link>https://evil.example/a</link>
<pubDate>Wed, 18 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>No date raises $1M</title><link>https://inc42.com/b</link></item>
</channel></rss>"""


class HeadlineTests(unittest.TestCase):
    def test_company_names_come_only_from_funding_headlines(self):
        self.assertEqual(fd.company_name('Acme Pay raises $12M in Series A'), 'Acme Pay')
        self.assertEqual(fd.company_name('Lumen Health AI secures $4M seed'), 'Lumen Health AI')
        self.assertIsNone(fd.company_name('Government approves new startup policy'))

    def test_keys_ignore_legal_suffixes_and_punctuation(self):
        self.assertEqual(fd.key_of('Acme Pay Pvt Ltd'), fd.key_of('acme-pay'))
        self.assertEqual(fd.slug_variants('Acme Pay Inc.'), ['acmepay', 'acme-pay'])


class FeedTests(unittest.TestCase):
    def test_entrackr_uses_published_rss_endpoint(self):
        self.assertIn('https://entrackr.com/rss', fd.FEEDS)
        self.assertNotIn('https://entrackr.com/feed', fd.FEEDS)
        with patch('backend.funding_discovery.requests.get') as get:
            response = get.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_content.return_value = [RSS.encode('utf-8')]
            text = fd.fetch_feed(fd.FEEDS[0], time.monotonic() + 30)
            self.assertEqual(len(fd.parse_feed(text)), 1)
            self.assertEqual(get.call_args.args[0], 'https://entrackr.com/rss')

    def test_parse_keeps_only_dated_allowlisted_entries(self):
        entries = fd.parse_feed(RSS)
        self.assertEqual([e['title'] for e in entries], ['Acme Pay raises $12M in Series A'])

    def test_entity_declarations_are_rejected(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss><channel/></rss>'
        with self.assertRaises(ValueError):
            fd.parse_feed(bomb)

    def test_fetch_refuses_hosts_outside_the_allowlist(self):
        with patch('backend.funding_discovery.requests.get') as get:
            with self.assertRaises(ValueError):
                fd.fetch_feed('https://evil.example/feed', time.monotonic() + 30)
            get.assert_not_called()


class BoardTests(unittest.TestCase):
    def test_lever_and_ashby_require_matching_posting_employer(self):
        for ats, host in (('lever', 'jobs.lever.co'), ('ashby', 'jobs.ashbyhq.com')):
            with self.subTest(ats=ats), patch.object(fd, 'greenhouse_board', return_value=None), \
                    patch('backend.pipeline_skeleton.fetch_jobs_for_company',
                          side_effect=lambda board: [{'url': f'https://{host}/acme/job-id'}]
                          if board['ats_type'] == ats else []), \
                    patch('backend.tavily_search.read_record') as read:
                read.return_value = {'hiringOrganization': {'name': 'Other'}}
                self.assertIsNone(fd.resolve_board('Acme', time.monotonic() + 30))
                read.return_value = {'hiringOrganization': {'name': 'Acme'}}
                self.assertEqual(fd.resolve_board('Acme', time.monotonic() + 30), (ats, 'acme'))

    def response(self, status, body=None):
        mock = MagicMock(status_code=status, content=b'{}')
        mock.json.return_value = body
        mock.raise_for_status.return_value = None
        return mock

    def test_board_must_carry_the_same_company_name(self):
        deadline = time.monotonic() + 30
        with patch('backend.funding_discovery.requests.get',
                   return_value=self.response(200, {'name': 'Acme Pay Inc'})):
            self.assertEqual(fd.greenhouse_board('Acme Pay', deadline), 'acmepay')
        with patch('backend.funding_discovery.requests.get',
                   return_value=self.response(200, {'name': 'Acme Plumbing'})):
            self.assertIsNone(fd.greenhouse_board('Acme Pay', deadline))
        with patch('backend.funding_discovery.requests.get', return_value=self.response(404)):
            self.assertIsNone(fd.greenhouse_board('Acme Pay', deadline))


class IngestTests(unittest.TestCase):
    def test_verified_board_is_registered_once_and_linked(self):
        with closing(database()) as conn:
            events = [entry('Acme Pay raises $12M in Series A')]
            first = fd.ingest(conn, events, lambda name: 'acmepay', NOW)
            second = fd.ingest(conn, events, lambda name: 'acmepay', NOW)
            self.assertEqual(first, dict(recorded=1, linked=1))
            self.assertEqual(second, dict(recorded=0, linked=0))
            self.assertEqual(conn.execute("SELECT name,ats_type,board_slug,active FROM companies").fetchall(),
                             [('Acme Pay', 'greenhouse', 'acmepay', 1)])
            self.assertIsNotNone(conn.execute('SELECT company_id FROM funded_companies').fetchone()[0])

    def test_unverified_company_stays_a_lead_and_can_link_later(self):
        with closing(database()) as conn:
            events = [entry('Lumen Health AI secures $4M seed')]
            fd.ingest(conn, events, lambda name: None, NOW)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0], 0)
            self.assertIsNone(conn.execute('SELECT company_id FROM funded_companies').fetchone()[0])
            result = fd.ingest(conn, events, lambda name: 'lumenhealthai', NOW)
            self.assertEqual(result, dict(recorded=0, linked=1))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM funded_companies').fetchone()[0], 1)
            self.assertIsNotNone(conn.execute('SELECT company_id FROM funded_companies').fetchone()[0])

    def test_ninety_day_boundary_future_dates_and_all_existing_ats(self):
        for ats in ('greenhouse', 'lever', 'ashby'):
            with self.subTest(ats=ats), closing(database()) as conn:
                conn.execute("INSERT INTO companies(name,ats_type,board_slug) VALUES('Acme',?, 'acme')", (ats,))
                result = fd.ingest(conn, [entry('Acme raises $1M', days_ago=90),
                    entry('Future raises $1M', days_ago=-1)], lambda name: self.fail('Existing board must be reused'), NOW)
                self.assertEqual(result, dict(recorded=1, linked=1))
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0], 1)

    def test_saved_lead_links_from_catalog_after_headline_leaves_feed(self):
        with closing(database()) as conn:
            fd.ingest(conn, [entry('Acme raises $1M')], None, NOW)
            conn.execute("INSERT INTO companies(name,ats_type,board_slug) VALUES('Acme','lever','acme-careers')")
            result = fd.ingest(conn, (), None, NOW + timedelta(days=2))
            self.assertEqual(result, dict(recorded=0, linked=1))
            self.assertEqual(conn.execute('SELECT company_id FROM funded_companies').fetchone()[0], 1)

    def test_ambiguous_catalog_names_and_conflicting_boards_stay_unlinked(self):
        with closing(database()) as conn:
            conn.executemany("INSERT INTO companies(name,ats_type,board_slug) VALUES(?,?,?)", [
                ('Acme', 'lever', 'acme'),
                ('Acme Inc', 'ashby', 'acme'),
                ('Other', 'lever', 'other'),
            ])
            result = fd.ingest(conn, [entry('Acme raises $1M'), entry('Beta raises $1M')],
                               lambda name: ('lever', 'other'), NOW)
            self.assertEqual(result, dict(recorded=2, linked=0))
            self.assertEqual(conn.execute('SELECT count(company_id) FROM funded_companies').fetchone()[0], 0)

    def test_old_events_are_ignored(self):
        with closing(database()) as conn:
            result = fd.ingest(conn, [entry('Old Co raises $1M', days_ago=91)], lambda name: 'oldco', NOW)
            self.assertEqual(result, dict(recorded=0, linked=0))

    def test_existing_company_record_is_linked_not_modified(self):
        with closing(database()) as conn:
            conn.execute("INSERT INTO companies(id,name,ats_type,board_slug,active) VALUES(7,'Acme Pay','greenhouse','acmepay',0)")
            fd.ingest(conn, [entry('Acme Pay raises $12M')], lambda name: 'acmepay', NOW)
            self.assertEqual(conn.execute('SELECT id,active FROM companies').fetchall(), [(7, 0)])
            self.assertEqual(conn.execute('SELECT company_id FROM funded_companies').fetchone()[0], 7)

    def test_probe_budget_is_bounded(self):
        with closing(database()) as conn:
            events = [entry('Company%d raises $1M' % i, days_ago=1) for i in range(fd.MAX_PROBES + 20)]
            calls = []
            fd.ingest(conn, events, lambda name: calls.append(name), NOW)
            self.assertEqual(len(calls), fd.MAX_PROBES)

    def test_a_failing_feed_does_not_stop_the_others(self):
        with closing(database()) as conn:
            def fetch(url, deadline):
                if 'inc42' in url:
                    raise ValueError('down')
                return RSS
            result = fd.run(conn, now=NOW, fetch=fetch, resolve=lambda name: None, feeds=fd.FEEDS)
            self.assertEqual(result['recorded'], 1)


class FundedPageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / 'jobs.db'
        self.conn = sqlite3.connect(self.database)
        self.addCleanup(self.conn.close)
        self.conn.executescript(SCHEMA)
        apply_migrations(self.conn)
        application = create_app(self.database)
        application.testing = True
        self.client = application.test_client()

    def sign_in(self):
        self.conn.execute("INSERT INTO users(id,name,email,resume_text) VALUES (1,'Student','one@example.test','Resume')")
        self.conn.execute("INSERT INTO accounts(user_id,password_hash,created_at,email_verified) VALUES(1,'unused-test-fixture','2026-10-01T00:00:00+00:00',1)")
        self.conn.commit()
        with self.client.session_transaction() as session:
            session['user_id'], session['session_version'] = 1, 0

    def test_funded_sort_supports_flat_and_company_pages(self):
        from backend import store
        self.conn.row_factory = sqlite3.Row
        self.sign_in()
        for cards in (False, True):
            selected = store.filters({'sort': 'funded'})
            selected['company_cards'] = cards
            rows, pages = store.matches(self.conn, 1, selected, NOW)
            self.assertEqual(rows, [])
            self.assertEqual(pages, 1)

    def test_recent_funding_prioritizes_actual_jobs_and_expires(self):
        from backend import store
        self.conn.row_factory = sqlite3.Row
        self.sign_in()
        for identifier, score in ((1, 95), (2, 65)):
            self.conn.execute("INSERT INTO companies(id,name,ats_type,board_slug) VALUES(?,?,'lever',?)",
                              (identifier, 'Company' + str(identifier), str(identifier)))
            self.conn.execute("""INSERT INTO jobs(id,company_id,external_id,title,url,first_seen_at,last_seen_at)
                VALUES(?,?,?,'Developer','https://example.test/job',?,?)""",
                (identifier, identifier, str(identifier), NOW.isoformat(), NOW.isoformat()))
            self.conn.execute("""INSERT INTO scores(job_id,user_id,fit_score,reasoning,matched_skills,missing_requirements,scored_at)
                VALUES(?,1,?,'Fit','[]','[]',?)""", (identifier, score, NOW.isoformat()))
        fd.ingest(self.conn, [entry('Company2 raises $1M')], lambda name: None, NOW)
        for cards in (False, True):
            selected = store.filters({'sort': 'funded'})
            selected['company_cards'] = cards
            rows, _ = store.matches(self.conn, 1, selected, NOW)
            self.assertEqual([row['job_id'] for row in rows], [2, 1])
            rows, _ = store.matches(self.conn, 1, selected, NOW + timedelta(days=91))
            self.assertEqual([row['job_id'] for row in rows], [1, 2])
            selected['min_score'] = 80
            rows, _ = store.matches(self.conn, 1, selected, NOW)
            self.assertEqual([row['job_id'] for row in rows], [1])

    def test_page_requires_sign_in(self):
        response = self.client.get('/funded')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_page_lists_verified_companies_with_matches_and_leads_safely(self):
        self.sign_in()
        self.conn.execute("INSERT INTO companies(id,name,ats_type,board_slug) VALUES(1,'Acme Pay','greenhouse','acmepay')")
        self.conn.execute("""INSERT INTO jobs(id,company_id,external_id,title,location,url,first_seen_at,last_seen_at,is_active)
            VALUES(1,1,'1','Android Developer','Bengaluru','https://example.test/1','2026-11-18T00:00:00+00:00','2026-11-18T00:00:00+00:00',1)""")
        self.conn.execute("""INSERT INTO scores(job_id,user_id,fit_score,reasoning,matched_skills,missing_requirements,scored_at)
            VALUES(1,1,81,'Good fit','[]','[]','2026-11-18T00:00:00+00:00')""")
        for key, name, headline, company in (('acmepay', 'Acme Pay', 'Acme Pay raises $12M', 1),
                                             ('lumen', 'Lumen', '<script>alert(1)</script> raises $4M', None)):
            self.conn.execute("""INSERT INTO funded_companies(name,name_key,headline,news_url,published_at,first_seen_at,company_id)
                VALUES(?,?,?,?,?,?,?)""", (name, key, headline, 'https://entrackr.com/x',
                                         '2026-11-18T00:00:00+00:00', '2026-11-19T00:00:00+00:00', company))
        self.conn.commit()
        response = self.client.get('/funded')
        self.assertEqual(response.status_code, 200)
        for text in (b'Acme Pay', b'1 open role', b'best match 81%', b'No verified job board yet'):
            self.assertIn(text, response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)


if __name__ == '__main__':
    unittest.main()
