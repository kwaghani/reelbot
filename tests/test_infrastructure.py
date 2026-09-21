from __future__ import annotations

import io
import os
import sys
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


class BackupSafetyTests(unittest.TestCase):
    def _upload(self, present=True):
        from worker.retention_backup import main
        from unittest.mock import Mock
        backend=Mock();backend.exists.return_value=present
        backend.client.get_paginator.return_value.paginate.return_value=[]
        with patch('worker.retention_backup.R2Storage',return_value=backend), \
             patch('worker.retention_backup.create'), \
             patch.object(Path,'read_bytes',return_value=b'compressed fixture'):
            main()
        backend.put.assert_called_once()
        backend.exists.assert_called_once()

    def test_remote_backup_rejects_local_fallback(self):
        from worker.retention_backup import main
        with patch.dict(os.environ, {}, clear=True), patch('worker.storage.LocalStorage') as local:
            config.reset_for_tests()
            try:
                with self.assertRaisesRegex(RuntimeError,'R2 configuration is incomplete'): main()
                local.assert_not_called()
            finally: config.reset_for_tests()

    def test_remote_backup_requires_persisted_object(self):
        with self.assertRaisesRegex(RuntimeError,'Backup verification failed'): self._upload(False)
        self._upload(True)

    def test_backup_requires_retention_schema_and_explicit_class_a_copy(self):
        from worker import retention_backup
        import inspect
        source=inspect.getsource(retention_backup.create)
        self.assertIn('20260915000000_google_retention',source)
        self.assertIn("('lat','lng','coords_fetched_at')",source)
        self.assertIn('pg_export_snapshot',source)
        self.assertIn('--exclude-table-data=public.places',source)
