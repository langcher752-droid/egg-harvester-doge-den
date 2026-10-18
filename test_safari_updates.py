"""Offline Safari polling checks: python -m unittest test_safari_updates -v."""
import ast
import asyncio
import json
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import stations


class SafariUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plist = Path(self.tmp.name) / "Bookmarks.plist"
        self.database = Path(self.tmp.name) / "eggs.db"
        for mocked in (patch.object(stations, "DB_PATH", str(self.database)),
                       patch.object(stations, "_safari_plist", return_value=str(self.plist)),
                       patch.object(stations.httpx, "get", side_effect=AssertionError("network forbidden")),
                       patch.object(stations.httpx, "post", side_effect=AssertionError("network forbidden"))):
            mocked.start()
            self.addCleanup(mocked.stop)

    def write_list(self, *urls):
        with self.plist.open("wb") as f:
            plistlib.dump({"Children": [
                {"URLString": url, "Title": " Example ", "ReadingList": {"PreviewText": " note "}}
                for url in urls
            ] + [{"URLString": "https://favorite.example", "Title": "Favorite"}]}, f)

    def state(self):
        with sqlite3.connect(self.database) as c:
            return json.loads(c.execute("SELECT value FROM settings WHERE key=?",
                                        (stations.SAFARI_UPDATES_KEY,)).fetchone()[0])

    def test_first_baseline_new_repeat_and_no_import(self):
        self.write_list("https://old.example")
        first = stations.safari_updates()
        self.assertTrue(first["ok"])
        self.assertEqual((first["status"], first["items"], first["count"]), ("baseline", [], 0))
        self.write_list("https://old.example", "https://new.example", "https://new.example")
        result = stations.safari_updates()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"], [{"url": "https://new.example", "title": "Example",
                                             "note": "note", "exists": False}])
        self.assertEqual(stations.safari_updates()["count"], 0)
        self.assertEqual(stations.list_stations(), [])
        self.assertEqual(set(self.state()), {"https://old.example", "https://new.example"})

    def test_empty_baseline_and_persistent_acknowledgment(self):
        self.write_list()
        self.assertEqual(stations.safari_updates()["status"], "baseline")
        self.write_list("https://new.example")
        self.assertEqual(stations.safari_updates()["count"], 1)
        self.assertIn("https://new.example", self.state())
        # No process-local seen set: a new connection still finds the acknowledgment.
        self.assertEqual(stations.safari_updates()["count"], 0)

    def test_already_imported_and_removed_readded(self):
        self.write_list()
        stations.safari_updates()
        stations.import_selected([{"url": "https://imported.example"}], "manual")
        self.write_list("https://imported.example", "https://new.example")
        self.assertEqual([i["url"] for i in stations.safari_updates()["items"]], ["https://new.example"])
        self.write_list()
        self.assertEqual(stations.safari_updates()["count"], 0)
        self.write_list("https://imported.example", "https://new.example")
        self.assertEqual(stations.safari_updates()["count"], 0)
        self.assertEqual(len(stations.list_stations()), 1)

    def test_unavailable_does_not_create_or_erase_baseline(self):
        missing = stations.safari_updates()
        self.assertEqual((missing["ok"], missing["status"], missing["items"], missing["count"]),
                         (False, "unavailable", [], 0))
        self.assertFalse(self.database.exists())
        self.write_list("https://old.example")
        self.assertEqual(stations.safari_updates()["status"], "baseline")
        before = self.state()
        self.write_list("https://old.example", "https://new.example")
        with patch("builtins.open", side_effect=PermissionError("denied")):
            self.assertEqual(stations.safari_updates()["status"], "unavailable")
        self.assertEqual(self.state(), before)
        self.assertEqual(stations.safari_updates()["count"], 1)

    def test_malformed_plist_and_corrupt_saved_baseline(self):
        self.plist.write_bytes(b"not a plist")
        self.assertEqual(stations.safari_updates()["status"], "unavailable")
        self.write_list("https://old.example")
        stations.safari_updates()
        with sqlite3.connect(self.database) as c:
            c.execute("UPDATE settings SET value='not json' WHERE key=?", (stations.SAFARI_UPDATES_KEY,))
        result = stations.safari_updates()
        self.assertEqual((result["status"], result["count"]), ("baseline", 0))

    def test_database_unavailable_is_clean(self):
        self.write_list("https://old.example")
        with patch.object(stations, "_conn", side_effect=sqlite3.OperationalError("locked")):
            result = stations.safari_updates()
        self.assertEqual((result["ok"], result["status"], result["count"]), (False, "unavailable", 0))

    def test_bounded_history_keeps_active_urls_and_recent_removals(self):
        with patch.object(stations, "SAFARI_SEEN_LIMIT", 3):
            self.write_list("https://a.example", "https://b.example", "https://c.example")
            stations.safari_updates()
            self.write_list("https://c.example", "https://d.example")
            self.assertEqual(stations.safari_updates()["count"], 1)
            self.assertEqual(len(self.state()), 3)
            self.write_list("https://b.example", "https://c.example", "https://d.example")
            self.assertEqual(stations.safari_updates()["count"], 0)
            before = self.state()
            self.write_list("https://b.example", "https://c.example", "https://d.example", "https://e.example")
            self.assertEqual(stations.safari_updates()["status"], "limit_exceeded")
            self.assertEqual(self.state(), before)

    def test_get_route_delegates_off_thread_without_app_startup(self):
        # Load only the real route from AST: importing app initializes unrelated
        # databases/services. Keep this test fully isolated from those side effects.
        from fastapi import FastAPI
        import httpx
        tree = ast.parse(Path(stations.__file__).with_name("app.py").read_text())
        route = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "stations_safari_updates")
        test_app = FastAPI()
        scope = {"app": test_app, "asyncio": asyncio, "stations": stations}
        exec(compile(ast.Module(body=[route], type_ignores=[]), "app.py", "exec"), scope)
        self.write_list("https://old.example")

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app),
                                         base_url="http://test") as client:
                response = await client.get("/stations/safari_updates")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "baseline")
                self.assertEqual(response.json()["count"], 0)

        asyncio.run(request())


if __name__ == "__main__":
    unittest.main()
