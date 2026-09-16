import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch
import test_personal as base
from worker.db import connect


class AccountsReleaseIntegrityTests(unittest.TestCase):
    setUpClass=classmethod(base.PersonalTests.setUpClass.__func__)
    setUp=base.PersonalTests.setUp
    register=base.PersonalTests.register
    save=base.PersonalTests.save
    venue=base.PersonalTests.venue

    def test_conflict_and_three_resolutions_preserve_writing(self):
        for choice in ('mine','theirs','merge'):
            entry,_=self.venue(self.save(self.a,choice),self.a)
            path='/items/'+str(entry['id'])
            self.client.patch(path,headers=self.a['headers'],json={'note':'server note'})
            mutation={'id':str(uuid4()),'path':path,'method':'PATCH','body':{'note':'offline note'},
                      'client_timestamp':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
            def submit(value):
                response=self.client.post('/sync',headers=self.a['headers'],json={'mutations':[value]})
                self.assertEqual(response.status_code,200,response.text)
                return response.json()['results'][0]
            result=submit(mutation)
            self.assertEqual(result['status'],'superseded')
            self.assertEqual(result['server_body']['note'],'server note')
            final={**mutation,'id':str(uuid4()),'client_timestamp':datetime.now(timezone.utc).isoformat(),
                   'resolution':'theirs' if choice=='theirs' else 'mine','expected_updated_at':result['server_version'],
                   'body':{'note':'merged note' if choice=='merge' else 'offline note'}}
            applied=submit(final);self.assertEqual(applied['status'],'applied');self.assertEqual(submit(final),applied)
            with connect() as conn:note=conn.execute('select note from entries where id=%s',(entry['id'],)).fetchone()['note']
            self.assertEqual(note,{'mine':'offline note','theirs':'server note','merge':'merged note'}[choice])

    @patch.dict('os.environ',{'REELBOT_APPLE_SIGN_IN_ENABLED':'false'})
    def test_disabled_apple_keeps_anonymous_sync_usable(self):
        with patch('api.accounts.verify_apple') as verify:
            result=self.client.post('/auth/apple',headers=self.a['headers'],json={'identity_token':'x'*30,'nonce':'a'*64,'authorization_code':'code'})
            self.assertEqual(result.status_code,503);verify.assert_not_called()
        self.assertEqual(self.client.get('/sync?since=0',headers=self.a['headers']).status_code,200)
