"""Protect newer user changes without network access or personal configuration."""
import os
import tempfile
import unittest
from unittest.mock import patch

import providers
import stations


class PreservedFeaturesTest(unittest.TestCase):
    def test_models_urls_and_browser_headers(self):
        for base, expected in (
            ('https://example.test/v1/', 'https://example.test/v1/models'),
            ('https://example.test', 'https://example.test/v1/models'),
            ('https://example.test/api/paas/v4/', 'https://example.test/api/paas/v4/models'),
        ):
            with self.subTest(base=base):
                self.assertEqual(providers._models_url(base), expected)
        self.assertIn('Mozilla', providers._UA_HEADERS['User-Agent'])
        self.assertIn('application/json', providers._UA_HEADERS['Accept'])

    def test_preview_does_not_import_and_selected_skips_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(stations, 'DB_PATH', os.path.join(folder, 'test.db')):
                entries = [{'url': 'https://one.example', 'title': 'One'},
                           {'url': 'https://two.example', 'title': 'Two'}]
                preview = stations.preview_items(entries)
                self.assertEqual(preview['total'], 2)
                self.assertFalse(stations.find_by_url(entries[0]['url']))
                first = stations.import_selected(entries[:1])
                self.assertEqual(first['added'], 1)
                self.assertFalse(stations.find_by_url(entries[1]['url']))
                second = stations.import_selected(entries)
                self.assertEqual(second['added'], 1)
                self.assertEqual(second['skipped'], 1)


if __name__ == '__main__':
    unittest.main()
