"""Back up and add the thirty verified missing ATS boards; no external requests."""
import sqlite3
import logging
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from backend.pipeline_skeleton import DB_PATH
from scripts.run_pipeline import exclusive_cycle

SQL = "-- Import the previously verified missing ATS catalog without replacing IDs or history.\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Mercor','ashby','mercor',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='mercor');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Mirendil','ashby','mirendil',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='mirendil');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Mavria','ashby','mavria',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='mavria');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Rox','ashby','Rox-Data-Corp',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='Rox-Data-Corp');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Daylight Security','greenhouse','daylightsecurity',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='greenhouse' AND board_slug='daylightsecurity');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Coframe','ashby','coframe',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='coframe');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Pliant','ashby','pliant',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='pliant');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Traversal','ashby','traversal',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='traversal');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Northwood Space','ashby','NorthwoodSpace',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='NorthwoodSpace');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Serval','ashby','serval',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='serval');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Wabi','ashby','wabi',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='wabi');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Infisical','ashby','infisical',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='infisical');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Hyperbolic','ashby','hyperbolic',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='hyperbolic');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Hex','ashby','hex',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='hex');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'General Compute','ashby','generalcompute',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='generalcompute');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Whop','ashby','whop',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='whop');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Legora','ashby','legora',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='legora');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Blacksmith','ashby','Blacksmith',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='Blacksmith');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Helicon','ashby','helicon',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='helicon');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Town','ashby','town',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='town');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Greptile','ashby','greptile',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='greptile');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Modal','ashby','modal',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='modal');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Causa Prima','ashby','causaprima',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='causaprima');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Mecka','ashby','mecka.ai',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='mecka.ai');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Vesta','ashby','vesta',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='vesta');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Preference Model','ashby','Preference-Model',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='Preference-Model');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Siena AI','ashby','siena',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='siena');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Melius','ashby','melius',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='melius');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Namespace','ashby','namespace',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='namespace');\nINSERT INTO companies(name,ats_type,board_slug,active) SELECT 'Armadin','ashby','armadin',1 WHERE NOT EXISTS (SELECT 1 FROM companies WHERE ats_type='ashby' AND board_slug='armadin');\n"

def import_boards(conn):
    with conn:
        for statement in SQL.splitlines():
            if statement and not statement.startswith('--'):
                conn.execute(statement)

def main():
    database = Path(DB_PATH).resolve(strict=True)
    directory = database.parent / 'backups'
    directory.mkdir(exist_ok=True)
    backup = directory / ('jobs-before-missing-boards-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
    with exclusive_cycle(database.parent / 'pipeline.lock'):
        with backup.open('xb'):
            pass
        with closing(sqlite3.connect(database.as_uri()+'?mode=rw', uri=True, timeout=5)) as conn:
            with closing(sqlite3.connect(backup, timeout=5)) as target:
                deadline = time.monotonic() + 60
                def progress(status, remaining, total):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Backup time budget exhausted')
                conn.backup(target, pages=100, progress=progress, sleep=0.1)
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup integrity check failed')
            import_boards(conn)
    return 0

if __name__ == '__main__':
    try:
        status = main()
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        logging.getLogger(__name__).error('Company import stopped (%s); inspect database and backup storage', type(error).__name__)
        status = 1
    raise SystemExit(status)
