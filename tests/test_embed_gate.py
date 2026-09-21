"""The API instance must never load the embedding model by accident."""
import os, sys, unittest
from unittest.mock import patch
from worker import embed


class QueryEmbeddingGateTests(unittest.TestCase):
    def test_disabled_by_default_without_importing_the_model(self):
        with patch.dict(os.environ, {'REELBOT_QUERY_EMBEDDINGS': ''}), patch.object(embed, 'get_model') as model:
            with self.assertRaises(RuntimeError): embed.embed_query('pizza in nashville')
            model.assert_not_called()

    def test_explicit_opt_in_uses_the_model(self):
        class Vector:
            def tolist(self): return [0.5, 0.5]
        with patch.dict(os.environ, {'REELBOT_QUERY_EMBEDDINGS': 'true'}), patch.object(embed, 'get_model') as model:
            model.return_value.encode.return_value = Vector()
            self.assertEqual(embed.embed_query('pizza'), [0.5, 0.5])


if __name__ == '__main__': unittest.main()
