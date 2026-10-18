"""baipiao.org 论坛采集器（Flarum JSON API）。

数据源：https://baipiao.org/bbs/api/discussions
  列表分页  ?page[offset]=N（每页 20，links.next 有下一页）
  单帖正文  /bbs/api/discussions/<id> → included[posts].attributes.contentHtml

正文是「鸡蛋」的直接来源：API key + 接口 url 明文发布。
权限字段：bpLevelLocked / bpMinLevel（锁定的帖子读不到正文，跳过）。
"""
import html as h
import json
import re

import httpx

from config import HTTP_TIMEOUT, httpx_kwargs
import store
from probe import probe
from db import store_valid_key

API = "https://baipiao.org/bbs/api/discussions"
PAGE_SIZE = 20
MAX_PAGES = 3          # 抓最近 3 页（60 帖）
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

KEY_RES = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"wbk_[A-Za-z0-9_-]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9_-]{16,}"),
]
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_IMG_HINT = ("img.", ".png", ".jpg", ".jpeg", ".gif", ".webp", "baipiao.org/bbs")


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


def fetch_discussions(pages=MAX_PAGES):
    """抓讨论列表（最近 pages 页）。返回 [{id, title, createdAt, commentCount}]。"""
    out = []
    for p in range(pages):
        d = _get(f"{API}?page%5Boffset%5D={p * PAGE_SIZE}")
        if not d:
            break
        data = d.get("data") or []
        if not data:
            break
        out.extend(data)
        if "next" not in (d.get("links") or {}):
            break
    return out


def _posts_text(d):
    """提取讨论所有 posts 的正文纯文本。"""
    parts = []
    for inc in d.get("included") or []:
        if inc.get("type") == "posts":
            c = inc.get("attributes", {}).get("contentHtml", "")
            t = re.sub(r"<[^>]+>", "\n", c)
            t = h.unescape(t)
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
    urls = [u for u in URL_RE.findall(text) if not any(x in u.lower() for x in _IMG_HINT)]
    base = urls[0].rstrip("/") if urls else None
    return [{"base_url": base, "api_key": k} for k in keys]


def ingest():
    """抓列表 → 逐帖正文 → 提取 key 验证入库。返回统计。"""
    discs = fetch_discussions()
    n_post = n_key = 0
    for disc in discs:
        did = disc.get("id")
        attrs = disc.get("attributes") or {}
        title = attrs.get("title") or ""
        if attrs.get("bpLevelLocked"):
            # 锁定帖读不到正文，仍记标题
            store.save_raw("web", "baipiao", f"bp:{did}", chat_name="白嫖社区",
                           sender_name="白嫖社区",
                           ts=_parse_ts(attrs.get("createdAt") or ""),
                           msg_type="text", content=f"[白嫖帖·锁定] {title}\n原帖:{API}/{did}")
            continue
        d = _get(f"{API}/{did}")
        if not d:
            continue
        text = _posts_text(d)
        if not text:
            continue
        n_post += 1
        store.save_raw("web", "baipiao", f"bp:{did}", chat_name="白嫖社区",
                           sender_name="白嫖社区",
                       ts=_parse_ts(attrs.get("createdAt") or ""),
                       msg_type="text", content=f"[白嫖帖] {title}\n原帖:{API}/{did}\n正文:\n{text}", raw_json=text[:4000])
        for c in _extract_pairs(text):
            try:
                res = probe(c.get("base_url"), c.get("api_key"))
            except Exception:
                continue
            if res.get("ok"):
                store_valid_key("web:baipiao", c.get("base_url"), c["api_key"],
                                res["provider"], res["models"], res["latency_ms"])
                n_key += 1
    return {"discussions": len(discs), "posts_parsed": n_post, "new_keys": n_key}


if __name__ == "__main__":
    store.init()
    r = ingest()
    print(f"baipiao 采集: 讨论 {r['discussions']} 条, 解析正文 {r['posts_parsed']} 帖, 新验证 key {r['new_keys']} 个")
