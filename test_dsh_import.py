import os
import tempfile
import unittest
from unittest.mock import patch

import api_extensions


PATCH = """- id: llm-pi-ai
  config:
    providers:
      existing:
        apiKeyEnv: EXISTING_KEY
        api: openai-completions
        baseURL: https://existing.example/v1
        models:
          - id: old
            name: old
- id: ui-settings
  config:
    enabled: true
"""


class DshImportTest(unittest.TestCase):
    def test_import_targets_desktop_patch_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch_path = os.path.join(tmp, "cordis.patch.yml")
            creds_path = os.path.join(tmp, "credentials.yaml")
            with open(patch_path, "w", encoding="utf-8") as handle:
                handle.write(PATCH)
            with open(creds_path, "w", encoding="utf-8") as handle:
                handle.write("version: 1\nrefs: {}\n")
            with patch.object(api_extensions, "DSH_PATCH_PATH", patch_path), \
                 patch.object(api_extensions, "DSH_CREDENTIALS_PATH", creds_path):
                first = api_extensions.import_to_dsh_keys(
                    "https://api.example/v1/", "secret-test-key", "My Provider", ["model-a", "model:b"])
                with open(patch_path, encoding="utf-8") as handle:
                    first_text = handle.read()
                second = api_extensions.import_to_dsh_keys(
                    "https://api.example/v1/", "secret-test-key", "My Provider", ["model-a", "model:b"])
                with open(patch_path, encoding="utf-8") as handle:
                    second_text = handle.read()
            self.assertTrue(first["ok"])
            self.assertEqual(first["action"], "added")
            self.assertTrue(first["verified"])
            self.assertTrue(second["ok"])
            self.assertEqual(second["action"], "updated")
            self.assertEqual(first_text, second_text)
            self.assertNotIn("secret-test-key", first_text)
            self.assertIn("EGG_HARVESTER_MY_PROVIDER_API_KEY", first_text)
            self.assertIn("model:b", first_text)

    def test_invalid_patch_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch_path = os.path.join(tmp, "cordis.patch.yml")
            creds_path = os.path.join(tmp, "credentials.yaml")
            with open(patch_path, "w", encoding="utf-8") as handle:
                handle.write("- id: unrelated\n")
            with open(patch_path, encoding="utf-8") as handle:
                original = handle.read()
            with patch.object(api_extensions, "DSH_PATCH_PATH", patch_path), \
                 patch.object(api_extensions, "DSH_CREDENTIALS_PATH", creds_path):
                result = api_extensions.import_to_dsh_keys("https://x", "key", "x", ["m"])
            self.assertFalse(result["ok"])
            with open(patch_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)
            self.assertFalse(os.path.exists(creds_path))


if __name__ == "__main__":
    unittest.main()
