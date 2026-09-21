"""Natural-language Q&A: parsing, prefiltering, citation validation and the fabrication guard."""
import contextlib
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import test_personal as personal
from psycopg.types.json import Jsonb
from worker import ask
from worker.db import connect, store_candidate


class Usage:
    def __init__(self, tokens_in=120, tokens_out=40):
        self.input_tokens, self.output_tokens = tokens_in, tokens_out


class Block:
    type = 'text'

    def __init__(self, text): self.text = text


class Reply:
    def __init__(self, text, tokens_in=120, tokens_out=40):
        self.content, self.usage = [Block(text)], Usage(tokens_in, tokens_out)


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued replies, records prompts."""

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []
        self.messages = self

    def create(self, **kwargs):
        self.prompts.append(kwargs['messages'][0]['content'])
        if not self.replies:
            raise AssertionError('The answer model was called more times than the test allows')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def __enter__(self): return self

    def __exit__(self, *_): return False


def model(*replies):
    client = FakeClient(replies)
    return patch('anthropic.Anthropic', return_value=client), client


def fake_vector(text):
    """Deterministic 384-d stand-in. These tests exercise ranking and grounding,
    not sentence-transformers, and loading the real model costs minutes."""
    values = [0.] * 384
    for index, token in enumerate(sorted(set(re.findall(r'[a-z]+', (text or '').lower())))[:384]):
        values[index] = 1. / (1 + len(token))
    norm = sum(value * value for value in values) ** .5 or 1.
    return [value / norm for value in values]


class AskTests(unittest.TestCase):
    setUp = personal.PersonalTests.setUp
    register = personal.PersonalTests.register
    save = personal.PersonalTests.save

    NOW = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)

    @classmethod
    def setUpClass(cls):
        personal.PersonalTests.setUpClass.__func__(cls)
        cls.embedding = patch('worker.ask.embed_query', side_effect=fake_vector)
        cls.embedding.start()
        # Every provider call is faked; the key only decides whether ask.py
        # attempts one at all, so the model path must be reachable here.
        import config as runtime
        cls.provider = patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key-never-sent'})
        cls.provider.start()
        runtime.reset_for_tests()

    @classmethod
    def tearDownClass(cls):
        import config as runtime
        cls.embedding.stop()
        cls.provider.stop()
        runtime.reset_for_tests()

    # ---- fixtures ------------------------------------------------------- #

    def entry(self, *, kind='place', title='Nonno Pizzeria', attrs=None, city='New York',
              summary='A neighbourhood pizza counter.', days_ago=0, owner=None, note=''):
        owner = owner or self.a
        saved = self.save(owner, str(uuid4()).replace('-', ''))
        attributes = attrs if attrs is not None else ({'venue_kind': 'restaurant'} if kind == 'place' else {})
        candidate = {'content_type': kind, 'title': title, 'summary': summary, 'attributes': attributes,
                     'confidence': .95, 'venue_name': title if kind == 'place' else None,
                     'city_hint': city if kind == 'place' else None, 'evidence': 'Caption names it'}
        with connect() as conn:
            row = store_candidate(conn, {'id': saved['id'], 'user_id': owner['user']}, candidate, None, .95)
            created = self.NOW - timedelta(days=days_ago)
            conn.execute('update entries set created_at=%s,organization_city=%s,note=%s,venue_kind=%s where id=%s',
                         (created, city or '', note, attributes.get('venue_kind', 'other'), row['id']))
            conn.execute("update saves set raw_signals=%s where id=%s",
                         (Jsonb({'caption': f'{title} — {summary}'}), saved['id']))
        return str(row['id'])

    def embed(self, entry_id, *, score_seed=0.):
        """Give one entry a vector so pgvector ranking has something to rank."""
        with connect() as conn:
            vector = '[' + ','.join(['0'] * 383 + [str(1. - score_seed)]) + ']'
            conn.execute('update entries set embedding=%s::vector where id=%s', (vector, entry_id))

    def plan(self, **kwargs):
        return ask.QueryPlan(semantic_query=kwargs.pop('semantic_query', 'pizza'), **kwargs)

    # ---- query parsing -------------------------------------------------- #

    def test_relative_dates_are_generous_not_a_single_day(self):
        start, end = ask.relative_window('what did I save two weeks ago', self.NOW)
        oldest, newest = (self.NOW - start).days, (self.NOW - end).days
        self.assertGreaterEqual(oldest, 17, 'the far edge must reach beyond day fourteen')
        self.assertLessEqual(oldest, 22)
        self.assertLessEqual(newest, 11)
        self.assertGreaterEqual(newest, 8)

    def test_numeric_relative_dates_and_no_date_at_all(self):
        start, end = ask.relative_window('the restaurant I saved 3 days ago', self.NOW)
        self.assertTrue(start < end <= self.NOW)
        self.assertGreater((end - start).days, 1, 'even a three-day-ago window is a window')
        self.assertEqual(ask.relative_window('what was that shoulder workout', self.NOW), (None, None))

    def test_enums_are_generated_from_the_registry_at_runtime(self):
        with tempfile_registry() as (path, patched):
            with patched:
                available = ask.enums()
        self.assertIn('craft', available['content_type'], 'an eleventh YAML type must appear untouched')
        self.assertEqual(available['labels']['craft'], 'Crafts')
        prompt = ask.parse_prompt('anything', available, self.NOW, [])
        self.assertIn('"craft"', prompt)
        del path

    def test_registry_enums_cover_every_live_type_and_venue_kind(self):
        from worker.registry import registry, venue_kinds
        available = ask.enums()
        self.assertEqual(available['content_type'], list(registry()))
        self.assertEqual(available['venue_kind'], list(venue_kinds()))

    def test_model_parse_is_merged_over_the_deterministic_floor(self):
        patched, client = model(Reply('{"semantic_query":"pizza","date_range":{"start":null,"end":null},'
                                      '"location":{"text":"New York","radius_km":null},'
                                      '"content_type":"place","venue_kind":"restaurant","limit":5}'))
        with patched:
            plan = ask.parse_question('which restaurant in New York', at=self.NOW)
        self.assertEqual((plan.content_type, plan.venue_kind, plan.location, plan.limit),
                         ('place', 'restaurant', 'New York', 5))
        self.assertEqual(plan.source, 'model')
        self.assertEqual(len(client.prompts), 1, 'parsing is exactly one model call')

    def test_malformed_json_retries_once_then_falls_back_without_an_error(self):
        patched, client = model(Reply('sorry, I cannot do that'), Reply('still not json'))
        with patched:
            plan = ask.parse_question('which restaurant in New York did I save two weeks ago', at=self.NOW)
        self.assertEqual(len(client.prompts), 2, 'one retry, then stop')
        self.assertEqual(plan.source, 'deterministic')
        self.assertEqual(plan.location, 'New York')
        self.assertEqual(plan.venue_kind, 'restaurant')
        self.assertIsNotNone(plan.start, 'the fallback still resolves "two weeks ago"')

    def test_a_useless_parse_degrades_to_pure_semantic_search(self):
        patched, _ = model(Reply('{"semantic_query":"","date_range":{},"location":{},'
                                 '"content_type":"nonsense","venue_kind":"nonsense","limit":"lots"}'))
        with patched:
            plan = ask.parse_question('show me something good', at=self.NOW)
        self.assertEqual(plan.semantic_query, 'show me something good')
        self.assertIsNone(plan.content_type)
        self.assertIsNone(plan.venue_kind)
        self.assertIsNone(plan.start)
        self.assertEqual(plan.limit, ask.MAX_CANDIDATES)

    def test_provider_failure_during_parsing_never_surfaces(self):
        patched, _ = model(RuntimeError('provider down'))
        with patched:
            plan = ask.parse_question('any italian place in New York', at=self.NOW)
        self.assertEqual(plan.source, 'deterministic')
        self.assertEqual(plan.location, 'New York')

    # ---- structured prefilter ------------------------------------------- #

    def test_prefilter_applies_date_type_and_venue_kind_in_sql(self):
        recent = self.entry(title='Nonno Pizzeria', days_ago=14)
        old = self.entry(title='Ancient Pizzeria', days_ago=200)
        workout = self.entry(kind='workout', title='Shoulder reset', city='',
                             attrs={'muscle_group': ['shoulders']}, summary='Band pull-aparts.')
        with connect() as conn:
            start, end = ask.relative_window('two weeks ago', self.NOW)
            dated = ask.prefilter_ids(conn, self.a['user'], self.plan(start=start, end=end))
            typed = ask.prefilter_ids(conn, self.a['user'], self.plan(content_type='workout'))
            kinded = ask.prefilter_ids(conn, self.a['user'], self.plan(content_type='place', venue_kind='restaurant'))
        self.assertIn(recent, dated)
        self.assertNotIn(old, dated)
        self.assertEqual(typed, [workout])
        self.assertCountEqual(kinded, [recent, old])

    def test_location_matches_city_text_when_coordinates_are_null(self):
        here = self.entry(title='Nonno Pizzeria', city='New York')
        elsewhere = self.entry(title='Tartine', city='San Francisco')
        with connect() as conn:
            rows = conn.execute('select lat,lng from places p join entries e on e.place_id=p.id where e.id=%s',
                                (here,)).fetchall()
            found = ask.prefilter_ids(conn, self.a['user'], self.plan(location='new york'))
        self.assertTrue(all(row['lat'] is None for row in rows) or not rows,
                        'this fixture has no coordinates, which is the point')
        self.assertIn(here, found)
        self.assertNotIn(elsewhere, found)

    def test_radius_widens_a_city_match_and_never_narrows_it(self):
        near = self.entry(title='Corner Cafe', city='New York', attrs={'venue_kind': 'cafe'})
        with connect() as conn:
            conn.execute("""update places set lat=40.7128,lng=-74.0060,coords_fetched_at=now()
                where id=(select place_id from entries where id=%s)""", (near,))
            far_text = self.entry(title='Brooklyn Bagels', city='New York')
            plan = self.plan(location='new york', radius_km=2)
            found = ask.prefilter_ids(conn, self.a['user'], plan, here=(40.7128, -74.0060))
        self.assertIn(near, found)
        self.assertIn(far_text, found, 'a null-coordinate entry still matches on city text')

    def test_an_abbreviation_or_typo_resolves_onto_a_city_the_owner_actually_has(self):
        here = self.entry(title='Sugo Social', city='Los Angeles')
        self.entry(title='Ornella', city='Munich')
        with connect() as conn:
            resolve = lambda term: ask.resolve_location(conn, self.a['user'], term)
            self.assertEqual(resolve('LA'), 'Los Angeles', 'initials match a city this owner has')
            self.assertEqual(resolve('los angeles'), 'Los Angeles')
            self.assertEqual(resolve('Los Angles'), 'Los Angeles', 'a near-miss spelling resolves')
            self.assertEqual(resolve('Mumbai'), 'Mumbai', 'an unheld city is left alone, not forced onto one')
            self.assertEqual(resolve('Munchen'), 'Munchen',
                             'the threshold stays conservative: a loose resemblance is not a match')
            self.assertEqual(resolve('  '), None)
            found = ask.prefilter_ids(conn, self.a['user'], self.plan(location='LA'))
            missing = ask.prefilter_ids(conn, self.a['user'], self.plan(location='Mumbai'))
        self.assertEqual(found, [here])
        self.assertEqual(missing, [])

    def test_an_entry_saved_by_another_owner_is_never_a_candidate(self):
        mine = self.entry(title='Nonno Pizzeria')
        theirs = self.entry(title='Nonno Pizzeria', owner=self.b)
        with connect() as conn:
            found = ask.prefilter_ids(conn, self.a['user'], self.plan())
        self.assertIn(mine, found)
        self.assertNotIn(theirs, found)

    # ---- ranking --------------------------------------------------------- #

    def test_entries_without_an_embedding_still_reach_a_filtered_answer(self):
        identifier = self.entry(title='Nonno Pizzeria', summary='Wood-fired pizza counter.')
        candidates, reason, _, diagnostics = ask.retrieve(self.a['user'], self.plan(semantic_query='pizza', content_type='place'))
        self.assertIsNone(reason)
        self.assertEqual([str(row['id']) for row in candidates], [identifier])
        self.assertEqual(diagnostics['missing_embeddings'], 1, 'the backfill count is reported, not hidden')

    # ---- citation validation and the fabrication guard ------------------- #

    def test_citations_outside_the_candidate_set_are_dropped_server_side(self):
        candidates = [{'id': 'aaa'}, {'id': 'bbb'}]
        self.assertEqual(ask.validated_citations([1, 2], candidates), ['aaa', 'bbb'])
        self.assertEqual(ask.validated_citations([2, 9, 0, -1, 2], candidates), ['bbb'])
        self.assertEqual(ask.validated_citations([7], candidates), [])

    def test_an_empty_candidate_set_never_reaches_the_answer_model(self):
        patched, client = model()  # any call raises
        with patched:
            result = ask.answer_question(self.a['user'], 'which restaurant did I save', at=self.NOW)
        self.assertEqual(result['entry_ids'], [])
        self.assertEqual(result['entries'], [])
        self.assertEqual(result['reason'], 'empty_library')
        self.assertFalse(result['grounded'])
        self.assertNotIn('[', result['answer'])

    def test_a_content_type_with_zero_entries_says_so_and_invents_nothing(self):
        self.entry(kind='place', title='Nonno Pizzeria')
        patched, client = model(Reply('{"semantic_query":"recipe","date_range":{"start":null,"end":null},'
                                      '"location":{"text":null,"radius_km":null},'
                                      '"content_type":"recipe","venue_kind":null,"limit":5}'))
        with patched:
            result = ask.answer_question(self.a['user'], 'what recipes do I have saved', at=self.NOW)
        self.assertEqual(result['entry_ids'], [])
        self.assertIn('recipe', result['answer'].lower())
        self.assertEqual(len(client.prompts), 1, 'no synthesis call happens with no candidates')

    def test_a_fabricated_citation_cannot_put_an_entry_on_screen(self):
        identifier = self.entry(title='Nonno Pizzeria', summary='Wood-fired pizza counter.')
        self.embed(identifier)
        parse = Reply('{"semantic_query":"pizza","date_range":{"start":null,"end":null},'
                      '"location":{"text":null,"radius_km":null},'
                      '"content_type":"place","venue_kind":"restaurant","limit":5}')
        invented = Reply('Nonno Pizzeria, and also Luigi\'s Trattoria nearby.\nSOURCES: 1,4,9')
        patched, _ = model(parse, invented)
        with patched:
            result = ask.answer_question(self.a['user'], 'which pizza place did I save', at=self.NOW)
        self.assertEqual(result['entry_ids'], [identifier], 'only the supplied candidate survives')
        self.assertTrue(result['grounded'])

    def test_an_answer_denying_supplied_evidence_is_rejected(self):
        identifier = self.entry(title='Nonno Pizzeria')
        self.embed(identifier)
        parse = Reply('{"semantic_query":"pizza","date_range":{"start":null,"end":null},'
                      '"location":{"text":null,"radius_km":null},'
                      '"content_type":"place","venue_kind":null,"limit":5}')
        denial = Reply("I don't see anything saved for that.\nSOURCES: 1")
        patched, _ = model(parse, denial)
        with patched:
            result = ask.answer_question(self.a['user'], 'which pizza place did I save', at=self.NOW)
        self.assertFalse(result['grounded'], 'the deterministic answer replaces the contradiction')
        self.assertIn('Nonno Pizzeria', result['answer'])
        self.assertEqual(result['entry_ids'], [identifier])

    def test_cited_entry_order_follows_the_answer_not_the_database(self):
        first = self.entry(title='Alpha Pizzeria', days_ago=1)
        second = self.entry(title='Beta Pizzeria', days_ago=9)
        for identifier in (first, second): self.embed(identifier)
        parse = Reply('{"semantic_query":"pizza","date_range":{"start":null,"end":null},'
                      '"location":{"text":null,"radius_km":null},'
                      '"content_type":"place","venue_kind":null,"limit":8}')
        patched, _ = model(parse, Reply('Beta first, then Alpha.\nSOURCES: 2,1'))
        with patched:
            result = ask.answer_question(self.a['user'], 'my pizza places', at=self.NOW)
        candidates = {second, first}
        self.assertEqual(set(result['entry_ids']), candidates)
        self.assertEqual(len(result['entry_ids']), 2)

    # ---- compliance ------------------------------------------------------ #

    def test_the_answer_context_carries_no_google_maps_content(self):
        row = {'id': 'one', 'title': 'Nonno Pizzeria', 'summary': 'Wood-fired pizza.', 'content_type': 'place',
               'created_at': self.NOW, 'place_name': 'Nonno Pizzeria', 'city': 'New York', 'venue_kind': 'restaurant',
               'note': 'Go before 6', 'formatted_address': '1 Mulberry St, New York, NY 10013',
               'attributes': {'neighborhood': 'Nolita', 'rating': 4.6, 'formatted_address': '1 Mulberry St',
                              'regular_opening_hours': 'Mon 9-5', 'phone': '+1 212 555 0100'}}
        block = ask.context_block(1, row)
        for forbidden in ('Mulberry', '4.6', 'Mon 9-5', '555 0100'):
            self.assertNotIn(forbidden, block, f'{forbidden} is Google content and must not re-enter this path')
        self.assertIn('Nolita', block)
        self.assertIn('Go before 6', block)
        self.assertIn('New York', block)

    # ---- cost and context bounds ---------------------------------------- #

    def test_cost_is_measured_from_provider_usage_for_both_calls(self):
        identifier = self.entry(title='Nonno Pizzeria')
        self.embed(identifier)
        parse = Reply('{"semantic_query":"pizza","date_range":{"start":null,"end":null},'
                      '"location":{"text":null,"radius_km":null},"content_type":"place",'
                      '"venue_kind":null,"limit":5}', tokens_in=900, tokens_out=60)
        patched, _ = model(parse, Reply('Nonno Pizzeria.\nSOURCES: 1', tokens_in=1500, tokens_out=40))
        with patched:
            result = ask.answer_question(self.a['user'], 'my pizza place', at=self.NOW)
        cost = result['cost']
        self.assertEqual(cost['llm_requests'], 2)
        self.assertEqual(cost['llm_tokens_in'], 2400)
        self.assertEqual(cost['llm_tokens_out'], 100)
        self.assertTrue(cost['complete'])
        self.assertGreater(cost['usd'], 0)
        self.assertEqual([call['call'] for call in cost['calls']], ['parse:1', 'synthesis'])

    def test_long_conversations_do_not_grow_the_prompt_without_bound(self):
        history = [{'role': 'user' if index % 2 == 0 else 'assistant', 'text': 'x' * 2000, 'entry_ids': []}
                   for index in range(40)]
        block = ask.history_block(history)
        self.assertEqual(len(block.splitlines()), ask.MAX_HISTORY_TURNS)
        self.assertLess(len(block), ask.MAX_HISTORY_TURNS * (ask.MAX_HISTORY_CHARS + 10))

    def test_session_entry_ids_carry_into_the_next_question(self):
        identifier = self.entry(title='Nonno Pizzeria')
        self.embed(identifier)
        history = [{'role': 'user', 'text': 'pizza?', 'entry_ids': []},
                   {'role': 'assistant', 'text': 'Nonno Pizzeria.', 'entry_ids': [identifier]}]
        parse = Reply('{"semantic_query":"the other one","date_range":{"start":null,"end":null},'
                      '"location":{"text":null,"radius_km":null},"content_type":null,'
                      '"venue_kind":null,"limit":5}')
        patched, client = model(parse, Reply('That one is Nonno Pizzeria.\nSOURCES: 1'))
        with patched:
            ask.answer_question(self.a['user'], 'what about the other one?', history=history, at=self.NOW)
        self.assertIn(identifier, client.prompts[1], 'already-surfaced ids reach the synthesis prompt')
        self.assertIn('Nonno Pizzeria', client.prompts[1])

    # ---- HTTP contract --------------------------------------------------- #

    def test_ask_endpoint_is_owner_scoped_and_returns_cards(self):
        identifier = self.entry(title='Nonno Pizzeria', summary='Wood-fired pizza counter.')
        self.embed(identifier)
        parse = Reply('{"semantic_query":"pizza","date_range":{"start":null,"end":null},'
                      '"location":{"text":null,"radius_km":null},"content_type":"place",'
                      '"venue_kind":null,"limit":5}')
        patched, _ = model(parse, Reply('Nonno Pizzeria, the wood-fired counter.\nSOURCES: 1'))
        with patched:
            response = self.client.post('/ask', headers=self.a['headers'], json={'text': 'my pizza place'})
            denied = self.client.post('/ask', headers=self.b['headers'], json={'text': 'my pizza place'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['entry_ids'], [identifier])
        self.assertEqual(body['entries'][0]['title'], 'Nonno Pizzeria')
        self.assertIsNone(body['entries'][0]['formatted_address'], 'cards inherit the retention projection')
        self.assertIn('usd', body['cost'])
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(denied.json()['entry_ids'], [], 'another owner sees none of this library')

    def test_ask_rejects_an_unauthenticated_or_oversized_question(self):
        self.assertEqual(self.client.post('/ask', json={'text': 'hi'}).status_code, 401)
        self.assertEqual(self.client.post('/ask', headers=self.a['headers'],
                                          json={'text': 'x' * 501}).status_code, 422)
        self.assertEqual(self.client.post('/ask', headers=self.a['headers'],
                                          json={'text': 'hi', 'unexpected': 1}).status_code, 422)


@contextlib.contextmanager
def tempfile_registry():
    """An eleventh content type, added only in YAML, with nothing else touched."""
    import config as runtime
    source = Path(runtime.ROOT / 'config/content_types.yaml').read_text()
    extra = ('\ncraft:\n  label: Craft\n  plural_label: Crafts\n  icon: scissors-cutting\n'
             '  color: "#345678"\n  geo: never\n  primary_facet: material\n'
             '  attributes:\n    material:\n      type: string\n')
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as handle:
        handle.write(source + extra)
        path = handle.name
    patched = patch.dict(os.environ, {'CONTENT_TYPES_PATH': path})
    try:
        yield path, patched
    finally:
        os.unlink(path)


if __name__ == '__main__':
    unittest.main()
