"""Real Postgres tests of the integrity protocol and the three alert conditions."""
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch
from psycopg.types.json import Jsonb
import test_personal as base
from worker.db import connect
from worker import operations

class IntegrityTests(unittest.TestCase):
    setUpClass=classmethod(base.PersonalTests.setUpClass.__func__)
    setUp=base.PersonalTests.setUp
    register=base.PersonalTests.register
    save=base.PersonalTests.save
    venue=base.PersonalTests.venue

    def mutation(self,entry,**extra):
        value={'id':str(uuid4()),'path':'/items/'+str(entry['id']),'method':'PATCH','body':{'note':'offline'},'client_timestamp':datetime.now(timezone.utc).isoformat(),**extra}
        response=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[value]})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['results'][0],value

    def test_I2_conflict_versions_owner_scope_and_three_resolutions(self):
        for choice in ('mine','theirs','merge'):
            with self.subTest(choice=choice):
                entry,_=self.venue(self.save(self.a,choice),self.a)
                route='/items/'+str(entry['id'])
                self.client.patch(route,headers=self.a['headers'],json={'note':'other device'})
                result,_=self.mutation(entry,client_timestamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
                self.assertEqual(result['status'],'superseded','I2: stale edit must conflict')
                self.assertEqual(result['server_body'],{'note':'other device'})
                resolved,value=self.mutation(entry,resolution='theirs' if choice=='theirs' else 'mine',expected_updated_at=result['server_version'],body={'note':'merged' if choice=='merge' else 'mine'})
                self.assertEqual(resolved['status'],'applied','I2: explicit resolution must acknowledge')
                if choice=='theirs':self.assertNotIn('coords_fetched_at',resolved['server_entry'])
                retry=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[value]}).json()['results'][0]
                self.assertEqual(retry,resolved,'I2: acknowledgment must replay idempotently')
                with connect() as conn: row=conn.execute('select note from entries where id=%s',(entry['id'],)).fetchone()
                self.assertEqual(row['note'],{'mine':'mine','theirs':'other device','merge':'merged'}[choice])
                foreign=self.client.post('/sync',headers=self.b['headers'],json={'mutations':[{**value,'id':str(uuid4())}]}).json()['results'][0]
                self.assertEqual(foreign['status'],'not_found');self.assertNotIn('server_body',foreign)

    def test_I2_resolution_race_conflicts_instead_of_overwriting_third_edit(self):
        entry,_=self.venue(self.save(self.a),self.a)
        result,_=self.mutation(entry,client_timestamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        self.client.patch('/items/'+str(entry['id']),headers=self.a['headers'],json={'note':'third version'})
        raced,_=self.mutation(entry,resolution='mine',expected_updated_at=result['server_version'])
        self.assertEqual(raced['status'],'superseded','I2: stale resolution must not overwrite')
        self.assertEqual(raced['server_body']['note'],'third version')

    def test_I2_stale_folder_assignment_is_not_acknowledged_as_an_applied_noop(self):
        entry,_=self.venue(self.save(self.a),self.a)
        folder=self.client.post('/folders',headers=self.a['headers'],json={'name':'Offline destination'}).json()
        body={'item_ids':[str(entry['id'])],'destination_id':folder['id']}
        operation={'id':str(uuid4()),'path':'/folders/assign','method':'POST','body':body,'client_timestamp':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
        result=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[operation]}).json()['results'][0]
        self.assertEqual(result['status'],'superseded','I2: stale membership must not be silently accepted')
        self.assertIn('memberships',result['server_body'])
        resolution={**operation,'id':str(uuid4()),'client_timestamp':datetime.now(timezone.utc).isoformat(),'resolution':'mine','expected_updated_at':result['server_version']}
        applied=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[resolution]})
        self.assertEqual(applied.status_code,200,applied.text);self.assertEqual(applied.json()['results'][0]['status'],'applied')
        with connect() as conn:self.assertTrue(conn.execute('select 1 from folder_items where entry_id=%s and folder_id=%s and deleted_at is null',(entry['id'],folder['id'])).fetchone())

    def test_I4_batched_reconciliation_returns_ten_cached_pins_with_no_provider_calls(self):
        entries=[self.venue(self.save(self.a,str(i)),self.a,name='Venue '+str(i))[0] for i in range(10)]
        with patch('worker.coordinate_refresh.refresh_place',side_effect=AssertionError('I4: fresh cache should not call provider')):
            result=self.client.post('/coordinates/reconcile',headers=self.a['headers'],json={'entry_ids':[str(e['id']) for e in entries]})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(len(result.json()['coordinates']),10)
        self.assertTrue(all(p['lat'] is not None and p['coords_fetched_at'] for p in result.json()['coordinates']))
        other=self.client.post('/coordinates/reconcile',headers=self.b['headers'],json={'entry_ids':[str(e['id']) for e in entries]})
        self.assertEqual(other.json()['coordinates'],[],'I4: reconciliation is owner scoped')

    def test_I4_refresh_advances_entry_timestamp_and_existing_cursor(self):
        from worker.coordinate_refresh import refresh_place
        entry,_=self.venue(self.save(self.a),self.a)
        cursor=self.client.get('/sync',headers=self.a['headers'],params={'since':'0'}).json()['cursor']
        with connect() as conn: conn.execute("update places set coords_fetched_at=now()-interval '26 days' where id=%s",(entry['place_id'],))
        refresh_place(entry['place_id'],fetch=lambda _:{'latitude':38,'longitude':-121},force=True)
        changes=self.client.get('/sync',headers=self.a['headers'],params={'since':cursor}).json()['changes']
        changed=[r['payload'] for r in changes if r['entity']=='entries' and r['payload']['id']==str(entry['id'])]
        self.assertEqual(changed[0]['lat'],38,'I4: refresh must reach incremental sync')
        self.assertGreater(datetime.fromisoformat(changed[0]['updated_at']),entry['updated_at'])

    def test_I2_malformed_mutation_is_a_permanent_rejection_not_an_acknowledgment(self):
        entry,_=self.venue(self.save(self.a),self.a)
        value={'id':str(uuid4()),'path':'/items/'+str(entry['id']),'method':'PATCH','body':{'note':'x'*5001},'client_timestamp':datetime.now(timezone.utc).isoformat()}
        response=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[value]})
        self.assertEqual(response.status_code,422,'I2: malformed edit must be visibly rejected')
        with connect() as conn:self.assertFalse(conn.execute('select 1 from sync_mutations where id=%s',(value['id'],)).fetchone())

    @patch.dict('os.environ', {'REELBOT_EMAIL_ALERTS_ENABLED':'true'})
    def test_alerts_threshold_delivery_failure_retry_dedup_and_readyz(self):
        now=datetime.now(timezone.utc)
        with connect() as conn:
            conn.execute('delete from retention_runs')
            conn.execute("insert into jobs(status,created_at) values('queued',%s)",(now-timedelta(minutes=31),))
            for status in ('failed','failed','resolved','resolved','resolved'):
                conn.execute("insert into events(kind,detail,created_at) values('save_finished',%s,%s)",(Jsonb({'status':status}),now-timedelta(minutes=35)))
        operations.heartbeat();data=operations.snapshot(now)
        self.assertEqual({a[0] for a in operations.evaluate(data,now)},{'worker_stalled','retention_sweep_missed','extraction_error_rate'})
        emitted=[]
        result=operations.run(send=emitted.append,at=now)
        self.assertEqual(len(emitted),3);self.assertEqual(len(result['alerts_sent']),3)
        self.assertEqual(operations.run(send=emitted.append,at=now)['alerts_sent'],[])
        with patch('worker.storage.exists',return_value=False):response=self.client.get('/readyz')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['operations']['queue_depth'],1)
        for field in ('worker_heartbeat_age_seconds','oldest_coords_fetched_at','last_sweep_at'):self.assertIn(field,response.json()['operations'])
        data.update(extractions_last_hour=10,failures_last_hour=3,last_sweep_at=now,oldest_queued_at=now)
        self.assertEqual(operations.evaluate(data,now),[])
        later=now+timedelta(hours=2)
        with self.assertLogs('worker.operations',level='ERROR'):operations.run(send=lambda _:(_ for _ in ()).throw(TimeoutError()),at=later)
        self.assertEqual(operations.run(send=emitted.append,at=later+timedelta(minutes=1))['alerts_sent'],[])
        self.assertTrue(operations.run(send=emitted.append,at=later+timedelta(minutes=6))['alerts_sent'])

    def test_email_transport_requires_tls_and_confirmed_recipient(self):
        env={'REELBOT_EMAIL_ALERTS_ENABLED':'true','REELBOT_SMTP_HOST':'smtp.example.test','REELBOT_ALERT_FROM':'reelbot@example.test','REELBOT_ALERT_TO':'krishwaghani@gmail.com'}
        with patch.dict('os.environ',env),patch('smtplib.SMTP') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value={}
            operations.send_email(operations.message('worker_stalled','Test',test=True))
            smtp.return_value.__enter__.return_value.starttls.assert_called_once()
            sent=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(sent['To'],'krishwaghani@gmail.com')
