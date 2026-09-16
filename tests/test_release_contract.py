"""Legacy anonymous HTTP calls remain usable beside the new change feed."""
import unittest
import test_personal as base


class ReleaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.PersonalTests.setUpClass.__func__(cls)

    register = base.PersonalTests.register
    save = base.PersonalTests.save
    venue = base.PersonalTests.venue
    setUp = base.PersonalTests.setUp

    def test_legacy_full_sync_and_direct_note_edit_reach_new_change_feed(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        response = self.client.get('/sync', headers=self.a['headers'])
        self.assertEqual(response.status_code, 200)
        self.assertTrue({'registry', 'venue_kinds', 'preferences', 'items', 'saves', 'folders', 'apple_linked'} <= response.json().keys())
        self.assertEqual(response.json()['items'][0]['id'], str(entry['id']))
        edited = self.client.patch('/items/' + str(entry['id']), headers=self.a['headers'], json={'note': 'Legacy-client note survives'})
        self.assertEqual(edited.status_code, 200)
        changes = self.client.get('/sync?since=0', headers=self.a['headers'])
        self.assertEqual(changes.status_code, 200)
        records = [change['payload'] for change in changes.json()['changes'] if change['entity'] == 'entries']
        self.assertTrue(any(row['id'] == str(entry['id']) and row['note'] == 'Legacy-client note survives' for row in records))

    def test_legacy_delete_is_hidden_from_full_sync_and_retained_as_tombstone(self):
        entry, _ = self.venue(self.save(self.a), self.a)
        response = self.client.delete('/items/' + str(entry['id']), headers=self.a['headers'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/sync', headers=self.a['headers']).json()['items'], [])
        changes = self.client.get('/sync?since=0', headers=self.a['headers']).json()['changes']
        self.assertTrue(any(change['entity'] == 'entries' and change['payload']['id'] == str(entry['id']) and change['payload'].get('deleted_at') for change in changes))

    def test_old_anonymous_bearer_cannot_read_another_owners_new_feed(self):
        self.venue(self.save(self.a), self.a)
        a = self.client.get('/sync?since=0', headers=self.a['headers']).json()
        response = self.client.get('/sync', params={'since': a['cursor']}, headers=self.b['headers'])
        self.assertEqual(response.status_code, 400)
