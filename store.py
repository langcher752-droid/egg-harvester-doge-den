"""原始消息存储层：raw-first + append-only。

核心原则（撤回保命）：
  **收到即落盘，永不删除。** 撤回/删除只在原记录上打标记，内容保留。
  任何"事后补数据"都做不到这件事——撤回发生在你还没读到的时刻。

表：
  groups        群登记（QQ 自动发现 / 微信白名单），带 enabled 开关
  raw_messages  原始消息，UNIQUE(platform, chat_id, msg_key) 保证幂等
                微信轮询会反复读到同一条，幂等是必须的
"""
import json
import os
import sqlite3
from datetime import datetime

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
    platform        TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    name            TEXT,
    enabled         INTEGER DEFAULT 1,
    auto_discovered INTEGER DEFAULT 0,
    first_seen      TEXT,
    last_seen       TEXT,
    PRIMARY KEY(platform, chat_id)
);
CREATE TABLE IF NOT EXISTS raw_messages(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    chat_id      TEXT NOT NULL,
    chat_name    TEXT,
    msg_key      TEXT NOT NULL,
    sender_id    TEXT,
    sender_name  TEXT,
    ts           INTEGER,
    msg_type     TEXT,
    content      TEXT,
    media_ref    TEXT,
    media_md5    TEXT,
    media_key    TEXT,
    file_status  TEXT,
    raw_json     TEXT,
    recalled     INTEGER DEFAULT 0,
    recalled_at  TEXT,
    gone         INTEGER DEFAULT 0,
    edited       INTEGER DEFAULT 0,
    content_history TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    UNIQUE(platform, chat_id, msg_key)
);
CREATE INDEX IF NOT EXISTS idx_raw_chat_ts  ON raw_messages(platform, chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_raw_recalled ON raw_messages(recalled);
CREATE INDEX IF NOT EXISTS idx_raw_type     ON raw_messages(msg_type);
"""


def _conn():
    parent = os.path.dirname(DB_PATH) or "."
    os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _now():
    return datetime.now().isoformat(timespec="seconds")


# 后加的列，用于给已存在的库做轻量迁移（CREATE TABLE IF NOT EXISTS 不会补列）
_MIGRATIONS = [
    ("raw_messages", "media_key", "TEXT"),
    ("raw_messages", "edited", "INTEGER DEFAULT 0"),
    ("raw_messages", "content_history", "TEXT"),
    ("raw_messages", "file_status", "TEXT"),
    ("raw_messages", "read", "INTEGER DEFAULT 0"),
    ("raw_messages", "extracted", "INTEGER DEFAULT 0"),
    ("raw_messages", "nsfw", "INTEGER"),
    ("raw_messages", "nsfw_score", "REAL"),
    ("raw_messages", "nsfw_reason", "TEXT"),
    ("raw_messages", "thumb_ref", "TEXT"),
]


def init():
    c = _conn()
    c.executescript(SCHEMA)
    has_raw = c.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='raw_messages'"
    ).fetchone()[0]
    if has_raw:
        cols = {r[1] for r in c.execute("PRAGMA table_info(raw_messages)")}
        for table, col, ddl in _MIGRATIONS:
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    c.commit()
    c.close()


# ---------- 群登记 ----------

def register_group(platform, chat_id, name=None, auto_discovered=False):
    """登记/更新一个群。已存在时只刷新 last_seen 与（非空的）name。"""
    chat_id = str(chat_id)
    c = _conn()
    now = _now()
    row = c.execute(
        "SELECT platform FROM groups WHERE platform=? AND chat_id=?", (platform, chat_id)
    ).fetchone()
    if row:
        c.execute(
            "UPDATE groups SET last_seen=?, name=COALESCE(NULLIF(?,''), name) WHERE platform=? AND chat_id=?",
            (now, name or "", platform, chat_id),
        )
    else:
        c.execute(
            """INSERT INTO groups(platform, chat_id, name, enabled, auto_discovered, first_seen, last_seen)
               VALUES(?,?,?,1,?,?,?)""",
            (platform, chat_id, name or chat_id, 1 if auto_discovered else 0, now, now),
        )
    c.commit()
    c.close()


def set_group_enabled(platform, chat_id, enabled: bool):
    c = _conn()
    c.execute(
        "UPDATE groups SET enabled=? WHERE platform=? AND chat_id=?",
        (1 if enabled else 0, platform, str(chat_id)),
    )
    c.commit()
    c.close()


def list_groups(platform=None, only_enabled=False):
    c = _conn()
    sql = "SELECT platform, chat_id, name, enabled, auto_discovered, first_seen, last_seen FROM groups"
    cond, args = [], []
    if platform:
        cond.append("platform=?"); args.append(platform)
    if only_enabled:
        cond.append("enabled=1")
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY platform, last_seen DESC"
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


def is_enabled(platform, chat_id):
    c = _conn()
    row = c.execute(
        "SELECT enabled FROM groups WHERE platform=? AND chat_id=?", (platform, str(chat_id))
    ).fetchone()
    c.close()
    return bool(row and row[0])


# ---------- 原始消息落盘 ----------

def save_raw(platform, chat_id, msg_key, *, chat_name=None, sender_id=None, sender_name=None,
             ts=None, msg_type="text", content="", media_ref=None, media_md5=None,
             media_key=None, raw_json=None):
    """幂等落盘。返回 (status, changed)：
       status: 'new' | 'dup'      changed: 内容相对库里是否变化（撤回检测用）
    """
    chat_id, msg_key = str(chat_id), str(msg_key)
    c = _conn()
    now = _now()
    old = c.execute(
        "SELECT id, content, msg_type FROM raw_messages WHERE platform=? AND chat_id=? AND msg_key=?",
        (platform, chat_id, msg_key),
    ).fetchone()

    if old is None:
        c.execute(
            """INSERT INTO raw_messages(platform, chat_id, chat_name, msg_key, sender_id, sender_name,
               ts, msg_type, content, media_ref, media_md5, media_key, raw_json,
               recalled, gone, first_seen, last_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)""",
            (platform, chat_id, chat_name, msg_key, sender_id, sender_name, ts, msg_type,
             content, media_ref, media_md5, media_key, raw_json, now, now),
        )
        c.commit(); c.close()
        return "new", True

    changed = (old["content"] or "") != (content or "") or (old["msg_type"] or "") != (msg_type or "")
    hist_json, edited = None, None
    if changed and (old["content"] or "").strip():
        # 内容被改写（微信撤回会把原文替换成系统消息）→ 旧版本入历史，永不丢
        prev = c.execute("SELECT content_history FROM raw_messages WHERE id=?", (old["id"],)).fetchone()[0]
        arr = []
        if prev:
            try:
                arr = json.loads(prev)
            except Exception:
                arr = []
        arr.append({"at": now, "content": old["content"]})
        hist_json = json.dumps(arr[-20:], ensure_ascii=False)
        edited = 1

    c.execute(
        """UPDATE raw_messages SET last_seen=?, content=?, msg_type=?, media_ref=COALESCE(?, media_ref),
           media_md5=COALESCE(?, media_md5), media_key=COALESCE(?, media_key),
           chat_name=COALESCE(NULLIF(?,''), chat_name),
           gone=0, edited=COALESCE(?, edited), content_history=COALESCE(?, content_history)
           WHERE id=?""",
        (now, content, msg_type, media_ref, media_md5, media_key, chat_name or "",
         edited, hist_json, old["id"]),
    )
    c.commit(); c.close()
    return "dup", changed


def attach_media(platform, chat_id, msg_key, media_ref, media_md5=None, note=None):
    """只挂媒体引用，**不覆盖正文**（图片下载完成后回填用）。"""
    c = _conn()
    if note:
        c.execute(
            """UPDATE raw_messages SET media_ref=?, media_md5=?, content=content || ' ' || ?, last_seen=?
               WHERE platform=? AND chat_id=? AND msg_key=?""",
            (media_ref, media_md5, note, _now(), platform, str(chat_id), str(msg_key)),
        )
    else:
        c.execute(
            """UPDATE raw_messages SET media_ref=?, media_md5=?, last_seen=?
               WHERE platform=? AND chat_id=? AND msg_key=?""",
            (media_ref, media_md5, _now(), platform, str(chat_id), str(msg_key)),
        )
    n = c.total_changes
    c.commit(); c.close()
    return n


def mark_recalled(platform, chat_id, msg_keys):
    """标记撤回。内容原样保留 —— 这就是"撤回保命"。"""
    if isinstance(msg_keys, (str, int)):
        msg_keys = [msg_keys]
    keys = [str(k) for k in msg_keys if k is not None]
    if not keys:
        return 0
    chat_id = str(chat_id)
    c = _conn()
    n = 0
    for k in keys:
        cur = c.execute(
            "UPDATE raw_messages SET recalled=1, recalled_at=? WHERE platform=? AND chat_id=? AND msg_key=?",
            (_now(), platform, chat_id, k),
        )
        n += cur.rowcount
    c.commit(); c.close()
    return n


def mark_gone(platform, chat_id, present_keys, since_ts=0):
    """源库对账：库里存过、但当前扫描不存在的消息 → gone=1。
    用于微信侧（没有撤回事件推送，只能靠对账发现消失/被替换）。
    since_ts 限定只对账这个时间点之后的消息，避免把窗口外的历史误判为消失。"""
    chat_id = str(chat_id)
    present = {str(k) for k in present_keys}
    c = _conn()
    rows = c.execute(
        "SELECT id, msg_key FROM raw_messages WHERE platform=? AND chat_id=? AND gone=0 AND ts>=?",
        (platform, chat_id, since_ts),
    ).fetchall()
    n = 0
    for r in rows:
        if r["msg_key"] not in present:
            c.execute("UPDATE raw_messages SET gone=1 WHERE id=?", (r["id"],))
            n += 1
    c.commit(); c.close()
    return n


def last_ts(platform, chat_id):
    c = _conn()
    r = c.execute(
        "SELECT max(ts) FROM raw_messages WHERE platform=? AND chat_id=?", (platform, str(chat_id))
    ).fetchone()
    c.close()
    return (r[0] or 0) if r else 0


# ---------- 查询 ----------

def stats():
    c = _conn()
    out = {}
    out["groups"] = {r["platform"]: r["n"] for r in c.execute(
        "SELECT platform, count(*) n FROM groups GROUP BY platform")}
    out["messages"] = {f"{r['platform']}/{r['msg_type']}": r["n"] for r in c.execute(
        "SELECT platform, msg_type, count(*) n FROM raw_messages GROUP BY platform, msg_type ORDER BY n DESC")}
    out["recalled"] = c.execute("SELECT count(*) FROM raw_messages WHERE recalled=1").fetchone()[0]
    out["gone"] = c.execute("SELECT count(*) FROM raw_messages WHERE gone=1").fetchone()[0]
    out["total"] = c.execute("SELECT count(*) FROM raw_messages").fetchone()[0]
    c.close()
    return out


def recent(platform=None, chat_id=None, limit=50, include_recalled=True, q=None, msg_type=None,
           only_recalled=False, exclude_types=None, start_ts=None, end_ts=None):
    c = _conn()
    sql = "SELECT * FROM raw_messages"
    cond, args = [], []
    if platform:
        cond.append("platform=?"); args.append(platform)
    if chat_id:
        cond.append("chat_id=?"); args.append(str(chat_id))
    if only_recalled:
        cond.append("recalled=1")
    elif not include_recalled:
        cond.append("recalled=0")
    if msg_type:
        cond.append("msg_type=?"); args.append(msg_type)
    if exclude_types:
        cond.append("msg_type NOT IN (%s)" % ",".join("?" * len(exclude_types)))
        args.extend(exclude_types)
    if start_ts:
        cond.append("CAST(ts AS INTEGER) >= ?"); args.append(int(start_ts))
    if end_ts:
        cond.append("CAST(ts AS INTEGER) <= ?"); args.append(int(end_ts))
    if q:
        cond.append("(content LIKE ? OR sender_name LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%"])
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


# ---------- 群文件决策 ----------

def set_file_status(platform, chat_id, msg_key, status):
    """标记群文件的处理状态：pending / downloaded / ignored。"""
    c = _conn()
    c.execute(
        "UPDATE raw_messages SET file_status=?, last_seen=? WHERE platform=? AND chat_id=? AND msg_key=?",
        (status, _now(), platform, str(chat_id), str(msg_key)),
    )
    c.commit()
    c.close()


def pending_files(platform=None):
    """待决策的群文件：未下载、未忽略。"""
    c = _conn()
    sql = """SELECT * FROM raw_messages
             WHERE msg_type='file' AND media_ref IS NULL
               AND COALESCE(file_status,'pending')='pending'"""
    args = []
    if platform:
        sql += " AND platform=?"
        args.append(platform)
    sql += " ORDER BY ts DESC, id DESC"
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


def recent_downloads(limit=8):
    """最近已下载落盘的群文件（media_ref 非空），新的在前。"""
    c = _conn()
    try:
        rows = c.execute(
            """SELECT * FROM raw_messages
               WHERE msg_type='file' AND media_ref IS NOT NULL AND media_ref != ''
               ORDER BY ts DESC, id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def file_by_id(rid):
    c = _conn()
    r = c.execute("SELECT * FROM raw_messages WHERE id=?", (int(rid),)).fetchone()
    c.close()
    return dict(r) if r else None


def set_msg_nsfw(platform, chat_id, msg_key, nsfw, score=0.0, reason="", thumb_ref=None):
    """记录某条消息媒体内容的不适宜检测结果（nsfw: 1 命中 / 0 正常 / -1 无法检测 / None 未检测）。"""
    c = _conn()
    fields = ["nsfw=?", "nsfw_score=?", "nsfw_reason=?", "last_seen=?"]
    if nsfw is None:
        value = None
    elif int(nsfw) == -1:
        value = -1
    else:
        value = 1 if nsfw else 0
    args = [value, float(score or 0), reason or "", _now()]
    if thumb_ref is not None:
        fields.append("thumb_ref=?")
        args.append(thumb_ref)
    args += [platform, str(chat_id), str(msg_key)]
    c.execute(f"UPDATE raw_messages SET {', '.join(fields)} WHERE platform=? AND chat_id=? AND msg_key=?", args)
    c.commit()
    c.close()


def set_media_nsfw(media_ref, nsfw, score=0.0, reason="", thumb_ref=None):
    """按 media_ref 回写检测结果（一条媒体可能被多条消息引用，全部更新）。"""
    if not media_ref:
        return 0
    c = _conn()
    fields = ["nsfw=?", "nsfw_score=?", "nsfw_reason=?", "last_seen=?"]
    args = [1 if nsfw else 0, float(score or 0), reason or "", _now()]
    if thumb_ref is not None:
        fields.append("thumb_ref=?")
        args.append(thumb_ref)
    args.append(str(media_ref))
    cur = c.execute(f"UPDATE raw_messages SET {', '.join(fields)} WHERE media_ref=?", args)
    n = cur.rowcount
    c.commit()
    c.close()
    return n


def media_row_by_ref(media_ref):
    """按 media_ref 取一条代表行（用于媒体检测结果缓存）。"""
    c = _conn()
    r = c.execute(
        "SELECT * FROM raw_messages WHERE media_ref=? ORDER BY id DESC LIMIT 1", (str(media_ref),)
    ).fetchone()
    c.close()
    return dict(r) if r else None


def scan_targets(limit=200, force=False, before_id=None):
    """待检测的媒体消息：有 media_ref；force=True 时连已检测过的也重扫。
    before_id 用于分批倒序翻页（只取 id < before_id 的记录）。"""
    c = _conn()
    where = "media_ref IS NOT NULL AND media_ref != ''"
    args = []
    if not force:
        where += " AND nsfw IS NULL"
    if before_id:
        where += " AND id < ?"
        args.append(int(before_id))
    args.append(int(limit))
    rows = c.execute(
        f"SELECT * FROM raw_messages WHERE {where} ORDER BY id DESC LIMIT ?",
        args,
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def nsfw_stats():
    c = _conn()
    r = c.execute(
        """SELECT
             SUM(CASE WHEN nsfw=1 THEN 1 ELSE 0 END) flagged,
             SUM(CASE WHEN nsfw=0 THEN 1 ELSE 0 END) clean,
             SUM(CASE WHEN nsfw IS NULL AND media_ref IS NOT NULL AND media_ref != '' THEN 1 ELSE 0 END) unchecked
           FROM raw_messages"""
    ).fetchone()
    c.close()
    return {"flagged": r[0] or 0, "clean": r[1] or 0, "unchecked": r[2] or 0}


def append_content(platform, chat_id, msg_key, extra):
    """在原 content 后追加语义标签（视觉理解结果），不覆盖。"""
    if not extra:
        return
    c = _conn()
    c.execute(
        "UPDATE raw_messages SET content = COALESCE(content,'') || ' ' || ?, last_seen=? "
        "WHERE platform=? AND chat_id=? AND msg_key=?",
        (extra, _now(), platform, str(chat_id), str(msg_key)),
    )
    c.commit()
    c.close()


def by_media_ref(platform, media_ref_like="%"):
    """按 media_ref 找消息（视觉回填：已知图片文件 → 定位消息）。"""
    c = _conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM raw_messages WHERE platform=? AND media_ref LIKE ?",
        (platform, media_ref_like),
    ).fetchall()]
    c.close()
    return rows


