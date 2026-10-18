"""买鸡鸡go 论坛采集器（Discourse JSON API）。

数据源：https://www.mjjgo.com
  ｜列表  /latest.json?page=N          每页 30 帖（topic_list.topics）
  ｜单帖  /t/<id>.json                 post_stream.posts[].cooked（HTML 正文）

和 baipiao 一样：正文里常有 API key + 接口地址的明文发布，
提取 → probe 连通性验证 → 只把验证通过的 key 落库（db.store_valid_key）。

注意：本模块只做「公开页面」采集，不登录、不点赞、不回帖；
请求带 UA + 轻量限速（每帖间隔 0.6s），遵守社区礼貌抓取。
"""
import html as h
import re
import time

import httpx

from config import HTTP_TIMEOUT, httpx_kwargs
import store
from probe import probe
from db import store_valid_key
from baipiao import KEY_RES

BASE = "https://www.mjjgo.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
MAX_PAGES = 1          # 首页最新 30 帖（6h 一轮足够覆盖）
TITLE_PREFIX = "[买鸡鸡go帖]"
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_IMG_HINT = ("img.", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
             "uploads/default", "emoji/", "mjjgo.com/uploads")


def _parse_ts(s):
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _get(url):
    try:
        r = httpx.get(url, timeout=HTTP_TIMEOUT, headers=UA, **httpx_kwargs())
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_latest(pages=MAX_PAGES):
    """抓最新帖子列表。返回 topic dict 列表（含 id/title/created_at/posts_count）。"""
    out, seen = [], set()
    for p in range(pages):
        d = _get(f"{BASE}/latest.json?page={p}")
        if not d:
            break
        topics = (d.get("topic_list") or {}).get("topics") or []
        if not topics:
            break
        for t in topics:
            tid = t.get("id")
            if tid in seen:
                continue
            seen.add(tid)
            out.append(t)
    return out


def _topic_text(tid):
    """抓单帖所有楼层正文，返回纯文本。"""
    d = _get(f"{BASE}/t/{tid}.json")
    if not d:
        return ""
    parts = []
    for post in (d.get("post_stream") or {}).get("posts") or []:
        cooked = post.get("cooked") or ""
        if not cooked:
            continue
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", cooked, flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", "\n", t)
        t = h.unescape(t)
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _extract_pairs(text):
    """从正文提取 (base_url, api_key) 候选。宽松收集，probe 过滤。"""
    keys = []
    for r in KEY_RES:
        keys += r.findall(text)
    keys = list(dict.fromkeys(keys))
    if not keys:
        return []
    urls = [u for u in URL_RE.findall(text)
            if not any(x in u.lower() for x in _IMG_HINT)
            and "mjjgo.com" not in u.lower()]
    base = urls[0].rstrip("/.,;") if urls else None
    return [{"base_url": base, "api_key": k} for k in keys]


def ingest():
    """抓列表 → 逐帖正文 → 提取 key 验证入库。返回统计。"""
    topics = fetch_latest()
    n_post = n_key = 0
    for t in topics:
        tid = t.get("id")
        title = t.get("title") or t.get("fancy_title") or ""
        if not tid:
            continue
        text = _topic_text(tid)
        if not text:
            continue
        n_post += 1
        store.save_raw("web", "mjjgo", f"mjj:{tid}", chat_name="买鸡鸡go",
                       sender_name="买鸡鸡go",
                       ts=_parse_ts(t.get("created_at") or ""),
                       msg_type="text", content=f"{TITLE_PREFIX} {title}\n原帖:{BASE}/t/{tid}\n正文:\n{text}",
                       raw_json=text[:4000])
        for c in _extract_pairs(text):
            try:
                res = probe(c.get("base_url"), c.get("api_key"))
            except Exception:
                continue
            if res.get("ok"):
                store_valid_key("web:mjjgo", c.get("base_url"), c["api_key"],
                                res["provider"], res["models"], res["latency_ms"])
                n_key += 1
        time.sleep(0.6)   # 礼貌限速
    return {"topics": len(topics), "posts_parsed": n_post, "new_keys": n_key}


if __name__ == "__main__":
    store.init()
    r = ingest()
    print(f"mjjgo 采集: 帖子 {r['topics']} 条, 解析正文 {r['posts_parsed']} 帖, "
          f"新验证 key {r['new_keys']} 个")
