import unittest
from uuid import uuid4
from unittest.mock import patch
from psycopg.types.json import Jsonb
import test_personal as base
from worker.db import connect, file_entry, items
from worker.geography import from_components, normalize_geography, enrich_owner, migrate_folders


class OrganizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): base.PersonalTests.setUpClass.__func__(cls)
    setUp = base.PersonalTests.setUp
    register = base.PersonalTests.register
    save = base.PersonalTests.save
    venue = base.PersonalTests.venue

    def test_provider_locality_and_metro_scope(self):
        geo = from_components([
            {'longText': 'Santa Monica', 'types': ['locality']},
            {'longText': 'California', 'shortText': 'CA', 'types': ['administrative_area_level_1']},
            {'longText': 'United States', 'shortText': 'US', 'types': ['country']},
        ])
        result = normalize_geography(geo)
        self.assertEqual((result['city'], result['neighborhood'], result['provider_city']), ('Los Angeles', 'Santa Monica', 'Santa Monica'))
        self.assertEqual(normalize_geography({'city': 'Silver Lake', 'country': 'US', 'region': 'WA'})['city'], 'Silver Lake')
        self.assertEqual(normalize_geography({'city': 'Brooklyn', 'country': 'US', 'region': 'NY'})['city'], 'New York')
        self.assertEqual(from_components([{'longText': 'Los Angeles County', 'types': ['administrative_area_level_2']}])['city'], '')

    def test_ten_places_five_neighborhoods_one_city_preserves_custom_notes_and_pins(self):
        with connect() as conn:
            custom = conn.execute("insert into folders(user_id,name,kind) values(%s,'Weekend','custom') returning id", (self.a['user'],)).fetchone()['id']
        neighborhoods = ['Venice Beach', 'Silver Lake', 'Koreatown', 'Hollywood', 'Echo Park']
        for i in range(10):
            entry, _ = self.venue(self.save(self.a, 'Geo' + str(i)), self.a, name='Venue ' + str(i))
            with connect() as conn:
                geo = {'city': 'Los Angeles', 'neighborhood': neighborhoods[i % 5], 'country': 'US', 'region': 'CA'}
                conn.execute('update places set organization_geography=%s where id=%s', (Jsonb(geo), entry['place_id']))
                conn.execute("update entries set note='Keep my note' where id=%s", (entry['id'],))
                conn.execute('insert into folder_items(folder_id,entry_id,user_id) values(%s,%s,%s)', (custom, entry['id'], self.a['user']))
                file_entry(conn, entry, organization={'city': neighborhoods[i % 5]})
        with connect() as conn:
            result = migrate_folders(conn)
            self.assertEqual((result['entries_before'], result['entries_after']), (10, 10))
            self.assertEqual(result['old_city_folders_removed'], 5)
            folders = conn.execute("select name from folders where user_id=%s and kind='auto_facet'", (self.a['user'],)).fetchall()
            self.assertEqual([f['name'] for f in folders], ['Los Angeles'])
            self.assertEqual(conn.execute('select count(*) as n from folder_items where folder_id=%s', (custom,)).fetchone()['n'], 10)
            self.assertEqual({e['attributes']['neighborhood'] for e in items(conn, self.a['user'])}, set(neighborhoods))
            self.assertEqual(migrate_folders(conn)['old_city_folders_removed'], 0)

    def test_geography_refresh_is_bounded_cached_and_owner_scoped(self):
        for i in range(3): self.venue(self.save(self.a, 'A' + str(i)), self.a, name='A' + str(i))
        other, _ = self.venue(self.save(self.b, 'B'), self.b, name='Private B')
        calls = []
        def provider(identifier):
            calls.append(identifier)
            return {'city': 'San Francisco', 'country': 'US', 'region': 'CA', 'neighborhood': 'Mission'}
        self.assertEqual(enrich_owner(self.a['user'], provider, enabled=True)['provider_calls'], 2)
        self.assertEqual(enrich_owner(self.a['user'], provider, enabled=True)['provider_calls'], 1)
        self.assertEqual(enrich_owner(self.a['user'], provider, enabled=True)['provider_calls'], 0)
        with connect() as conn:
            untouched = conn.execute('select organization_checked_at from places where id=%s', (other['place_id'],)).fetchone()
            self.assertIsNone(untouched['organization_checked_at'])
            self.assertEqual(conn.execute('select sum(organization_calls) as n from places').fetchone()['n'], 3)
        self.assertEqual(len(calls), 3)

    def test_thumbnail_uses_fetch_key_and_legacy_fallback(self):
        save = self.save(self.a)
        self.venue(save, self.a)
        with connect() as conn:
            conn.execute('update saves set raw_signals=%s where id=%s', (Jsonb({'thumbnail_url': 'https://example.test/cover.jpg', 'thumbnail': 'https://example.test/old.jpg'}), save['id']))
            self.assertEqual(items(conn, self.a['user'])[0]['thumbnail'], 'https://example.test/cover.jpg')
            conn.execute('update saves set raw_signals=%s where id=%s', (Jsonb({'thumbnail': 'https://example.test/old.jpg'}), save['id']))
            self.assertEqual(items(conn, self.a['user'])[0]['thumbnail'], 'https://example.test/old.jpg')

    def test_metadata_failure_does_not_move_pin_or_rebill_every_sync(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        with connect() as conn: before = conn.execute('select place_id from entries where id=%s', (entry['id'],)).fetchone()
        def failing(_identifier): raise TimeoutError('provider unavailable')
        self.assertEqual(enrich_owner(self.a['user'], failing, enabled=True)['provider_calls'], 1)
        self.assertEqual(enrich_owner(self.a['user'], failing, enabled=True)['provider_calls'], 0)
        with connect() as conn: self.assertEqual(before, conn.execute('select place_id from entries where id=%s', (entry['id'],)).fetchone())

    def test_note_edit_retains_legacy_neighborhood_and_pin_change_resets_it(self):
        entry, _ = self.venue(self.save(self.a, 'Old'), self.a)
        other, _ = self.venue(self.save(self.a, 'New'), self.a, name='New venue')
        with connect() as conn:
            file_entry(conn, entry, organization={'city': 'Los Angeles', 'neighborhood': 'Venice Beach'})
        response = self.client.patch('/items/' + str(entry['id']), headers=self.a['headers'], json={'note': 'Keep this note'})
        self.assertEqual(response.status_code, 200, response.text)
        with connect() as conn:
            row = conn.execute('select * from entries where id=%s', (entry['id'],)).fetchone()
            self.assertEqual((row['organization_city'], row['attributes']['neighborhood']), ('Los Angeles', 'Venice Beach'))
        response = self.client.patch('/items/' + str(entry['id']), headers=self.a['headers'], json={'place_id': str(other['place_id'])})
        self.assertEqual(response.status_code, 200, response.text)
        with connect() as conn:
            row = conn.execute('select * from entries where id=%s', (entry['id'],)).fetchone()
            self.assertEqual(row['organization_city'], '')
            self.assertNotIn('neighborhood', row['attributes'])
            self.assertEqual(row['note'], 'Keep this note')

    def test_metadata_refresh_retains_migrated_name_without_repeated_refiling(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        with connect() as conn:
            file_entry(conn, entry, organization={'city': 'Los Angeles', 'neighborhood': 'Venice Beach'})
        def provider(_): return {'city': 'Los Angeles', 'neighborhood': 'Venice', 'country': 'US', 'region': 'CA'}
        first = enrich_owner(self.a['user'], provider, enabled=True)
        second = enrich_owner(self.a['user'], provider, enabled=True)
        self.assertEqual((first['refiled_entries'], second['refiled_entries'], second['provider_calls']), (0, 0, 0))
        with connect() as conn:
            row = conn.execute('select attributes from entries where id=%s', (entry['id'],)).fetchone()
            self.assertEqual(row['attributes']['neighborhood'], 'Venice Beach')

    def test_optional_recipe_pin_does_not_trigger_place_folder_refresh(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        response = self.client.patch('/items/' + str(entry['id']), headers=self.a['headers'], json={'content_type': 'recipe', 'attributes': {'cuisine': 'Thai'}})
        self.assertEqual(response.status_code, 200, response.text)
        with connect() as conn:
            conn.execute('update places set organization_geography=%s where id=%s', (Jsonb({'city': 'Los Angeles', 'country': 'US', 'region': 'CA'}), entry['place_id']))
            before = conn.execute('select updated_at from entries where id=%s', (entry['id'],)).fetchone()
        def provider(_): raise AssertionError('A linked recipe does not need place-folder metadata')
        result = enrich_owner(self.a['user'], provider, enabled=True)
        self.assertEqual((result['provider_calls'], result['refiled_entries']), (0, 0))
        with connect() as conn:
            self.assertEqual(before, conn.execute('select updated_at from entries where id=%s', (entry['id'],)).fetchone())
