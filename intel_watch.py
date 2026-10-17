#!/usr/bin/env python3
"""情报定时任务：每 5 分钟检测重要事件，每 6 小时生成简报。"""
import sys
import time
from datetime import datetime

import intel
import api_extensions

# launchd 重定向到文件时 stdout 默认块缓冲，强制行缓冲保证日志实时可见
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

SUMMARY_INTERVAL = 6 * 3600   # 简报每 6 小时
EVENT_INTERVAL = 5 * 60       # 重要事件检测每 5 分钟


def run_summaries():
    print(f"[intel-watch] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 生成简报...")
    try:
        summaries = intel.generate_all_summaries(window_hours=6)
        print(f"[intel-watch] 生成了 {len(summaries)} 份简报")
        for s in summaries:
            print(f"  - {s.get('chat_name') or s['chat_id']} ({s['platform']}): {s['message_count']} 条消息")
    except Exception as e:
        print(f"[intel-watch] 简报生成失败: {e}")


def run_event_detect():
    try:
        t0 = time.time()
        events = intel.detect_important_events()
        if events:
            print(f"[intel-watch] 检测到 {len(events)} 个重要事件 ({time.time()-t0:.1f}s):")
            for ev in events:
                print(f"  - [{ev['event_type']}] {ev['title']}")
        else:
            print(f"[intel-watch] 事件检测完成，无新事件 ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"[intel-watch] 事件检测失败: {e}")


def run_cache_cleanup():
    """按设置里的 cache_auto_days 自动清理媒体缓存（每 6 小时最多一次）。"""
    import os
    import config

    try:
        days = int(api_extensions.get_setting("cache_auto_days", "0") or 0)
    except Exception:
        return
    if days <= 0:
        return
    last = float(api_extensions.get_setting("cache_last_cleanup", "0") or 0)
    if time.time() - last < 6 * 3600:
        return
    root = config.MEDIA_DIR
    cutoff = time.time() - days * 86400
    deleted = freed = 0
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
                if st.st_mtime < cutoff:
                    os.remove(p)
                    deleted += 1
                    freed += st.st_size
            except OSError:
                continue
    api_extensions.set_setting("cache_last_cleanup", str(int(time.time())))
    if deleted:
        print(f"[intel-watch] 自动清理媒体缓存：删除 {deleted} 个文件，释放 {freed/1048576:.1f}MB")


def main():
    print(f"[intel-watch] 启动定时任务（事件检测每 {EVENT_INTERVAL // 60} 分钟，简报每 {SUMMARY_INTERVAL // 3600} 小时）")

    # 首次启动立即执行一次
    run_event_detect()
    run_summaries()
    last_summary = time.time()

    while True:
        time.sleep(EVENT_INTERVAL)
        run_event_detect()
        if time.time() - last_summary >= SUMMARY_INTERVAL:
            run_summaries()
            last_summary = time.time()
        run_cache_cleanup()


if __name__ == "__main__":
    main()
