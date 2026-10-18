import base64
import unittest

from extract import extract_from_text


class ExtractDigestTest(unittest.TestCase):
    def _encoded(self, key):
        return base64.b64encode(key.encode()).decode()

    def test_long_digest_pairs_url_and_next_line_base64_with_metadata(self):
        key = "sk-" + "A" * 24
        text = """【第1组】
Base URL
https://gateway.example/v1
Jia mi Base 64
%s
备注：发布者说用不了
【第2组】
Base URL
https://other.example/v1
Jia mi Base 64
%s
""" % (self._encoded(key), self._encoded("sk-" + "B" * 24))
        result = extract_from_text(text)
        self.assertEqual([(x["base_url"], x["api_key"]) for x in result], [
            ("https://gateway.example/v1", key),
            ("https://other.example/v1", "sk-" + "B" * 24),
        ])
        self.assertEqual(result[0]["reported_status"], "possibly_expired")
        self.assertEqual(result[0]["note"], "备注：发布者说用不了")

    def test_invalid_or_masked_base64_is_rejected(self):
        text = """【第1组】
Base URL: https://gateway.example/v1
Jia mi Base 64
not-valid***
【第2组】
Base URL: https://other.example/v1
Jia mi Base 64
%s
""" % self._encoded("sk-abc****xyz")
        self.assertEqual(extract_from_text(text), [])

    def test_duplicate_pair_is_deduplicated_and_groups_do_not_cross_pair(self):
        key = "sk-" + "C" * 24
        encoded = self._encoded(key)
        text = """【第1组】
Base URL: https://gateway.example/v1
Jia mi Base 64
%s
【第2组】
Base URL: https://gateway.example/v1
Jia mi Base 64
%s
""" % (encoded, encoded)
        result = extract_from_text(text)
        self.assertEqual(result, [{"base_url": "https://gateway.example/v1", "api_key": key}])

    def test_metadata_labels_on_following_lines_are_preserved(self):
        key = "sk-" + "E" * 24
        text = """【第1组】
Base URL
https://gateway.example/v1
Jia mi Base 64
%s
可用模型
 deepseek-v4
截止日期
2026-12-31
账号余额
$3.2
贡献者
Alice
""" % self._encoded(key)
        result = extract_from_text(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["models"], ["deepseek-v4"])
        self.assertEqual(result[0]["expires"], "2026-12-31")
        self.assertEqual(result[0]["balance"], "$3.2")
        self.assertEqual(result[0]["contributor"], "Alice")

        key = "sk-" + "D" * 24
        self.assertEqual(extract_from_text("https://gateway.example/v1|" + key), [
            {"base_url": "https://gateway.example/v1", "api_key": key}
        ])


if __name__ == "__main__":
    unittest.main()
