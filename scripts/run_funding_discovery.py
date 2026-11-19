"""Collect recent funding news and register verified ATS boards.

Default is a preview: feeds and boards are read, then the database changes are
rolled back. --run saves them and fetches jobs from active funded boards.
The normal matching pipeline handles scoring.
"""

import argparse
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path

from backend import funding_discovery, pipeline_skeleton as pipeline

logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='keep the recorded funding events and boards')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        with closing(sqlite3.connect(Path(pipeline.DB_PATH).resolve().as_uri() + '?mode=rw', uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            result = funding_discovery.run(conn)
            if args.run:
                conn.commit()
                now = datetime.now(timezone.utc)
                boards = conn.execute("""SELECT c.* FROM companies c WHERE c.active=1 AND c.discovery_only=0
                    AND EXISTS (SELECT 1 FROM funded_companies f WHERE f.company_id=c.id
                    AND julianday(f.published_at) BETWEEN julianday(?) AND julianday(?))""",
                    ((now - timedelta(days=funding_discovery.RECENT_DAYS)).isoformat(), now.isoformat())).fetchall()
                total = failures = 0
                for board in boards:
                    try:
                        jobs = pipeline.fetch_jobs_for_company(board)
                        pipeline.upsert_jobs(conn, board['id'], jobs)
                        total += len(jobs)
                    except (ValueError, KeyError, TypeError, funding_discovery.requests.RequestException) as error:
                        failures += 1
                        logger.warning('Funded board fetch failed: company_id=%s error=%s', board['id'], type(error).__name__)
                logger.info('Funded boards=%s jobs_fetched=%s failures=%s', len(boards), total, failures)
            else:
                conn.rollback()
        logger.info('Funding discovery %s: recorded=%s boards_linked=%s',
                    'saved' if args.run else 'preview (nothing saved)', result['recorded'], result['linked'])
        return int(args.run and failures > 0)
    except (sqlite3.Error, OSError, ValueError, funding_discovery.requests.RequestException) as error:
        logger.error('Funding discovery stopped (%s)', type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
