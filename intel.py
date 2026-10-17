"""情报分类与简报生成模块。

核心功能：
1. 自动分类消息（破甲方法、技术讨论、机会分享、行业动态等）
2. 按群 × 时间窗口提取关键信息
3. 生成结构化简报

标签分类：
- exploit: 破甲方法、漏洞利用
- tech: 技术讨论、实现细节
- opportunity: 机会分享、福利信息
- industry: 行业动态、新闻
- key: 包含 API key
- social: 闲聊、社交
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import List, Dict

import httpx

from config import DB_PATH, GATEWAY_BASE, GATEWAY_KEY, SUMMARY_MODEL, HTTP_TIMEOUT, httpx_kwargs


def _parse_json(content: str):
    """剥离 markdown 代码块后解析 JSON。qwen 常返回 ```json ... ``` 包裹。"""
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


# ---------- 标签系统 ----------

INTEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS intel_tags(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at TEXT,
    UNIQUE(message_id, tag),
    FOREIGN KEY(message_id) REFERENCES raw_messages(id)
);
CREATE INDEX IF NOT EXISTS idx_intel_tag ON intel_tags(tag);
CREATE INDEX IF NOT EXISTS idx_intel_msg ON intel_tags(message_id);

CREATE TABLE IF NOT EXISTS intel_summaries(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    window_start INTEGER,
    window_end INTEGER,
    message_count INTEGER,
    summary TEXT,
    tags_json TEXT,
    key_count INTEGER DEFAULT 0,
    created_at TEXT,
    UNIQUE(platform, chat_id, window_start, window_end)
);
CREATE INDEX IF NOT EXISTS idx_summary_chat ON intel_summaries(platform, chat_id);
CREATE INDEX IF NOT EXISTS idx_summary_time ON intel_summaries(window_start);
"""


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init_intel():
    """初始化情报表（独立于 store.init，可单独调用）"""
    c = _conn()
    c.executescript(INTEL_SCHEMA)
    c.commit()
    c.close()


# ---------- 消息分类 ----------

