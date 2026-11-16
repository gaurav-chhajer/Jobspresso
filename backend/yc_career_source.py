"""Public YC directory observations; curated pages are never closure evidence."""

import json
import re
from urllib.parse import urlsplit

from backend import career_sources as sources


def properties(text):
    pages = [attrs['data-page'] for attrs in sources.Page(text).attributes if 'data-page' in attrs]
    if len(pages) != 1:
        raise ValueError('YC public page data is missing or ambiguous')
    value = json.loads(pages[0])
    if not isinstance(value, dict) or not isinstance(value.get('props'), dict):
        raise ValueError('YC public page data is invalid')
    return value['props']


def employer(raw):
    """Validate source-owned employer identity before any database writes."""
    name, path = raw.get('employer_name'), raw.get('employer_path')
    if (not isinstance(name, str) or not name.strip() or len(name) > 300 or
            not isinstance(path, str) or not re.fullmatch(r'/companies/[a-zA-Z0-9-]{1,200}', path)):
        raise ValueError('YC employer identity is invalid')
    return name.strip(), path.rsplit('/', 1)[1]


def listings(feed, rows):
    root = properties(feed.request('/jobs/role/all'))
    roles = root.get('jobRoles')
    locations = root.get('jobLocations')
    if not isinstance(roles, list) or not isinstance(locations, list) or len(roles) > 20 or len(locations) > 30:
        raise ValueError('YC public directory categories are invalid')
    def slugs(items):
        result = []
        for item in items:
            slug = item.get('slug') if isinstance(item, dict) else None
            if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', slug):
                raise ValueError('YC public directory category is invalid')
            result.append(slug)
        return result
    roles = ['all'] + slugs(roles)
    locations = [''] + list(dict.fromkeys(slugs(locations) + ['remote']))
    seen = {}
    for role in roles:
        for location in locations:
            path = '/jobs/role/' + role + ('/' + location if location else '')
            page = root if path == '/jobs/role/all' else properties(feed.request(path))
            items = page.get('jobPostings')
            if not isinstance(items, list) or len(items) > sources.MAX_LISTINGS:
                raise ValueError('YC public listings are invalid')
            for item in items:
                if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0:
                    raise ValueError('YC job identity is invalid')
                raw = dict(career_source='yc', employer_name=item.get('companyName'), employer_path=item.get('companyUrl'))
                employer(raw)
                url = item.get('url')
                if (not isinstance(url, str) or not url.startswith(raw['employer_path'] + '/jobs/') or
                        urlsplit(url).netloc or urlsplit(url).query or urlsplit(url).fragment or
                        not re.fullmatch(r'/companies/[a-zA-Z0-9-]+/jobs/[a-zA-Z0-9_-]+', url)):
                    raise ValueError('YC job URL is invalid')
                identifier = 'yc-' + str(item['id'])
                row = sources.job('yc', identifier, item.get('title'), item.get('location'),
                                  feed.origin + url, employment=item.get('type'))
                row['raw_json'].update(raw)
                if identifier in seen and seen[identifier] != row:
                    raise ValueError('YC job changed across directory pages')
                if identifier not in seen:
                    rows.append(row)
                    seen[identifier] = row
                if len(rows) > sources.MAX_LISTINGS:
                    raise ValueError('YC listing limit exceeded')
    return rows
