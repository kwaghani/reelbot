"""Conversational retrieval over one person's saved entries.

Recovered from the deleted group-era `worker/retrieval.py` and rebuilt for the
personal library: structured date/location/type filters run in SQL, pgvector
ranks what survives, and one synthesis call may only cite candidate ids that
this module supplied. Fabricating a saved entry is the worst failure here, so
citations are validated against the candidate set after the model answers and
the answer context carries an allowlist of Class A fields only.
"""
from __future__ import annotations
import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import settings
from worker.db import connect, items, vector_literal
from worker.embed import embed_query

LOG = logging.getLogger('reelbot.ask')

MAX_QUESTION_CHARS = 500
MAX_HISTORY_TURNS = 8
MAX_HISTORY_CHARS = 400
MAX_CARRIED_IDS = 20
MAX_CANDIDATES = 8
PREFILTER_CEILING = 400
ANSWER_CHARS = 700

# Filters already bound the set, so a filtered search may keep looser matches.
SEMANTIC_FLOOR = {True: .30, False: .45}
LEXICAL_FLOOR = .12
RELATIVE_SPREAD = .35

# The exact contradiction the old app shipped: evidence supplied, answer denies it.
ANSWER_DENIES_EVIDENCE = re.compile(
    r"\b(?:"
    r"(?:do not|don't|dont|did not|didn't|cannot|can't|cant) (?:actually )?(?:have|see|find) (?:any|anything)|"
    r"(?:not|aren't|isn't) (?:actually )?(?:seeing|finding) (?:any|anything)|"
    r"nothing (?:is )?saved|nothing (?:here|to work with)|"
    r"no saved (?:items?|entries|reels?|places?)|working with nothing"
    r")\b", re.IGNORECASE)

STOPWORDS = {'a', 'about', 'any', 'are', 'can', 'could', 'did', 'do', 'for', 'from', 'give', 'good', 'have',
             'i', 'in', 'is', 'it', 'me', 'my', 'of', 'on', 'one', 'please', 'save', 'saved', 'show', 'some',
             'that', 'the', 'this', 'to', 'was', 'we', 'what', 'when', 'where', 'which', 'who', 'with', 'you', 'your'}


class AskUnavailable(RuntimeError):
    """The answer model could not be reached; saved entries are still returned."""


@dataclass
class QueryPlan:
    semantic_query: str
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    radius_km: float | None = None
    content_type: str | None = None
    venue_kind: str | None = None
    limit: int = MAX_CANDIDATES
    source: str = 'model'

    def payload(self):
        return {'semantic_query': self.semantic_query,
                'date_range': {'start': self.start.isoformat() if self.start else None,
                               'end': self.end.isoformat() if self.end else None},
                'location': {'text': self.location, 'radius_km': self.radius_km},
                'content_type': self.content_type, 'venue_kind': self.venue_kind,
                'limit': self.limit, 'source': self.source}


@dataclass
class AskMetrics:
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    unavailable: int = 0
    calls: list = field(default_factory=list)

    def record(self, label, response):
        self.requests += 1
        self.tokens_in += response.usage.input_tokens
        self.tokens_out += response.usage.output_tokens
        self.calls.append({'call': label, 'tokens_in': response.usage.input_tokens,
                           'tokens_out': response.usage.output_tokens})

    def priced(self):
        current = settings()
        usd = round(self.tokens_in * current.llm_input_usd_per_million / 1_000_000
                    + self.tokens_out * current.llm_output_usd_per_million / 1_000_000, 8)
        return {'llm_requests': self.requests, 'llm_tokens_in': self.tokens_in,
                'llm_tokens_out': self.tokens_out, 'calls': self.calls, 'usd': usd,
                'complete': not self.unavailable,
                'basis': 'Measured provider usage; configured API rates. Local embedding compute excluded.'
                         if not self.unavailable else
                         'Known usage only; provider usage was unavailable for one or more requests.'}


# --------------------------------------------------------------------------- #
# Query parsing
# --------------------------------------------------------------------------- #

def enums():
    """Registry-derived, never hardcoded: an eleventh type works untouched."""
    from worker.registry import registry, venue_kinds
    types = registry()
    return {'content_type': list(types),
            'venue_kind': list(venue_kinds()),
            'labels': {key: spec.get('plural_label') or spec['label'] for key, spec in types.items()}}


