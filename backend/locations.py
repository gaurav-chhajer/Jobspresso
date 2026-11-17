"""Offline country/city matching using GeoNames; no external geocoding calls."""
from functools import lru_cache
import re
import unicodedata
from geonamescache import GeonamesCache


def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c)).split())


@lru_cache(maxsize=1)
def geography():
    """Build one bounded dataset index per process, outside per-row SQL work."""
    source=GeonamesCache(min_city_population=500)
    countries={}
    for code,country in source.get_countries().items():
        for alias in (code,country['iso3'],country['name']):
            countries[normalized(alias)]=code
    for alias,code in {'uk':'GB','usa':'US','u.s.a.':'US','u.s.':'US','uae':'AE','south korea':'KR','north korea':'KP','russia':'RU','vietnam':'VN','czech republic':'CZ','turkey':'TR','bharat':'IN'}.items():
        countries[alias]=code
    cities={}
    city_countries={}
    us_state_cities={}
    for identifier,city in source.get_cities().items():
        identifier=int(identifier)
        city_countries[identifier]=city['countrycode']
        if city['countrycode']=='US':
            us_state_cities.setdefault(city['admin1code'].casefold(),set()).add(identifier)
        for alias in (city['name'],*city['alternatenames']):
            name=normalized(alias)
            if name and (len(name)>=3 or alias==city['name']):
                previous=cities.get(name)
                if previous is None:
                    cities[name]=identifier
                elif isinstance(previous,int):
                    if previous!=identifier:
                        cities[name]=(previous,identifier)
                elif identifier not in previous:
                    cities[name]=(*previous,identifier)
    regions={normalized(state['name']):'US' for state in source.get_us_states().values()}
    for name in INDIAN_STATES:
        regions[name]='IN'
    return countries,cities,city_countries,regions,us_state_cities


INDIAN_STATES=frozenset("""andhra pradesh|arunachal pradesh|assam|bihar|chhattisgarh|goa|gujarat|haryana|himachal pradesh|jharkhand|karnataka|kerala|madhya pradesh|maharashtra|manipur|meghalaya|mizoram|nagaland|odisha|orissa|punjab|rajasthan|sikkim|tamil nadu|telangana|tripura|uttar pradesh|uttarakhand|west bengal|andaman and nicobar|chandigarh|dadra and nagar haveli|daman and diu|delhi|new delhi|jammu and kashmir|ladakh|lakshadweep|puducherry|pondicherry""".split('|'))


def city_ids(cities, name):
    value=cities.get(name)
    return () if value is None else (value,) if isinstance(value,int) else value


def places(value):
    countries,cities,city_countries,regions,us_state_cities=geography()
    parts=[normalized(p) for p in re.split(r'[,/|()]|\s+-\s+',value)]
    parts=[re.sub(r'^(?:remote|hybrid|onsite|on-site)\s*[-:]\s*','',p) for p in parts]
    explicit={countries[p] for p in parts if p in countries}
    ids=set().union(*(city_ids(cities,p) for p in parts))
    for part in parts:
        if part in us_state_cities and ids & us_state_cities[part]:
            explicit.discard(countries.get(part))
            explicit.add('US')
    if explicit:
        ids={identifier for identifier in ids if city_countries[identifier] in explicit}
        return explicit,ids
    inferred={regions[p] for p in parts if p in regions}
    for part in parts:
        candidates={city_countries[i] for i in city_ids(cities,part)}
        if len(candidates)==1:
            inferred.update(candidates)
    return inferred,ids


def matches(location, query):
    """Match countries through unambiguous cities, and city aliases through IDs.

    Semicolons separate offices with independent country context. Short country
    codes must be standalone segments. Remote-only/unknown values imply no country.
    """
    if not isinstance(location,str) or not isinstance(query,str):
        return False
    location,query=location.strip(),query.strip()
    if not query:
        return True
    countries,cities,city_countries,regions,us_state_cities=geography()
    wanted=normalized(query)
    query_countries,query_ids=places(query)
    for office in location.split(';'):
        office_countries,office_ids=places(office)
        if wanted in countries:
            if countries[wanted] in office_countries:
                return True
        elif query_ids:
            common=query_ids & office_ids
            if common and (not query_countries or not office_countries or query_countries & office_countries):
                return True
        elif wanted in regions:
            if wanted in normalized(office) and regions[wanted] in office_countries:
                return True
        elif wanted in normalized(office):
            return True
    return False


_REMOTE_WORK = re.compile(r'\b(?:remote|remotely|work from home|work from anywhere|wfh)\b', re.I)
_WORLDWIDE_WORK = re.compile(
    r'\b(?:worldwide|world[- ]wide|globally|global|any country)\b|'
    r'\banywhere in the world\b|\banywhere\b(?!\s+in\b)', re.I)
_INDIA_EXCLUSION = re.compile(
    r'\b(?:except|excluding|outside)\s+india\b|'
    r'\bnot\s+(?:(?:available|open|eligible|supported|permitted)\s+)?(?:(?:in|from|to|for)\s+)?india\b|'
    r'\b(?:cannot|can\x27t|do not|don\x27t|unable to|not able to)\s+(?:currently\s+)?'
    r'(?:hire|employ|support)\b[^.;\n]{0,60}\bindia\b|'
    r'\bindia\b[^.;\n]{0,35}\b(?:not eligible|not supported|excluded|not permitted)\b', re.I)
