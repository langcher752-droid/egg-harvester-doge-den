import asyncio
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.datastructures import UploadFile

import app
import db


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


class IconMemeTest(unittest.TestCase):
    def test_upload_list_delete_uses_configured_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "eggs.db")
            icon_dir = os.path.join(tmp, "iconmeme")
            upload = UploadFile(io.BytesIO(PNG), filename="my-doge.png", headers={"content-type": "image/png"})
            with patch.object(db, "DB_PATH", db_path), patch.object(app, "ICONMEME_DIR", icon_dir):
                result = asyncio.run(app.iconmeme_upload(upload, "My Doge"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["item"]["name"], "My Doge")
                self.assertTrue(os.path.exists(os.path.join(icon_dir, result["item"]["url"].rsplit("/", 1)[-1])))
                self.assertEqual(len(app._iconmeme_items()), 1)
                deleted = asyncio.run(app.iconmeme_delete(result["item"]["id"]))
                self.assertTrue(deleted["ok"])
                self.assertEqual(app._iconmeme_items(), [])
                self.assertEqual(os.listdir(icon_dir), [])

    def test_rejects_non_image_and_oversized_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", os.path.join(tmp, "eggs.db")), patch.object(app, "ICONMEME_DIR", os.path.join(tmp, "iconmeme")):
                with self.assertRaises(Exception):
                    asyncio.run(app.iconmeme_upload(UploadFile(io.BytesIO(b"not an image"), filename="x.txt"), "x"))
                with self.assertRaises(Exception):
                    asyncio.run(app.iconmeme_upload(UploadFile(io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * (app.ICONMEME_MAX_BYTES + 1)), filename="x.png"), "x"))


if __name__ == "__main__":
    unittest.main()