def user_now(zone, at=None):
    at = at or datetime.now(timezone.utc)
    try:
        return at.astimezone(ZoneInfo(zone)) if zone else at
    except (ZoneInfoNotFoundError, ValueError):
        return at


def widen(start, end, now):
    """"Two weeks ago" is a 10-to-20-day window, not day 14."""
    if start is None and end is None:
        return None, None
    if start is not None and end is not None:
        span, distance = end - start, now - (start + (end - start) / 2)
        wanted = max(timedelta(days=2), abs(distance) * RELATIVE_SPREAD)
        if span < wanted * 2:
            middle = start + span / 2
            start, end = middle - wanted, middle + wanted
    return start, min(end, now) if end else end


RELATIVE = [
    (r'\b(?:today|so far today)\b', 0, 0),
    (r'\b(?:yesterday)\b', 1, 1),
    (r'\b(?:this week|past few days|last few days|recently|the other day)\b', 0, 7),
    (r'\b(?:last week|a week ago|about a week ago)\b', 7, 7),
    (r'\b(?:two weeks?|2 weeks?|a couple of weeks?|couple weeks?|fortnight)\b', 14, 14),
    (r'\b(?:three weeks?|3 weeks?)\b', 21, 21),
    (r'\b(?:last month|a month ago|about a month ago|four weeks?|4 weeks?)\b', 30, 30),
    (r'\b(?:two months?|2 months?|a couple of months?)\b', 60, 60),
    (r'\b(?:this month|past month)\b', 0, 31),
    (r'\b(?:last year|a year ago)\b', 365, 365),
    (r'\b(?:this year)\b', 0, 365),
]


def relative_window(text, now):
    lowered = text.lower()
    match = re.search(r'\b(\d{1,3})\s+(day|week|month|year)s?\s+ago\b', lowered)
    if match:
        days = int(match.group(1)) * {'day': 1, 'week': 7, 'month': 30, 'year': 365}[match.group(2)]
        return widen(now - timedelta(days=days), now - timedelta(days=days), now)
    for pattern, oldest, newest in RELATIVE:
        if re.search(pattern, lowered):
            start, end = now - timedelta(days=max(oldest, newest)), now - timedelta(days=min(oldest, newest))
            return widen(start, end + timedelta(days=1), now) if oldest == newest else (start, min(end + timedelta(days=1), now))
    return None, None


# A place name ends where ordinary sentence words begin: "in New York did I
# save two weeks ago" is New York, not "New York Did I Save Two".
LOCATION_STOP = {
    'a', 'about', 'ago', 'an', 'and', 'any', 'are', 'around', 'at', 'best', 'but', 'can', 'could', 'day', 'days',
    'did', 'do', 'does', 'find', 'for', 'from', 'get', 'go', 'going', 'good', 'got', 'had', 'has', 'have', 'here',
    'how', 'i', 'in', 'is', 'it', 'last', 'library', 'list', 'me', 'month', 'months', 'my', 'near', 'need', 'next',
    'on', 'or', 'ours', 'our', 'recently', 'reel', 'reels', 'save', 'saved', 'saves', 'should', 'show', 'some',
    'that', 'the', 'there', 'this', 'to', 'today', 'tomorrow', 'tonight', 'us', 'want', 'was', 'we', 'week',
    'weeks', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'with', 'would', 'year', 'years', 'you', 'your',
}


def location_from_text(text):
    match = re.search(r'\b(?:in|near|around|at)\s+(.{1,60})', text)
    if not match:
        return None
    words = []
    for word in re.findall(r"[A-Za-z][A-Za-z.'-]*", match.group(1)):
        if word.lower() in LOCATION_STOP or len(words) >= 4:
            break
        # Keep NYC and SF as written; only lowercase input needs capitalizing.
        words.append(word if word[:1].isupper() else word.capitalize())
    hint = ' '.join(words).strip(" ,.'-")
    return hint if len(hint) > 1 else None


