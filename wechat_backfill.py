"""微信历史图片回填：图片消息 → 解密 .dat → 视觉理解（一次性）。

用法：python wechat_backfill.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import media
import store
import vision
import wechat_db
from config import WECHAT_DECRYPTED


def main():
    store.init()
    convs = wechat_db.list_conversations(WECHAT_DECRYPTED, groups_only=True)
    total = dec = vision_done = 0
    for g in convs:
        u = g["username"]
        try:
            msgs = wechat_db.read_group_messages(WECHAT_DECRYPTED, u, since_ts=0)
        except Exception as e:
            print(f"[群 {g['display']}] 读取失败 {e}", flush=True)
            continue
        imgs = [m for m in msgs if m["msg_type"] == "image" and m.get("media_key")]
        for m in imgs:
            total += 1
            rel, md5 = media.grab_wechat_image(m["media_key"], u)
            if not rel:
                continue
            dec += 1
            store.attach_media("wechat", u, m["local_id"], rel, md5, note=f"[图已存 {md5[:8]}]")
            if not str(rel).endswith(".wxgf"):
                vision.process_media("wechat", u, m["local_id"], rel)
                vision_done += 1
            if dec % 20 == 0:
                print(f"  ...进度 {dec}/{total}（视觉 {vision_done}）", flush=True)
        print(f"[群 {g['display']}] 图片 {len(imgs)} 完成", flush=True)
    print(f"\n完成：图片 {total}，解密 {dec}，视觉识别 {vision_done}", flush=True)


if __name__ == "__main__":
    main()