_ELIGIBILITY_LINE = re.compile(
    r'\bthis (?:role|position|job)\b|\b(?:candidates?|applicants?|employees?)\b|'
    r'\bwe (?:can )?hire\b|\bwe are hiring\b|\bwork(?:ing)? (?:remotely )?from\b|'
    r'\b(?:job )?location\s*:|\b(?:remote(?: work)?|eligible (?:countries|locations)|eligibility)\s*:', re.I)
_INDIA_WORK = re.compile(
    r'\b(?:based|located|resid(?:e|ing)|liv(?:e|ing)|work(?:ing)?)\s+(?:remotely\s+)?'
    r'(?:in|from)\s+(?:anywhere in\s+)?india\b|'
    r'\b(?:location|eligibility|eligible (?:countries|locations)|remote(?: work)?(?: eligibility| locations?)?)'
    r'\s*[:=-][^.;\n]{0,120}\bindia\b|'
    r'\bthis (?:role|position|job)\b[^.;\n]{0,100}\b(?:open|available|remote|based|located)\b'
    r'[^.;\n]{0,60}\bindia\b|\bhire\s+remotely\s+(?:in|from)\s+india\b', re.I)
_RESIDENCY_REQUIREMENT = re.compile(
    r'\b(?:must|need to|required to)\s+(?:currently\s+)?(?:be\s+)?'
    r'(?:based|located|resident|reside|live|work)\s+(?:in|within)\s+(?:the\s+)?([^.;\n]{1,150})', re.I)
_ONLY_REGION = re.compile(r'\b([a-z][a-z ]{1,35}?)[ -]only\b|\bonly\s+(?:in|within)\s+(?:the\s+)?([^.;\n]{1,100})', re.I)
_WORLDWIDE_PERMISSION = re.compile(
    r'\bthis (?:role|position|job)\b|\bhire\s+(?:remotely\s+)?(?:worldwide|globally)\b|'
    r'\b(?:you|candidates?|applicants?|employees?)\s+(?:can|may)\s+work\b|'
    r'\bwork(?:ing)?\s+(?:remotely\s+)?from\b|\bremote(?: work)?(?: eligibility| locations?)?\s*:', re.I)


def _india_residency_allowed(value):
    """Explicit regional restrictions need a recognized India option."""
    if re.search(r'\bindia\b', value, re.I):
        return True
    countries = set()
    for region in re.split(r'[,;/]|\s+(?:or|and)\s+', value, flags=re.I):
        words = region.strip().split()
        for length in range(1, min(len(words), 6) + 1):
            identified, _ = places(' '.join(words[:length]))
            if identified:
                countries.update(identified)
                break
    return 'IN' in countries


def india_work_eligible(location, description=''):
    """Conservative work-location eligibility, not an immigration assessment.

    A foreign remote location is not worldwide. Company mentions of India do not
    establish hiring eligibility, and unspecified remote locations stay unknown.
    """
    if location is None:
        location = ''
    if (not isinstance(location, str) or not isinstance(description, str)
            or len(location) > 4000 or len(description) > 251000):
        return False
    lines = [line.strip() for line in description.splitlines()
             if (_ELIGIBILITY_LINE.search(line) or _INDIA_EXCLUSION.search(line)
                 or _RESIDENCY_REQUIREMENT.search(line))]
    policy = [location, *lines]
    if any(_INDIA_EXCLUSION.search(line) for line in policy):
        return False
    for line in policy:
        restrictions = [match.group(1) for match in _RESIDENCY_REQUIREMENT.finditer(line)]
        restrictions.extend(match.group(1) or match.group(2) for match in _ONLY_REGION.finditer(line))
        if any(not _india_residency_allowed(value) for value in restrictions):
            return False
    if matches(location, 'India') or re.search(r'\bindia\b', location, re.I):
        return True
    for line in lines:
        explicit_location = re.match(r'\s*(?:job\s+)?location\s*:\s*(.+)', line, re.I)
        if explicit_location and matches(explicit_location.group(1).rstrip('.'), 'India'):
            return True
    # Each location office retains its own country context. An explicitly foreign
    # remote office must not borrow a worldwide claim from company prose.
    for office in location.split(';'):
        countries, _ = places(office)
        if countries and 'IN' not in countries:
            continue
        if _REMOTE_WORK.search(office) and _WORLDWIDE_WORK.search(office):
            return True
    location_countries = set().union(*(places(office)[0] for office in location.split(';')))
    if location_countries and 'IN' not in location_countries:
        return False
    remote = bool(_REMOTE_WORK.search(location) or any(_REMOTE_WORK.search(line) for line in lines))
    if not remote:
        return False
    return any(_INDIA_WORK.search(line) or
               (_REMOTE_WORK.search(line) and _WORLDWIDE_WORK.search(line)
                and _WORLDWIDE_PERMISSION.search(line)) for line in lines)
