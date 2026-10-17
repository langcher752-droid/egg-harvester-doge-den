"""福利站采集器：AI 福利日报（聚合 6 源）+ baipiao.org。

数据源 = ai-welfare-daily 的 data/topics.jsonl（静态数据文件，每日多次更新），
聚合 linux.sb / linux.do / baipiao.org / nodeloc / vibex / nextbuf 的福利帖。

字段：id/title/url/tags/score/source/created_at/offer{key,site,amount,brand,campaign}
注意：offer.key 是活动/站点标识（域名、邀请码），不是 API key——真正的 key 在帖子正文里，
需要进 url 抓正文才能拿到（可选）。
"""
import json

import httpx

from config import HTTP_TIMEOUT, httpx_kwargs
import store

TOPICS_URL = "https://raw.githubusercontent.com/xiaopeng66/ai-welfare-daily/main/data/topics.jsonl"


def _parse_ts(s):
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def fetch_topics():
    """抓 topics.jsonl，返回帖子 dict 列表。失败返回 []。"""
    try:
        r = httpx.get(TOPICS_URL, timeout=HTTP_TIMEOUT, **httpx_kwargs())
        r.raise_for_status()
    except Exception:
        return []
    out = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _render(r):
    """把一条帖子渲染成人类可读的 content。"""
    o = r.get("offer") or {}
    site = o.get("site") or ""
    amount = o.get("amount") or o.get("campaign") or ""
    brand = o.get("brand") or ""
    title = (r.get("title") or "").strip()
    parts = [f"[福利] {title}"]
    if site:
        parts.append(f"站点:{site}")
    if amount:
        parts.append(f"额度:{amount}")
    if brand:
        parts.append(f"品牌:{brand}")
    tags = "/".join(r.get("tags") or [])
    if tags:
        parts.append(f"tags:{tags}")
    if r.get("url"):
        parts.append(f"原帖:{r['url']}")
    # topics.jsonl 可能带摘要/正文；写入 raw_messages 让情报链路可见。
    body = r.get("content") or r.get("summary") or r.get("description") or ""
    if body:
        parts.append(f"正文:{body}")
    return " | ".join(parts)


def ingest_topics():
    """抓取 + 幂等入库。返回 {total, new}。"""
    rows = fetch_topics()
    new = 0
    for r in rows:
        tid = r.get("id")
        if tid is None:
            continue
        status, _ = store.save_raw(
            "web", "welfare", f"topic:{tid}",
            chat_name="AI福利日报",
            sender_id=str(r.get("source") or ""),
            ts=_parse_ts(r.get("created_at") or ""),
             msg_type="text",
            content=_render(r),
            raw_json=json.dumps(r, ensure_ascii=False)[:8000],
        )
        if status == "new":
            new += 1
    return {"total": len(rows), "new": new}


if __name__ == "__main__":
    store.init()
    r = ingest_topics()
    print(f"福利站采集: 抓取 {r['total']} 条, 新入库 {r['new']} 条")
