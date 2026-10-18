import sqlite3
import os
import hashlib
import json
from datetime import datetime

from config import DB_PATH


def _conn():
    parent = os.path.dirname(DB_PATH) or "."
    os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS keys(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            base_url TEXT,
            api_key_hash TEXT UNIQUE,
            api_key_masked TEXT,
            api_key TEXT,
            provider TEXT,
            models TEXT,
            status TEXT,
            latency_ms INTEGER,
            first_tested TEXT,
            last_tested TEXT,
            note TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            raw TEXT,
            summary TEXT,
            source TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS iconmeme(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            filename TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS markers(
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    # 迁移：给老库补 api_key 明文列（用户拍板 A：明文落盘）
    cols = {r[1] for r in c.execute("PRAGMA table_info(keys)")}
    if "api_key" not in cols:
        c.execute("ALTER TABLE keys ADD COLUMN api_key TEXT")
    c.commit()
    return c


def get_last_marker(key: str):
    c = _conn()
    row = c.execute("SELECT value FROM markers WHERE key=?", (key,)).fetchone()
    c.close()
    return row[0] if row else None


def set_last_marker(key: str, value):
    c = _conn()
    c.execute(
        "INSERT INTO markers(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    c.commit()
    c.close()


def key_hash(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()[:16]


def mask_key(k: str) -> str:
    if len(k) <= 8:
        return "****"
    return k[:4] + "*" * (len(k) - 8) + k[-4:]


def save_message(raw: str, summary: str = "", source: str = "text"):
    c = _conn()
    c.execute(
        "INSERT INTO messages(ts, raw, summary, source) VALUES(?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), raw, summary, source),
    )
    c.commit()
    c.close()


def store_valid_key(source, base_url, api_key, provider, models, latency, note=""):
    """仅当 key 已通过连通性测试才调用。明文落盘（用户拍板 A），hash 用于去重。"""
    h = key_hash(api_key)
    m = mask_key(api_key)
    c = _conn()
    cur = c.execute("SELECT id FROM keys WHERE api_key_hash=?", (h,)).fetchone()
    now = datetime.now().isoformat(timespec="seconds")
    if cur:
        c.execute(
            "UPDATE keys SET last_tested=?, status='valid', latency_ms=?, models=?, provider=?, api_key=?, api_key_masked=? WHERE id=?",
            (now, latency, json.dumps(models), provider, api_key, m, cur[0]),
        )
    else:
        c.execute(
            """INSERT INTO keys(source, base_url, api_key_hash, api_key_masked, api_key, provider,
               models, status, latency_ms, first_tested, last_tested, note)
               VALUES(?,?,?,?,?,?,?,'valid',?,?,?,?)""",
            (source, base_url, h, m, api_key, provider, json.dumps(models), latency, now, now, note),
        )
    c.commit()
    c.close()
    return h


def list_keys(full=False):
    c = _conn()
    sel = "id, source, base_url, api_key_masked, provider, models, latency_ms, last_tested, status, note" + (", api_key" if full else "")
    rows = c.execute(f"SELECT {sel} FROM keys ORDER BY last_tested DESC").fetchall()
    c.close()
    return rows


def mark_key_test_failed(base_url, api_key, error):
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    cur = c.execute(
        "UPDATE keys SET status='invalid', last_tested=?, note=? WHERE api_key_hash=? AND base_url=?",
        (now, str(error or "测试失败")[:500], key_hash(api_key), base_url),
    )
    c.commit()
    c.close()
    return cur.rowcount


def delete_key_with_snapshot(key_id):
    snapshots = delete_keys_with_snapshots([key_id])
    return snapshots[0] if snapshots else None


def delete_keys_with_snapshots(key_ids=None, invalid_only=False):
    """Delete keys and return their private snapshots as one transaction.

    The caller must keep the returned values private.  Selecting and deleting
    under one write transaction prevents an undo token from describing a row
    which was concurrently changed or deleted.
    """
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        columns = [item[1] for item in c.execute("PRAGMA table_info(keys)")]
        if invalid_only:
            where, args = "status IS NULL OR status != 'valid'", []
        else:
            ids = [int(value) for value in (key_ids or [])]
            if not ids:
                c.rollback()
                return []
            placeholders = ",".join("?" for _ in ids)
            where, args = f"id IN ({placeholders})", ids
        rows = c.execute(f"SELECT * FROM keys WHERE {where}", args).fetchall()
        if rows:
            c.execute(f"DELETE FROM keys WHERE {where}", args)
        c.commit()
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def restore_key(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("api_key_hash"):
        return False
    columns = [
        "id", "source", "base_url", "api_key_hash", "api_key_masked", "api_key",
        "provider", "models", "status", "latency_ms", "first_tested", "last_tested", "note",
    ]
    values = [snapshot.get(column) for column in columns]
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        # A key may have been re-ingested after deletion.  The hash is the
        # logical identity; never create a duplicate.  Preserve the old id
        # where it is still available, otherwise let SQLite allocate a new id.
        existing = c.execute("SELECT 1 FROM keys WHERE api_key_hash=?", (snapshot["api_key_hash"],)).fetchone()
        if existing:
            c.rollback()
            return False
        try:
            cur = c.execute(
                "INSERT INTO keys(" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                values,
            )
        except sqlite3.IntegrityError:
            # The original numeric id is occupied by another key.
            values_without_id = values[1:]
            cur = c.execute(
                "INSERT INTO keys(" + ",".join(columns[1:]) + ") VALUES (" + ",".join("?" for _ in columns[1:]) + ")",
                values_without_id,
            )
        restored = cur.rowcount > 0
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return restored


def list_iconmemes():
    c = _conn()
    rows = c.execute(
        "SELECT id, name, filename, mime_type, size_bytes, created_at "
        "FROM iconmeme ORDER BY created_at DESC, id DESC"
    ).fetchall()
    c.close()
    return rows


def create_iconmeme(item_id, name, filename, mime_type, size_bytes):
    c = _conn()
    c.execute(
        "INSERT INTO iconmeme(id, name, filename, mime_type, size_bytes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, name, filename, mime_type, int(size_bytes), datetime.now().isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def delete_iconmeme(item_id):
    c = _conn()
    row = c.execute("SELECT filename FROM iconmeme WHERE id=?", (item_id,)).fetchone()
    if row:
        c.execute("DELETE FROM iconmeme WHERE id=?", (item_id,))
        c.commit()
    c.close()
    return row[0] if row else None