def deterministic_plan(text, available, now):
    """The floor under every model answer; also the malformed-JSON fallback."""
    lowered = text.lower()
    content_type = next((key for key, label in available['labels'].items()
                         if key != 'other' and (re.search(rf'\b{re.escape(key)}s?\b', lowered)
                                                or re.search(rf'\b{re.escape(label.lower())}\b', lowered))), None)
    venue_kind = next((kind for kind in available['venue_kind']
                       if kind != 'other' and re.search(rf'\b{re.escape(kind)}s?\b', lowered)), None)
    if venue_kind and not content_type:
        content_type = 'place' if 'place' in available['content_type'] else None
    start, end = relative_window(text, now)
    return QueryPlan(semantic_query=text.strip()[:MAX_QUESTION_CHARS], start=start, end=end,
                     location=location_from_text(text), content_type=content_type,
                     venue_kind=venue_kind, source='deterministic')


def parse_prompt(text, available, now, history):
    """Started from the recovered few-shot template; enums come from the registry."""
    examples = [
        ('which restaurant in NYC did I save about two weeks ago',
         {'semantic_query': 'restaurant', 'date_range': {'start': '<now-20d>', 'end': '<now-10d>'},
          'location': {'text': 'NYC', 'radius_km': None}, 'content_type': 'place',
          'venue_kind': 'restaurant', 'limit': 8}),
        ('what was that shoulder mobility workout',
         {'semantic_query': 'shoulder mobility', 'date_range': {'start': None, 'end': None},
          'location': {'text': None, 'radius_km': None}, 'content_type': 'workout',
          'venue_kind': None, 'limit': 5}),
        ('any good italian food?',
         {'semantic_query': 'italian food', 'date_range': {'start': None, 'end': None},
          'location': {'text': None, 'radius_km': None}, 'content_type': 'place',
          'venue_kind': 'restaurant', 'limit': 8}),
        ('coffee spots within 2km of me',
         {'semantic_query': 'coffee', 'date_range': {'start': None, 'end': None},
          'location': {'text': None, 'radius_km': 2}, 'content_type': 'place',
          'venue_kind': 'cafe', 'limit': 8}),
        ('what else do you know',
         {'semantic_query': 'saved entries overview', 'date_range': {'start': None, 'end': None},
          'location': {'text': None, 'radius_km': None}, 'content_type': None,
          'venue_kind': None, 'limit': 8}),
    ]
    shots = '\n'.join(f'Q: {question}\nA: {json.dumps(shape)}' for question, shape in examples)
    conversation = history_block(history)
    return f"""
Parse this question about one person's privately saved entries, extracted from
reels they shared: places, workouts, recipes, products and more.

Today is {now.date().isoformat()} ({now.tzname() or 'UTC'}). Resolve every
relative date against that day.

Return only one JSON object with exactly these keys:
semantic_query: the topical part of the question with dates, cities and type
  words removed. Never empty; if the question is purely a filter, restate the
  subject ("restaurant", "saved entries overview").
date_range: {{"start": ISO8601 or null, "end": ISO8601 or null}}. Be generous.
  "About two weeks ago" means roughly ten to twenty days back, not exactly day
  fourteen. Use null for both when no time is mentioned.
location: {{"text": city or neighbourhood string or null, "radius_km": number or
  null}}. Expand abbreviations to the full canonical name ("la" is Los Angeles,
  "nyc" is New York, "sf" is San Francisco) and correct obvious misspellings.
  radius_km only when they ask for a distance from themselves.
content_type: exactly one of {json.dumps(available['content_type'])}, or null.
venue_kind: exactly one of {json.dumps(available['venue_kind'])}, or null. Only
  meaningful when content_type is "place".
limit: how many saved entries would answer this, between 3 and {MAX_CANDIDATES}.

Never invent a value that is not asked for. A vague question gets nulls; pure
semantic search is a better answer than a wrong filter.
{'' if not conversation else chr(10) + 'Conversation so far:' + chr(10) + conversation + chr(10)}
Examples:
{shots}

Q: {text}
A:
""".strip()


def response_text(response):
    return ''.join(block.text for block in response.content if getattr(block, 'type', None) == 'text').strip()


def json_object(text):
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def as_datetime(value, now):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo or timezone.utc)
    return parsed


