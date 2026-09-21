"""Release defaults keep unavailable integrations inert, not half configured."""
from unittest.mock import patch
import unittest
import test_personal as personal
from worker.db import connect
from worker import operations


class DisabledFeaturesTests(unittest.TestCase):
    setUpClass=classmethod(personal.PersonalTests.setUpClass.__func__)
    setUp=personal.PersonalTests.setUp
    register=personal.PersonalTests.register
    @patch.dict('os.environ', {'REELBOT_APPLE_SIGN_IN_ENABLED':'false'})
    def test_disabled_apple_has_no_provider_or_nonce_side_effect(self):
        with patch('api.accounts.verify_apple') as verify:
            for path, body in (
                ('/auth/apple/challenge', {'nonce':'a'*64}),
                ('/auth/apple', {'identity_token':'x'*30,'authorization_code':'code','nonce':'a'*64}),
            ):
                response=self.client.post(path,headers=self.a['headers'],json=body)
                self.assertEqual(response.status_code,503,response.text)
            verify.assert_not_called()
        with connect() as conn:
            self.assertEqual(conn.execute("select count(*) n from events where kind='apple_nonce'").fetchone()['n'],0)
        self.assertEqual(self.client.get('/sync',headers=self.a['headers']).status_code,200)

    @patch.dict('os.environ', {'REELBOT_EMAIL_ALERTS_ENABLED':'false'})
    def test_disabled_email_does_not_send_or_claim_but_keeps_health(self):
        with patch('smtplib.SMTP') as smtp,patch('smtplib.SMTP_SSL') as smtp_ssl:
            result=operations.run()
            self.assertFalse(result['email_alerts_enabled'])
            self.assertIn('queue_depth',result['snapshot'])
            self.assertEqual(result['alerts_sent'],[])
            with self.assertRaisesRegex(RuntimeError,'disabled'):
                operations.send_email(operations.message('worker_stalled','test',test=True))
            smtp.assert_not_called();smtp_ssl.assert_not_called()
        with connect() as conn:
            self.assertEqual(conn.execute("select count(*) n from events where kind='operations_alert'").fetchone()['n'],0)
