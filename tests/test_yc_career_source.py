import html
import json
import sqlite3
import unittest
from unittest.mock import patch

import requests

from backend import career_sources, pipeline_skeleton as pipeline
from backend.migrate import apply_migrations
from backend.yc_career_source import employer
from scripts.sync_hosted import identity


def page(items):
    props = dict(jobPostings=items, jobRoles=[], jobLocations=[])
    return '<div data-page="' + html.escape(json.dumps(dict(props=props)), quote=True) + '"></div>'


def posting(number=1, company='Alpha', slug='alpha'):
    return dict(id=number, title='Software Engineer', location='Remote', type='Full-time',
                companyName=company, companyUrl='/companies/' + slug,
                url='/companies/' + slug + '/jobs/abc-engineer')


class YCTests(unittest.TestCase):
    def fetch(self, items):
        with patch.object(career_sources.Feed, 'request', return_value=page(items)):
            return career_sources.fetch('yc')

    def test_repeated_directory_rows_are_deduplicated(self):
        rows = self.fetch([posting(), posting()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['external_id'], 'yc-1')
        self.assertEqual(rows[0]['raw_json']['employer_name'], 'Alpha')

    def test_failed_later_page_retains_observations(self):
        with patch.object(career_sources.Feed, 'request', side_effect=[page([posting()]), requests.Timeout]), \
                self.assertLogs('backend.career_sources', level='WARNING'):
            self.assertEqual(len(career_sources.fetch('yc')), 1)

    def test_invalid_employer_and_url_fail(self):
        for item in (dict(posting(), companyName=''), dict(posting(), url='https://evil.test/job'),
                     dict(posting(), companyUrl='/companies/../admin'), dict(posting(), id=True)):
            with self.assertRaises(ValueError):
                self.fetch([item])
        with self.assertRaises(ValueError):
            employer({})

    def test_missing_page_data_and_empty_directory(self):
        with patch.object(career_sources.Feed, 'request', return_value='<html></html>'):
            with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
                career_sources.fetch('yc')
        self.assertEqual(self.fetch([]), [])

    def test_employers_and_repeat_ingestion_and_empty_observation(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript(pipeline.SCHEMA)
        apply_migrations(conn)
        conn.execute("INSERT INTO companies(id,name,ats_type,board_slug,career_source) VALUES(1,'YC','greenhouse','career-site-yc','yc')")
        conn.commit()
        rows = self.fetch([posting(), posting(2, 'Beta', 'beta')])
        pipeline.upsert_jobs(conn, 1, rows)
        pipeline.upsert_jobs(conn, 1, rows)
        pipeline.upsert_jobs(conn, 1, [])
        saved = conn.execute('SELECT c.name,j.external_id,j.is_active FROM jobs j JOIN companies c ON c.id=j.company_id ORDER BY j.external_id').fetchall()
        self.assertEqual([tuple(row) for row in saved], [('Alpha', 'yc-1', 1), ('Beta', 'yc-2', 1)])
        for row in conn.execute("SELECT * FROM companies WHERE discovery_only=1"):
            self.assertEqual(identity(row), ('career', 'yc'))
        before = conn.total_changes
        invalid = dict(rows[0], external_id='yc-3', raw_json={})
        with self.assertRaises(ValueError):
            pipeline.upsert_jobs(conn, 1, [rows[0], invalid])
        self.assertEqual(conn.total_changes, before)

    def test_full_description_uses_existing_structured_detail_path(self):
        row = self.fetch([posting()])[0]
        row['raw_json'] = json.dumps(row['raw_json'])
        detail = dict({'@type': 'JobPosting'}, title='Software Engineer', description='<p>Build reliable Python services.</p>',
                      hiringOrganization={'name': 'Alpha'})
        with patch.object(career_sources.Feed, 'request', return_value='<script type="application/ld+json">' + json.dumps(detail) + '</script>'):
            prepared = career_sources.prepare_job(row)
        self.assertEqual(json.loads(prepared['raw_json'])['descriptionHtml'], detail['description'])
        detail['hiringOrganization']['name'] = 'Another startup'
        with patch.object(career_sources.Feed, 'request', return_value='<script type="application/ld+json">' + json.dumps(detail) + '</script>'):
            with self.assertRaisesRegex(ValueError, 'employer changed'):
                career_sources.prepare_job(row)