def normalize_plan(raw, text, available, now):
    """Model output is merged over the deterministic floor, never trusted alone."""
    fallback = deterministic_plan(text, available, now)
    semantic = str(raw.get('semantic_query') or '').strip()
    dates = raw.get('date_range') if isinstance(raw.get('date_range'), dict) else {}
    start, end = widen(as_datetime(dates.get('start'), now), as_datetime(dates.get('end'), now), now)
    if start is None and end is None:
        start, end = fallback.start, fallback.end
    place = raw.get('location') if isinstance(raw.get('location'), dict) else {}
    location = str(place.get('text') or '').strip() or fallback.location
    radius = place.get('radius_km')
    radius = float(radius) if isinstance(radius, (int, float)) and 0 < float(radius) <= 500 else None
    content_type = raw.get('content_type') if raw.get('content_type') in available['content_type'] else fallback.content_type
    venue_kind = raw.get('venue_kind') if raw.get('venue_kind') in available['venue_kind'] else fallback.venue_kind
    if venue_kind and content_type not in (None, 'place'):
        venue_kind = None
    limit = raw.get('limit')
    limit = min(MAX_CANDIDATES, max(3, int(limit))) if isinstance(limit, int) else MAX_CANDIDATES
    return QueryPlan(semantic_query=(semantic or text.strip())[:MAX_QUESTION_CHARS], start=start, end=end,
                     location=location or None, radius_km=radius, content_type=content_type,
                     venue_kind=venue_kind, limit=limit)


def parse_question(text, *, history=None, timezone_name=None, metrics=None, available=None, at=None):
    """One model call, strict JSON. Malformed output retries once, then falls back."""
    metrics = metrics or AskMetrics()
    available = available or enums()
    now = user_now(timezone_name, at)
    key = settings().anthropic_api_key
    if not key:
        return deterministic_plan(text, available, now)
    import anthropic
    prompt = parse_prompt(text, available, now, history)
    try:
        with anthropic.Anthropic(api_key=key, timeout=20, max_retries=0) as client:
            for attempt in (1, 2):
                response = client.messages.create(
                    model=settings().anthropic_fast_model, max_tokens=400,
                    system='Return one JSON object and nothing else. No markdown, no prose.',
                    messages=[{'role': 'user', 'content': prompt}])
                metrics.record(f'parse:{attempt}', response)
                parsed = json_object(response_text(response))
                if parsed:
                    return normalize_plan(parsed, text, available, now)
                LOG.warning('ask_parse_malformed attempt=%s', attempt)
    except Exception:
        metrics.unavailable += 1
        LOG.warning('ask_parse_unavailable')
    # A parse failure is never surfaced; pure semantic search still answers.
    return deterministic_plan(text, available, now)


# --------------------------------------------------------------------------- #
# Structured prefilter
# --------------------------------------------------------------------------- #

LOCATION_SQL = """(
     lower(coalesce(e.organization_city,'')) like %(location)s
  or lower(coalesce(e.candidate->>'city_hint','')) like %(location)s
  or lower(coalesce(p.extracted_city,'')) like %(location)s
  or lower(coalesce(p.extracted_neighborhood,'')) like %(location)s
  or lower(coalesce(e.attributes->>'neighborhood','')) like %(location)s
  or lower(coalesce(e.attributes->>'destination','')) like %(location)s
)"""

# Coordinates are leased and often null, so radius can only ever widen a match.
RADIUS_SQL = """(
  p.lat is not null and p.lng is not null
  and p.coords_fetched_at > now() - interval '30 days' and p.coords_fetched_at <= now()
  and 6371 * acos(least(1, greatest(-1,
        sin(radians(%(lat)s)) * sin(radians(p.lat))
      + cos(radians(%(lat)s)) * cos(radians(p.lat)) * cos(radians(p.lng) - radians(%(lng)s))))) <= %(radius)s
)"""


OWNED_CITIES_SQL = """select distinct value from (
     select e.organization_city as value from entries e where e.user_id = %(user)s and e.deleted_at is null
     union all
     select e.candidate->>'city_hint' from entries e where e.user_id = %(user)s and e.deleted_at is null
     union all
     select p.extracted_city from entries e join places p on p.id = e.place_id
       where e.user_id = %(user)s and e.deleted_at is null
     union all
     select p.extracted_neighborhood from entries e join places p on p.id = e.place_id
       where e.user_id = %(user)s and e.deleted_at is null
   ) values where nullif(btrim(value), '') is not null"""


def initials(value):
    return ''.join(word[0] for word in re.findall(r"[A-Za-z][A-Za-z']*", value)).lower()


