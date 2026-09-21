"""Regression guard for the recurring placeholder: signed reel-cover URLs expire."""
import tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import httpx
import test_personal as base
from test_imagery import photo
from worker import cover_refresh as cr
from worker import imagery as im
from worker.db import connect
from psycopg.types.json import Jsonb

NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


def tiktok(at):
    return f'https://p16-common-sign.tiktokcdn-us.com/obj/cover.jpeg?x-expires={int(at.timestamp())}&x-signature=abc'


def instagram(at):
    return f'https://scontent-den2-1.cdninstagram.com/v/t51/cover.jpg?stp=dst&oe={int(at.timestamp()):X}&oh=abc'


def forbidden(url):
    request = httpx.Request('GET', url)
    return httpx.HTTPStatusError('403', request=request, response=httpx.Response(403, request=request))


class CoverRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): base.PersonalTests.setUpClass.__func__(cls)
    register = base.PersonalTests.register
    save = base.PersonalTests.save
    venue = base.PersonalTests.venue

    def setUp(self):
        base.PersonalTests.setUp(self)
        with connect() as conn: conn.execute("delete from retention_runs where job='cover_refresh'")
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        cache = patch.object(im, 'cache_dir', return_value=Path(self.tmp.name)); cache.start(); self.addCleanup(cache.stop)
        ocr = patch.object(im, 'text_coverage', return_value=0.0); ocr.start(); self.addCleanup(ocr.stop)
        pause = patch.object(cr.time, 'sleep'); pause.start(); self.addCleanup(pause.stop)
        self.row = self.venue(self.save(self.a), self.a)[0]

    def cover(self, url, **extra):
        with connect() as conn:
            conn.execute('update saves set raw_signals=%s,cover_imagery=%s where id=%s',
                         (Jsonb({'thumbnail_url': url, **extra}), Jsonb({}), self.row['save_id']))

    def signals(self):
        with connect() as conn:
            return conn.execute('select raw_signals,cover_imagery from saves where id=%s', (self.row['save_id'],)).fetchone()

    def resolve(self):
        return im.resolve_batch(self.a['user'], [self.row['id']], context='library')['items'][str(self.row['id'])]

    def test_expiry_parameters_parse_for_both_platforms(self):
        at = datetime(2026, 9, 12, 11, 14, 19, tzinfo=timezone.utc)
        self.assertEqual(cr.url_expiry(tiktok(at)), at)
        self.assertEqual(cr.url_expiry(instagram(at)), at)
        self.assertIsNone(cr.url_expiry('https://i.ytimg.com/vi/abc/hqdefault.jpg'))
        self.assertTrue(cr.is_signed('https://scontent.cdninstagram.com/x.jpg'))
        self.assertFalse(cr.is_signed('https://i.ytimg.com/vi/abc/hqdefault.jpg'))

    def test_valid_stored_cover_renders_the_cover_not_the_placeholder(self):
        self.cover(tiktok(datetime.now(timezone.utc) + timedelta(days=2)))
        with patch.object(im, 'acquire', return_value=[]), patch.object(im, 'fetch', return_value=(photo(), 'x')), \
             patch.object(cr, 'fresh_cover_url') as refresh:
            item = self.resolve()
        refresh.assert_not_called()
        self.assertEqual(item['selected']['source'], 'cover')
        self.assertNotEqual(item['selection']['selected'], 'placeholder')

    def test_expired_url_is_refreshed_before_fetch_never_the_placeholder(self):
        dead = tiktok(datetime.now(timezone.utc) - timedelta(hours=1))
        fresh = tiktok(datetime.now(timezone.utc) + timedelta(days=3))
        self.cover(dead)
        fetched = []
        with patch.object(im, 'acquire', return_value=[]), patch.object(cr, 'fresh_cover_url', return_value=fresh) as refresh, \
             patch.object(im, 'fetch', side_effect=lambda url, **kw: fetched.append(url) or (photo(), url)):
            item = self.resolve()
        refresh.assert_called_once()
        self.assertEqual(fetched, [fresh])
        self.assertEqual(item['selected']['source'], 'cover')
        stored = self.signals()['raw_signals']
        self.assertEqual(stored['thumbnail_url'], fresh)
        self.assertEqual(datetime.fromisoformat(stored['thumbnail_expires_at']), cr.url_expiry(fresh))

    def test_unparsed_403_triggers_one_refresh_and_retry(self):
        self.cover('https://p16-sign.tiktokcdn-us.com/cover.jpeg')
        fresh = tiktok(datetime.now(timezone.utc) + timedelta(days=3))
        def fetch(url, **kw):
            if url != fresh: raise forbidden(url)
            return photo(), url
        with patch.object(im, 'acquire', return_value=[]), patch.object(cr, 'fresh_cover_url', return_value=fresh), \
             patch.object(im, 'fetch', side_effect=fetch):
            self.assertEqual(self.resolve()['selected']['source'], 'cover')

    def test_transient_failure_and_retry_button_keep_a_working_cover(self):
        self.cover(tiktok(datetime.now(timezone.utc) + timedelta(days=3)))
        with patch.object(im, 'acquire', return_value=[]), patch.object(im, 'fetch', return_value=(photo(), 'x')):
            self.assertEqual(self.resolve()['selected']['source'], 'cover')
        # The retry control and the backfill used to erase the validated cover.
        self.assertEqual(self.client.post('/items/' + str(self.row['id']) + '/image/retry', headers=self.a['headers']).status_code, 200)
        self.assertIn('candidate', self.signals()['cover_imagery'])
        with patch.object(im, 'acquire', return_value=[]), patch.object(cr, 'fresh_cover_url', side_effect=ValueError('blocked')), \
             patch.object(im, 'fetch', side_effect=forbidden('https://cdn')):
            item = self.resolve()
            self.assertEqual(item['selected']['source'], 'cover')
            self.assertEqual(im.resolve_batch(self.a['user'], [self.row['id']], refresh_covers=True)['items'][str(self.row['id'])]['selected']['source'], 'cover')
        self.assertIn('candidate', self.signals()['cover_imagery'])

    def test_job_refreshes_before_expiry_backs_off_and_reports(self):
        self.cover(instagram(NOW + timedelta(hours=6)))
        self.assertEqual(cr.due(NOW), [self.row['save_id']])
        self.assertEqual(cr.due(NOW - timedelta(days=1)), [])
        fresh = instagram(NOW + timedelta(days=5))
        with patch.object(cr, 'revalidate') as revalidate:
            totals = cr.run(lambda source: fresh, at=NOW)
        self.assertEqual((totals['refreshed'], totals['failed']), (1, 0))
        revalidate.assert_called_once_with(self.row['save_id'])
        self.assertEqual(self.signals()['raw_signals']['thumbnail_url'], fresh)
        self.assertEqual(cr.due(NOW), [])
        # A failed renewal keeps the old URL and waits out the backoff.
        self.cover(instagram(NOW - timedelta(hours=1)))
        def blocked(source): raise ValueError('login_wall')
        self.assertEqual(cr.run(blocked, at=NOW)['failed'], 1)
        stored = self.signals()['raw_signals']
        self.assertEqual(stored['thumbnail_refresh_error'], 'login_wall')
        self.assertEqual(stored['thumbnail_url'], instagram(NOW - timedelta(hours=1)))
        self.assertEqual(cr.due(NOW), [])
        self.assertEqual(cr.due(NOW + cr.FAILURE_BACKOFF), [self.row['save_id']])
        with connect() as conn:
            runs = conn.execute("select count(*) n from retention_runs where job='cover_refresh'").fetchone()['n']
        self.assertEqual(runs, 2)

    def test_readyz_reports_expired_and_placeholder_covers(self):
        self.cover(tiktok(datetime.now(timezone.utc) - timedelta(hours=1)))
        imagery = self.client.get('/readyz').json()['imagery']
        self.assertEqual(imagery['expired_cover_urls'], 1)
        self.assertIn('last_cover_refresh_at', imagery)
        self.cover(tiktok(datetime.now(timezone.utc) + timedelta(days=2)))
        with connect() as conn:
            conn.execute("update entries set image_selection=%s where id=%s", (Jsonb({'selected': 'placeholder'}), self.row['id']))
        imagery = self.client.get('/readyz').json()['imagery']
        self.assertEqual((imagery['expired_cover_urls'], imagery['placeholder_with_available_cover']), (0, 1))
        self.assertEqual(cr.stale_placeholders(), [self.row['save_id']])

    def test_legacy_site_cache_loses_google_derived_urls(self):
        legacy = {'strategy': 'cover-first-v1', 'provider': 'site', 'retry_at': '2999-01-01',
                  'candidates': [{'source': 'site', 'key': 'site:0', 'url': 'https://venue.example/food.jpg',
                                  'attribution': {'label': 'venue.example', 'url': 'https://venue.example/'}}]}
        with connect() as conn:
            conn.execute('update places set imagery=%s where id=%s', (Jsonb({**legacy, 'map': legacy}), self.row['place_id']))
        self.assertEqual(cr.scrub_legacy_site_urls(), 1)
        with connect() as conn:
            stored = conn.execute('select imagery from places where id=%s', (self.row['place_id'],)).fetchone()['imagery']
        self.assertNotIn('venue.example', str(stored))
        self.assertEqual(cr.scrub_legacy_site_urls(), 0)


if __name__ == '__main__': unittest.main()
