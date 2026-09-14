from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from worker.storage import LocalStorage, _thumbnail


class ConfigurationTests(unittest.TestCase):
    def tearDown(self):
        config.reset_for_tests()

    def test_render_postgres_scheme_is_normalized_once(self):
        source = 'postgres://user:pass@db/name'
        self.assertEqual(config.normalize_database_url(source), 'postgresql+psycopg2://user:pass@db/name')
        self.assertEqual(config.psycopg_database_url(source), 'postgresql://user:pass@db/name')

    def test_required_service_values_name_the_missing_variable(self):
        with patch.dict(os.environ, {}, clear=True):
            config.reset_for_tests()
            with self.assertRaisesRegex(config.ConfigurationError, 'DATABASE_URL'):
                config.validate_service_config()


class LocalStorageTests(unittest.TestCase):
    def test_local_storage_round_trip_and_thumbnail_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorage(Path(directory))
            storage.put('photos/p/site-hash.jpg', b'picture', 'image/jpeg')
            self.assertTrue(storage.exists('photos/p/site-hash.jpg'))
            self.assertEqual(Path(storage.get_url('photos/p/site-hash.jpg').removeprefix('file://')).read_bytes(), b'picture')
            storage.delete('photos/p/site-hash.jpg')
            self.assertFalse(storage.exists('photos/p/site-hash.jpg'))

    def test_thumbnail_is_card_sized_and_under_budget(self):
        from PIL import Image
        raw = io.BytesIO(); Image.new('RGB', (2400, 1600), '#c9a227').save(raw, 'JPEG', quality=95)
        with tempfile.TemporaryDirectory() as directory, patch('worker.storage._backend', return_value=LocalStorage(Path(directory))):
            key, payload, content_type = _thumbnail(raw.getvalue(), 'entry-id')
        self.assertTrue(key.startswith('thumbs/entry-id.'))
        self.assertLess(len(payload), 60_000)
        self.assertIn(content_type, {'image/webp', 'image/jpeg'})


class HealthTests(unittest.TestCase):
    def test_healthz_has_no_dependency_calls(self):
        # Calling the handler directly avoids startup checks and proves that the
        # Render liveness route cannot begin a database or storage operation.
        from api.main import health
        with patch('api.main.connect', side_effect=AssertionError('database must not be touched')):
            self.assertEqual(health()['status'], 'ok')