def resolve_location(conn, user_id, text):
    """Map the asked-for place onto a place this owner actually has.

    "LA" never matches "Los Angeles" as a substring, and a misspelling never
    matches at all. Matching against the owner's own city values keeps this
    general: no city list is hardcoded, and a term that matches nothing is left
    alone so the answer stays an honest "nothing saved there".
    """
    term = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not term:
        return None
    owned = [str(row['value']).strip() for row in conn.execute(OWNED_CITIES_SQL, {'user': user_id}).fetchall()]
    lowered = term.lower()
    for city in owned:
        if city.lower() == lowered:
            return city
    for city in owned:
        if lowered in city.lower() or city.lower() in lowered:
            return city
    if ' ' not in term and 2 <= len(term) <= 4:
        for city in owned:
            if initials(city) == lowered:
                return city
    best = max(owned, key=lambda city: difflib.SequenceMatcher(None, lowered, city.lower()).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, lowered, best.lower()).ratio() >= .85:
        return best
    return term


def prefilter_ids(conn, user_id, plan, here=None):
    """Date, location, content type and venue kind applied in SQL, not embeddings."""
    clauses = ['e.user_id = %(user)s', 'e.deleted_at is null', 's.deleted_at is null']
    params = {'user': user_id}
    if plan.start:
        clauses.append('e.created_at >= %(start)s')
        params['start'] = plan.start
    if plan.end:
        clauses.append('e.created_at <= %(end)s')
        params['end'] = plan.end
    if plan.content_type:
        clauses.append('e.content_type = %(content_type)s')
        params['content_type'] = plan.content_type
    if plan.venue_kind:
        clauses.append('e.venue_kind = %(venue_kind)s')
        params['venue_kind'] = plan.venue_kind
    if plan.location:
        params['location'] = '%' + (resolve_location(conn, user_id, plan.location) or '').lower() + '%'
        if plan.radius_km and here:
            params |= {'lat': here[0], 'lng': here[1], 'radius': plan.radius_km}
            clauses.append(f'({LOCATION_SQL} or {RADIUS_SQL})')
        else:
            clauses.append(LOCATION_SQL)
    elif plan.radius_km and here:
        params |= {'lat': here[0], 'lng': here[1], 'radius': plan.radius_km}
        clauses.append(RADIUS_SQL)
    rows = conn.execute(f"""select e.id from entries e
        join saves s on s.id = e.save_id
        left join places p on p.id = e.place_id
        where {' and '.join(clauses)}
        order by e.created_at desc limit {PREFILTER_CEILING}""", params).fetchall()
    return [str(row['id']) for row in rows]


def relax(plan):
    """Drop the narrowest filter so a near-miss becomes a wider honest answer."""
    if plan.venue_kind:
        return QueryPlan(**{**plan.__dict__, 'venue_kind': None, 'source': plan.source + '+relaxed_kind'})
    if plan.start or plan.end:
        return QueryPlan(**{**plan.__dict__, 'start': None, 'end': None, 'source': plan.source + '+relaxed_dates'})
    return None


# --------------------------------------------------------------------------- #
# Semantic ranking
# --------------------------------------------------------------------------- #

def tokens(text):
    found = set()
    for token in re.findall(r'[a-z0-9]+', (text or '').lower()):
        if len(token) < 2 or token in STOPWORDS:
            continue
        found.add(token)
        if token.endswith('ies') and len(token) > 4: found.add(token[:-3] + 'y')
        elif token.endswith('s') and len(token) > 3: found.add(token[:-1])
    return found


def entry_text(row):
    parts = [row.get('title'), row.get('summary'), row.get('place_name'), row.get('city'),
             row.get('note'), attribute_text(row.get('attributes')),
             ' '.join(folder['name'] for folder in row.get('folders') or [])]
    return re.sub(r'\s+', ' ', ' '.join(str(part) for part in parts if part)).lower()


def attribute_text(attributes):
    if not isinstance(attributes, dict):
        return ''
    values = []
    for value in attributes.values():
        for item in (value if isinstance(value, list) else [value]):
            if item is not None and item != '':
                values.append(str(item).replace('_', ' '))
    return ' '.join(values)


