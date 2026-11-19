"""Recent-funding discovery: funding headlines to verified Greenhouse boards.

Feed content is untrusted. Only a company name is taken from a headline, and a
board is registered only when the public Greenhouse board's own name equals that
company name after normalisation, so a guessed slug can never attach another
employer's jobs. Registered boards flow through the existing fetch, scoring and
digest pipeline; companies without a verified board are kept as leads only.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import requests

logger = logging.getLogger(__name__)

FEEDS = ('https://entrackr.com/rss', 'https://inc42.com/feed/', 'https://yourstory.com/feed')
FEED_HOSTS = ('entrackr.com', 'inc42.com', 'yourstory.com')
GREENHOUSE_BOARD = 'https://boards-api.greenhouse.io/v1/boards/'
MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 200
MAX_NAME = 80
MAX_REDIRECTS = 2
RECENT_DAYS = 90
MAX_PROBES = 60
TIME_BUDGET_SECONDS = 120
LEGAL_SUFFIXES = {'inc', 'ltd', 'llc', 'pvt', 'private', 'limited', 'corp'}
HEADLINE = re.compile(
    r"^(?P<name>[A-Z0-9][\w.&'\- ]{1,%d}?)\s+(?i:raises|secures|bags|closes|lands|"
    r"mops up|picks up|gets|garners|nets)\b" % MAX_NAME)


def allowed_host(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username
            and not parsed.password and any(host == h or host.endswith('.' + h) for h in FEED_HOSTS))


def fetch_feed(url, deadline):
    """Read one bounded feed body; redirects stay on allowlisted hosts."""
    for _ in range(MAX_REDIRECTS + 1):
        if not allowed_host(url):
            raise ValueError('Feed host is not allowlisted')
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise ValueError('Funding discovery time budget exhausted')
        with requests.get(url, timeout=(min(10, remaining), min(20, remaining)),
                          allow_redirects=False, stream=True,
                          headers={'User-Agent': 'jobspresso-funding/1.0'}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES or time.monotonic() >= deadline:
                    raise ValueError('Feed exceeds size or time limits')
                chunks.append(chunk)
            return b''.join(chunks).decode('utf-8', 'replace')
    raise ValueError('Too many feed redirects')


def parse_feed(text):
    """Return [{'title','link','published'}] from an RSS document; reject entity tricks."""
    lowered = text[:4096].lower()
    if '<!doctype' in lowered or '<!entity' in lowered or '<!entity' in text.lower():
        raise ValueError('Feed declares entities')
    root = ET.fromstring(text)
    entries = []
    for item in list(root.iter('item'))[:MAX_ENTRIES]:
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        try:
            published = parsedate_to_datetime((item.findtext('pubDate') or '').strip())
            published = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if title and allowed_host(link):
            entries.append(dict(title=title[:300], link=link[:2000], published=published.astimezone(timezone.utc)))
    return entries


def company_name(headline):
    match = HEADLINE.match(headline.strip())
    return match.group('name').strip() if match else None


def key_of(name):
    tokens = [t for t in re.findall(r'[a-z0-9]+', name.casefold()) if t not in LEGAL_SUFFIXES]
    return ''.join(tokens)


def slug_variants(name):
    tokens = [t for t in re.findall(r'[a-z0-9]+', name.casefold()) if t not in LEGAL_SUFFIXES]
    variants = [''.join(tokens), '-'.join(tokens)]
    return [v for i, v in enumerate(variants) if v and len(v) <= 60 and v not in variants[:i]]


def greenhouse_board(name, deadline):
    """Return a Greenhouse slug whose own board name matches, else None."""
    key = key_of(name)
    for slug in slug_variants(name):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise ValueError('Funding discovery time budget exhausted')
        response = requests.get(GREENHOUSE_BOARD + slug, timeout=(min(5, remaining), min(10, remaining)),
                                allow_redirects=False)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        board = response.json() if len(response.content) <= 100000 else None
        if isinstance(board, dict) and isinstance(board.get('name'), str) and key_of(board['name']) == key:
            return slug
    return None


def resolve_board(name, deadline):
    from backend.pipeline_skeleton import fetch_jobs_for_company
    from backend.tavily_search import read_record

    for ats, host in (('greenhouse', ''), ('lever', 'jobs.lever.co'), ('ashby', 'jobs.ashbyhq.com')):
        if time.monotonic() >= deadline - 1:
            break
        try:
            if ats == 'greenhouse':
                slug = greenhouse_board(name, deadline)
                if slug:
                    return ats, slug
                continue
            for slug in slug_variants(name):
                if time.monotonic() >= deadline - 1:
                    return None
                try:
                    jobs = fetch_jobs_for_company({'ats_type': ats, 'board_slug': slug})
                    for job in jobs[:1]:
                        url = job.get('url')
                        prefix = f'https://{host}/{slug}/'
                        if not isinstance(url, str) or not url.startswith(prefix):
                            continue
                        if not re.fullmatch(r'[a-zA-Z0-9-]+/?', url[len(prefix):]):
                            continue
                        organization = read_record(url, deadline).get('hiringOrganization')
                        employer = organization.get('name') if isinstance(organization, dict) else None
                        if isinstance(employer, str) and key_of(employer) == key_of(name):
                            return ats, slug
                except requests.HTTPError as error:
                    if error.response is None or error.response.status_code != 404:
                        raise
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            logger.warning('Funding board verification skipped: ats=%s error=%s', ats, type(error).__name__)
    return None


def ingest(conn, entries, resolve, now, days=RECENT_DAYS):
    """Record recent funding events; link or register boards that resolve.

    The caller owns the transaction. `resolve(name)` returns an ATS/slug pair or None.
    """
    cutoff = now - timedelta(days=days)
    stamp = now.astimezone(timezone.utc).isoformat()
    recorded = linked = probes = 0
    for entry in sorted(entries, key=lambda e: e['published'], reverse=True):
        name = company_name(entry['title'])
        key = key_of(name) if name else ''
        if not key or not cutoff <= entry['published'] <= now:
            continue
        row = conn.execute('SELECT id,company_id FROM funded_companies WHERE name_key=?', (key,)).fetchone()
        if row and row[1] is not None:
            conn.execute('''UPDATE funded_companies SET headline=?,news_url=?,published_at=?
                WHERE id=? AND julianday(published_at)<julianday(?)''',
                (entry['title'], entry['link'], entry['published'].isoformat(), row[0], entry['published'].isoformat()))
            continue
        existing = [company for company in conn.execute(
            "SELECT id,name FROM companies WHERE ats_type IN ('greenhouse','lever','ashby')")
            if key_of(company[1]) == key]
        slug = None
        if not existing and probes < MAX_PROBES:
            probes += 1
            slug = resolve(name)
        company_id = existing[0][0] if len(existing) == 1 else None
        if company_id is not None:
            linked += 1
        if slug:
            ats, slug = slug if isinstance(slug, tuple) else ('greenhouse', slug)
            conn.execute("""INSERT OR IGNORE INTO companies(name,ats_type,board_slug,active)
                VALUES(?, ?, ?, 1)""", (name, ats, slug))
            company_id = conn.execute("SELECT id FROM companies WHERE ats_type=? AND board_slug=?",
                                      (ats, slug)).fetchone()[0]
            linked += 1
        if row:
            conn.execute('''UPDATE funded_companies SET company_id=?,headline=?,news_url=?,published_at=?
                WHERE id=?''', (company_id, entry['title'], entry['link'], entry['published'].isoformat(), row[0]))
        else:
            conn.execute("""INSERT INTO funded_companies(name,name_key,headline,news_url,published_at,first_seen_at,company_id)
                VALUES(?,?,?,?,?,?,?)""", (name, key, entry['title'], entry['link'],
                                         entry['published'].isoformat(), stamp, company_id))
            recorded += 1
    return dict(recorded=recorded, linked=linked)


def run(conn, now=None, fetch=fetch_feed, resolve=None, feeds=FEEDS):
    """Collect feeds and ingest them. A failing feed is skipped, never fatal."""
    now = now or datetime.now(timezone.utc)
    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    resolve = resolve or (lambda name: resolve_board(name, deadline))
    entries = []
    for url in feeds:
        try:
            entries += parse_feed(fetch(url, deadline))
        except (requests.RequestException, ValueError, ET.ParseError) as error:
            logger.warning('Funding feed skipped: host=%s error=%s', urlsplit(url).hostname, type(error).__name__)
    return ingest(conn, entries, resolve, now)
