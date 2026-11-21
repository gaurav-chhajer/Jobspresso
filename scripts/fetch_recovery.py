"""Durable direct-feed recovery. Only acknowledged transfers advance a company."""

from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
import uuid

import requests

from backend import career_sources
from backend.job_ingestion import deduplicate, mode_for_source, observation_batches
from scripts import run_cloud_worker as worker

logger = logging.getLogger(__name__)
STATE_PATH = Path(__file__).resolve().parents[1] / 'data' / 'feed_recovery.db'
REFRESH_SECONDS = 3600
MAX_FEED_BYTES = 64 * 1024 * 1024
MAX_SPOOL_BYTES = 512 * 1024 * 1024


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(endpoint, payload):
    return hashlib.sha256(encode(dict(endpoint=endpoint, payload=payload)).encode()).hexdigest()


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS feeds (
        company_id INTEGER PRIMARY KEY, company TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
        payload TEXT, batch_index INTEGER NOT NULL DEFAULT 0, request TEXT, previous_token TEXT,
        last_success REAL, job_count INTEGER, error TEXT NOT NULL DEFAULT '')''')
    return conn


def synchronize(conn, companies):
    if (not isinstance(companies, list) or not 1 <= len(companies) <= worker.MAX_COMPANIES or
            any(not isinstance(c, dict) or type(c.get('id')) is not int or c['id'] <= 0 or
                not isinstance(c.get('fingerprint'), str) or len(c['fingerprint']) != 64 for c in companies) or
            len({c['id'] for c in companies}) != len(companies)):
        raise ValueError('Invalid recovery catalog')
    with conn:
        for company in companies:
            stored = conn.execute('SELECT company FROM feeds WHERE company_id=?', (company['id'],)).fetchone()
            if stored and json.loads(stored['company'])['fingerprint'] != company['fingerprint']:
                conn.execute("UPDATE feeds SET status='pending',attempts=0,due=0,payload=NULL,"
                             "batch_index=0,request=NULL,error='catalog_changed' WHERE company_id=?", (company['id'],))
            conn.execute('''INSERT INTO feeds(company_id,company,status) VALUES(?,?,'pending')
                ON CONFLICT(company_id) DO UPDATE SET company=excluded.company''', (company['id'], encode(company)))
        ids = {c['id'] for c in companies}
        for row in conn.execute('SELECT company_id FROM feeds').fetchall():
            if row['company_id'] not in ids:
                conn.execute("UPDATE feeds SET status='inactive',payload=NULL,request=NULL WHERE company_id=?",
                             (row['company_id'],))
        for company in companies:
            conn.execute("UPDATE feeds SET status='pending',due=0 WHERE company_id=? AND status='inactive'",
                         (company['id'],))


def receipt(client, company_id):
    value = client.call('feed-receipt?company_id=' + str(company_id))
    if 'receipt' not in value:
        raise ValueError('Hosted release does not provide transfer receipts')
    result = value['receipt']
    if result is not None and (not isinstance(result, dict) or
            not isinstance(result.get('token'), str) or len(result['token']) != 32 or
            not isinstance(result.get('digest'), str) or len(result['digest']) != 64 or
            type(result.get('saved')) is not int or result['saved'] < 0):
        raise ValueError('Invalid hosted transfer receipt')
    return result


def attempt(conn, client, row, deadline, stop):
    company = json.loads(row['company'])
    company_id = company['id']
    payload = json.loads(row['payload']) if row['payload'] else None
    previous = row['previous_token']
    if payload is None:
        current = receipt(client, company_id)
        previous = current['token'] if current else None
        # Partial observed pages remain valid elsewhere, but cannot prove this cycle complete.
        source = company.get('career_source')
        jobs = (career_sources.fetch(source, deadline - 120, require_complete=True) if source else
                worker._download_company(company, deadline, stop))
        mode = mode_for_source(source)
        jobs = deduplicate(jobs, mode)
        envelope = dict(company_id=company_id, fingerprint=company['fingerprint'], mode=mode)
        endpoint = 'snapshot'
        # Reserve room for transfer identity before enforcing existing request limits.
        if mode == 'snapshot' and (len(jobs) > worker.MAX_JOBS or
                len(json.dumps(dict(envelope, jobs=jobs), allow_nan=False).encode()) > worker.MAX_BODY_BYTES - 256):
            mode, endpoint = 'observed', 'observations'
            envelope['mode'] = mode
        batches = (list(observation_batches(envelope, jobs, worker.MAX_JOBS, worker.MAX_BODY_BYTES - 256))
                   if mode == 'observed' else [dict(envelope, jobs=jobs)])
        payload = dict(endpoint=endpoint, batches=batches, count=len(jobs))
        serialized = encode(payload)
        size = len(serialized.encode())
        used = conn.execute('SELECT coalesce(sum(length(cast(payload AS BLOB))),0) FROM feeds').fetchone()[0]
        if size > MAX_FEED_BYTES or used + size > MAX_SPOOL_BYTES:
            raise ValueError('Durable feed spool size limit reached')
        with conn:
            conn.execute("UPDATE feeds SET payload=?,status='transferring',batch_index=0,request=NULL,previous_token=? WHERE company_id=?",
                         (serialized, previous, company_id))
    index = row['batch_index'] if row['payload'] else 0
    pending = json.loads(row['request']) if row['request'] else None
    for index in range(index, len(payload['batches'])):
        if stop.is_set() or time.monotonic() >= deadline - 120:
            return
        current = receipt(client, company_id)
        if pending is None:
            pending = dict(payload['batches'][index], transfer=dict(
                token=uuid.uuid4().hex, previous=previous))
            with conn:
                conn.execute('UPDATE feeds SET request=? WHERE company_id=?', (encode(pending), company_id))
        expected = digest(payload['endpoint'], pending)
        if current is not None and current['token'] == pending['transfer']['token']:
            reply = current  # Reconcile a committed write whose response was lost.
        else:
            reply = client.call(payload['endpoint'], pending)
        if (reply.get('token') != pending['transfer']['token'] or reply.get('digest') != expected or
                type(reply.get('saved')) is not int or reply['saved'] != len(pending['jobs'])):
            raise ValueError('Job transfer was not fully acknowledged')
        with conn:
            previous = pending['transfer']['token']
            conn.execute('UPDATE feeds SET batch_index=?,request=NULL,previous_token=? WHERE company_id=?',
                         (index + 1, previous, company_id))
        pending = None
    now = time.time()
    with conn:
        conn.execute("UPDATE feeds SET status='saved',attempts=0,due=?,payload=NULL,request=NULL,"
                     "batch_index=0,last_success=?,job_count=?,error='' WHERE company_id=?",
                     (now + REFRESH_SECONDS, now, payload['count'], company_id))
    worker.report_health(client, 'fetch', 'success', company_id, payload['count'])
    logger.info('Verified feed save: company_id=%s jobs=%s', company_id, payload['count'])


def run_round(client, deadline, stop, *, path=STATE_PATH):
    """One bounded attempt per due company; the scheduler owns subsequent attempts."""
    worker.report_health(client, 'fetch', 'running')
    with closing(connect(path)) as conn:
        synchronize(conn, client.call('companies').get('companies'))
        rows = conn.execute("SELECT company_id FROM feeds WHERE status<>'inactive' AND due<=? ORDER BY due,company_id",
                            (time.time(),)).fetchall()
        for identity in rows:
            if stop.is_set() or time.monotonic() >= deadline - 120:
                break
            row = conn.execute('SELECT * FROM feeds WHERE company_id=?', (identity['company_id'],)).fetchone()
            try:
                worker.report_health(client, 'fetch', 'running', row['company_id'])
                attempt(conn, client, row, deadline, stop)
            except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as error:
                attempts = min(row['attempts'] + 1, 32)
                delay = min(3600, 60 * 2 ** min(attempts - 1, 6))
                response = error.response if isinstance(error, requests.RequestException) else None
                status = (error.status if isinstance(error, worker.HostedOperationError) else
                          response.status_code if response is not None else None)
                code = type(error).__name__ + (':' + str(status) if status else '')
                with conn:
                    if isinstance(error, worker.HostedInputsChanged):
                        conn.execute('UPDATE feeds SET payload=NULL,request=NULL,batch_index=0 WHERE company_id=?',
                                     (row['company_id'],))
                    conn.execute("UPDATE feeds SET status='pending',attempts=?,due=?,error=? WHERE company_id=?",
                                 (attempts, time.time() + delay, code, row['company_id']))
                transferred = conn.execute('SELECT payload IS NOT NULL FROM feeds WHERE company_id=?',
                                           (row['company_id'],)).fetchone()[0]
                worker.report_health(client, 'fetch', 'failed', row['company_id'],
                                     error_code='transfer_error' if transferred else 'source_error')
                logger.error('Feed remains pending: company_id=%s error=%s retry_seconds=%s attempts=%s',
                             row['company_id'], code, delay, attempts)
        counts = dict(conn.execute('SELECT status,count(*) FROM feeds GROUP BY status').fetchall())
        logger.info('Durable feed coverage: saved=%s pending=%s transferring=%s inactive=%s',
                    counts.get('saved', 0), counts.get('pending', 0), counts.get('transferring', 0), counts.get('inactive', 0))
        incomplete = bool(counts.get('pending', 0) or counts.get('transferring', 0))
        worker.report_health(client, 'fetch', 'failed' if incomplete else 'idle',
                             job_count=counts.get('saved', 0), error_code='worker_error' if incomplete else '')
    return 0