def cosine_scores(conn, user_id, plan, ids):
    if not ids:
        return {}
    try:
        vector = vector_literal(embed_query(plan.semantic_query))
    except Exception:
        LOG.exception('Semantic ranking unavailable; lexical evidence remains')
        return {}
    rows = conn.execute("""select id, 1 - (embedding <=> %s::vector) as score from entries
        where user_id = %s and deleted_at is null and embedding is not null and id = any(%s::uuid[])
        order by embedding <=> %s::vector""", (vector, user_id, ids, vector)).fetchall()
    return {str(row['id']): float(row['score']) for row in rows}


def rank(rows, plan, scores, *, filtered):
    """Recovered hybrid weighting, minus the group-era save_count tie-breaker."""
    query_tokens = tokens(plan.semantic_query)
    phrase = re.sub(r'\s+', ' ', plan.semantic_query.strip().lower())
    ranked = []
    for original in rows:
        row = dict(original)
        blob = entry_text(row)
        semantic = max(0., min(1., scores.get(str(row['id']), 0.)))
        overlap = len(query_tokens & tokens(blob)) / max(len(query_tokens), 1)
        exact = 1. if len(phrase) >= 4 and phrase in blob else 0.
        row['semantic_score'] = round(semantic, 4)
        row['lexical_score'] = round(overlap, 4)
        row['relevance_score'] = round(.72 * semantic + .24 * overlap + .04 * exact, 6)
        row['embedding_missing'] = str(row['id']) not in scores
        ranked.append(row)
    ranked.sort(key=lambda row: (row['relevance_score'], row['created_at']), reverse=True)
    kept = [row for row in ranked
            if row['lexical_score'] >= LEXICAL_FLOOR or row['semantic_score'] >= SEMANTIC_FLOOR[filtered]
            or (row['embedding_missing'] and filtered)]
    if not kept:
        return []
    best = kept[0]['relevance_score']
    return [row for row in kept if row['relevance_score'] >= best - .18][:plan.limit]


def retrieve(user_id, plan, *, here=None):
    """Prefilter, rank, and say precisely why an empty result is empty."""
    with connect() as conn:
        total = conn.execute('select count(*) as n from entries where user_id=%s and deleted_at is null',
                             (user_id,)).fetchone()['n']
        if not total:
            return [], 'empty_library', plan, {'library_size': 0, 'prefiltered': 0, 'missing_embeddings': 0, 'candidates': 0}
        used, ids = plan, prefilter_ids(conn, user_id, plan, here)
        if not ids:
            wider = relax(plan)
            if wider:
                ids = prefilter_ids(conn, user_id, wider, here)
                if ids:
                    used = wider
        if not ids:
            return [], 'filter_empty', used, {'library_size': total, 'prefiltered': 0, 'missing_embeddings': 0, 'candidates': 0}
        with connect() as reader:
            rows = items(reader, user_id, ids)
            scores = cosine_scores(reader, user_id, used, ids)
        missing = sum(1 for row in rows if str(row['id']) not in scores)
        filtered = bool(used.content_type or used.venue_kind or used.location or used.start or used.end)
        kept = rank(rows, used, scores, filtered=filtered)
        diagnostics = {'library_size': total, 'prefiltered': len(ids),
                       'missing_embeddings': missing, 'candidates': len(kept)}
        return kept, (None if kept else 'no_match'), used, diagnostics


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #

PERSONA = (
    'You are ReelBot answering questions about one person\'s own saved entries. '
    'You write the way a friend texts back: warm, brief, contractions, no corporate phrasing. '
    'Use normal sentence capitalization and keep proper nouns exactly as the evidence spells them. '
    'Never mention being an AI, assistant, bot or model. Never promise future actions. '
    'Plain text, no markdown, no URLs.')

# Class A only. Google Maps content (address, rating, hours, phone) is not
# persisted and must not re-enter the library through this path.
CONTEXT_FIELDS = [('type', 'content_type'), ('saved', 'created_at'), ('venue', 'place_name'),
                  ('city', 'city'), ('kind', 'venue_kind'), ('note', 'note')]
FORBIDDEN_CONTEXT = {'formatted_address', 'address', 'rating', 'user_rating_count', 'opening_hours',
                     'regular_opening_hours', 'phone', 'national_phone_number', 'website', 'price_level'}


