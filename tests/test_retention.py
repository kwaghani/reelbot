"""Real Postgres lease boundaries, independent deletion, provenance and cost tests."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import test_personal as base
from worker.db import connect, items
from worker import coordinate_refresh as refresh, coordinate_sweep as sweep
from worker.retention_policy import safe_candidate, valid_coordinates
from worker.retention_monitor import snapshot
from worker.retention_objects import references, clean_imagery

class RetentionTests(unittest.TestCase):
    setUpClass = classmethod(base.PersonalTests.setUpClass.__func__)
    setUp = base.PersonalTests.setUp
    register = base.PersonalTests.register
    save = base.PersonalTests.save
    venue = base.PersonalTests.venue

    def place(self, age=30):
        entry,_=self.venue(self.save(self.a),self.a)
        at=datetime.now(timezone.utc)
        with connect() as conn:
            conn.execute('update places set coords_fetched_at=%s where id=%s',(at-timedelta(days=age),entry['place_id']))
        return entry,at

    def test_refresh_disabled_still_deletes_exactly_day_30(self):
        entry,at=self.place()
        with patch.object(refresh,'run',side_effect=AssertionError('Refresh must be completely disabled')):
            result=sweep.run(at=at)
        self.assertEqual(result['places_nulled'],1)
        with connect() as conn:
            row=conn.execute('select * from places where id=%s',(entry['place_id'],)).fetchone()
            self.assertIsNone(row['lat']);self.assertIsNone(row['lng']);self.assertIsNone(row['coords_fetched_at'])
            saved=items(conn,self.a['user'])[0]
            self.assertEqual(saved['title'],'Tartine Bakery');self.assertTrue(saved['folders'])

    def test_day_25_refresh_is_not_deleted_at_old_day_30(self):
        entry,at=self.place(25)
        with patch('time.sleep'):
            stats=refresh.run(fetch=lambda _: {'latitude':38,'longitude':-122},at=at)
        self.assertEqual((stats['attempted'],stats['succeeded'],stats['provider_calls']),(1,1,1))
        self.assertEqual(sweep.run(at=at+timedelta(days=5))['places_nulled'],0)

    def test_read_hides_expired_coordinates_even_before_sweep(self):
        entry,at=self.place(31)
        with connect() as conn:
            row=items(conn,self.a['user'])[0]
            self.assertIsNone(row['lat']);self.assertIsNone(row['lng'])

    def test_on_demand_is_owner_checked_and_repairs_missing_pin(self):
        entry,_=self.place(31)
        sweep.run()
        route='/items/'+str(entry['id'])+'/coordinates'
        self.assertEqual(self.client.post(route,headers=self.b['headers']).status_code,404)
        # Invoke the real renewal with only its network boundary replaced.
        real=refresh.refresh_place
        with patch.object(refresh,'refresh_place',side_effect=lambda ident:real(ident,fetch=lambda _: {'latitude':38,'longitude':-122})):
            response=self.client.post(route,headers=self.a['headers'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['lat'],38)
        self.assertIn('no-store',response.headers['cache-control'])

    def test_refresh_failure_never_extends_lease(self):
        entry,at=self.place(29)
        def fail(_): raise TimeoutError()
        with patch('time.sleep'): result=refresh.run(fetch=fail,at=at)
        self.assertEqual(result['failed'],1);self.assertEqual(result['provider_calls'],3)
        self.assertAlmostEqual(result['estimated_usd'],.015)
        with connect() as conn:
            row=conn.execute('select * from places where id=%s',(entry['place_id'],)).fetchone()
            self.assertEqual(row['coords_fetched_at'],at-timedelta(days=29))
        self.assertEqual(sweep.run(at=at+timedelta(days=1))['places_nulled'],1)

    def test_alerts_for_day_28_and_missed_sweep(self):
        _,at=self.place(28)
        with connect() as conn: conn.execute('delete from retention_runs')
        data=snapshot(at)
        self.assertEqual(len(data['alerts']),2)
        sweep.run(at=at)
        self.assertEqual(len(snapshot(at)['alerts']),1)

    def test_successful_coordinates_are_shared_not_per_entry(self):
        first=self.save(self.a,'one'); second=self.save(self.b,'two')
        entry,_=self.venue(first,self.a);self.venue(second,self.b)
        with connect() as conn:conn.execute("update places set coords_fetched_at=now()-interval '26 days'")
        with patch('time.sleep'): stats=refresh.run(fetch=lambda _: {'latitude':38,'longitude':-122})
        self.assertEqual(stats['provider_calls'],1)

    def test_schema_has_no_class_c_columns(self):
        with connect() as conn:
            names={r['column_name'] for r in conn.execute("select column_name from information_schema.columns where table_name='places'").fetchall()}
        self.assertTrue({'extracted_name','extracted_city','coords_fetched_at'}<=names)
        self.assertFalse({'name','formatted_address','primary_type','details','map_thumbnail'}&names)

    def test_provider_values_never_override_extracted_name(self):
        entry,_=self.venue(self.save(self.a),self.a)
        with connect() as conn:row=conn.execute('select * from places where id=%s',(entry['place_id'],)).fetchone()
        self.assertEqual(row['extracted_name'],'Tartine Bakery')
        self.assertNotIn('primary_type',row)

    def test_sanitizer_preserves_own_attributes_and_only_candidate_ids(self):
        raw={'title':'Own name','attributes':{'rating':4,'price_level':'cheap'},'place_candidates':[{'place':{'id':'opaque','displayName':{'text':'Google name'},'location':{'latitude':38,'longitude':-122}}}], 'venue_kind_derivation':{'primary_type':'restaurant','kind':'restaurant'}}
        clean=safe_candidate(raw)
        self.assertEqual(clean['attributes'],raw['attributes']);self.assertEqual(clean['place_candidate_ids'],['opaque'])
        self.assertNotIn('Google name',str(clean));self.assertNotIn('latitude',str(clean));self.assertNotIn('primary_type',str(clean))

    def test_unknown_future_invalid_and_boundary_leases(self):
        at=datetime.now(timezone.utc)
        for stamp,lat in [(None,38),(at+timedelta(seconds=1),38),(at-timedelta(days=30),38),(at,float('nan')),(at,91)]:
            self.assertFalse(valid_coordinates({'lat':lat,'lng':-122,'coords_fetched_at':stamp},at))
        self.assertTrue(valid_coordinates({'lat':38,'lng':-122,'coords_fetched_at':at-timedelta(days=29)},at))

    def test_imagery_purge_keeps_site_and_commons(self):
        own={'provider':'site','candidates':[{'source':'site','asset':'a'*64}]}
        self.assertEqual(clean_imagery(own),own)
        mixed={'provider':'google','candidates':[{'source':'google','asset':'b'*64}],'map':own}
        self.assertNotIn('candidates',clean_imagery(mixed));self.assertEqual(clean_imagery(mixed)['map'],own)
        self.assertIn(('thumbs/'+'b'*64+'.jpg','google'),list(references(mixed)))

    def test_exact_cheapest_refresh_mask(self):
        self.assertEqual(refresh.FIELD_MASK,'location')

    def test_stalled_refresh_does_not_block_deletion_or_duplicate_calls(self):
        import threading
        entry,at=self.place(30)
        entered,release=threading.Event(),threading.Event()
        results=[]
        def fetch(_):
            entered.set()
            if not release.wait(8): raise TimeoutError()
            return {'latitude':38,'longitude':-122}
        thread=threading.Thread(target=lambda:results.append(refresh.refresh_place(entry['place_id'],fetch=fetch,at=at)))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(refresh.refresh_place(entry['place_id'],fetch=lambda _: self.fail('Duplicate provider call'),at=at)['attempted'],0)
            self.assertEqual(sweep.run(at=at)['places_nulled'],1)
        finally: release.set();thread.join(8)
        self.assertFalse(thread.is_alive());self.assertEqual(results[0]['succeeded'],1)
