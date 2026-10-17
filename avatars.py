"""头像抓取与缓存：QQ 群/用户头像走 qlogo CDN，下载后压到 96px 存进 media/avatars。

微信与本机 Web 采集拿不到头像，调用方回退成首字母色块。
"""
from __future__ import annotations

import hashlib
import os
import subprocess

import httpx

import config

AVATAR_DIR = os.path.join(config.MEDIA_DIR, "avatars")
SIPS = "/usr/bin/sips"
CACHE_DAYS = 7


def _cache_path(kind: str, key: str) -> str:
    h = hashlib.md5(f"{kind}:{key}".encode()).hexdigest()[:16]
    return os.path.join(AVATAR_DIR, f"{kind}_{h}.png")


def _group_url(chat_id: str) -> str:
    return f"https://p.qlogo.cn/gh/{chat_id}/{chat_id}/100"


def _user_url(uin: str) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=100"


def _compress(path: str, size: int = 96) -> None:
    try:
        subprocess.run([SIPS, "-Z", str(size), path, "--out", path],
                       check=False, capture_output=True, timeout=10)
    except Exception:
        pass


def fetch_avatar(platform: str, kind: str, key: str, force: bool = False) -> str | None:
    """返回本地缓存文件路径；失败返回 None。kind: group|user。"""
    if platform != "qq" or not str(key).isdigit():
        return None
    os.makedirs(AVATAR_DIR, exist_ok=True)
    path = _cache_path(kind, str(key))
    if os.path.isfile(path) and not force:
        import time as _t
        if _t.time() - os.path.getmtime(path) < CACHE_DAYS * 86400:
            return path

    url = _group_url(key) if kind == "group" else _user_url(key)
    try:
        r = httpx.get(url, timeout=8, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            return None
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(r.content)
        _compress(tmp)
        os.replace(tmp, path)
        return path
    except Exception:
        return None


def stats() -> dict:
    if not os.path.isdir(AVATAR_DIR):
        return {"count": 0, "bytes": 0}
    n = b = 0
    for name in os.listdir(AVATAR_DIR):
        p = os.path.join(AVATAR_DIR, name)
        if os.path.isfile(p):
            n += 1
            b += os.path.getsize(p)
    return {"count": n, "bytes": b}
