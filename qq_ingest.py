"""QQ 腿（NapCat / OneBot11）事件解析与落盘。

三类事件，全部**先落盘再谈其他**：
  message        群消息（文本 / 图片 / 表情 / 文件）
  notice.group_recall   撤回 → 打标记（内容早在库里了）
  notice.group_upload   群文件 → 记事件 + 尝试下载

NapCat 配置为 messagePostFormat="array"，msg 是段数组。
"""
import json
import os
import re

import store
import media
import napcat
from config import QQ_AUTO_FILE_BYTES

CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

_TYPE_PRIORITY = ["image", "file", "video", "mface", "face", "text"]


def _text_of(ev):
    """兼容 raw_message 与段数组两种格式。"""
    raw = ev.get("raw_message") or ""
    if raw:
        return CQ_RE.sub("", raw)
    msg = ev.get("message")
    if isinstance(msg, list):
        return "".join(
            s.get("data", {}).get("text", "")
            for s in msg if isinstance(s, dict) and s.get("type") == "text"
        )
    if isinstance(msg, str):
        return msg
    return ""


def parse_segments(msg):
    """把段数组拆成 (kind, payload)。kind ∈ text/image/sticker/file/video/at/reply/other"""
    out = []
    if isinstance(msg, str):
        return [("text", {"text": msg})]
    if not isinstance(msg, list):
        return out
    for seg in msg:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type")
        d = seg.get("data") or {}
        if t == "text":
            out.append(("text", d))
        elif t == "image":
            out.append(("image", d))
        elif t in ("mface", "face", "bface", "sface"):
            out.append(("sticker", d))
        elif t == "file":
            out.append(("file", d))
        elif t == "video":
            out.append(("video", d))
        elif t == "at":
            out.append(("at", d))
        elif t == "reply":
            out.append(("reply", d))
        else:
            out.append(("other", d))
    return out


def _kind_of(segs):
    kinds = {k for k, _ in segs}
    for k in _TYPE_PRIORITY:
        if k in kinds:
            return {"image": "image", "file": "file", "video": "video",
                    "mface": "sticker", "face": "sticker", "text": "text"}[k]
    return "other"


def _pretty(segs, faces=0):
    """人类可读的 content（媒体用占位标记，正文保留）。"""
    parts = []
    for k, d in segs:
        if k == "text":
            t = (d.get("text") or "").strip()
            if t:
                parts.append(t)
        elif k == "image":
            parts.append(f"[图片 {d.get('summary') or d.get('file') or ''}]".strip())
        elif k == "sticker":
            parts.append(f"[表情 {d.get('summary') or d.get('id') or ''}]".strip())
        elif k == "file":
            parts.append(f"[文件 {d.get('name') or d.get('file') or ''}]")
        elif k == "video":
            parts.append("[视频]")
        elif k == "at":
            parts.append(f"@{d.get('qq')}")
    return " ".join(parts).strip()


def ingest_message(ev):
    """群消息落盘。返回 dict（含待下载的图片列表）。"""
    gid = str(ev.get("group_id") or "")
    if not gid:
        return None
    mid = str(ev.get("message_id") or "")
    if not mid:
        return None

    sender = ev.get("sender") or {}
    sender_name = sender.get("card") or sender.get("nickname") or str(ev.get("user_id") or "")

    segs = parse_segments(ev.get("message"))
    if not segs:
        txt = _text_of(ev)
        segs = [("text", {"text": txt})] if txt else []
    if not segs:
        return None

    kind = _kind_of(segs)
    content = _pretty(segs)
    images = [d for k, d in segs if k == "image"]
    files = [d for k, d in segs if k == "file"]

    store.register_group("qq", gid, name=ev.get("group_name"), auto_discovered=True)
    status, changed = store.save_raw(
        "qq", gid, mid,
        chat_name=ev.get("group_name"),
        sender_id=str(ev.get("user_id") or ""),
        sender_name=sender_name,
        ts=int(ev.get("time") or 0),
        msg_type=kind,
        content=content,
        raw_json=json.dumps(ev, ensure_ascii=False)[:20000],
    )
    return {"status": status, "changed": changed, "chat_id": gid, "msg_key": mid,
            "kind": kind, "content": content, "images": images, "files": files}


def _vision_extract(rel_path, chat_id, msg_key=None):
    """对已落盘图片做视觉理解（走 vision.process_media 统一逻辑）。"""
    from vision import process_media
    process_media("qq", chat_id, msg_key, rel_path)


def download_images(chat_id, msg_key, images):
    """QQ 图片必须立即下载（CDN rkey 会过期），下载后立即视觉理解。返回落盘条数。"""
    n = 0
    for d in images:
        url = d.get("url") or ""
        if not url and str(d.get("file", "")).startswith("http"):
            url = d["file"]
        if not url:
            continue
        rel, md5 = media.grab_qq_image(url, chat_id, msg_key)
        if rel:
            store.attach_media("qq", chat_id, msg_key, rel, md5, note=f"[图已存 {md5[:8]}]")
            n += 1
            _vision_extract(rel, chat_id, msg_key)
    return n


def handle_recall(ev):
    """撤回通知 → 打标记。内容早在库里，这就是"撤回保命"。"""
    gid = str(ev.get("group_id") or "")
    mid = ev.get("message_id")
    if not gid or mid is None:
        return 0
    return store.mark_recalled("qq", gid, mid)


def _human(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"


def handle_upload(ev):
    """群文件上传 → 元数据立即落盘；小文件自动下，大文件进待决策。"""
    gid = str(ev.get("group_id") or "")
    f = ev.get("file") or {}
    if not gid or not f:
        return None
    file_id = str(f.get("id") or f.get("file_id") or "")
    name = f.get("name") or ""
    key = file_id or name
    if not key:
        return None
    size = int(f.get("size") or 0)
    busid = f.get("busid") or 0
    url = f.get("url") or ""
    group_name = ev.get("group_name") or gid

    store.register_group("qq", gid, name=group_name, auto_discovered=True)
    store.save_raw(
        "qq", gid, f"upload:{key}",
        chat_name=group_name,
        sender_id=str(ev.get("user_id") or ""),
        ts=int(ev.get("time") or 0),
        msg_type="file",
        content=f"[群文件] {name} ({_human(size)})" if size else f"[群文件] {name}",
        raw_json=json.dumps(ev, ensure_ascii=False)[:20000],
    )

    if 0 < size <= QQ_AUTO_FILE_BYTES:
        # 小文件：自动下（保命）
        dl = url
        if not dl and file_id:
            data = napcat.get_group_file_url(gid, file_id, busid)
            if isinstance(data, dict):
                dl = data.get("url") or ""
        if dl:
            raw = media.fetch_url(dl)
            if raw:
                ext = os.path.splitext(name)[1] or None
                rel, md5 = media.save_bytes(raw, "qq", gid, key, ext=ext)
                store.attach_media("qq", gid, f"upload:{key}", rel, md5, note=f"[已下载 {md5[:8]}]")
                store.set_file_status("qq", gid, f"upload:{key}", "downloaded")
                return {"status": "downloaded", "pending": False, "name": name, "size": size}

    # 大文件 / 没下成 → 进待决策
    store.set_file_status("qq", gid, f"upload:{key}", "pending")
    return {"status": "pending", "pending": True, "name": name, "size": size,
            "chat_id": gid, "group_name": group_name}
