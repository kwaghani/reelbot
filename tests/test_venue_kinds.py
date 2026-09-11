import unittest
from worker.db import connect, file_entry
from worker.venue_kinds import backfill, kind_for
from worker.registry import venue_kinds
import test_personal as base

class VenueKindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): base.PersonalTests.setUpClass.__func__(cls)
    setUp = base.PersonalTests.setUp
    register = base.PersonalTests.register
    save = base.PersonalTests.save
    venue = base.PersonalTests.venue

    def test_complete_registry_and_unknown(self):
        kinds = venue_kinds()
        self.assertEqual(len(kinds), 16)
        self.assertEqual(len({v['icon'] for v in kinds.values()}), 16)
        for key, spec in kinds.items():
            for primary in spec['google_types']: self.assertEqual(kind_for(primary), key)
        self.assertEqual(kind_for('future_provider_type'), 'other')

    def test_override_survives_resolution_backfill_and_other_owner(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        response = self.client.patch('/items/' + str(entry['id']), headers=self.a['headers'], json={'venue_kind': 'bar'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.patch('/items/' + str(entry['id']), headers=self.b['headers'], json={'venue_kind': 'shop'}).status_code, 404)
        with connect() as conn:
            conn.execute("update places set primary_type='cafe' where id=%s", (entry['place_id'],))
            row = conn.execute('select * from entries where id=%s', (entry['id'],)).fetchone()
            file_entry(conn, row)
            backfill(conn)
            updated = conn.execute('select * from entries where id=%s', (entry['id'],)).fetchone()
            self.assertEqual((updated['venue_kind'], updated['venue_kind_source'], updated['attributes']['venue_kind']), ('bar', 'user', 'bar'))
            self.assertEqual(updated['place_id'], entry['place_id'])

    def test_missing_type_fetch_once_failure_and_unknown_log(self):
        for i in range(3): self.venue(self.save(self.a, 'Kind' + str(i)), self.a, name='Kind' + str(i))
        calls=[]
        def fetch(identifier): calls.append(identifier); return 'future_provider_type'
        with connect() as conn:
            conn.execute("update places set primary_type=''")
            self.assertEqual(backfill(conn, fetch, enabled=True)['provider_calls'], 2)
            self.assertEqual(backfill(conn, fetch, enabled=True)['provider_calls'], 1)
            before=conn.execute('select * from venue_kind_unmapped order by primary_type').fetchall()
            self.assertEqual(backfill(conn, fetch, enabled=True)['provider_calls'], 0)
            self.assertEqual(before, conn.execute('select * from venue_kind_unmapped order by primary_type').fetchall())
        self.assertEqual(len(calls), 3)

    def test_failed_details_attempt_is_not_repeated(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        def fail(_): raise TimeoutError('provider unavailable')
        with connect() as conn:
            conn.execute("update places set primary_type='' where id=%s", (entry['place_id'],))
            self.assertEqual(backfill(conn, fail, enabled=True)['provider_calls'], 1)
            self.assertEqual(backfill(conn, fail, enabled=True)['provider_calls'], 0)
            self.assertEqual(conn.execute('select place_id from entries where id=%s', (entry['id'],)).fetchone()['place_id'], entry['place_id'])

    def test_preferences_are_per_owner_and_validated(self):
        response=self.client.patch('/preferences',headers=self.a['headers'],json={'groupBy':'type','distanceUnits':'imperial'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/sync',headers=self.a['headers']).json()['preferences']['groupBy'],'type')
        self.assertNotEqual(self.client.get('/sync',headers=self.b['headers']).json()['preferences'].get('groupBy'),'type')
        self.assertEqual(self.client.patch('/preferences',headers=self.a['headers'],json={'groupBy':'nonsense'}).status_code,422)

    def test_account_merge_retains_user_kind_and_preferences(self):
        from api.main import merge_library
        source, _ = self.venue(self.save(self.a), self.a)
        self.client.patch('/items/' + str(source['id']), headers=self.a['headers'], json={'venue_kind':'bar'})
        self.client.patch('/preferences', headers=self.a['headers'], json={'groupBy':'type'})
        with connect() as conn:
            merge_library(conn, self.a['user'], self.b['user'])
            row = conn.execute('select * from entries where user_id=%s', (self.b['user'],)).fetchone()
            self.assertEqual((row['venue_kind'], row['venue_kind_source']), ('bar','user'))
            self.assertEqual(row['place_id'], source['place_id'])
            self.assertEqual(conn.execute('select ui_preferences from users where id=%s', (self.b['user'],)).fetchone()['ui_preferences']['groupBy'], 'type')

    def test_compilation_kind_survives_provider_and_user_override_wins(self):
        from psycopg.types.json import Jsonb
        entry, _ = self.venue(self.save(self.a), self.a)
        with connect() as conn:
            conn.execute("update places set primary_type='bar' where id=%s", (entry['place_id'],))
            conn.execute("update entries set candidate=%s,attributes=%s where id=%s", (
                Jsonb({'compilation_context': {'venue_kind':'restaurant','source':'reel_shared_context','title':'7 Rooftop Restaurants'}}),
                Jsonb({'venue_kind':'restaurant','rooftop':True}),entry['id']))
            row=conn.execute('select * from entries where id=%s',(entry['id'],)).fetchone()
            file_entry(conn,row)
            saved=conn.execute('select * from entries where id=%s',(entry['id'],)).fetchone()
            self.assertEqual(saved['venue_kind'],'restaurant')
            self.assertTrue(saved['attributes']['rooftop'])
        response=self.client.patch('/items/'+str(entry['id']),headers=self.a['headers'],json={'venue_kind':'bar'})
        self.assertEqual(response.status_code,200)
        with connect() as conn:
            backfill(conn)
            saved=conn.execute('select * from entries where id=%s',(entry['id'],)).fetchone()
            self.assertEqual((saved['venue_kind'],saved['venue_kind_source']),('bar','user'))
