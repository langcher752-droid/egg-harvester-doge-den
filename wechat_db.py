"""微信腿读取层：解析解密后的明文 SQLite（实测校准 4.1.15 真实 schema）。

结构：
  <decrypted>/contact/contact.db   表 contact(username, nick_name, remark, alias, local_type...)
  <decrypted>/message/message_*.db 表 Msg_<md5(chatroom_username)>
                                   列 local_id, server_id, local_type, create_time,
                                      real_sender_id, message_content, packed_info_data,
                                      WCDB_CT_message_content
  群 = contact.username LIKE '%@chatroom'；create_time 为 unix 秒。

关键事实（逐项实测）：
  * 群文本消息的 message_content **自带 `wxid_xxx:\n正文` 前缀** → 发送人直接切，不必 join Name2Id
  * WCDB_CT_message_content=4 表示内容被 **zstd** 压缩（魔数 28 B5 2F FD），多为系统消息
  * md5(群username) 与 Msg_ 表后缀完全对应
"""
import hashlib
import html
import os
import re
import sqlite3

_SENDER_RE = re.compile(r"^([A-Za-z0-9_@\-]{4,}):\s*")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_DES_RE = re.compile(r"<des[^>]*>(.*?)</des>", re.S | re.I)
_URL_RE = re.compile(r"<url[^>]*>(.*?)</url>", re.S | re.I)
_CONTENT_RE = re.compile(r"<content[^>]*>(.*?)</content>", re.S | re.I)
_REF_RE = re.compile(r"<refermsg.*?</refermsg>", re.S | re.I)
_WS_RE = re.compile(r"\s+")
_TEMPLATE_RE = re.compile(r"<template[^>]*><!\[CDATA\[(.*?)\]\]></template>", re.S | re.I)
_MEMBER_RE = re.compile(
    r"<member>.*?<username><!\[CDATA\[(.*?)\]\]></username>"
    r".*?<nickname><!\[CDATA\[(.*?)\]\]></nickname>.*?</member>",
    re.S | re.I,
)

# local_type → 归一化类型
TYPE_MAP = {
    1: "text", 3: "image", 34: "voice", 37: "friend_req", 42: "card",
    43: "video", 47: "sticker", 48: "location", 49: "link", 50: "voip",
    10000: "system",
}


def normalize_type(ltype):
    """微信 local_type 会带高位标志：
       21474836529 = 0x500000031、244813135921 = 0x3900000031 → 基础类型都是 0x31 = 49。
    """
    if ltype in TYPE_MAP:
        return TYPE_MAP[ltype]
    low = ltype & 0xFF
    if low in TYPE_MAP:
        return TYPE_MAP[low]
    return f"t{ltype}"


def _attr(xml, name):
    m = re.search(rf'{name}="([^"]*)"', xml)
    return html.unescape(m.group(1)) if m else ""


def _inner(rx, xml, limit=400):
    m = rx.search(xml)
    if not m:
        return ""
    return _WS_RE.sub(" ", html.unescape(m.group(1))).strip()[:limit]


def parse_content(ltype, text):
    """把微信 XML 内容解析成 (可读正文, 媒体标识, 子类)。

    返回 media 值用于定位本地文件：
      sticker → emoji md5（可去 msg/attach 或表情缓存找）
      image   → aeskey（CDN 密钥，本地 .dat 靠时间戳邻近关联）
    """
    t = text or ""
    if "<" not in t:
        return _WS_RE.sub(" ", t).strip(), None, None

    if 'type="revokemsg"' in t or "<revokemsg" in t:
        who = _inner(_CONTENT_RE, t, 120) or "有人"
        return f"[撤回] {who}", None, "recall"

    # 微信群系统事件不是普通 XML 正文。旧代码把整个 sysmsg 原样塞进
    # 聊天流，既难读，也会把聊天群 ID 当成正文前缀。优先把模板和成员
    # 名称还原成一句可读事件，同时保留原始数据库内容的历史版本。
    if "<sysmsg" in t or "<content_template" in t:
        template = _inner(_TEMPLATE_RE, t, 300)
        if template:
            members = [(html.unescape(u).strip(), html.unescape(n).strip())
                       for u, n in _MEMBER_RE.findall(t)]
            labels = [n or u for u, n in members]
            inviter = labels[0] if labels else "有人"
            names = ", ".join(labels[1:]) if len(labels) > 1 else "成员"
            readable = template.replace('"$username$"', inviter).replace('"$names$"', names)
            readable = readable.replace("$username$", inviter).replace("$names$", names)
            readable = re.sub(r"\$[A-Za-z0-9_]+\$", "相关成员", readable)
            return f"[群系统] {readable}", None, "system"
        return "[群系统事件]", None, "system"

    if "<emoji" in t:
        return "[表情包]", _attr(t, "md5"), "sticker"

    if "<img" in t:
        w, h = _attr(t, "cdnthumbwidth"), _attr(t, "cdnthumbheight")
        size = f" {w}x{h}" if w and h else ""
        return f"[图片{size}]", _attr(t, "aeskey"), "image"

    if "<videomsg" in t:
        return f"[视频 {_attr(t,'playlength')}s]", None, "video"

    if "<appmsg" in t:
        title = _inner(_TITLE_RE, t)
        des = _inner(_DES_RE, t)
        url = _inner(_URL_RE, t, 300)
        has_ref = bool(_REF_RE.search(t))
        parts = []
        if has_ref:
            parts.append("[引用]")
        if title:
            parts.append(title)
        if des:
            parts.append(des)
        if url:
            parts.append(url)
        body = " | ".join(parts) if parts else "[链接/卡片]"
        return body[:600], None, "link"

    return _WS_RE.sub(" ", t).strip()[:400], None, "xml"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _img_md5_from_packed(packed_info):
    """从图片消息的 packed_info_data 提取图片 md5（ASCII hex，24-32 位）。
    实测 99.8% 命中（944/946），未命中是图片文件已被微信清理。"""
    if not packed_info:
        return ""
    b = bytes(packed_info) if isinstance(packed_info, (bytes, bytearray)) else b""
    if not b:
        return ""
    m = re.search(rb"[0-9a-f]{24,32}", b)
    return m.group(0).decode() if m else ""


