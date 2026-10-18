"""鸡蛋站站点库：手工收录 + 自动抓标题/logo + 分类 + 靠谱程度 + AI 总结 + 导入。

数据表 stations（eggs.db）：
  id / url(唯一) / name / type(egg|relay|forum) / reliability(1-5) / note
  / title / intro / logo / ai_summary / tags / source / last_checked / created_at / updated_at

只做目录管理：访问站点只为抓 <title> / meta / favicon，不登录、不提交表单。
"""
import hashlib
import html as _html
import json
import os
import plistlib
import re
import sqlite3
import threading
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx

from config import DB_PATH, GATEWAY_BASE, GATEWAY_KEY, SUMMARY_MODEL, HTTP_TIMEOUT, httpx_kwargs

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
ROOT = os.path.dirname(os.path.abspath(__file__))
LOGO_DIR = os.path.join(ROOT, "static", "logos")
MAX_LOGO = 300 * 1024

TYPE_LABELS = {"egg": "鸡蛋站", "relay": "API 中转", "forum": "论坛"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL UNIQUE,
  name TEXT,
  type TEXT DEFAULT 'egg',
  reliability INTEGER DEFAULT 3,
  note TEXT,
  title TEXT,
  intro TEXT,
  logo TEXT,
  ai_summary TEXT,
  tags TEXT,
  source TEXT DEFAULT 'manual',
  last_checked TEXT,
  created_at TEXT,
  updated_at TEXT
)
"""


def _conn():
    parent = os.path.dirname(DB_PATH) or "."
    os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init():
    c = _conn(); c.commit(); c.close()


# ---------- URL / 类型 ----------

def normalize_url(url):
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    if not p.netloc or "." not in p.netloc:
        return ""
    return u


def host_of(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


FORUM_HINTS = ("bbs", "forum", "discourse", "linux.do", "v2ex", "community", "社区", "论坛", "贴吧")
RELAY_HINTS = ("api", "中转", "relay", "one-api", "new-api", "oneapi", "newapi", "proxy", "接口", "网关", "gateway")


def infer_type(url="", name="", note=""):
    text_ = f"{url} {name} {note}".lower()
    if any(h in text_ for h in FORUM_HINTS):
        return "forum"
    if any(h in text_ for h in RELAY_HINTS):
        return "relay"
    return "egg"


# ---------- 抓取站点元信息 ----------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(r"<meta\s+([^>]+?)/?>", re.I | re.S)
_LINK_RE = re.compile(r"<link\s+([^>]+?)/?>", re.I | re.S)
_ATTR_RE = re.compile(r"([a-zA-Z:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.S)


def _attrs(tag):
    d = {}
    for m in _ATTR_RE.finditer(tag):
        d[m.group(1).lower()] = _html.unescape(m.group(2) if m.group(2) is not None else m.group(3) or "")
    return d


def _fetch_bytes(url, limit=None):
    r = httpx.get(url, headers=UA, timeout=HTTP_TIMEOUT, follow_redirects=True, **httpx_kwargs())
    r.raise_for_status()
    data = r.content if limit is None else r.content[:limit]
    return r, data


def _save_logo(url, data):
    os.makedirs(LOGO_DIR, exist_ok=True)
    ext = ".png"
    low = url.lower().split("?")[0]
    for e in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".gif"):
        if low.endswith(e):
            ext = e
            break
    name = hashlib.md5(url.encode()).hexdigest()[:16] + ext
    with open(os.path.join(LOGO_DIR, name), "wb") as f:
        f.write(data)
    return f"/static/logos/{name}"


def fetch_meta(url, download_logo=True):
    """抓 title / description / favicon。失败返回 {}，不抛。"""
    u = normalize_url(url)
    if not u:
        return {}
    out = {"url": u}
    try:
        r, raw = _fetch_bytes(u)
        enc = r.encoding or "utf-8"
        try:
            page = raw.decode(enc, "replace")
        except Exception:
            page = raw.decode("utf-8", "replace")
        m = _TITLE_RE.search(page)
        if m:
            out["title"] = _html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:200]
        title, desc, icon, ogimg = "", "", "", ""
        for mm in _META_RE.finditer(page):
            a = _attrs(mm.group(1))
            key = (a.get("property") or a.get("name") or "").lower()
            val = (a.get("content") or "").strip()
            if not val:
                continue
            if key in ("og:title", "twitter:title") and not title:
                title = val
            elif key in ("description", "og:description", "twitter:description") and not desc:
                desc = val
            elif key in ("og:image", "twitter:image") and not ogimg:
                ogimg = val
        for mm in _LINK_RE.finditer(page):
            a = _attrs(mm.group(1))
            rel = (a.get("rel") or "").lower()
            href = (a.get("href") or "").strip()
            if not href:
                continue
            if "apple-touch-icon" in rel and not icon:
                icon = href
            elif rel in ("icon", "shortcut icon") and not icon:
                icon = href
        if not out.get("title") and title:
            out["title"] = title
        if desc:
            out["intro"] = _html.unescape(desc).strip()[:600]
        icon = icon or ogimg
        if download_logo and icon:
            try:
                full = urljoin(u + "/", icon)
                _r2, data = _fetch_bytes(full, MAX_LOGO)
                if data and len(data) > 200:
                    out["logo"] = _save_logo(full, data)
            except Exception:
                pass
        if download_logo and not out.get("logo"):
            # 退路：/favicon.ico
            try:
                full = urljoin(u + "/", "/favicon.ico")
                _r3, data = _fetch_bytes(full, MAX_LOGO)
                if data and len(data) > 200:
                    out["logo"] = _save_logo(full, data)
            except Exception:
                pass
        out["ok"] = True
        return out
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
        return out


# ---------- CRUD ----------

def _row(r):
    return dict(r) if r is not None else None


def get_station(sid):
    c = _conn()
    r = c.execute("SELECT * FROM stations WHERE id=?", (int(sid),)).fetchone()
    c.close()
    return _row(r)


def find_by_url(url):
    u = normalize_url(url)
    if not u:
        return None
    c = _conn()
    r = c.execute("SELECT * FROM stations WHERE url=?", (u,)).fetchone()
    c.close()
    return _row(r)


def list_stations(type_="", q="", source=""):
    sql = "SELECT * FROM stations WHERE 1=1"
    args = []
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if source:
        if source == "manual":
            sql += " AND (source IS NULL OR source = '' OR source = 'manual')"
        else:
            sql += " AND source=?"
            args.append(source)
    if q:
        sql += " AND (name LIKE ? OR url LIKE ? OR note LIKE ? OR ai_summary LIKE ? OR intro LIKE ?)"
        like = f"%{q}%"
        args += [like] * 5
    sql += " ORDER BY reliability DESC, updated_at DESC, id DESC"
    c = _conn()
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


def add_station(url, name="", type_="", reliability=3, note="", source="manual", auto_meta=True, logo_override=""):
    u = normalize_url(url)
    if not u:
        return None, "URL 不合法"
    exist = find_by_url(u)
    if exist:
        return exist["id"], "已存在"
    meta = {}
    if auto_meta and (not name or True):
        meta = fetch_meta(u)
    type_val = type_ or infer_type(u, name or meta.get("title", ""), note)
    now = _now()
    c = _conn()
    cur = c.execute(
        """INSERT INTO stations(url,name,type,reliability,note,title,intro,logo,ai_summary,tags,source,last_checked,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            u,
            (name or meta.get("title") or host_of(u))[:120],
            type_val if type_val in TYPE_LABELS else "egg",
            max(1, min(5, int(reliability or 3))),
            note or "",
            (meta.get("title") or "")[:200],
            (meta.get("intro") or "")[:600],
            logo_override or meta.get("logo") or "",
            "",
            "",
            source,
            now if meta.get("ok") else "",
            now,
            now,
        ),
    )
    c.commit()
    sid = cur.lastrowid
    c.close()
    return sid, ""


