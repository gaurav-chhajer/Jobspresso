"""Verified public career data for the requested distributed employers."""

import json
import re
from html import escape, unescape
from html.parser import HTMLParser

from backend import career_sources as sources

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def objects(value, depth=0):
    """Walk bounded published JSON without interpreting scripts or references."""
    if depth > 80:
        raise ValueError('Career data nesting exceeds its limit')
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child, depth + 1)


def composition(text):
    marker = re.search(r'window\._uniformPreloadedComposition\s*=\s*', text)
    if marker is None:
        raise ValueError('Atlassian career composition is missing')
    value, _ = json.JSONDecoder().raw_decode(text[marker.end():])
    if not isinstance(value, dict):
        raise ValueError('Atlassian career composition is invalid')
    return value


def rich_text(node, depth=0):
    if depth > 80 or not isinstance(node, dict):
        raise ValueError('Career rich text is invalid')
    if node.get('nodeType') == 'text' or 'text' in node:
        value = node['value'] if node.get('nodeType') == 'text' else node['text']
        if not isinstance(value, str):
            raise ValueError('Career rich text is invalid')
        return escape(value)
    children = node.get('content')
    if not isinstance(children, list):
        raise ValueError('Career rich text is incomplete')
    text = ''.join(rich_text(child, depth + 1) for child in children)
    return text + ('\n' if node.get('nodeType') != 'document' else '')


class Element(HTMLParser):
    """Keep the full server-rendered description, including nested markup."""
    def __init__(self, text, attribute, value):
        super().__init__(convert_charrefs=False)
        self.attribute, self.value = attribute, value
        self.depth, self.parts, self.matches = 0, [], 0
        self.feed(text)
        if self.depth or self.matches != 1:
            raise ValueError('Career description element is missing or incomplete')

    def handle_starttag(self, tag, attrs):
        if not self.depth and dict(attrs).get(self.attribute) == self.value:
            self.depth = 0 if tag in ('meta', 'link') else 1
            self.matches += 1
            if 'content' in dict(attrs):
                self.parts.append(escape(dict(attrs)['content']))
        elif self.depth:
            self.parts.append(self.get_starttag_text())
            if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
                self.depth += 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if self.depth:
                self.parts.append('</' + tag + '>')

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text())

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self.depth:
            self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        if self.depth:
            self.parts.append('&#' + name + ';')


def listings(feed, rows):
    key, origin = feed.key, feed.origin
    if key == 'remote':
        items = feed.json('/api/public/jobs')['jobs']
        if not isinstance(items, list) or len(items) > sources.MAX_LISTINGS:
            raise ValueError('Remote career listing is invalid')
        for item in items:
            identifier = item['public_id']
            if not isinstance(identifier, str) or not re.fullmatch(UUID, identifier):
                raise ValueError('Remote career identifier is invalid')
            if item.get('accepting_applications') is not True:
                continue
            rows.append(sources.job(key, identifier, item['title'], item.get('location'),
                                   origin + '/jobs/' + identifier,
                                   version=item.get('opened_at'), employment=item.get('employment_type')))
    elif key == 'atlassian':
        data = composition(feed.request('/company/careers/all-jobs'))
        galleries = [node for node in objects(data) if node.get('type') == 'careersGallery']
        if len(galleries) != 1:
            raise ValueError('Atlassian jobs gallery is unavailable')
        items = galleries[0]['parameters']['data']['value']
        if not isinstance(items, list) or len(items) > sources.MAX_LISTINGS:
            raise ValueError('Atlassian career listing is invalid')
        for record in items:
            item = record['fields']
            identifier = item['id']
            locations = [] if item['locations'] is None else item['locations']
            if (not isinstance(identifier, str) or not identifier.isascii() or not identifier.isdigit() or
                    not isinstance(locations, list) or any(not isinstance(place, str) for place in locations)):
                raise ValueError('Atlassian career identity or locations are invalid')
            rows.append(sources.job(key, identifier, item['title'], '; '.join(locations) or None,
                                   origin + '/company/careers/details/' + identifier,
                                   department=item.get('category'), employment=item.get('type')))
    elif key == 'deel':
        text = feed.request('/careers/')
        batches = []
        for match in re.finditer(r'self\.__next_f\.push\((\[.*?\])\)\s*;?\s*</script>', text, re.S):
            record = json.loads(match.group(1))
            if not isinstance(record, list):
                raise ValueError('Deel career stream is invalid')
            if len(record) == 2 and isinstance(record[1], str):
                marker = re.search(r'"jobs":\s*(\[)', record[1])
                if marker:
                    items, _ = json.JSONDecoder().raw_decode(record[1][marker.start(1):])
                    batches.append(items)
        if len(batches) != 1 or not isinstance(batches[0], list) or len(batches[0]) > sources.MAX_LISTINGS:
            raise ValueError('Deel career listing is unavailable')
        for record in batches[0]:
            item = record['attributes']
            if item.get('is_listed') is not True:
                raise ValueError('Deel career publication state is invalid')
            identifier = item['ashby_id']
            if not isinstance(identifier, str) or not re.fullmatch(UUID, identifier):
                raise ValueError('Deel career identifier is invalid')
            url = item['external_link']
            base = 'https://jobs.deel.com/deel/job-details/' + identifier
            if url not in (base + '/appl', base + '/application'):
                raise ValueError('Deel career application identity changed')
            locations = item['locations_list']
            if not isinstance(locations, list) or any(not isinstance(place, dict) or not isinstance(place.get('location'), str) for place in locations):
                raise ValueError('Deel career locations are invalid')
            rows.append(sources.job(key, identifier, item['title'], '; '.join(place['location'] for place in locations),
                                   url, version=item.get('ashby_updated_at'),
                                   employment=item.get('employment_type')))
    elif key == 'shopify':
        text = feed.request('/careers')
        for match in re.finditer(r'<a\b([^>]*)>(.*?)</a>', text, re.S):
            attrs, body = match.groups()
            identifier = re.search(r'data-component-extra-job-posting-id="(' + UUID + ')"', attrs)
            if identifier is None:
                continue
            href = re.search(r'href="(/careers/[^"?]+_' + identifier[1] + ')"', attrs)
            title = re.search(r'<h3\b[^>]*>(.*?)</h3>', body, re.S)
            location = re.search(r'<p\b[^>]*>(.*?)</p>', body, re.S)
            if not href or not title or not location:
                raise ValueError('Shopify career card is incomplete')
            rows.append(sources.job(key, identifier[1], unescape(re.sub('<[^>]*>', '', title[1])),
                                   unescape(re.sub('<[^>]*>', '', location[1])), origin + unescape(href[1])))
        if not rows:
            raise ValueError('Shopify career listing is unavailable')
    return rows


