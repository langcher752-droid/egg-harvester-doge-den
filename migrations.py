"""数据库迁移脚本 - 增加新功能所需的表和字段"""
import sqlite3
from config import DB_PATH

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. raw_messages 增加 read 字段
    try:
        cur.execute("ALTER TABLE raw_messages ADD COLUMN read INTEGER DEFAULT 0")
        print("✓ 已添加 raw_messages.read 字段")
    except sqlite3.OperationalError:
        print("• raw_messages.read 字段已存在")

    # 2. 创建 important_events 表（重要事件）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS important_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            related_messages TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            notified INTEGER DEFAULT 0,
            dismissed INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON important_events(created_at)")
    print("✓ 已创建 important_events 表")

    # 3. 创建 群文件决策 表（如果不存在）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            chat_name TEXT,
            file_id TEXT NOT NULL,
            file_name TEXT,
            file_size INTEGER,
            file_url TEXT,
            uploader_id TEXT,
            uploader_name TEXT,
            upload_time INTEGER,
            discovered_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            local_path TEXT,
            summary TEXT,
            context_summary TEXT,
            UNIQUE(platform, chat_id, file_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON group_files(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_chat ON group_files(platform, chat_id)")
    print("✓ 已创建 group_files 表")

    # 4. 创建 settings 表（用户配置）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    print("✓ 已创建 settings 表")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS iconmeme (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            filename TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    print("✓ 已创建 iconmeme 表")

    conn.commit()
    conn.close()
    print("\n数据库迁移完成！")

if __name__ == "__main__":
    migrate()
