"""Verified Lever employer identities and bounded platform observations."""

import io
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

import requests

from backend import career_sources as sources
from backend.job_ingestion import mode_for_source
from backend.migrate import apply_migrations
from backend.pipeline_skeleton import SCHEMA, extract_job_description
from scripts.run_local_fetch import seed_companies


class AdditionalCompanyTests(unittest.TestCase):
    def item(self, identifier='1'):
        return dict(id=identifier, text='Engineering Intern',
                    categories=dict(location='India', team='Engineering', commitment='Internship'),
                    hostedUrl='https://jobs.lever.co/jobgether/' + identifier,
                    description='<p>Opening</p>', lists=[dict(content='<p>Python</p>')],
                    additional='<p>FINAL REQUIREMENT</p>')

    def test_employers_use_verified_case_sensitive_board_slugs(self):
        for key, slug in (('drivetrain', 'drivetrain'), ('smartworking', 'smart-working-solutions'),
                          ('sprinto', 'Sprinto'), ('weekday', 'weekdayworks')):
            with self.subTest(key=key), patch.object(sources.Feed, 'request', return_value=json.dumps([self.item()])) as request:
                jobs = sources.fetch(key)
            params = dict(mode='json', limit=100, skip=0) if key == 'drivetrain' else dict(mode='json')
            request.assert_called_once_with('/v0/postings/' + slug, params=params)
            self.assertEqual(mode_for_source(key), 'observed' if key == 'drivetrain' else 'snapshot')
            self.assertEqual(jobs[0]['raw_json']['employmentType'], 'Internship')
            self.assertIn('FINAL REQUIREMENT', extract_job_description(dict(jobs[0], raw_json=json.dumps(jobs[0]['raw_json']))))

    def test_jobgether_reads_every_india_page_and_preserves_full_descriptions(self):
        pages = [[self.item('1'), self.item('2')], [self.item('3')]]
        with patch.object(sources, 'LEVER_PAGE_SIZE', 2), \
                patch.object(sources.Feed, 'request', side_effect=[json.dumps(page) for page in pages]) as request:
            jobs = sources.fetch('jobgether')
        self.assertEqual(len(jobs), 3)
        self.assertEqual([call.kwargs['params'] for call in request.call_args_list], [
            dict(mode='json', location='India', limit=2, skip=0),
            dict(mode='json', location='India', limit=2, skip=2)])
        self.assertEqual(mode_for_source('jobgether'), 'observed')
        self.assertIn('FINAL REQUIREMENT', jobs[-1]['raw_json']['descriptionHtml'])

    def test_repeated_failed_or_oversized_later_pages_preserve_only_accepted_observations(self):
        first = [self.item('1'), self.item('2')]
        for second in (json.dumps(first), requests.Timeout('private'), json.dumps(first + [self.item('3')])):
            with self.subTest(second=type(second).__name__), patch.object(sources, 'LEVER_PAGE_SIZE', 2), \
                    patch.object(sources.Feed, 'request', side_effect=[json.dumps(first), second]), \
                    self.assertLogs(sources.__name__, level='WARNING') as logs:
                jobs = sources.fetch('jobgether')
            self.assertEqual([job['external_id'] for job in jobs], ['1', '2'])
            self.assertNotIn('private', ' '.join(logs.output))

    def test_empty_posting_is_withheld_with_warning_and_later_postings_continue(self):
        empty = dict(self.item('2'), description='', lists=[], additional='')
        with patch.object(sources.Feed, 'request', return_value=json.dumps([self.item('1'), empty, self.item('3')])), \
                self.assertLogs(sources.__name__, level='WARNING') as logs:
            jobs = sources.fetch('drivetrain')
        self.assertEqual([job['external_id'] for job in jobs], ['1', '3'])
        self.assertIn('reason=empty_description', ' '.join(logs.output))

    def test_empty_and_invalid_first_pages_do_not_report_false_success(self):
        with patch.object(sources.Feed, 'request', return_value='[]'):
            self.assertEqual(sources.fetch('jobgether'), [])
        for invalid in ({}, [None], [dict(self.item(), id=None)]):
            with self.subTest(invalid=invalid), patch.object(sources.Feed, 'request', return_value=json.dumps(invalid)):
                with self.assertRaises(ValueError):
                    sources.fetch('jobgether')
        with patch.object(sources.Feed, 'request', side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                sources.fetch('jobgether')

    def test_catalog_is_idempotent_and_platform_names_do_not_invent_employers(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript(SCHEMA)
            apply_migrations(conn)
            with redirect_stdout(io.StringIO()):
                seed_companies(conn)
                before = conn.execute('SELECT id,name,career_source,active FROM companies ORDER BY id').fetchall()
                seed_companies(conn)
            self.assertEqual(before, conn.execute('SELECT id,name,career_source,active FROM companies ORDER BY id').fetchall())
            for key in ('drivetrain', 'smartworking', 'sprinto', 'jobgether', 'weekday', 'highlevel'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM companies WHERE career_source=? AND active=1', (key,)).fetchone()[0], 1)
            for key in ('jobgether', 'weekday'):
                self.assertIn('hiring platform', conn.execute('SELECT name FROM companies WHERE career_source=?', (key,)).fetchone()[0])