def classify_messages(messages: List[Dict]) -> Dict[int, List[str]]:
    """批量分类消息，返回 {message_id: [tags]}。
    
    使用 LLM 对消息内容进行分类：
    - exploit: 破甲方法、漏洞、绕过技术
    - tech: 技术讨论、实现细节、代码
    - opportunity: 机会分享、福利、优惠
    - industry: 行业动态、新闻、政策
    - key: 明确包含 API key
    - social: 闲聊、表情包、无关内容
    """
    if not GATEWAY_KEY or not messages:
        return {}

    # 构造批量分类 prompt
    msg_lines = []
    for m in messages:
        content = m.get("content") or ""
        if not content.strip():
            continue
        msg_lines.append(f"[{m['id']}] {content[:300]}")  # 限制长度
    
    if not msg_lines:
        return {}

    prompt = """下面是一批群消息，请为每条消息打标签（可多选）：
- exploit: 破甲方法、漏洞利用、绕过技术
- tech: 技术讨论、实现细节、代码片段
- opportunity: 机会分享、福利信息、优惠
- industry: 行业动态、新闻、政策变化
- key: 包含 API key 或接口信息
- social: 闲聊、表情包、无实质内容

请严格按照 JSON 格式输出：
{"1": ["tech", "key"], "2": ["social"], ...}

消息：
""" + "\n".join(msg_lines[:30])  # 限制批次大小

    try:
        payload = {
            "model": SUMMARY_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        r = httpx.post(
            f"{GATEWAY_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            json=payload,
            timeout=90,  # 分类任务 + qwen thinking 耗时
            **httpx_kwargs()
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        tags_map = _parse_json(content) or {}
        
        # 转换 key 为 int
        result = {}
        for msg in messages:
            mid = str(msg["id"])
            if mid in tags_map:
                result[msg["id"]] = tags_map[mid]
        return result
    except Exception as e:
        print(f"[intel] 分类失败: {e}")
        return {}


def tag_messages(message_ids: List[int], force=False):
    """为消息打标签（未打标签或 force=True 时执行）"""
    c = _conn()
    
    # 过滤已打标签的消息
    if not force:
        placeholders = ",".join("?" * len(message_ids))
        tagged = {r[0] for r in c.execute(
            f"SELECT DISTINCT message_id FROM intel_tags WHERE message_id IN ({placeholders})",
            message_ids
        )}
        message_ids = [mid for mid in message_ids if mid not in tagged]
    
    if not message_ids:
        c.close()
        return 0
    
    # 读取消息内容
    placeholders = ",".join("?" * len(message_ids))
    messages = [dict(r) for r in c.execute(
        f"SELECT id, content FROM raw_messages WHERE id IN ({placeholders})",
        message_ids
    )]
    
    # 批量分类
    tags_map = classify_messages(messages)
    
    # 存储标签
    now = datetime.now().isoformat(timespec="seconds")
    count = 0
    for msg_id, tags in tags_map.items():
        for tag in tags:
            try:
                c.execute(
                    "INSERT INTO intel_tags(message_id, tag, created_at) VALUES(?,?,?) "
                    "ON CONFLICT(message_id, tag) DO NOTHING",
                    (msg_id, tag, now)
                )
                count += 1
            except Exception:
                pass
    
    c.commit()
    c.close()
    return count


# ---------- 简报生成 ----------

def generate_summary(platform: str, chat_id: str, window_hours: int = 6) -> Dict:
    """为指定群生成简报（最近 N 小时）。
    
    返回：
    {
        "platform": "qq",
        "chat_id": "1108850523",
        "window_start": timestamp,
        "window_end": timestamp,
        "message_count": 42,
        "summary": "简报文本",
        "tags": {"tech": 15, "key": 3, "social": 24},
        "key_count": 3
    }
    """
    c = _conn()
    
    # 计算时间窗口
    now_ts = int(datetime.now().timestamp())
    start_ts = now_ts - (window_hours * 3600)
    
    # 读取该时间窗口的消息
    messages = [dict(r) for r in c.execute(
        """SELECT id, sender_name, content, ts 
           FROM raw_messages 
           WHERE platform=? AND chat_id=? 
             AND ts >= ? AND ts <= ?
             AND recalled=0
              AND (msg_type IN ('text', 'mixed') OR platform='web')
           ORDER BY ts ASC""",
        (platform, chat_id, start_ts, now_ts)
    )]
    
    if not messages:
        c.close()
        return None
    
    # 先打标签（如果还没打）
    msg_ids = [m["id"] for m in messages]
    tag_messages(msg_ids)
    
    # 统计标签分布
    tags_dist = {}
    placeholders = ",".join("?" * len(msg_ids))
    for r in c.execute(
        f"SELECT tag, count(*) as cnt FROM intel_tags WHERE message_id IN ({placeholders}) GROUP BY tag",
        msg_ids
    ):
        tags_dist[r[0]] = r[1]
    
    # 统计 key 数量
    key_count = c.execute(
        """SELECT count(DISTINCT api_key_hash) FROM keys 
           WHERE source LIKE ? AND last_tested >= datetime(?, 'unixepoch')""",
        (f"%{platform}:{chat_id}%", start_ts)
    ).fetchone()[0] or 0
    
    # 构造消息文本用于总结
    msg_texts = []
    for m in messages[:100]:  # 限制数量避免过长
        sender = m.get("sender_name") or "匿名"
        content = (m.get("content") or "").strip()
        if content and len(content) < 500:  # 过滤超长消息
            msg_texts.append(f"{sender}: {content}")
    
    # 调用 LLM 生成简报
    summary_text = ""
    if GATEWAY_KEY and msg_texts:
        prompt = f"""下面是最近 {window_hours} 小时内的群消息（共 {len(messages)} 条），请生成一份简洁的中文简报：

1. 主要话题（3-5 个关键词）
2. 重要信息（资源、链接、建议等）
3. API key 情况（如果有，只说『已自动提取 N 个』）
4. 值得关注的讨论

控制在 300 字以内，用简洁的条目式表达。

消息内容：
""" + "\n".join(msg_texts[:50])

        try:
            payload = {
                "model": SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            }
            r = httpx.post(
                f"{GATEWAY_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
                json=payload,
                timeout=90,
                **httpx_kwargs()
            )
            r.raise_for_status()
            summary_text = r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            summary_text = f"（简报生成失败：{e}）"
    
    # 存储简报
    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        """INSERT INTO intel_summaries(platform, chat_id, window_start, window_end, 
                                       message_count, summary, tags_json, key_count, created_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(platform, chat_id, window_start, window_end) 
           DO UPDATE SET summary=excluded.summary, tags_json=excluded.tags_json, 
                        key_count=excluded.key_count, created_at=excluded.created_at""",
        (platform, chat_id, start_ts, now_ts, len(messages), 
         summary_text, json.dumps(tags_dist), key_count, now)
    )
    c.commit()
    c.close()
    
    return {
        "platform": platform,
        "chat_id": chat_id,
        "window_start": start_ts,
        "window_end": now_ts,
        "message_count": len(messages),
        "summary": summary_text,
        "tags": tags_dist,
        "key_count": key_count
    }


def generate_all_summaries(window_hours: int = 6) -> List[Dict]:
    """为所有活跃群生成简报（有新消息的群）"""
    c = _conn()
    now_ts = int(datetime.now().timestamp())
    start_ts = now_ts - (window_hours * 3600)
    
    # 找出有新消息的群
    groups = c.execute(
        """SELECT DISTINCT platform, chat_id, chat_name 
           FROM raw_messages 
           WHERE ts >= ? 
           GROUP BY platform, chat_id
           HAVING count(*) > 5""",  # 至少 5 条消息才生成简报
        (start_ts,)
    ).fetchall()
    
    c.close()
    
    summaries = []
    for g in groups:
        try:
            s = generate_summary(g["platform"], g["chat_id"], window_hours)
            if s:
                s["chat_name"] = g["chat_name"]
                summaries.append(s)
        except Exception as e:
            print(f"[intel] 简报生成失败 {g['platform']}:{g['chat_id']}: {e}")
    
    return summaries


def list_summaries(limit: int = 20) -> List[Dict]:
    """列出最近的简报"""
    c = _conn()
    rows = c.execute(
        """SELECT s.*, 
                  (SELECT name FROM groups WHERE platform=s.platform AND chat_id=s.chat_id) as chat_name
           FROM intel_summaries s
           ORDER BY window_end DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    c.close()
    
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "platform": r["platform"],
            "chat_id": r["chat_id"],
            "chat_name": r["chat_name"],
            "window_start": r["window_start"],
            "window_end": r["window_end"],
            "message_count": r["message_count"],
            "summary": r["summary"],
            "tags": json.loads(r["tags_json"] or "{}"),
            "key_count": r["key_count"],
            "created_at": r["created_at"]
        })
    return result


# ---------- 重要事件检测 ----------

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS important_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    related_messages TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    notified INTEGER DEFAULT 0,
    dismissed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_time ON important_events(created_at);
"""


def _get_setting(key: str, default: str = "") -> str:
    c = _conn()
    try:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    except Exception:
        return default
    finally:
        c.close()


def _set_setting(key: str, value: str):
    c = _conn()
    c.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
        (key, value),
    )
    c.commit()
    c.close()


def detect_important_events(lookback_seconds: int = None) -> List[Dict]:
    """检测最近消息中的重要事件（API key、账号、机会、紧急资源等）。

    使用 settings 表维护检测水位线 last_event_check，避免重复检测。
    返回本次新写入的事件列表。
    """
    c = _conn()
    try:
        c.executescript(EVENT_SCHEMA)
        c.commit()

        watermark = _get_setting("last_event_check", "0")
        try:
            wm = float(watermark)
        except (TypeError, ValueError):
            wm = 0.0

        now = time.time()
        if lookback_seconds is not None:
            wm = max(wm, now - lookback_seconds)

        rows = c.execute(
            """SELECT id, platform, chat_id, content, sender_name, ts
               FROM raw_messages
               WHERE ts >= ? AND recalled=0 AND content IS NOT NULL AND TRIM(content) != ''
               ORDER BY ts ASC
               LIMIT 200""",
            (wm,),
        ).fetchall()
        c.close()
    except Exception:
        try:
            c.close()
        except Exception:
            pass
        return []

    if not rows:
        return []

    candidates = [dict(r) for r in rows]
    events = _llm_scan_events(candidates)
    if not events:
        # 即便无事件也要推进水位线，防止同一批消息反复扫描
        _set_setting("last_event_check", str(int(candidates[-1]["ts"]) + 1))
        return []

    c = _conn()
    now_iso = datetime.now().isoformat(timespec="seconds")
    saved = []
    for ev in events:
        title = (ev.get("title") or "").strip()
        if not title:
            continue
        etype = ev.get("type") or "opportunity"
        msg_ids = [int(x) for x in ev.get("msg_ids", []) if str(x).isdigit()]
        try:
            cur = c.execute(
                """INSERT INTO important_events(event_type, title, summary, related_messages, created_at)
                   VALUES(?,?,?,?,?)""",
                (etype, title[:120], (ev.get("summary") or "")[:600],
                 json.dumps(msg_ids), now_iso),
            )
            saved.append({
                "id": cur.lastrowid, "event_type": etype, "title": title[:120],
                "summary": (ev.get("summary") or "")[:600], "related_messages": msg_ids,
            })
        except Exception as e:
            print(f"[intel] 事件写入失败: {e}")
    c.commit()
    c.close()

    _set_setting("last_event_check", str(int(candidates[-1]["ts"]) + 1))
    return saved


def _llm_scan_events(candidates: List[Dict]) -> List[Dict]:
    """调用 LLM 判断候选消息中是否存在重要事件。"""
    if not GATEWAY_KEY or not candidates:
        return []

    lines = []
    for m in candidates:
        content = (m.get("content") or "").replace("\n", " ")[:200]
        lines.append(f"[{m['id']}] ({m['platform']}) {content}")

    prompt = """下面是一批最新群消息。请判断其中是否存在【重要事件】。重要事件指：
- key: 出现 API key、token、密钥、接口地址等可立即使用的资源
- account: 出现账号密码、登录凭证、充值卡密
- opportunity: 重大福利、限时活动、可薅羊毛的明确机会
- urgent: 服务宕机、账号封禁、政策突变等紧急警告
- announcement: 群主/管理员的重要公告、规则变更

要求：
1. 同一主题的多条消息合并为一个事件（msg_ids 列出相关消息 id）
2. 普通闲聊、日常讨论不算重要事件，宁缺毋滥
3. title 不超过 25 字，summary 不超过 100 字，用中文
4. 严格按 JSON 输出：{"events": [{"msg_ids": [1,2], "type": "key", "title": "...", "summary": "..."}]}
5. 如果没有重要事件，输出 {"events": []}

消息：
""" + "\n".join(lines)

    try:
        payload = {
            "model": SUMMARY_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        r = httpx.post(
            f"{GATEWAY_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            json=payload,
            timeout=90,
            **httpx_kwargs(),
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = _parse_json(content) or {}
        return data.get("events") or []
    except Exception as e:
        print(f"[intel] 事件检测失败: {e}")
        return []


if __name__ == "__main__":
    # 初始化表
    init_intel()
    print("[intel] 表已初始化")
    
    # 生成所有群的简报
    summaries = generate_all_summaries(window_hours=6)
    print(f"[intel] 生成了 {len(summaries)} 份简报")
    
    for s in summaries:
        print(f"\n{'='*60}")
        print(f"群：{s.get('chat_name') or s['chat_id']} ({s['platform']})")
        print(f"消息数：{s['message_count']} | Key数：{s['key_count']}")
        print(f"标签分布：{s['tags']}")
        print(f"\n{s['summary']}")