def context_block(index, row):
    lines = [f"[{index}] {str(row.get('title') or 'Saved entry').strip()}"]
    for label, key in CONTEXT_FIELDS:
        value = row.get(key)
        if key == 'created_at' and value:
            value = value.date().isoformat() if hasattr(value, 'date') else str(value)[:10]
        if key == 'note' and value:
            value = re.sub(r'\s+', ' ', str(value))[:300]
        if value not in (None, '', 'other'):
            lines.append(f'  {label}: {value}')
    summary = re.sub(r'\s+', ' ', str(row.get('summary') or '')).strip()
    if summary:
        lines.append(f'  summary: {summary[:400]}')
    attributes = {key: value for key, value in (row.get('attributes') or {}).items()
                  if key not in FORBIDDEN_CONTEXT and value not in (None, '', [])}
    if attributes:
        lines.append('  details: ' + '; '.join(f"{key.replace('_', ' ')}: {attribute_text({key: value})}"
                                               for key, value in attributes.items()))
    return '\n'.join(lines)


def history_block(history):
    if not history:
        return ''
    lines = []
    for turn in list(history)[-MAX_HISTORY_TURNS:]:
        role = 'Them' if str(turn.get('role')) == 'user' else 'You'
        text = re.sub(r'\s+', ' ', str(turn.get('text') or '')).strip()[:MAX_HISTORY_CHARS]
        if text:
            lines.append(f'{role}: {text}')
    return '\n'.join(lines)


def synthesis_prompt(question, candidates, history, carried):
    context = '\n'.join(context_block(index + 1, row) for index, row in enumerate(candidates))
    conversation = history_block(history)
    return f"""
{PERSONA}

These are the saved entries most relevant to what they asked, with everything
known about them. There is nothing else; you have no other knowledge of their
library and no general knowledge is admissible here.

{'Conversation so far:' + chr(10) + conversation + chr(10) if conversation else ''}\
{'Entries already shown earlier in this conversation: ' + ', '.join(carried) + chr(10) if carried else ''}
They just asked: {question}

Answer in one to three sentences:
- Lead with the substance ("The shoulder mobility one is Daily Shoulder Reset —
  band pull-aparts and wall slides"), never meta-talk like "I found an entry".
- Pull concrete details out of summary, details and note: exercises,
  ingredients, neighbourhoods, vibes.
- Reel content can contain background-song lyrics, hashtags, sponsor copy and
  OCR mistakes. Ignore those unless they are clearly part of the subject. Never
  turn a lyric fragment into a fact.
- Treat missing information as missing. Never fill a gap with standard advice or
  a likely detail. Do not add sets, reps, ingredients, prices, opening hours,
  ratings, addresses or place facts that are not written below. "Targets lower
  chest" does not justify inventing a decline exercise.
- If none of these entries actually answers the question, say so plainly in one
  sentence and cite nothing. Never approximate.
- If this is a follow-up, answer just the follow-up.
- Plain text, under 80 words, no bracketed citations: tappable cards are shown
  under your reply.
- After your reply add one final line exactly like: SOURCES: 1,3
  listing the numbers of the entries your reply actually used, or SOURCES: none.
  That line is stripped before anyone sees it.

Saved entries:
{context}
""".strip()


def synthesize(question, candidates, *, history=None, carried=(), metrics=None):
    """One model call; cited numbers are validated against this candidate set."""
    metrics = metrics or AskMetrics()
    key = settings().anthropic_api_key
    if not key or not candidates:
        raise AskUnavailable('no answer model or no candidates')
    import anthropic
    try:
        with anthropic.Anthropic(api_key=key, timeout=30, max_retries=0) as client:
            response = client.messages.create(
                model=settings().anthropic_fast_model, max_tokens=500,
                messages=[{'role': 'user', 'content': synthesis_prompt(question, candidates, history, carried)}])
        metrics.record('synthesis', response)
    except Exception as error:
        metrics.unavailable += 1
        raise AskUnavailable('answer model unavailable') from error
    answer = response_text(response)
    used = []
    match = re.search(r'\n?\s*SOURCES\s*:\s*([0-9,\s]*|none)\s*$', answer, re.IGNORECASE)
    if match:
        answer = answer[:match.start()].strip()
        used = [int(part) for part in re.findall(r'\d+', match.group(1))]
    if not answer:
        raise AskUnavailable('empty answer')
    if ANSWER_DENIES_EVIDENCE.search(re.sub(r'\s+', ' ', answer)) and used:
        # Evidence was supplied and cited; an "I have nothing" sentence is wrong.
        raise AskUnavailable('answer contradicts supplied evidence')
    return answer[:ANSWER_CHARS], validated_citations(used, candidates)


