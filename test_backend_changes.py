"""离线回归检查：不连接网络、不读取真实配置、不修改真实数据库。"""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import media
import store


class BackendChangesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        self.old_db = store.DB_PATH
        store.DB_PATH = self.db

    def tearDown(self):
        store.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_init_and_safe_locator(self):
        store.init()
        cols = {r[1] for r in sqlite3.connect(self.db).execute("PRAGMA table_info(raw_messages)")}
        self.assertIn("read", cols)
        self.assertIn("extracted", cols)
        store.save_raw("web", "mjjgo", "mjj:1", msg_type="text", content="secret body")
        hit = store.locate_message("web", "mjjgo", "mjj:1")
        self.assertEqual(hit["msg_key"], "mjj:1")
        self.assertNotIn("content", hit)

    def test_decrypt_failure_does_not_advance_marker(self):
        decrypted = os.path.join(self.tmp.name, "decrypted")
        dbdir = os.path.join(self.tmp.name, "db")
        os.makedirs(decrypted); os.makedirs(dbdir)
        open(os.path.join(dbdir, "msg.db"), "wb").close()
        marker = os.path.join(decrypted, ".last_mtime")
        with open(marker, "w") as f:
            f.write("0")
        real_exists = os.path.exists
        fake_proc = type("P", (), {"returncode": 2, "stdout": "", "stderr": "secret"})()
        with patch.object(media, "WECHAT_KEYS_JSON", "keys.json"), \
             patch.object(media, "WECHAT_DB_DIR", dbdir), \
             patch.object(media, "WECHAT_DECRYPTED", decrypted), \
             patch.object(media, "WECHAT_PY", "python"), \
             patch.object(media.subprocess, "run", return_value=fake_proc), \
             patch("os.path.exists", side_effect=lambda p: True if p.endswith("decrypt.py") or p == marker else real_exists(p)):
            result = media.refresh_wechat_decrypted_status()
        self.assertFalse(result["ok"])
        with open(marker, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "0")

    def test_stale_decrypted_output_triggers_refresh_even_with_marker(self):
        decrypted = os.path.join(self.tmp.name, "decrypted")
        dbdir = os.path.join(self.tmp.name, "db")
        os.makedirs(os.path.join(decrypted, "message")); os.makedirs(os.path.join(dbdir, "message"))
        src = os.path.join(dbdir, "message", "message_0.db")
        dst = os.path.join(decrypted, "message", "message_0.db")
        open(src, "wb").close(); open(dst, "wb").close()
        os.utime(src, (20, 20)); os.utime(dst, (10, 10))
        marker = os.path.join(decrypted, ".last_mtime")
        with open(marker, "w") as f:
            f.write("999")
        real_exists = os.path.exists
        fake_proc = type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(media, "WECHAT_KEYS_JSON", "keys.json"), \
             patch.object(media, "WECHAT_DB_DIR", dbdir), \
             patch.object(media, "WECHAT_DECRYPTED", decrypted), \
             patch.object(media, "WECHAT_PY", "python"), \
             patch.object(media.subprocess, "run", return_value=fake_proc), \
             patch("os.path.exists", side_effect=lambda p: True if p.endswith("decrypt.py") else real_exists(p)):
            result = media.refresh_wechat_decrypted_status()
        self.assertTrue(result["ok"])
        self.assertTrue(result["ran"])


if __name__ == "__main__":
    unittest.main()
