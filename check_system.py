#!/usr/bin/env python3
"""系统完整性检查 - 验证所有功能"""
import subprocess
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def check_process(name, port=None):
    """检查进程是否运行"""
    if port:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        return result.returncode == 0
    else:
        result = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True)
        return bool(result.stdout.strip())

def check_daemon(plist_name):
    """检查launchd守护进程"""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(["launchctl", "list", plist_name], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0

def check_file(path):
    """检查文件是否存在"""
    return os.path.exists(os.path.expanduser(path))

def check_db_table(table_name):
    """检查数据库表"""
    import sqlite3
    db_path = os.getenv("DB_PATH", str(ROOT / "eggs.db"))
    if not os.path.exists(db_path):
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is not None
    except sqlite3.Error:
        return False

def main():
    print("=" * 70)
    print("群情报系统 - 完整性检查")
    print("=" * 70)
    
    checks = {
        "核心服务": [
            ("FastAPI 后端", lambda: check_process("app.py", 8890)),
            ("守护进程: egg-harvester", lambda: check_daemon("website.lanche.egg-harvester")),
            ("守护进程: wechat-watch", lambda: check_daemon("website.lanche.wechat-watch")),
            ("守护进程: web-watch", lambda: check_daemon("website.lanche.web-watch")),
            ("守护进程: intel-watch", lambda: check_daemon("website.lanche.intel-watch")),
        ],
        "核心模块": [
            ("配置文件 .env", lambda: check_file(".env")),
            ("数据库 eggs.db", lambda: check_file("eggs.db")),
            ("情报分析模块", lambda: check_file("intel.py")),
            ("API扩展模块", lambda: check_file("api_extensions.py")),
            ("买鸡鸡go采集", lambda: check_file("mjjgo.py")),
            ("通知App源码", lambda: check_file(ROOT / "app/src/Notifications.swift")),
        ],
        "数据库表": [
            ("raw_messages", lambda: check_db_table("raw_messages")),
            ("messages", lambda: check_db_table("messages")),
            ("keys", lambda: check_db_table("keys")),
            ("groups", lambda: check_db_table("groups")),
            ("intel_tags", lambda: check_db_table("intel_tags")),
            ("intel_summaries", lambda: check_db_table("intel_summaries")),
            ("important_events", lambda: check_db_table("important_events")),
            ("settings", lambda: check_db_table("settings")),
            ("file_summaries", lambda: check_db_table("file_summaries")),
        ],
        "前端功能": [
            ("Markdown渲染库", lambda: "marked" in _page()),
            ("代码高亮库", lambda: "highlight.js" in _page()),
            ("已读标记按钮", lambda: "全部已读" in _page()),
            ("气泡布局样式", lambda: "msg-bubble" in _page()),
            ("下载进度条(流式)", lambda: "downloadFile" in _page() and "dl-progress" in _page()),
            ("重要事件检测按钮", lambda: "detectEvents" in _page()),
            ("智能标题展示", lambda: "f.title" in _page()),
            ("下载目录配置", lambda: "setting-download-dir" in _page()),
        ],
    }
    
    def _page():
        return subprocess.run(["curl", "-s", "http://127.0.0.1:8890/"], capture_output=True, text=True).stdout
    
    total = 0
    passed = 0
    
    for category, items in checks.items():
        print(f"\n【{category}】")
        for name, check_func in items:
            total += 1
            try:
                result = check_func()
                status = "✅" if result else "❌"
                if result:
                    passed += 1
            except Exception as e:
                status = "💥"
                result = False
            
            print(f"  {status} {name}")
    
    print("\n" + "=" * 70)
    print(f"检查结果: {passed}/{total} 通过")
    print("=" * 70)
    
    if passed == total:
        print("✅ 所有检查通过，系统运行正常")
        return 0
    else:
        print(f"⚠️  {total - passed} 项检查失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())
