import base64
import unittest
from unittest.mock import patch

import providers


class ProviderDecodeTest(unittest.TestCase):
    def test_decode_key_accepts_prefixed_and_plain_base64(self):
        key = "sk-" + "Z" * 24
        encoded = base64.b64encode(key.encode()).decode()
        self.assertEqual(providers._decode_key(encoded), key)
        self.assertEqual(providers._decode_key("base64:" + encoded), key)
        self.assertEqual(providers._decode_key("data:text/plain;base64," + encoded), key)

    def test_decode_key_keeps_invalid_or_masked_values(self):
        self.assertEqual(providers._decode_key("not-base64-value"), "not-base64-value")
        self.assertEqual(providers._decode_key("sk-abc****xyz"), "sk-abc****xyz")

    def test_dsh_entry_decodes_key_and_preserves_custom_endpoint(self):
        key = "sk-" + "Q" * 24
        encoded = base64.b64encode(key.encode()).decode()
        item = providers._dsh_provider_entry(
            "custom", {"baseURL": "https://relay.example/custom/v4", "apiKey": encoded},
            {}, "desktop", "/tmp/provider.yml", "patch")
        self.assertEqual(item["api_key"], key)
        self.assertEqual(item["base_url"], "https://relay.example/custom/v4")

    @patch("providers.httpx.get")
    def test_test_one_curl_is_redacted_and_uses_custom_endpoint(self, get):
        class Response:
            status_code = 401
            text = "bad"
            def json(self):
                return {}
        get.return_value = Response()
        key = "sk-" + "R" * 24
        result = providers.test_one({"base_url": "https://relay.example/custom/v4", "api_key": key, "api_style": "openai"})
        self.assertIn("https://relay.example/custom/v4", result["curl"])
        self.assertNotIn(key, result["curl"])
        self.assertIn("***", result["curl"])


if __name__ == "__main__":
    unittest.main()
