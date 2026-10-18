import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Keep this focused database test runnable in a bare checkout; the application
# optionally uses python-dotenv, but undo persistence itself has no dependency
# on it.
if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

import db


class KeyUndoDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.path_patch = patch.object(db, "DB_PATH", self.db_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        os.unlink(self.db_path)

    def _insert(self, key, status="valid"):
        db.store_valid_key("test", "https://example.test/v1", key,
                           "test", ["test-model"], 1)
        if status != "valid":
            c = db._conn()
            c.execute("UPDATE keys SET status=? WHERE api_key_hash=?", (status, db.key_hash(key)))
            c.commit()
            c.close()

    def test_delete_snapshot_and_restore_preserves_metadata(self):
        self._insert("secret-undo-key")
        before = db.list_keys(full=True)[0]
        snapshot = db.delete_key_with_snapshot(before[0])
        self.assertEqual(db.list_keys(), [])
        self.assertTrue(db.restore_key(snapshot))
        after = db.list_keys(full=True)[0]
        self.assertEqual(after, before)

    def test_batch_delete_and_invalid_filter_are_atomic_snapshots(self):
        self._insert("valid-key")
        self._insert("invalid-key", status="invalid")
        rows = db.list_keys()
        invalid_id = next(row[0] for row in rows if row[8] == "invalid")
        snapshots = db.delete_keys_with_snapshots(invalid_only=True)
        self.assertEqual([item["id"] for item in snapshots], [invalid_id])
        self.assertEqual(len(db.list_keys()), 1)

    def test_restore_skips_reingested_hash_and_allocates_on_id_conflict(self):
        self._insert("original-key")
        snapshot = db.delete_key_with_snapshot(db.list_keys()[0][0])
        self._insert("replacement-key")
        self.assertTrue(db.restore_key(snapshot))
        restored = [row for row in db.list_keys(full=True) if row[10] == "original-key"]
        self.assertEqual(len(restored), 1)

        # Re-ingestion of the same hash is a conflict-safe no-op.
        db.delete_key_with_snapshot(restored[0][0])
        duplicate = dict(snapshot)
        self.assertTrue(db.restore_key(duplicate))
        self.assertFalse(db.restore_key(duplicate))


if __name__ == "__main__":
    unittest.main()