def _find(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def contact_db(decrypted_dir):
    return _find([
        os.path.join(decrypted_dir, "contact", "contact.db"),
        os.path.join(decrypted_dir, "decrypted", "contact", "contact.db"),
    ])


def message_dir(decrypted_dir):
    return _find([
        os.path.join(decrypted_dir, "message"),
        os.path.join(decrypted_dir, "decrypted", "message"),
    ])


def list_conversations(decrypted_dir, groups_only=True):
    cdb = contact_db(decrypted_dir)
    out = []
    if not cdb:
        return out
    conn = sqlite3.connect(cdb)
    try:
        for username, nick, remark, alias in conn.execute(
            "SELECT username, nick_name, remark, alias FROM contact"
        ):
            if groups_only and not (username or "").endswith("@chatroom"):
                continue
            out.append({"username": username, "display": (remark or nick or username)})
    finally:
        conn.close()
    return out


def _decompress(raw, ct):
    """WCDB_CT_message_content=4 → zstd 压缩体。"""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    b = bytes(raw)
    if ct == 4 or b[:4] == b"\x28\xb5\x2f\xfd":
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(b, max_output_size=8 << 20).decode("utf-8", "replace")
        except Exception:
            return ""
    return b.decode("utf-8", "replace")


def read_group_messages(decrypted_dir, username, since_ts=0, limit=0, id_set_out=None):
    """读取某会话自 since_ts 起的消息。

    id_set_out: 传入一个 set，函数会把扫描到的所有 local_id 塞进去（撤回对账用）。
    """
    table = "Msg_" + _md5(username)
    mdir = message_dir(decrypted_dir)
    out = []
    if not mdir:
        return out
    for f in sorted(os.listdir(mdir)):
        if not (f.startswith("message_") and f.endswith(".db")):
            continue
        db_path = os.path.join(mdir, f)
        conn = sqlite3.connect(db_path)
        try:
            tabs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if table not in tabs:
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            has_ct = "WCDB_CT_message_content" in cols
            sel = ("local_id, server_id, local_type, create_time, real_sender_id, message_content, packed_info_data"
                   + (", WCDB_CT_message_content" if has_ct else ", 0"))
            sql = f"SELECT {sel} FROM {table} WHERE create_time >= ? ORDER BY create_time ASC"
            if limit:
                sql += f" LIMIT {int(limit)}"
            for row in conn.execute(sql, (since_ts,)):
                lid, sid, ltype, ctime, _rsid, content, pid, ct = row
                if id_set_out is not None:
                    id_set_out.add(str(lid))
                raw_text = _decompress(content, ct)
                sender = ""
                m = _SENDER_RE.match(raw_text or "")
                if m:
                    sender = m.group(1)
                    raw_text = raw_text[m.end():]
                body, media_key, sub = parse_content(ltype, raw_text)
                # 图片消息：从 packed_info 提取真实 md5（用于定位 .dat 文件）
                if sub == "image":
                    md5 = _img_md5_from_packed(pid)
                    if md5:
                        media_key = md5
                # 内容形态比 local_type 更可靠（高位标志位无法枚举）
                mt = normalize_type(ltype)
                if sub and sub not in ("recall", "xml"):
                    mt = sub
                out.append({
                    "local_id": lid,
                    "server_id": sid,
                    "ts": ctime,
                    "type": ltype,
                    "msg_type": mt,
                    "sub": sub,
                    "sender": sender,
                    "text": (body or "").strip(),
                    "media_key": media_key,
                })
        except Exception:
            pass
        finally:
            conn.close()
    return out


def scan_local_ids(decrypted_dir, username, since_ts=0):
    """只扫 local_id 集合（轻量，用于撤回对账）。"""
    s = set()
    read_group_messages(decrypted_dir, username, since_ts=since_ts, id_set_out=s)
    return s