def detail(feed, result):
    key, identifier = feed.key, result['external_id']
    if key == 'remote':
        if result['url'] != feed.origin + '/jobs/' + identifier or not re.fullmatch(UUID, identifier):
            raise ValueError('Remote career detail identity is invalid')
        item = feed.json('/api/public/jobs/' + identifier)['job']
        if item['public_id'] != identifier:
            raise ValueError('Remote career detail identity changed')
        sections = item['advert_sections']
        if not isinstance(sections, list):
            raise ValueError('Remote career description is unavailable')
        description = '\n'.join(rich_text(section['doc']) for section in sections)
        if not description.strip():
            raise ValueError('Remote career description is unavailable')
        return item['title'], item.get('location'), description, item.get('employment_type')
    if key == 'atlassian':
        if not identifier.isascii() or not identifier.isdigit() or result['url'] != feed.origin + '/company/careers/details/' + identifier:
            raise ValueError('Atlassian career detail identity is invalid')
        data = composition(feed.request(result['url']))
        nodes = list(objects(data))
        headings = [node['parameters']['text']['value'] for node in nodes if node.get('type') == 'heading']
        if result['title'] not in headings:
            raise ValueError('Atlassian career detail title changed')
        bodies = [node['parameters']['body']['value'] for node in nodes if node.get('type') == 'bodyContent']
        if not bodies:
            raise ValueError('Atlassian career description is unavailable')
        return result['title'], result.get('location'), '\n'.join(rich_text(body) for body in bodies), None
    if key == 'shopify':
        if not re.fullmatch(UUID, identifier) or not result['url'].startswith(feed.origin + '/careers/') or not result['url'].endswith('_' + identifier):
            raise ValueError('Shopify career detail identity is invalid')
        text = feed.request(result['url'])
        if 'data-component-extra-job-posting-id="' + identifier + '"' not in text:
            raise ValueError('Shopify career detail identity changed')
        title = unescape(re.sub('<[^>]*>', '', ''.join(Element(text, 'itemprop', 'title').parts))).strip()
        return title, result.get('location'), ''.join(Element(text, 'itemprop', 'description').parts), None
    if key == 'deel':
        if not re.fullmatch(UUID, identifier) or result['url'] not in ('https://jobs.deel.com/deel/job-details/' + identifier + '/appl', 'https://jobs.deel.com/deel/job-details/' + identifier + '/application'):
            raise ValueError('Deel career detail identity is invalid')
        text = feed.request(result['url'])
        if '\\"id\\":\\"' + identifier + '\\"' not in text:
            raise ValueError('Deel career detail identity changed')
        item = sources.structured_job(text)
        return item['title'], result.get('location'), item['description'], None
    raise ValueError('Unsupported distributed career source')
