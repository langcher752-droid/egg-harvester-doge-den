#!/usr/bin/env python3
"""群情报系统状态查看工具。"""
import sqlite3
import subprocess
from datetime import datetime, timedelta

from config import DB_PATH


def check_services():
    """检查 launchd 守护进程状态。"""
    services = [
        "website.lanche.egg-harvester",
        "website.lanche.wechat-watch",
        "website.lanche.web-watch",
        "website.lanche.intel-watch",
    ]
    
    print("=== 守护进程状态 ===")
    for svc in services:
        try:
            result = subprocess.run(
                ["launchctl", "list", svc],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                print(f"  ✓ {svc}")
            else:
                print(f"  ✗ {svc} (未运行)")
        except Exception:
            print(f"  ? {svc} (检查失败)")
    print()


def check_database():
    """检查数据库统计信息。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 消息统计
    c.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN recalled = 1 THEN 1 ELSE 0 END) as recalled,
            SUM(CASE WHEN msg_type IN ('text', 'mixed') THEN 1 ELSE 0 END) as text_msgs,
            SUM(CASE WHEN extracted = 1 THEN 1 ELSE 0 END) as extracted
        FROM raw_messages
    """)
    msg_stats = c.fetchone()
    
    # 群统计
    c.execute("SELECT COUNT(*) FROM groups WHERE enabled = 1")
    enabled_groups = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM groups")
    total_groups = c.fetchone()[0]
    
    # Key 统计
    try:
        c.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN substr(last_tested, 1, 10) >= date('now', '-1 day') THEN 1 ELSE 0 END) as tested_24h
            FROM keys
        """)
        key_stats = c.fetchone()
    except Exception:
        key_stats = (0, 0)
    
    # 简报统计
    try:
        c.execute("""
            SELECT COUNT(*) 
            FROM intel_summaries 
            WHERE window_end > strftime('%s', 'now') - 86400
        """)
        recent_summaries = c.fetchone()[0]
    except Exception:
        recent_summaries = 0
    
    # 最新消息时间
    c.execute("SELECT MAX(ts) FROM raw_messages")
    latest_ts = c.fetchone()[0]
    
    conn.close()
    
    print("=== 数据库统计 ===")
    print(f"  消息总数：{msg_stats[0]}")
    print(f"    - 已撤回：{msg_stats[1]}")
    print(f"    - 文本消息：{msg_stats[2]}")
    print(f"    - 已提取：{msg_stats[3]}")
    print(f"    - 待提取：{msg_stats[2] - msg_stats[3]}")
    print(f"  群聊：{enabled_groups}/{total_groups} 已启用")
    print(f"  API Key：{key_stats[0]} 个（24h 内测试：{key_stats[1]}）")
    print(f"  简报：{recent_summaries} 份（24h 内生成）")
    
    if latest_ts:
        latest = datetime.fromtimestamp(latest_ts)
        age = datetime.now() - latest
        if age < timedelta(minutes=5):
            freshness = f"✓ {age.seconds // 60} 分钟前"
        elif age < timedelta(hours=1):
            freshness = f"⚠ {age.seconds // 60} 分钟前"
        else:
            freshness = f"✗ {age.seconds // 3600} 小时前"
        print(f"  最新消息：{freshness}")
    print()


def check_api():
    """检查 API 服务状态。"""
    import httpx
    
    print("=== API 服务 ===")
    try:
        r = httpx.get("http://127.0.0.1:8890/stats", timeout=3)
        if r.status_code == 200:
            print(f"  ✓ FastAPI :8890 运行中")
            stats = r.json()
            print(f"    - 数据库：{stats.get('db_path', 'unknown')}")
        else:
            print(f"  ✗ FastAPI :8890 响应异常 ({r.status_code})")
    except Exception as e:
        print(f"  ✗ FastAPI :8890 未运行")
    print()


def main():
    print("\n🥚 群情报系统状态\n")
    check_services()
    check_database()
    check_api()
    
    print("Web UI:")
    print("  - 主页（手动提取）: http://127.0.0.1:8890/")
    print("  - 情报简报: http://127.0.0.1:8890/intel")
    print("  - 群文件决策: http://127.0.0.1:8890/files")
    print()


if __name__ == "__main__":
    main()
