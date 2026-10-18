"""web 数据源定时采集：福利日报（topics.jsonl）+ baipiao（Flarum）+ 买鸡鸡go（Discourse）。

三个源都是公开 web 数据，无需登录：
  welfare.ingest_topics()  聚合 6 源福利帖（300 条，每日多次更新）
  baipiao.ingest()         白嫖论坛鸡蛋帖（正文提取 key → 验证 → 入库）
  mjjgo.ingest()           买鸡鸡go 最新帖（正文提取 key → 验证 → 入库）
  gicp.ingest()            羊毛站（tkkgv8.gicp.fun）收录的全部站点

用法：
  web_watch.py --once   跑一轮
  web_watch.py          常驻循环（间隔 WEB_POLL_INTERVAL，默认 6 小时）
"""
import os
import sys
import time

import store
import welfare
import baipiao
import mjjgo
import gicp

INTERVAL = int(os.getenv("WEB_POLL_INTERVAL", str(6 * 3600)))


def once():
    store.init()
    try:
        w = welfare.ingest_topics()
    except Exception as e:
        w = {"error": str(e)}
    try:
        b = baipiao.ingest()
    except Exception as e:
        b = {"error": str(e)}
    try:
        m = mjjgo.ingest()
    except Exception as e:
        m = {"error": str(e)}
    try:
        g = gicp.ingest()
    except Exception as e:
        g = {"error": str(e)}
    print(f"[web] welfare={w} baipiao={b} mjjgo={m} gicp={g}", flush=True)


if __name__ == "__main__":
    if "--once" in sys.argv:
        once()
    else:
        while True:
            once()
            time.sleep(INTERVAL)
