"""微信腿常驻采集守护。

微信没有事件推送接口 → 只能轮询本地解密库。
**轮询间隔 = 撤回恢复窗口**（config.WECHAT_POLL_INTERVAL，默认 30s）：
撤回后微信会把原文替换成系统消息，只有"撤回前已经落盘"的内容能保住。

每轮：
  1. 增量解密（DB 文件 mtime 变化才跑，避免空转）
  2. 白名单群的增量消息 → store.raw_messages（幂等）
  3. 撤回对账（近 24h）：源库不存在的 → gone；内容被替换成撤回系统消息 → recalled
  4. 文本消息喂 pipeline 捞鸡蛋

用法：python wechat_watch.py   （launchd 常驻）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import media
import store
import vision
import wechat_db
from config import WECHAT_DECRYPTED, WECHAT_POLL_INTERVAL
from pipeline import process_text

RECALL_HINTS = ("撤回了一条消息", "撤回了一条", "recalled a message")
RECONCILE_WINDOW = 86400                      # 撤回对账只回看 24h
OVERLAP = 300                                 # 增量扫描重叠量（靠幂等去重）
FIRST_WINDOW_DAYS = int(os.getenv("WECHAT_FIRST_WINDOW_DAYS", "7"))


def _is_recall_system(t):
    return any(h in (t or "") for h in RECALL_HINTS)


def poll_once(verbose=False):
    out = {"groups": 0, "scanned": 0, "new": 0, "edited": 0, "recalled": 0, "gone": 0, "eggs": 0}
    decrypt = media.refresh_wechat_decrypted_status()
    out["decrypt"] = {k: v for k, v in decrypt.items() if k != "source_mtime"}
    if not decrypt.get("ok"):
        # 解密失败时不能继续拿旧快照做对账，否则会把正常消息误标成 gone。
        return out
    convs = wechat_db.list_conversations(WECHAT_DECRYPTED, groups_only=True)
    now = int(time.time())

    for g in convs:
        u = g["username"]
        store.register_group("wechat", u, name=g["display"])
        if not store.is_enabled("wechat", u):
            continue
        out["groups"] += 1

        last = store.last_ts("wechat", u)
        since = max(0, last - OVERLAP) if last else (now - FIRST_WINDOW_DAYS * 86400)
        msgs = wechat_db.read_group_messages(WECHAT_DECRYPTED, u, since_ts=since)
        out["scanned"] += len(msgs)

        texts = []
        for m in msgs:
            if not m["text"] and m["msg_type"] not in ("image", "sticker", "file", "video", "voice"):
                continue
            status, changed = store.save_raw(
                "wechat", u, m["local_id"],
                chat_name=g["display"],
                sender_id=m["sender"], sender_name=m["sender"],
                ts=m["ts"], msg_type=m["msg_type"], content=m["text"],
                media_key=m.get("media_key"),
            )
            if status == "new":
                out["new"] += 1
            elif changed:
                # 只在**已存在且内容变了**时才算改写（微信撤回会把原文换成系统消息）
                out["edited"] += 1
                if m.get("sub") == "recall" or _is_recall_system(m["text"]):
                    store.mark_recalled("wechat", u, m["local_id"])
                    out["recalled"] += 1
            # 图片消息：解密 .dat 落盘 + 视觉理解
            if m["msg_type"] == "image" and m.get("media_key"):
                try:
                    rel, md5 = media.grab_wechat_image(m["media_key"], u)
                    if rel:
                        store.attach_media("wechat", u, m["local_id"], rel, md5, note=f"[图已存 {md5[:8]}]")
                        vision.process_media("wechat", u, m["local_id"], rel)
                except Exception:
                    pass
            if m["msg_type"] == "text" and m["text"]:
                texts.append(m["text"])
            if verbose and status == "new":
                # verbose 输出也不打印可能包含 API key 的正文。
                print(f"   + [{g['display'][:14]}] {m['msg_type']:<8} message={m['local_id']}", flush=True)

        # 撤回对账：源库里已经没有的，标记 gone
        ids = wechat_db.scan_local_ids(WECHAT_DECRYPTED, u, since_ts=now - RECONCILE_WINDOW)
        out["gone"] += store.mark_gone("wechat", u, ids, since_ts=now - RECONCILE_WINDOW)

        if texts:
            try:
                out["eggs"] += len(process_text("\n".join(texts), source=f"wechat:{g['display']}"))
            except Exception:
                pass

    return out


def main():
    store.init()
    print(f"[wechat-watch] 启动 interval={WECHAT_POLL_INTERVAL}s decrypted={WECHAT_DECRYPTED}", flush=True)
    if not os.path.isdir(WECHAT_DECRYPTED):
        print("[wechat-watch] ⚠ 解密目录不存在，先跑 wechat-key-hook/decrypt.py", flush=True)
    while True:
        try:
            t0 = time.time()
            r = poll_once()
            print(f"[wechat-watch] {time.strftime('%H:%M:%S')} {r} ({time.time()-t0:.1f}s)", flush=True)
        except Exception as e:
            print(f"[wechat-watch] ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(WECHAT_POLL_INTERVAL)


if __name__ == "__main__":
    if "--once" in sys.argv:
        store.init()
        print(poll_once(verbose=True))
    else:
        main()