def by_ids(ids):
    """按消息 id 批量取（保持传入顺序）"""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return []
    c = _conn()
    ph = ",".join("?" * len(ids))
    rows = {r["id"]: dict(r) for r in c.execute(f"SELECT * FROM raw_messages WHERE id IN ({ph})", ids).fetchall()}
    c.close()
    return [rows[i] for i in ids if i in rows]


def locate_message(platform, chat_id, msg_key):
    """按来源稳定键定位消息，只返回定位所需的非敏感字段。"""
    c = _conn()
    row = c.execute(
        """SELECT id, platform, chat_id, chat_name, msg_key, sender_name, ts,
                  msg_type, media_ref, recalled, gone
             FROM raw_messages WHERE platform=? AND chat_id=? AND msg_key=?""",
        (str(platform), str(chat_id), str(msg_key)),
    ).fetchone()
    c.close()
    return dict(row) if row else None


def messages_for_key(api_key, limit=40):
    """按已验证 key 定位原始消息，供鸡蛋库跳转来源聊天使用。

    这是本机单用户工具，允许用已存明文 key 做精确匹配；返回消息上下文，
    但不把 raw_json 单独暴露给这个定位接口，避免把整段原始 payload 带出。
    """
    if not api_key:
        return []
    c = _conn()
    rows = c.execute(
        """SELECT id, platform, chat_id, chat_name, msg_key, sender_id,
                  sender_name, ts, msg_type, content, media_ref, recalled, gone
           FROM raw_messages
           WHERE content LIKE ? OR raw_json LIKE ?
           ORDER BY CAST(ts AS INTEGER) DESC, id DESC LIMIT ?""",
        (f"%{api_key}%", f"%{api_key}%", int(limit)),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def context(msg_id, before=6, after=6):
    """取某条消息的前后文（同群按时间）"""
    c = _conn()
    t = c.execute("SELECT * FROM raw_messages WHERE id=?", (int(msg_id),)).fetchone()
    if not t:
        c.close()
        return []
    t = dict(t)
    prev = [dict(r) for r in c.execute(
        """SELECT * FROM raw_messages
           WHERE platform=? AND chat_id=? AND (ts < ? OR (ts = ? AND id < ?))
           ORDER BY ts DESC, id DESC LIMIT ?""",
        (t["platform"], t["chat_id"], t["ts"], t["ts"], t["id"], int(before))).fetchall()][::-1]
    nxt = [dict(r) for r in c.execute(
        """SELECT * FROM raw_messages
           WHERE platform=? AND chat_id=? AND (ts > ? OR (ts = ? AND id > ?))
           ORDER BY ts ASC, id ASC LIMIT ?""",
        (t["platform"], t["chat_id"], t["ts"], t["ts"], t["id"], int(after))).fetchall()]
    c.close()
    return prev + [t] + nxt


def chats(platform=None, limit=80):
    """会话列表（像 IM 左侧栏）：每个群/来源一行，带最后一条消息与未读数"""
    c = _conn()
    sql = """
        SELECT r.platform, r.chat_id,
               COALESCE(MAX(r.chat_name), r.chat_id) AS chat_name,
               COUNT(*) AS total,
               SUM(CASE WHEN r.read = 0 THEN 1 ELSE 0 END) AS unread,
               MAX(CAST(r.ts AS INTEGER)) AS last_ts,
               (SELECT content FROM raw_messages x
                 WHERE x.platform = r.platform AND x.chat_id = r.chat_id
                   AND x.msg_type != 'system'
                 ORDER BY CAST(x.ts AS INTEGER) DESC, x.id DESC LIMIT 1) AS last_text
          FROM raw_messages r
         WHERE r.msg_type != 'system'
    """
    args = []
    if platform:
        sql += " AND r.platform = ?"
        args.append(platform)
    sql += " GROUP BY r.platform, r.chat_id HAVING total > 0 ORDER BY last_ts DESC LIMIT ?"
    args.append(int(limit))
    rows = [dict(x) for x in c.execute(sql, args).fetchall()]
    c.close()
    return rows
