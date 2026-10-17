#!/usr/bin/env python3
"""批量从已存储消息中提取鸡蛋（API key）。

用途：历史消息回采、数据迁移、初次启动时快速建库
"""
import argparse
import time

import store
from pipeline import process_text


def backfill(platform: str = None, chat_id: str = None, limit: int = 5000, skip_extracted: bool = True):
    """批量提取已存储消息中的 key。
    
    参数：
        platform: 平台过滤（qq/wechat），None 表示全部
        chat_id: 群聊过滤，None 表示全部
        limit: 最多处理多少条消息
        skip_extracted: 是否跳过已标记为 extracted=1 的消息（默认跳过）
    """
    # 查询未提取的文本消息
    import sqlite3
    from config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    where = ["msg_type IN ('text', 'mixed')", "recalled = 0"]
    params = []
    
    if skip_extracted:
        where.append("extracted = 0")
    
    if platform:
        where.append("platform = ?")
        params.append(platform)
    
    if chat_id:
        where.append("chat_id = ?")
        params.append(chat_id)
    
    sql = f"""
        SELECT id, chat_id, content, platform
        FROM raw_messages
        WHERE {' AND '.join(where)}
        ORDER BY ts DESC
        LIMIT ?
    """
    params.append(limit)
    
    c.execute(sql, params)
    rows = c.fetchall()
    
    print(f"[backfill] 待处理 {len(rows)} 条消息（platform={platform or '全部'}, chat_id={chat_id or '全部'}）")
    
    count_processed = 0
    count_keys = 0
    
    for row in rows:
        msg_id, chat_id_val, content, plat = row
        
        if not content or not content.strip():
            continue
        
        source = f"{plat}:{chat_id_val}"
        
        # 调用 pipeline 提取
        try:
            results = process_text(content, source)
            if results:
                count_keys += len([r for r in results if r.get("status") == "valid"])
        except Exception as e:
            print(f"[backfill] 提取失败 msg_id={msg_id}: {e}")
            continue
        
        # 标记为已提取
        c2 = conn.cursor()
        c2.execute("UPDATE raw_messages SET extracted = 1 WHERE id = ?", (msg_id,))
        conn.commit()
        
        count_processed += 1
        
        if count_processed % 100 == 0:
            print(f"[backfill] 已处理 {count_processed}/{len(rows)}，提取到 {count_keys} 个有效 key")
    
    print(f"[backfill] 完成：处理 {count_processed} 条消息，提取到 {count_keys} 个有效 key")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量从已存储消息提取 API key")
    parser.add_argument("--platform", choices=["qq", "wechat"], help="平台过滤")
    parser.add_argument("--chat-id", help="群聊 ID 过滤")
    parser.add_argument("--limit", type=int, default=5000, help="最多处理消息数（默认 5000）")
    parser.add_argument("--all", action="store_true", help="包含已提取过的消息（重新提取）")
    
    args = parser.parse_args()
    
    backfill(
        platform=args.platform,
        chat_id=args.chat_id,
        limit=args.limit,
        skip_extracted=not args.all
    )
