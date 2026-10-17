"""媒体落地层：把消息里的图片/文件抓成本地实体，撤回也删不掉。

两条路径的**时效性完全不同**，这是设计的关键约束：
  QQ 图片   OneBot 给的是腾讯 CDN 直链（带 rkey，几小时~几天失效）
            → **必须收到即下载**，晚一步就永久丢失
  微信图片  文件已在本地 msg/attach/*.dat，只是加密
            → 随时可解，不急（撤回也不会删本地文件）

微信 V2 .dat 解密公式（账号级离线派生，实测 4.1.15 复现）：
  aes_key = md5(f"{code}{clean_wxid}").hexdigest()[:16].encode()
  xor_key = code & 0xFF
"""
import hashlib
import os
import struct
import subprocess

import httpx

from config import (
    MEDIA_DIR, HTTP_TIMEOUT, WECHAT_AES_KEY, WECHAT_XOR_KEY,
    WECHAT_KEYS_JSON, WECHAT_DB_DIR, WECHAT_DECRYPTED, WECHAT_ATTACH_DIR, WECHAT_PY,
)

V2_MAGIC = b"\x07\x08V2\x08\x07"
SNIFF = [(b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"),
         (b"GIF8", ".gif"), (b"RIFF", ".webp"), (b"wxgf", ".wxgf")]


def _ext_from_bytes(b: bytes, default=".bin"):
    for magic, ext in SNIFF:
        if b[:len(magic)] == magic:
            return ext
    return default


def ensure_dir(*parts, base=None):
    d = os.path.join(base or MEDIA_DIR, *[str(p) for p in parts])
    os.makedirs(d, exist_ok=True)
    return d


def save_bytes(data: bytes, platform, chat_id, msg_key, ext=None, base=None):
    """落盘媒体，返回 (相对路径, md5)。同内容只存一份（按 md5 去重）。"""
    md5 = hashlib.md5(data).hexdigest()
    ext = ext or _ext_from_bytes(data)
    d = ensure_dir(platform, chat_id, base=base)
    fname = f"{md5}{ext}"
    path = os.path.join(d, fname)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return os.path.relpath(path, base or MEDIA_DIR), md5


def save_file(tmp_path: str, md5: str, platform, chat_id, msg_key, ext=None, base=None):
    """把已下载的临时文件按 md5 去重落盘，返回 (相对路径, md5)。"""
    ext = ext or os.path.splitext(tmp_path)[1] or ".bin"
    d = ensure_dir(platform, chat_id, base=base)
    fname = f"{md5}{ext}"
    path = os.path.join(d, fname)
    if not os.path.exists(path):
        os.replace(tmp_path, path)
    else:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return os.path.relpath(path, base or MEDIA_DIR), md5


def fetch_url(url):
    """下载 URL，返回 bytes 或 None。"""
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def grab_qq_image(url, chat_id, msg_key):
    """QQ：立即下载（CDN 链接会过期）。返回 (rel_path, md5) 或 (None, None)。"""
    data = fetch_url(url)
    if not data:
        return None, None
    rel, md5 = save_bytes(data, "qq", chat_id, msg_key)
    return rel, md5


# ---------- 微信图片 ----------

def decrypt_v2(path, aes_key=None, xor_key=None):
    """解开一个 V2 .dat，返回明文 bytes；非 V2 或失败返回 None。"""
    aes_key = aes_key or WECHAT_AES_KEY
    xor_key = WECHAT_XOR_KEY if xor_key is None else xor_key
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return None
    if data[:6] != V2_MAGIC:
        return None
    aes_size = struct.unpack("<I", data[6:10])[0]
    xor_size = struct.unpack("<I", data[10:14])[0]
    if len(data) != 15 + aes_size + 16 + xor_size:
        return None
    try:
        from Crypto.Cipher import AES
        head = AES.new(aes_key, AES.MODE_ECB).decrypt(data[15:15 + aes_size])
    except Exception:
        return None
    tail = bytes(b ^ xor_key for b in data[15 + aes_size + 16: 15 + aes_size + 16 + xor_size])
    return head + tail


def wechat_chat_dirname(chat_id):
    """会话目录名 = md5(群 wxid)，与 Msg_<md5> 同算法（已逐群验证）。"""
    return hashlib.md5(str(chat_id).encode()).hexdigest()


def grab_wechat_image(image_md5, chat_id):
    """微信：从 msg/attach/<md5(会话)>/<月>/Img/ 找 <md5>.dat 并解密。
    image_md5 可能是 24 位截断（packed_info 提取），前缀匹配补全。"""
    if not image_md5 or not WECHAT_ATTACH_DIR:
        return None, None
    base = os.path.join(WECHAT_ATTACH_DIR, wechat_chat_dirname(chat_id))
    if not os.path.isdir(base):
        return None, None
    for month in sorted(os.listdir(base), reverse=True):
        imgdir = os.path.join(base, month, "Img")
        if not os.path.isdir(imgdir):
            continue
        # 精确匹配优先
        for suffix in ("_h.dat", ".dat", "_t.dat"):
            p = os.path.join(imgdir, f"{image_md5}{suffix}")
            if os.path.exists(p):
                data = decrypt_v2(p)
                if data:
                    rel, md5 = save_bytes(data, "wechat", chat_id, image_md5)
                    return rel, md5
        # 前缀匹配（image_md5 可能是 24 位截断）
        cands = [f for f in os.listdir(imgdir) if f.startswith(image_md5) and f.endswith(".dat")]
        for order in ("_h.dat", ".dat", "_t.dat"):
            for f in cands:
                if f.endswith(order):
                    data = decrypt_v2(os.path.join(imgdir, f))
                    if data:
                        full = f[:-len(order)] if order != ".dat" else f[:-4]
                        rel, md5 = save_bytes(data, "wechat", chat_id, full)
                        return rel, md5
    return None, None


def refresh_wechat_decrypted_status():
    """重新解密微信本地库（增量：DB 文件 mtime 变化才跑）。
    返回可诊断状态；不会在解密失败时推进 .last_mtime。"""
    if not (WECHAT_KEYS_JSON and WECHAT_DB_DIR and WECHAT_DECRYPTED):
        return {"ok": False, "ran": False, "status": "not_configured", "error": "微信解密路径未配置"}
    marker = os.path.join(WECHAT_DECRYPTED, ".last_mtime")
    newest = 0.0
    for root, _dirs, files in os.walk(WECHAT_DB_DIR):
        for f in files:
            if f.endswith(".db") or f.endswith(".db-wal"):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
    last = 0.0
    if os.path.exists(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                last = float(f.read().strip())
        except Exception:
            last = 0.0
    # 不只依赖全局 marker：微信把新消息写进某个 message/*.db 或 WAL 时，
    # 其他数据库的 mtime 可能更晚，旧逻辑会推进 marker 却留下过期的
    # decrypted/message/message_0.db，之后永远返回 unchanged。
    stale_outputs = []
    for root, _dirs, files in os.walk(WECHAT_DB_DIR):
        for name in files:
            if not name.endswith((".db", ".db-wal")):
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, WECHAT_DB_DIR)
            if rel.endswith(".db-wal"):
                rel = rel[:-4]
            dst = os.path.join(WECHAT_DECRYPTED, rel)
            try:
                if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                    stale_outputs.append(rel)
            except OSError:
                stale_outputs.append(rel)
    if newest <= last and not stale_outputs and os.path.isdir(WECHAT_DECRYPTED):
        return {"ok": True, "ran": False, "status": "unchanged", "source_mtime": newest}
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wechat-key-hook", "decrypt.py")
    script = os.path.normpath(script)
    if not os.path.exists(script):
        return {"ok": False, "ran": False, "status": "script_missing", "error": "解密脚本不存在"}
    try:
        proc = subprocess.run([WECHAT_PY, script, WECHAT_KEYS_JSON, WECHAT_DB_DIR, WECHAT_DECRYPTED],
                              capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return {"ok": False, "ran": True, "status": "exec_error", "error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        # stdout/stderr 可能含路径或账号信息，不写日志；仅保留可诊断的退出码与短错误类型。
        return {"ok": False, "ran": True, "status": "failed", "returncode": proc.returncode,
                "error": "解密命令失败"}
    try:
        with open(marker, "w") as f:
            f.write(str(newest))
    except Exception:
        pass
    return {"ok": True, "ran": True, "status": "updated", "source_mtime": newest,
            "stale_outputs": stale_outputs[:20]}


def refresh_wechat_decrypted():
    """兼容旧调用方：返回本次是否成功执行了解密。"""
    result = refresh_wechat_decrypted_status()
    return bool(result.get("ok") and result.get("ran"))
