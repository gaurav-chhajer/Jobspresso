"""Bounded, credential-free worker observations for administrators."""

from datetime import datetime, timedelta, timezone


def save(conn, value):
    if (not isinstance(value, dict) or set(value) != {'worker', 'company_id', 'state', 'job_count', 'error_code'}
            or value['worker'] not in ('fetch', 'score')
            or type(value['company_id']) is not int or not 0 <= value['company_id'] <= 2147483647
            or value['state'] not in ('running', 'idle', 'failed', 'success')
            or type(value['job_count']) is not int or not 0 <= value['job_count'] <= 1000000
            or value['error_code'] not in ('', 'source_error', 'transfer_error', 'worker_error')):
        raise ValueError('Invalid worker observation')
    stamp = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute('''INSERT INTO worker_health(worker,company_id,state,updated_at,last_success_at,job_count,error_code)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(worker,company_id) DO UPDATE SET
            state=excluded.state,updated_at=excluded.updated_at,
            last_success_at=COALESCE(excluded.last_success_at,worker_health.last_success_at),
            job_count=excluded.job_count,error_code=excluded.error_code''',
            (value['worker'], value['company_id'], value['state'], stamp,
             stamp if value['state'] == 'success' else None, value['job_count'], value['error_code']))


def observations(conn, now):
    result = {}
    for row in conn.execute('SELECT * FROM worker_health'):
        item = dict(row)
        item['display_state'] = ('Stale' if now - datetime.fromisoformat(item['updated_at']) > timedelta(minutes=15)
                                 else item['state'].title())
        result[(item['worker'], item['company_id'])] = item
    return result