def validated_citations(used, candidates):
    """Never trust the model to comply: drop every id it was not handed."""
    supplied = [str(row['id']) for row in candidates]
    cited, dropped = [], []
    for number in used:
        if 1 <= number <= len(supplied):
            if supplied[number - 1] not in cited:
                cited.append(supplied[number - 1])
        else:
            dropped.append(number)
    if dropped:
        LOG.warning('ask_citation_dropped numbers=%s supplied=%s', dropped, len(supplied))
    return cited


EMPTY_ANSWERS = {
    'empty_library': 'Nothing is saved yet. Share a reel and I will start filing things away.',
    'filter_empty': 'Nothing in your library matches that.',
    'no_match': 'Nothing in your library matches that.',
}


def described_filters(plan, labels):
    bits = []
    if plan.venue_kind and plan.venue_kind != 'other':
        bits.append(plan.venue_kind.replace('_', ' ') + 's')
    elif plan.content_type:
        bits.append((labels.get(plan.content_type) or plan.content_type).lower())
    if plan.location:
        bits.append('in ' + plan.location)
    if plan.start or plan.end:
        bits.append('from that time')
    return ' '.join(bits)


def empty_answer(reason, plan, labels):
    if reason == 'empty_library':
        return EMPTY_ANSWERS['empty_library']
    described = described_filters(plan, labels)
    return f"I don't have any {described} saved." if described else EMPTY_ANSWERS['no_match']


def deterministic_answer(candidates, plan, labels):
    """Used when the answer model is unavailable; still grounded, still cited."""
    names = [str(row.get('title') or 'a saved entry').strip() for row in candidates[:3]]
    joined = names[0] if len(names) == 1 else ' and '.join(names) if len(names) == 2 else \
        ', '.join(names[:-1]) + ', and ' + names[-1]
    described = described_filters(plan, labels)
    return f"From your saves{' — ' + described if described else ''}: {joined}."


def answer_question(user_id, text, *, history=None, timezone_name=None, here=None, at=None):
    """Parse, prefilter, rank, synthesize. Returns an answer plus cited entries."""
    question = re.sub(r'\s+', ' ', str(text or '')).strip()[:MAX_QUESTION_CHARS]
    if not question:
        raise ValueError('Ask a question about your saves.')
    metrics, available = AskMetrics(), enums()
    carried = [str(identifier) for turn in (history or []) for identifier in (turn.get('entry_ids') or [])][-MAX_CARRIED_IDS:]
    plan = parse_question(question, history=history, timezone_name=timezone_name,
                          metrics=metrics, available=available, at=at)
    candidates, reason, used_plan, diagnostics = retrieve(user_id, plan, here=here)
    if not candidates:
        # An empty candidate set never reaches the answer model, so it can never
        # invent an entry that is not there.
        return {'answer': empty_answer(reason, used_plan, available['labels']), 'entry_ids': [], 'entries': [],
                'reason': reason, 'parsed': used_plan.payload(), 'cost': metrics.priced(),
                'diagnostics': diagnostics, 'grounded': False}
    try:
        answer, cited = synthesize(question, candidates, history=history, carried=carried, metrics=metrics)
        grounded = True
    except AskUnavailable as error:
        LOG.warning('ask_synthesis_fallback reason=%s', error)
        answer, cited, grounded = deterministic_answer(candidates, used_plan, available['labels']), \
            [str(row['id']) for row in candidates[:3]], False
    order = {identifier: index for index, identifier in enumerate(cited)}
    entries = sorted((row for row in candidates if str(row['id']) in order), key=lambda row: order[str(row['id'])])
    LOG.info('ask user=%s parsed=%s candidates=%s cited=%s usd=%s', user_id, used_plan.source,
             len(candidates), len(entries), metrics.priced()['usd'])
    return {'answer': answer, 'entry_ids': [str(row['id']) for row in entries], 'entries': entries,
            'reason': None if entries else 'no_citation', 'parsed': used_plan.payload(),
            'cost': metrics.priced(), 'diagnostics': diagnostics, 'grounded': grounded}