def update_station(sid, **fields):
    allowed = {"name", "type", "reliability", "note", "title", "intro", "logo", "ai_summary", "tags", "last_checked"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    args.append(_now())
    args.append(int(sid))
    c = _conn()
    c.execute(f"UPDATE stations SET {', '.join(sets)} WHERE id=?", args)
    c.commit()
    c.close()
    return True


def delete_station(sid):
    c = _conn()
    c.execute("DELETE FROM stations WHERE id=?", (int(sid),))
    c.commit()
    c.close()
    return True


def refresh_meta(sid):
    st = get_station(sid)
    if not st:
        return {"ok": False, "error": "站点不存在"}
    meta = fetch_meta(st["url"])
    if not meta.get("ok"):
        return {"ok": False, "error": meta.get("error") or "抓取失败"}
    update_station(
        sid,
        title=meta.get("title") or st.get("title") or "",
        intro=meta.get("intro") or st.get("intro") or "",
        logo=meta.get("logo") or st.get("logo") or "",
        last_checked=_now(),
        name=(st.get("name") or meta.get("title") or host_of(st["url"]))[:120],
    )
    return {"ok": True, "station": get_station(sid)}


# ---------- AI 总结 ----------

def summarize_station(sid):
    st = get_station(sid)
    if not st:
        return {"ok": False, "error": "站点不存在"}
    prompt = (
        "下面是一个 AI 相关的站点信息。请用简体中文写一段 80~140 字的站点速评，"
        "包含：1) 它大概是做什么的；2) 适合谁用；3) 值得注意的风险或门槛。"
        "只输出正文，不要标题。\n\n"
        f"名称：{st.get('name') or ''}\nURL：{st['url']}\n"
        f"类型：{TYPE_LABELS.get(st.get('type'), st.get('type'))}\n"
        f"靠谱程度（人工 1-5）：{st.get('reliability')}\n"
        f"备注：{st.get('note') or '（无）'}\n"
        f"网页标题：{st.get('title') or '（无）'}\n"
        f"网页描述：{st.get('intro') or '（无）'}\n"
    )
    payload = {"model": SUMMARY_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4}
    try:
        kwargs = dict(headers={"Authorization": f"Bearer {GATEWAY_KEY}"}, json=payload, timeout=HTTP_TIMEOUT)
        kwargs.update(httpx_kwargs())
        endpoint = GATEWAY_BASE.rstrip("/") + "/chat/completions"
        if not GATEWAY_BASE.rstrip("/").endswith("/v1"):
            endpoint = GATEWAY_BASE.rstrip("/") + "/v1/chat/completions"
        r = httpx.post(endpoint, **kwargs)
        r.raise_for_status()
        text_ = (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        return {"ok": False, "error": f"AI 总结失败: {e}"}
    update_station(sid, ai_summary=text_)
    return {"ok": True, "ai_summary": text_, "station": get_station(sid)}


# ---------- 导入 ----------

def _walk_safari(node, mode, out):
    """递归遍历 Safari Bookmarks.plist。"""
    if not isinstance(node, dict):
        return
    url = node.get("URLString") or ""
    title = node.get("Title") or ""
    is_reading = "ReadingList" in node
    if url:
        if mode == "reading" and is_reading:
            preview = ((node.get("ReadingList") or {}).get("PreviewText") or "").strip()
            out.append({"url": url, "title": title, "note": preview[:300]})
        elif mode == "favorites" and not is_reading:
            out.append({"url": url, "title": title, "note": ""})
    for ch in node.get("Children") or []:
        _walk_safari(ch, mode, out)


def _safari_plist():
    return os.path.expanduser("~/Library/Safari/Bookmarks.plist")


def import_safari(mode="reading"):
    path = _safari_plist()
    if not os.path.exists(path):
        return {"ok": False, "error": "找不到 ~/Library/Safari/Bookmarks.plist", "added": 0, "skipped": 0}
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
    except PermissionError:
        return {"ok": False, "error": "没有磁盘访问权限（系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 勾选 GroupIntel.app）", "added": 0, "skipped": 0}
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {e}", "added": 0, "skipped": 0}
    items = []
    _walk_safari(data, mode, items)
    return {"ok": True, "items": items, "mode": mode}


CHROME_PATHS = [
    "~/Library/Application Support/Google/Chrome/Default/Bookmarks",
    "~/Library/Application Support/Google/Chrome/Profile 1/Bookmarks",
    "~/Library/Application Support/Microsoft Edge/Default/Bookmarks",
    "~/Library/Application Support/Arc/User Data/Default/Bookmarks",
]


def _walk_chrome(node, out):
    if not isinstance(node, dict):
        return
    if node.get("type") == "url" and node.get("url"):
        out.append({"url": node["url"], "title": node.get("name") or "", "note": ""})
    for ch in node.get("children") or []:
        _walk_chrome(ch, out)


def import_chrome():
    for p in CHROME_PATHS:
        path = os.path.expanduser(p)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                return {"ok": False, "error": f"读取失败: {e}", "items": [], "path": path}
            items = []
            for root in (data.get("roots") or {}).values():
                _walk_chrome(root, items)
            return {"ok": True, "items": items, "path": path}
    return {"ok": False, "error": "没找到 Chrome / Edge / Arc 的书签文件", "items": []}


def import_items(items, source="import", auto_meta=False, limit=500):
    """批量入库（去重 URL）。auto_meta=True 时逐条抓标题/logo（慢）。"""
    added = skipped = failed = 0
    for it in items[:limit]:
        u = normalize_url(it.get("url"))
        if not u:
            failed += 1
            continue
        if find_by_url(u):
            skipped += 1
            continue
        name = (it.get("title") or "").strip()
        note = (it.get("note") or "").strip()
        sid, err = add_station(u, name=name, type_=infer_type(u, name, note), note=note,
                               source=source, auto_meta=auto_meta)
        if sid:
            added += 1
        else:
            failed += 1
    return {"ok": True, "added": added, "skipped": skipped, "failed": failed, "total": len(items)}


_enrich_state = {"running": False, "done": 0, "total": 0, "failed": 0}


def enrich_missing(limit=40):
    """后台补全缺 logo 的站点（按 id 倒序，最多 limit 个）。"""
    if _enrich_state.get("running"):
        return {"ok": False, "error": "补全任务正在运行", "state": dict(_enrich_state)}

    def _run():
        _enrich_state.update({"running": True, "done": 0, "total": 0, "failed": 0})
        try:
            c = _conn()
            rows = c.execute(
                "SELECT id, url, title, logo FROM stations WHERE logo IS NULL OR logo = '' ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            c.close()
            _enrich_state["total"] = len(rows)
            for r in rows:
                try:
                    meta = fetch_meta(r["url"])
                    if meta.get("ok"):
                        update_station(
                            r["id"],
                            title=meta.get("title") or r["title"] or "",
                            intro=meta.get("intro") or "",
                            logo=meta.get("logo") or "",
                            last_checked=_now(),
                        )
                    else:
                        _enrich_state["failed"] += 1
                except Exception:
                    _enrich_state["failed"] += 1
                _enrich_state["done"] += 1
        except Exception:
            pass
        finally:
            _enrich_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "started": True, "limit": int(limit)}


_sum_state = {"running": False, "done": 0, "total": 0, "failed": 0}


def summarize_missing(limit=20):
    """后台给缺 AI 速评的站点批量生成（按靠谱程度 + 更新时间排序）。"""
    if _sum_state.get("running"):
        return {"ok": False, "error": "批量速评正在运行", "state": dict(_sum_state)}

    def _run():
        _sum_state.update({"running": True, "done": 0, "total": 0, "failed": 0})
        try:
            c = _conn()
            rows = c.execute(
                "SELECT id FROM stations WHERE ai_summary IS NULL OR ai_summary = '' "
                "ORDER BY reliability DESC, updated_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            c.close()
            _sum_state["total"] = len(rows)
            for r in rows:
                try:
                    res = summarize_station(r["id"])
                    if not res.get("ok"):
                        _sum_state["failed"] += 1
                except Exception:
                    _sum_state["failed"] += 1
                _sum_state["done"] += 1
        except Exception:
            pass
        finally:
            _sum_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "started": True, "limit": int(limit)}


def summarize_status():
    return {"ok": True, "state": dict(_sum_state)}


def enrich_status():
    return {"ok": True, "state": dict(_enrich_state)}


def seed_from_gicp():
    """把羊毛站目录（Supabase sites）导入站点库。"""
    try:
        import gicp
    except Exception as e:
        return {"ok": False, "error": f"gicp 模块不可用: {e}", "added": 0, "skipped": 0}
    sites = gicp.fetch_sites()
    if not sites:
        return {"ok": False, "error": "羊毛站目录拉取失败", "added": 0, "skipped": 0}
    items = []
    for s in sites:
        url = s.get("url") or ""
        name = (s.get("name") or "").strip()
        desc = (s.get("description") or "").strip()
        code = (s.get("code") or "").strip()
        note = desc + (f"\n邀请码：{code}" if code else "")
        items.append({"url": url, "title": name, "note": note[:500]})
    res = import_items(items, source="gicp", auto_meta=False)
    return res


# ---------- 书签 HTML 导入（Safari 文件 → 导出书签） ----------

_A_HREF_RE = re.compile(r"<a\s+([^>]*)>(.*?)</a>", re.I | re.S)
_H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.I | re.S)


def parse_bookmarks_html(text_):
    """解析 Netscape 书签 HTML。返回 [{url,title,note,folder}]。"""
    items = []
    folder = ""
    pos = 0
    token_re = re.compile(r"<h3[^>]*>.*?</h3>|<a\s+[^>]*>.*?</a>", re.I | re.S)
    for m in token_re.finditer(text_):
        chunk = m.group(0)
        if chunk.lower().startswith("<h3"):
            mm = _H3_RE.match(chunk)
            if mm:
                folder = _html.unescape(re.sub(r"<[^>]+>", "", mm.group(1))).strip()
            continue
        a = _A_HREF_RE.match(chunk)
        if not a:
            continue
        attrs = _attrs(a.group(1))
        url = (attrs.get("href") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        title = _html.unescape(re.sub(r"<[^>]+>", "", a.group(2))).strip()
        note = attrs.get("tags") or attrs.get("description") or ""
        items.append({"url": url, "title": title, "note": str(note)[:300], "folder": folder})
    return items


def import_html_text(text_):
    items = parse_bookmarks_html(text_)
    return import_items(items, source="bookmarks_html", auto_meta=False)


# ---------- 导入预览 / 选择导入 ----------
#
# 设计：所有导入入口先「预览」给前端一个可勾选的候选列表，用户在弹窗里挑要哪些，
# 再调 import_selected() 真正入库。这样书签导入不会一次性灌进几百条垃圾站。


def preview_items(items, source="import"):
    """给候选条目补 exists 标记，返回可勾选列表（不写库）。"""
    out = []
    seen = set()
    for it in items or []:
        u = normalize_url(it.get("url") or "")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({
            "url": u,
            "title": (it.get("title") or "").strip()[:200],
            "note": (it.get("note") or "").strip()[:300],
            "exists": bool(find_by_url(u)),
        })
    return {
        "ok": True,
        "source": source,
        "items": out,
        "total": len(out),
        "existing": sum(1 for x in out if x["exists"]),
    }


def preview_safari(mode="reading"):
    raw = import_safari(mode)
    if not raw.get("ok"):
        return raw
    return preview_items(raw.get("items") or [], "safari_" + mode)


SAFARI_UPDATES_KEY = "safari_reading_seen_urls_v1"
SAFARI_SEEN_LIMIT = 10000
SAFARI_URL_LIMIT = 8192
_safari_updates_lock = threading.Lock()


def safari_updates():
    """Read-only Safari poll; acknowledge candidates in settings, never import.

    The first successful read (including an empty list) establishes a baseline.
    Keep recently observed removed URLs too, so re-adding them is quiet until
    they age out of the bounded history. Oversized snapshots are left alone
    rather than evicting active URLs and repeatedly announcing them.
    """
    empty = {"items": [], "count": 0, "source": "safari_reading"}
    with _safari_updates_lock:
        try:
            preview = preview_safari("reading")
        except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
            return {**empty, "ok": False, "status": "unavailable",
                    "error": "Safari 阅读列表暂时不可读"}
        if not preview.get("ok"):
            return {**empty, "ok": False, "status": "unavailable",
                    "error": preview.get("error") or "Safari 阅读列表暂时不可读"}

        current = [item["url"] for item in preview["items"]]
        if len(current) > SAFARI_SEEN_LIMIT or any(len(u) > SAFARI_URL_LIMIT for u in current):
            return {**empty, "ok": False, "status": "limit_exceeded",
                    "error": "Safari 阅读列表超过检测容量"}

        c = None
        try:
            c = _conn()
            # Serialize read/ack across workers as well as threads.
            c.execute("BEGIN IMMEDIATE")
            c.execute("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )""")
            row = c.execute("SELECT value FROM settings WHERE key=?", (SAFARI_UPDATES_KEY,)).fetchone()
            try:
                seen = json.loads(row["value"]) if row else None
            except (ValueError, TypeError):
                seen = None
            # A missing/corrupt baseline must not turn the backlog into alerts.
            baseline = not (isinstance(seen, list) and len(seen) <= SAFARI_SEEN_LIMIT
                            and all(isinstance(u, str) and len(u) <= SAFARI_URL_LIMIT for u in seen))
            seen = [] if baseline else seen
            known = set(seen)
            # Check imported URLs inside the acknowledgment transaction too.
            imported = {r["url"] for r in c.execute("SELECT url FROM stations")}
            items = [] if baseline else [item for item in preview["items"]
                                        if item["url"] not in known
                                        and not item["exists"] and item["url"] not in imported]
            active = set(current)
            retained = [u for u in seen if u not in active]
            history = retained + current
            history = history[-SAFARI_SEEN_LIMIT:]
            c.execute("""INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                      (SAFARI_UPDATES_KEY, json.dumps(history), _now()))
            c.commit()
            return {"ok": True, "status": "baseline" if baseline else "ready",
                    "source": "safari_reading", "items": items, "count": len(items)}
        except (sqlite3.Error, OSError):
            if c is not None:
                c.rollback()
            return {**empty, "ok": False, "status": "unavailable",
                    "error": "Safari 检测状态暂时不可用"}
        finally:
            if c is not None:
                c.close()


def preview_chrome():
    raw = import_chrome()
    if not raw.get("ok"):
        return raw
    return preview_items(raw.get("items") or [], "chrome")


def preview_gicp():
    try:
        import gicp
    except Exception as e:
        return {"ok": False, "error": f"gicp 模块不可用: {e}", "items": [], "total": 0}
    sites = gicp.fetch_sites()
    if not sites:
        return {"ok": False, "error": "羊毛站目录拉取失败", "items": [], "total": 0}
    items = []
    for s in sites:
        desc = (s.get("description") or "").strip()
        code = (s.get("code") or "").strip()
        note = desc + (f"\n邀请码：{code}" if code else "")
        items.append({"url": s.get("url") or "", "title": (s.get("name") or "").strip(), "note": note[:500]})
    return preview_items(items, "gicp")


def preview_html_text(text_):
    return preview_items(parse_bookmarks_html(text_), "bookmarks_html")


def import_selected(items, source="import"):
    """只入库用户勾选的条目（已存在的自动跳过，不重复抓 meta）。"""
    clean = []
    for it in items or []:
        u = normalize_url(it.get("url") or "")
        if not u:
            continue
        clean.append({"url": u, "title": (it.get("title") or "").strip(),
                      "note": (it.get("note") or "").strip()[:500]})
    if not clean:
        return {"ok": False, "error": "没有选中任何站点", "added": 0, "skipped": 0, "failed": 0}
    res = import_items(clean, source=source, auto_meta=False)
    res["requested"] = len(clean)
    return res
