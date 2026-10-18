"""羊毛站（https://tkkgv8.gicp.fun）采集器。

站点本身是纯前端页面，数据放在 Supabase（匿名 key 公开可读）：
  GET {SUPABASE}/rest/v1/sites?select=*&order=pinned.desc,id.asc&limit=1000
字段：
  id / name / url / code（邀请码）/ description / tags[] / category / created_at / pinned
category: gongyi=公益 / juhe=聚合 / wending=稳定 / recommend=推荐

只做只读采集：把每个收录的站点存成一条 web 原始消息
（platform=web, chat_id=gicp, msg_key=gicp:<id>），供「实时情报流」展示、
「结构化简报」按来源筛选与总结使用。不自动注册、不领任何邀请码。
"""
import json
from datetime import datetime

import httpx

from config import HTTP_TIMEOUT, httpx_kwargs
import store

BASE = "https://tkkgv8.gicp.fun"
SUPABASE_URL = "https://anxmkemjhqwolugoerny.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFueG1rZW1qaHF3b2x1Z29lcm55Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5Mzk0NjksImV4cCI6MjEwNDUxNTQ2OX0."
    "d3rzv3vZTCDTPQyte77Z_0PIeSWid7iwsI6ODpkPiJo"
)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
MAX_SITES = 1000
CHAT_ID = "gicp"
CHAT_NAME = "羊毛站"
CATEGORY = {"gongyi": "公益", "juhe": "聚合", "wending": "稳定", "recommend": "推荐"}


def _parse_ts(s):
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def fetch_sites(limit=MAX_SITES):
    """拉取全部收录站点（置顶优先、再按 id 升序）。失败返回 []。"""
    try:
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/sites",
            params={
                "select": "*",
                "order": "pinned.desc,id.asc",
                "limit": str(limit),
            },
            headers={
                **UA,
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            },
            timeout=HTTP_TIMEOUT,
            **httpx_kwargs(),
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _site_text(s):
    """把一条站点记录排版成可读的群消息文本。"""
    name = (s.get("name") or "").strip() or "(未命名站点)"
    cat = CATEGORY.get(s.get("category") or "", s.get("category") or "")
    lines = [f"[羊毛站] {name}（{cat}）" if cat else f"[羊毛站] {name}"]
    url = (s.get("url") or "").strip()
    if url:
        lines.append(f"链接: {url}")
    code = (s.get("code") or "").strip()
    if code:
        lines.append(f"邀请码: {code}")
    desc = (s.get("description") or "").strip()
    if desc:
        lines.append(f"说明: {desc}")
    tags = s.get("tags") or []
    if tags:
        lines.append("标签: " + "、".join(str(t) for t in tags))
    return "\n".join(lines)


def ingest():
    """抓全部站点 → 幂等落库。返回 {"sites": 总数, "new": 新增}。"""
    sites = fetch_sites()
    n_new = 0
    for s in sites:
        sid = s.get("id")
        if sid is None:
            continue
        status, _changed = store.save_raw(
            "web", CHAT_ID, f"gicp:{sid}",
            chat_name=CHAT_NAME,
            sender_name=CHAT_NAME,
            ts=_parse_ts(s.get("created_at") or ""),
            msg_type="text",
            content=_site_text(s),
            raw_json=json.dumps(s, ensure_ascii=False)[:4000],
        )
        if status == "new":
            n_new += 1
    return {"sites": len(sites), "new": n_new}


if __name__ == "__main__":
    store.init()
    r = ingest()
    print(f"羊毛站采集: 站点 {r['sites']} 个, 新增 {r['new']} 条")
