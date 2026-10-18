"""群情报系统 —— QQ(NapCat) + 微信(本地库) 双源采集入口。

数据流（raw-first）：
  Ingest ──► store.raw_messages（收到即落盘，永不删）
                │           └── 撤回只在原记录打标记 → "撤回保命"
                ▼
           media（QQ 图片立即下载 / 微信 .dat 解密）
                ▼
           pipeline.process_text（捞鸡蛋 → 连通性测试 → 入库）
"""
import asyncio
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import secrets
import threading
import time
import uuid
from collections import OrderedDict

# NSFW 视觉模型依赖装在项目内 vendor/（不改动 App 自带 Python 的 site-packages）
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from fastapi import FastAPI, UploadFile, File, Form, Body, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from extract import extract_from_image
from pipeline import process_text
from db import list_keys, list_iconmemes, create_iconmeme, delete_iconmeme
from summarize import summarize
import store
import qq_ingest
import media
import napcat
import intel
import stations
import api_extensions
import wxgf_decoder

app = FastAPI(title="群情报系统（QQ + 微信）")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

media_dir = os.path.join(os.path.dirname(__file__), "media")
if os.path.exists(media_dir):
    app.mount("/media", StaticFiles(directory=media_dir), name="media")

# wxgf 按需解码缓存目录（图片浏览时惰性转换）
WXGF_CACHE_DIR = os.path.join(media_dir, ".wxgf_cache")


@app.get("/media-view/{path:path}")
async def media_view(path: str):
    """wxgf 图片按需解码展示：GET /media-view/<media相对路径>.

    前端 img src 对 .wxgf 文件走此端点（浏览器无法直接渲染 wxgf）。
    解码结果缓存到 media/.wxgf_cache/，按源文件 mtime 失效。
    """
    if not path.lower().endswith(".wxgf"):
        raise HTTPException(404, "仅支持 wxgf 解码")

    media_root = os.path.realpath(media_dir)
    src = os.path.realpath(os.path.join(media_root, path))
    if not src.startswith(media_root + os.sep) or not os.path.exists(src):
        raise HTTPException(404, "文件不存在")

    cache_key = hashlib.md5(path.encode()).hexdigest()
    cached = os.path.join(WXGF_CACHE_DIR, cache_key + ".png")
    if not (os.path.exists(cached) and os.path.getmtime(cached) >= os.path.getmtime(src)):
        result = await asyncio.to_thread(wxgf_decoder.decode_wxgf, src)
        if not result:
            raise HTTPException(404, "wxgf 解码失败")
        data, _ext = result
        os.makedirs(WXGF_CACHE_DIR, exist_ok=True)
        tmp_path = cached + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, cached)

    return FileResponse(
        cached,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )

# ---------- 媒体预览图 + 不适宜内容（NSFW）检测 ----------

THUMB_DIR = os.path.join(static_dir, "thumbs")
_FILES_INSPECT_MAX_BYTES = 16 * 1024 * 1024


def _thumb_path(key: str) -> str:
    os.makedirs(THUMB_DIR, exist_ok=True)
    return os.path.join(THUMB_DIR, hashlib.md5(str(key).encode()).hexdigest() + ".jpg")


def _thumb_url(key: str) -> str:
    return "/media-thumb?ref=" + key.replace("%", "%25").replace(" ", "%20")


@app.get("/media-thumb")
async def media_thumb(ref: str, w: int = 520):
    """媒体缩略图（图片缩放 / 视频抽首帧），带磁盘缓存。

    ref 有两种：媒体相对路径（media/...）或群文件合成键（file:<id>，由 /files/inspect 预生成）。
    """
    import nsfw as nsfw_mod
    if ref.startswith("file:"):
        dst = _thumb_path(f"{ref}|{int(w)}") if w != 520 else _thumb_path(ref)
        for cand in (dst, _thumb_path(ref)):
            if os.path.exists(cand):
                return FileResponse(cand, media_type="image/jpeg",
                                    headers={"Cache-Control": "public, max-age=604800"})
        raise HTTPException(404, "预览图还没生成")
    path = _resolve_media_path(ref)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "媒体不存在")
    key = f"{ref}|{int(w)}"
    dst = _thumb_path(key)
    src_mtime = os.path.getmtime(path)
    if not (os.path.exists(dst) and os.path.getmtime(dst) >= src_mtime):
        data = await asyncio.to_thread(nsfw_mod.make_thumb, path, int(w))
        if not data:
            raise HTTPException(415, "无法生成预览图（格式不支持或解码失败）")
        tmp = dst + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
    return FileResponse(dst, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/media-nsfw")
async def media_nsfw(ref: str, refresh: int = 0):
    """检测某个媒体是否疑似不适宜；结果缓存在 raw_messages.nsfw 上。"""
    import nsfw as nsfw_mod
    row = store.media_row_by_ref(ref)
    if row and row.get("nsfw") is not None and not refresh:
        return {
            "ok": True, "cached": True, "nsfw": bool(row.get("nsfw")),
            "score": row.get("nsfw_score") or 0, "reason": row.get("nsfw_reason") or "",
        }
    path = _resolve_media_path(ref)
    if not path:
        return {"ok": False, "error": "媒体不存在", "nsfw": False, "score": 0, "reason": ""}
    res = await asyncio.to_thread(nsfw_mod.check_path, path, nsfw_mod.DEFAULT_THRESHOLD,
                                 (row or {}).get("content") or "")
    store.set_media_nsfw(ref, res.get("nsfw"), res.get("score"), res.get("reason"))
    return {"ok": True, "cached": False, **res}


_scan_state = {"running": False, "done": 0, "total": 0, "flagged": 0, "failed": 0, "last_error": ""}


def _scan_worker(limit: int, force: bool = False, before_id: int = 0):
    import nsfw as nsfw_mod
    rows = store.scan_targets(limit, force=force, before_id=before_id or None)
    _scan_state.update(running=True, done=0, total=len(rows), flagged=0, failed=0, last_error="")
    for r in rows:
        try:
            ref = r.get("media_ref")
            path = _resolve_media_path(ref)
            res = nsfw_mod.check_path(path, context=r.get("content") or "") if path else \
                {"nsfw": False, "score": 0, "reason": "文件不存在"}
            store.set_media_nsfw(ref, res.get("nsfw"), res.get("score"), res.get("reason"))
            if res.get("nsfw"):
                _scan_state["flagged"] += 1
        except Exception as e:  # 单个失败不影响整批
            _scan_state["failed"] += 1
            _scan_state["last_error"] = str(e)[:200]
        finally:
            _scan_state["done"] += 1
    _scan_state["running"] = False


@app.post("/media-scan")
async def media_scan(limit: int = Form(200), force: int = Form(0), before_id: int = Form(0)):
    """批量扫描历史媒体（图片/视频），把结果写回 raw_messages.nsfw。"""
    if _scan_state.get("running"):
        return {"ok": False, "error": "已有扫描在进行中", **_scan_state}
    import threading
    t = threading.Thread(target=_scan_worker, args=(int(limit), bool(force), int(before_id)), daemon=True)
    t.start()
    return {"ok": True, "started": True, "limit": int(limit)}


@app.get("/media-scan/status")
async def media_nsfw_status():
    return {"ok": True, **_scan_state, "stats": store.nsfw_stats()}


@app.post("/files/inspect/{file_id}")
async def file_inspect(file_id: int):
    """群文件「预检」：先做文件名/标题关键词判断，图片/视频再抓远端内容做检测。
    命中即标记 nsfw=1（后续下载会被拦截）。"""
    import nsfw as nsfw_mod
    row = store.file_by_id(file_id)
    if not row:
        return {"ok": False, "error": "not found"}
    meta = _parse_file_meta(row.get("raw_json"), row.get("content") or "")
    name = meta.get("name") or meta.get("file") or row.get("content") or ""
    title = api_extensions.get_file_title(file_id) or ""
    kind = nsfw_mod.kind_of(name)

    hit, word = nsfw_mod.keyword_hit(f"{name} {title} {row.get('content') or ''}")
    if hit:
        store.set_msg_nsfw(row["platform"], row["chat_id"], row["msg_key"], True, 0.99, f"名称/描述命中敏感词「{word}」")
        return {"ok": True, "nsfw": True, "score": 0.99, "reason": f"名称/描述命中敏感词「{word}」",
                "kind": kind, "thumb": "", "name": name}

    thumb = ""
    if kind in ("image", "video"):
        size = int(meta.get("size") or 0)
        if size and size > _FILES_INSPECT_MAX_BYTES:
            reason = f"文件超过 {_FILES_INSPECT_MAX_BYTES // (1024 * 1024)}MB，未做内容检测"
            store.set_msg_nsfw(row["platform"], row["chat_id"], row["msg_key"], -1, 0.0, reason)
            return {"ok": True, "nsfw": -1, "score": 0, "reason": reason,
                    "kind": kind, "thumb": "", "name": name}
        url = meta.get("url") or ""
        if not url:
            file_id_str = str(meta.get("id") or meta.get("file_id") or "")
            busid = meta.get("busid") or 0
            if file_id_str:
                data = napcat.get_group_file_url(row["chat_id"], file_id_str, busid)
                if isinstance(data, dict):
                    url = data.get("url") or ""
        if url:
            raw = await asyncio.to_thread(media.fetch_url, url)
            if raw:
                if kind == "image":
                    res = await asyncio.to_thread(nsfw_mod.check_image_bytes, raw, nsfw_mod.DEFAULT_THRESHOLD, name)
                    key = f"file:{file_id}"
                    dst = _thumb_path(key)
                    try:
                        from PIL import Image, ImageOps
                        import io as _io
                        img = Image.open(_io.BytesIO(raw))
                        img = ImageOps.exif_transpose(img)
                        if img.mode in ("RGBA", "LA", "P"):
                            bg = Image.new("RGB", img.size, (255, 255, 255))
                            img = img.convert("RGBA")
                            bg.paste(img, mask=img.split()[-1])
                            img = bg
                        img = img.convert("RGB")
                        img.thumbnail((520, 520))
                        img.save(dst, format="JPEG", quality=78, optimize=True)
                        thumb = _thumb_url(key)
                    except Exception:
                        thumb = ""
                else:
                    # 视频：写临时文件后用 ffmpeg 抽首帧检测（不落媒体库、不外出）
                    import tempfile
                    tmp = None
                    try:
                        ext = os.path.splitext(name)[1] or ".mp4"
                        fd, tmp = tempfile.mkstemp(suffix=ext)
                        with os.fdopen(fd, "wb") as fh:
                            fh.write(raw)
                        res = await asyncio.to_thread(nsfw_mod.check_video_path, tmp)
                        tbytes = await asyncio.to_thread(nsfw_mod.make_thumb, tmp)
                        if tbytes:
                            key = f"file:{file_id}"
                            dst = _thumb_path(key)
                            with open(dst, "wb") as fh:
                                fh.write(tbytes)
                            thumb = _thumb_url(key)
                    finally:
                        if tmp and os.path.exists(tmp):
                            try:
                                os.unlink(tmp)
                            except OSError:
                                pass
                store.set_msg_nsfw(row["platform"], row["chat_id"], row["msg_key"],
                                   bool(res.get("nsfw")), res.get("score") or 0,
                                   res.get("reason") or "", thumb_ref=thumb or None)
                return {"ok": True, "nsfw": bool(res.get("nsfw")), "score": res.get("score") or 0,
                        "reason": res.get("reason") or "", "thumb": thumb, "kind": kind, "name": name,
                        "tier": res.get("tier"), "checked": res.get("checked")}

        reason = "无法获取文件地址（链接可能已过期），未做内容检测"
        store.set_msg_nsfw(row["platform"], row["chat_id"], row["msg_key"], -1, 0.0, reason)
        return {"ok": True, "nsfw": -1, "score": 0, "reason": reason,
                "kind": kind, "thumb": "", "name": name}

    # 其他类型（压缩包/文档）不做内容检测，只显示了类型图标
    store.set_msg_nsfw(row["platform"], row["chat_id"], row["msg_key"], False, 0.0, "")
    return {"ok": True, "nsfw": False, "score": 0, "reason": "", "kind": kind, "thumb": "", "name": name}


CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

store.init()
intel.init_intel()


def _warm_nsfw_model():
    """启动后异步预热 NSFW 模型，避免第一个检测请求等 10s+。"""
    try:
        import threading
        def _run():
            try:
                import nsfw as _n
                if _n.model_available():
                    import nsfw_model as _m
                    _m._get_session()
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


_warm_nsfw_model()
import sqlite3 as _sq
from config import DB_PATH as _DB
_conn0 = _sq.connect(_DB, timeout=15)
try:
    _conn0.execute("ALTER TABLE file_summaries ADD COLUMN title TEXT")
except _sq.OperationalError:
    pass
_conn0.commit()
_conn0.close()


# Delete undo data is deliberately process-local: tokens are opaque and the
# secret-bearing snapshots never cross the HTTP boundary.  Bound both age and
# number of batches so an unattended UI cannot retain unbounded credentials.
UNDO_TTL_SECONDS = 10 * 60
UNDO_MAX_BATCHES = 20
_undo_lock = threading.Lock()
_undo_batches = OrderedDict()


def _store_undo_batch(snapshots):
    if not snapshots:
        return None
    now = time.monotonic()
    token = secrets.token_urlsafe(32)
    with _undo_lock:
        for old_token, (_created, _snapshots) in list(_undo_batches.items()):
            if now - _created >= UNDO_TTL_SECONDS:
                _undo_batches.pop(old_token, None)
        _undo_batches[token] = (now, snapshots)
        while len(_undo_batches) > UNDO_MAX_BATCHES:
            _undo_batches.popitem(last=False)
    return token


def _take_undo_batch(token):
    if not isinstance(token, str) or not token.strip():
        return None
    now = time.monotonic()
    with _undo_lock:
        entry = _undo_batches.pop(token.strip(), None)
        if not entry:
            return None
        created, snapshots = entry
        if now - created >= UNDO_TTL_SECONDS:
            return None
        return snapshots


def _event_text(ev: dict) -> str:
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


# ---------- 后台任务（不阻塞 NapCat 推送） ----------

def _bg_images(items):
    for chat_id, msg_key, imgs in items:
        try:
            qq_ingest.download_images(chat_id, msg_key, imgs)
        except Exception:
            pass


def _bg_extract(texts):
    for text, src in texts:
        try:
            process_text(text, source=src)
        except Exception:
            pass


def _human(n):
    n = int(n or 0)
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"


def _ts_str(ts):
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _parse_file_meta(raw_json, content=""):
    """从 raw_json 提取文件元数据，兼容三种结构：
    1) {"file": {...}}（群文件上传事件）
    2) OneBot 消息段 {"message": [{"type":"file","data":{...}}]}（CQ:file 消息）
    3) raw_message 里的 [CQ:file,...] 码（URL 编码，需 unquote）
    返回 dict: id/file_id/size/name/url/busid（缺省为空）。"""
    meta = {}
    try:
        raw = json.loads(raw_json or "{}")
    except Exception:
        raw = {}
    if isinstance(raw.get("file"), dict):
        meta = dict(raw["file"])
    else:
        for seg in raw.get("message") or []:
            if isinstance(seg, dict) and seg.get("type") == "file" and isinstance(seg.get("data"), dict):
                meta = dict(seg["data"])
                break
    if not meta.get("url"):
        rm = raw.get("raw_message") or ""
        m = re.search(r"\[CQ:file,([^\]]+)\]", rm)
        if m:
            for kv in m.group(1).split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    meta.setdefault(k.strip(), v.strip())
            if meta.get("url"):
                from urllib.parse import unquote
                meta["url"] = unquote(meta["url"])
    if not meta.get("name") and not meta.get("file"):
        meta["name"] = (content or "").replace("[群文件] ", "").replace("[文件 ", "").rstrip("]")
    return meta


def notify_group_file(up):
    """弹 macOS 通知：有新群文件待下载。"""
    name = (up.get("name") or "文件").replace('"', "'")
    size = _human(up.get("size"))
    group = (up.get("group_name") or up.get("chat_id") or "").replace('"', "'")
    body = f"{group} 上传了 {name}（{size}），待下载"
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body}" with title "群文件" sound name "Glass"'],
            timeout=5,
        )
    except Exception:
        pass


# ---------- 路由 ----------

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/intel", response_class=HTMLResponse)
async def intel_page():
    with open(os.path.join(os.path.dirname(__file__), "templates", "intel.html"), encoding="utf-8") as f:
        return f.read()


DOGE_STATIC_DIR = os.path.join(static_dir, "doge")
ICONMEME_DIR = os.path.join(static_dir, "iconmeme")
ICONMEME_MAX_BYTES = 5 * 1024 * 1024
ICONMEME_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


def _iconmeme_items():
    return [
        {"id": row[0], "name": row[1], "url": f"/static/iconmeme/{row[2]}",
         "mime_type": row[3], "size_bytes": row[4], "created_at": row[5]}
        for row in list_iconmemes()
    ]


def _doge_listing(sub, exts):
    d = os.path.join(DOGE_STATIC_DIR, sub)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if n.lower().endswith(exts))


@app.get("/doge/manifest")
async def doge_manifest():
    """贴纸/立绘清单，给主界面吉祥物（随机换脸、判断当前贴纸）用。"""
    return {
        "stickers": [f"/static/doge/stickers/{n}" for n in _doge_listing("stickers", (".webp", ".png"))],
        "heroes": [f"/static/doge/img/{n}" for n in _doge_listing("img", (".webp", ".png"))],
        "iconmeme": _iconmeme_items(),
    }


@app.get("/iconmeme")
async def iconmeme_list():
    return {"items": _iconmeme_items()}


@app.post("/iconmeme")
async def iconmeme_upload(file: UploadFile = File(...), name: str = Form("")):
    data = await file.read(ICONMEME_MAX_BYTES + 1)
    if not data:
        raise HTTPException(400, "图标文件为空")
    if len(data) > ICONMEME_MAX_BYTES:
        raise HTTPException(413, "图标文件不能超过 5MB")
    match = next((item for item in ICONMEME_SIGNATURES if data.startswith(item[0])), None)
    if not match:
        # WebP 需要同时校验 RIFF/WEBP，避免仅凭扩展名接收任意文件。
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime_type, extension = "image/webp", ".webp"
        else:
            raise HTTPException(415, "只支持 PNG、JPEG、GIF 或 WebP 图片")
    else:
        _, mime_type, extension = match
    display_name = re.sub(r"\s+", " ", (name or "").strip())[:80]
    if not display_name:
        display_name = os.path.splitext(os.path.basename(file.filename or "我的 iconmeme"))[0][:80] or "我的 iconmeme"
    item_id = uuid.uuid4().hex
    filename = item_id + extension
    os.makedirs(ICONMEME_DIR, exist_ok=True)
    target = os.path.join(ICONMEME_DIR, filename)
    with open(target, "xb") as handle:
        handle.write(data)
    try:
        create_iconmeme(item_id, display_name, filename, mime_type, len(data))
    except Exception:
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        raise
    item = next(item for item in _iconmeme_items() if item["id"] == item_id)
    return {"ok": True, "item": item}


@app.delete("/iconmeme/{item_id}")
async def iconmeme_delete(item_id: str):
    filename = delete_iconmeme(item_id)
    if not filename:
        raise HTTPException(404, "iconmeme 不存在")
    path = os.path.realpath(os.path.join(ICONMEME_DIR, filename))
    if path.startswith(os.path.realpath(ICONMEME_DIR) + os.sep):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    return {"ok": True, "id": item_id}


@app.get("/doge", response_class=HTMLResponse)
async def doge_page():
    """Doge 动画页（贴纸墙 + 20 种 CSS 动画）。

    贴纸/立绘清单在请求时扫目录注入，重新跑 tools/build_doge_assets.py 后无需改模板。
    """
    root = os.path.dirname(__file__)
    src = os.path.join(root, "templates", "doge.html")
    with open(src, encoding="utf-8") as f:
        html = f.read()

    stickers = _doge_listing("stickers", (".webp", ".png"))
    heroes = _doge_listing("img", (".webp", ".png"))
    iconmeme = _iconmeme_items()

    html = html.replace("/*__STICKERS__*/[]", json.dumps(stickers, ensure_ascii=False))
    html = html.replace("/*__HEROES__*/[]", json.dumps(heroes, ensure_ascii=False))
    html = html.replace("/*__ICONMEME__*/[]", json.dumps(iconmeme, ensure_ascii=False))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.post("/harvest")
async def harvest(text: str = Form(""), image: UploadFile = File(None)):
    results = []
    if image and image.filename:
        data = await image.read()
        mime = image.content_type or "image/png"
        candidates = await asyncio.to_thread(extract_from_image, data, mime)
        from probe import probe
        from db import store_valid_key

        for c in candidates:
            res = await asyncio.to_thread(probe, c.get("base_url"), c.get("api_key"))
            if res["ok"]:
                h = store_valid_key("web", c.get("base_url"), c["api_key"],
                                    res["provider"], res["models"], res["latency_ms"])
                results.append({"base_url": c.get("base_url"), "provider": res["provider"],
                                "models": res["models"], "latency_ms": res.get("latency_ms"),
                                "status": "valid", "hash": h,
                                "curl": res.get("curl"), "trace": res.get("trace")})
            else:
                ak = c.get("api_key") or ""
                results.append({"base_url": c.get("base_url"),
                                "api_key_mask": (ak[:4] + "****") if ak else "****",
                                "latency_ms": res.get("latency_ms"), "status": "invalid",
                                "curl": res.get("curl"), "trace": res.get("trace")})
    if text and text.strip():
        results = await asyncio.to_thread(process_text, text, "web")
        # 视频 / 公众号文章 / 网盘分享 等链接不可能提供 API，统计后提示用户，
        # 避免「明明粘贴了带 key 的消息却显示没扒到」的困惑。
        from extract import BASEURL_RE
        from probe import is_content_url, is_plausible_api_url
        ignored = sum(
            1 for u in BASEURL_RE.findall(text)
            if is_content_url(u) or not is_plausible_api_url(u)
        )
        return {"count": len(results), "results": results, "ignored": ignored}
    return {"count": len(results), "results": results}


@app.post("/onebot")
async def onebot(bg: BackgroundTasks, payload: dict = Body(...)):
    """NapCat(OneBot11) 推送入口。三类事件：群消息 / 撤回 / 群文件上传。"""
    events = payload if isinstance(payload, list) else [payload]
    n_msg = n_recall = n_upload = 0
    images_todo, texts = [], []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        pt = ev.get("post_type")

        if pt == "message" and ev.get("message_type") == "group":
            try:
                r = await asyncio.to_thread(qq_ingest.ingest_message, ev)
            except Exception:
                r = None
            if r:
                n_msg += 1
                if r["images"]:
                    images_todo.append((r["chat_id"], r["msg_key"], r["images"]))
                if r["kind"] == "text" and r["content"]:
                    texts.append((r["content"], f"qq:{r['chat_id']}"))

        elif pt == "notice":
            nt = ev.get("notice_type")
            if nt == "group_recall":
                n_recall += await asyncio.to_thread(qq_ingest.handle_recall, ev)
            elif nt == "group_upload":
                up = await asyncio.to_thread(qq_ingest.handle_upload, ev)
                if up:
                    n_upload += 1
                    if up.get("pending"):
                        notify_group_file(up)

    if images_todo:
        bg.add_task(_bg_images, images_todo)
    if texts:
        bg.add_task(_bg_extract, texts)

    return {"messages": n_msg, "recalls": n_recall, "uploads": n_upload,
            "images_pending": sum(len(x[2]) for x in images_todo)}


@app.get("/groups")
async def groups(platform: str = None):
    rows = store.list_groups(platform=platform)
    out = []
    for g in rows:
        cnt = store.recent(platform=g["platform"], chat_id=g["chat_id"], limit=1)
        out.append({**g, "has_messages": bool(cnt)})
    return {"count": len(out), "groups": out}


@app.post("/groups/toggle")
async def toggle_group(platform: str = Form(...), chat_id: str = Form(...), enabled: bool = Form(True)):
    store.set_group_enabled(platform, chat_id, enabled)
    return {"ok": True, "platform": platform, "chat_id": chat_id, "enabled": enabled}


@app.get("/stats")
async def stats():
    return store.stats()


@app.get("/messages")
async def messages(
    platform: str = None,
    chat_id: str = None,
    limit: int = 50,
    include_recalled: bool = True,
    q: str = None,
    msg_type: str = None,
    only_recalled: bool = False,
    exclude_sticker: bool = False,
    start_ts: str = None,
    end_ts: str = None,
):
    rows = store.recent(
        platform=platform,
        chat_id=chat_id,
        limit=limit,
        include_recalled=include_recalled,
        q=q,
        msg_type=msg_type,
        only_recalled=only_recalled,
        exclude_types=["sticker"] if exclude_sticker else None,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if exclude_sticker:
        rows = [r for r in rows if not _is_sticker_noise(r)]
    return {"count": len(rows), "messages": rows}


_PLACEHOLDER_RE = re.compile(r"(\[[^\]]{1,14}\])")


def _is_sticker_noise(m: dict) -> bool:
    """纯表情 / 表情包图片（[表情 472]、[动画表情]、[表情包: xxx]）"""
    mtype = (m.get("msg_type") or "")
    content = (m.get("content") or "").strip()
    if mtype == "sticker":
        return True
    if not content:
        return False
    # 表情包图片：含 QQ 表情标记，且不是情报截图
    if mtype == "image":
        if ("[动画表情]" in content or "[表情包" in content) and "情报截图" not in content:
            return True
        return False
    # 其它类型（文件/链接/视频）保留
    if mtype in ("file", "link", "video"):
        return False
    rest = _PLACEHOLDER_RE.sub("", content).strip()
    return rest == "" and len(content) <= 80


@app.get("/chats")
async def chats(platform: str = None, limit: int = 80):
    """会话列表：给情报流左侧群列表用"""
    rows = await asyncio.to_thread(store.chats, platform, limit)
    return {"count": len(rows), "chats": rows}


@app.get("/messages/context")
async def messages_context(id: int, before: int = 6, after: int = 6):
    """某条消息的前后文（点击气泡 / 事件详情用）"""
    rows = await asyncio.to_thread(store.context, id, before, after)
    return {"count": len(rows), "messages": rows}


@app.get("/messages/by_ids")
async def messages_by_ids(ids: str):
    """按 id 列表批量取消息（related_messages 用），逗号分隔"""
    id_list = [x for x in (ids or "").split(",") if x.strip().isdigit()]
    rows = await asyncio.to_thread(store.by_ids, id_list)
    return {"count": len(rows), "messages": rows}


@app.get("/messages/by_key")
async def message_by_key(platform: str, chat_id: str, key: str):
    """按来源稳定键定位消息；响应不含正文、raw_json 或任何密钥。"""
    row = await asyncio.to_thread(store.locate_message, platform, chat_id, key)
    return {"found": bool(row), "message": row}


@app.post("/messages/ai_summarize")
async def ai_summarize_messages(
    ids: str = Form(""),
    platform: str = Form(""),
    chat_id: str = Form(""),
    start_ts: int = Form(0),
    end_ts: int = Form(0),
):
    """手动 AI 总结一段上下文消息。ids 优先（逗号分隔），否则按 platform/chat_id/时间窗取。"""
    import httpx as _httpx
    from config import GATEWAY_BASE, GATEWAY_KEY, SUMMARY_MODEL, HTTP_TIMEOUT, PROXIES

    id_list = [int(x) for x in (ids or "").split(",") if x.strip().isdigit()][:400]
    if id_list:
        msgs = await asyncio.to_thread(store.by_ids, id_list)
        msgs.sort(key=lambda m: (m.get("ts") or 0, m.get("id") or 0))
    else:
        def _q():
            c = store._conn()
            sql = "SELECT * FROM raw_messages WHERE 1=1"
            args = []
            if platform:
                sql += " AND platform=?"
                args.append(platform)
            if chat_id:
                sql += " AND chat_id=?"
                args.append(chat_id)
            if start_ts:
                sql += " AND CAST(ts AS INTEGER) >= ?"
                args.append(int(start_ts))
            if end_ts:
                sql += " AND CAST(ts AS INTEGER) <= ?"
                args.append(int(end_ts))
            sql += " ORDER BY CAST(ts AS INTEGER) ASC, id ASC LIMIT 300"
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]
            c.close()
            return rows
        msgs = await asyncio.to_thread(_q)

    if not msgs:
        return {"ok": False, "error": "没有可总结的消息"}

    lines = []
    for m in msgs:
        ts = m.get("ts") or 0
        tstr = datetime.datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M") if ts else "?"
        who = m.get("sender_name") or m.get("sender_id") or "匿名"
        txt = (m.get("content") or "").strip().replace("\n", " ")
        if not txt:
            txt = f"[{m.get('msg_type') or '非文本'}]"
        lines.append(f"[{tstr}] {who}: {txt[:200]}")
    transcript = "\n".join(lines)
    chat_name = msgs[0].get("chat_name") or chat_id or ""

    prompt = (
        f"以下是群聊【{chat_name}】里的一段上下文消息（共 {len(msgs)} 条）。"
        "请用简体中文总结：1) 讨论了什么话题；2) 结论或决定；3) 待办与行动项；"
        "4) 提到的资源、链接、API key（只标注『有』，不要复述密钥本身）。"
        "300 字以内，Markdown 格式。"
        "注意：内容形如 [非文本]/[图片] 的行是图片或表情等非文字消息；"
        "若实际文字内容极少，如实说明即可，不要误以为聊天记录缺失。\n\n" + transcript
    )
    payload = {
        "model": SUMMARY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        _kwargs = dict(
            headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        from config import httpx_kwargs
        _kwargs.update(httpx_kwargs())
        endpoint = GATEWAY_BASE.rstrip("/") + "/chat/completions"
        if not GATEWAY_BASE.rstrip("/").endswith("/v1"):
            endpoint = GATEWAY_BASE.rstrip("/") + "/v1/chat/completions"
        r = _httpx.post(endpoint, **_kwargs)
        r.raise_for_status()
        summary = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return {"ok": False, "error": f"AI 总结失败: {e}"}
    return {"ok": True, "summary": summary, "message_count": len(msgs), "chat_name": chat_name}


@app.post("/messages/ai_ask")
async def ai_ask_messages(
    ids: str = Form(""),
    platform: str = Form(""),
    chat_id: str = Form(""),
    question: str = Form(""),
):
    """针对真实消息上下文问答；不允许没有上下文的空请求。"""
    import httpx as _httpx
    from config import GATEWAY_BASE, GATEWAY_KEY, SUMMARY_MODEL, HTTP_TIMEOUT, httpx_kwargs

    question = (question or "").strip() or "请总结这段聊天的重点、结论和下一步行动。"
    id_list = [int(x) for x in (ids or "").split(",") if x.strip().isdigit()][:200]
    if id_list:
        msgs = await asyncio.to_thread(store.by_ids, id_list)
    else:
        msgs = await asyncio.to_thread(store.recent, platform or None, chat_id or None, 200)
    if not msgs:
        return {"ok": False, "error": "没有可供 AI 阅读的聊天消息"}

    msgs = sorted(msgs, key=lambda m: (int(m.get("ts") or 0), int(m.get("id") or 0)))
    lines = []
    for m in msgs:
        txt = (m.get("content") or "").strip().replace("\n", " ")
        if not txt:
            txt = f"[{m.get('msg_type') or '非文本'}]"
        lines.append(f"{m.get('sender_name') or '匿名'}: {txt[:240]}")
    chat_name = msgs[0].get("chat_name") or chat_id or "当前会话"
    prompt = (
        f"你正在回答群聊【{chat_name}】的问题。用户问题：{question}\n\n"
        "请只根据下面真实消息回答；如果消息不足以判断，要明确说不知道。"
        "不要复述或输出 API key、密码、token 等敏感凭据，只能说相关信息是否存在。"
        "使用简体中文，结构清楚，控制在 500 字以内。\n\n" + "\n".join(lines)
    )
    payload = {"model": SUMMARY_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    try:
        kwargs = {"headers": {"Authorization": f"Bearer {GATEWAY_KEY}"}, "json": payload, "timeout": HTTP_TIMEOUT}
        kwargs.update(httpx_kwargs())
        endpoint = GATEWAY_BASE.rstrip("/") + "/chat/completions"
        if not GATEWAY_BASE.rstrip("/").endswith("/v1"):
            endpoint = GATEWAY_BASE.rstrip("/") + "/v1/chat/completions"
        r = _httpx.post(endpoint, **kwargs)
        r.raise_for_status()
        answer = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return {"ok": False, "error": f"AI 问答失败: {e}"}
    return {"ok": True, "answer": answer, "message_count": len(msgs), "chat_name": chat_name}


@app.get("/logs")
async def logs(name: str = "backend", lines: int = 200):
    """读取 logs/ 下的日志尾巴（只允许白名单文件名，防目录穿越）"""
    import glob as _glob
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    files = sorted(os.path.basename(p) for p in _glob.glob(os.path.join(logs_dir, "*.log")))
    sizes = {}
    for f in files:
        try:
            sizes[f] = os.path.getsize(os.path.join(logs_dir, f))
        except OSError:
            sizes[f] = 0
    if name not in files:
        return {"count": 0, "name": name, "files": files, "sizes": sizes, "lines": [], "error": f"日志不存在: {name}"}
    path = os.path.join(logs_dir, name)
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 400 * 1024))
            data = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"count": 0, "name": name, "files": files, "sizes": sizes, "lines": [], "error": str(e)}
    tail = [ln for ln in data.splitlines() if ln.strip()][-max(10, min(lines, 2000)):]
    return {"count": len(tail), "name": name, "files": files, "sizes": sizes, "lines": tail}


@app.post("/summarize")
async def do_summary(text: str = Form("")):
    s = await asyncio.to_thread(summarize, text)
    return {"summary": s}


@app.get("/keys")
async def keys(full: bool = False):
    rows = list_keys(full=full)
    out = []
    for r in rows:
        item = {"id": r[0], "source": r[1], "base_url": r[2], "masked": r[3],
                "provider": r[4], "models": r[5], "latency_ms": r[6], "last_tested": r[7],
                "status": r[8], "note": r[9]}
        if full:
            item["api_key"] = r[10]
        out.append(item)
    return {"count": len(out), "keys": out}


@app.get("/keys/{key_id}/context")
async def key_context(key_id: int):
    """从鸡蛋库跳回提取该 key 的原始聊天消息。"""
    from db import _conn as key_conn
    c = key_conn()
    row = c.execute("SELECT api_key FROM keys WHERE id=?", (int(key_id),)).fetchone()
    c.close()
    if not row or not row[0]:
        return {"found": False, "messages": []}
    messages = await asyncio.to_thread(store.messages_for_key, row[0], 40)
    return {"found": bool(messages), "messages": messages}


@app.post("/keys/test")
async def test_key(base_url: str = Form(...), api_key: str = Form(...)):
    """测试并重新校准 Key 连通性，成功则刷新库中记录。"""
    from probe import probe
    from db import store_valid_key, mark_key_test_failed

    res = await asyncio.to_thread(probe, base_url, api_key)
    if res.get("ok"):
        store_valid_key(
            "retest",
            base_url,
            api_key,
            res["provider"],
            res["models"],
            res["latency_ms"],
        )
    else:
        mark_key_test_failed(base_url, api_key, res.get("error"))
    return res


# ---------- 头像 ----------

@app.get("/avatar/{kind}/{platform}/{key}")
async def get_avatar(kind: str, platform: str, key: str):
    """QQ 群/用户头像：下载 → 压到 96px → 缓存到 media/avatars。"""
    import avatars as avatars_mod
    from fastapi.responses import FileResponse, Response

    if kind not in ("group", "user"):
        return Response(status_code=404)
    path = await asyncio.to_thread(avatars_mod.fetch_avatar, platform, kind, key)
    if not path:
        # 前端收到 404 会退回首字母色块
        return Response(status_code=404)
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


# ---------- 供应商体检 ----------

@app.get("/providers")
async def get_providers():
    """列出 DSH / WorkBuddy / CCswitch 里配置的 provider。"""
    import providers as providers_mod
    return await asyncio.to_thread(providers_mod.list_all)


@app.post("/providers/test")
async def test_providers(ids: str = Form(None)):
    """测一批 provider 的连通性；ids 为空则全测。"""
    import providers as providers_mod

    want = [x for x in (ids or "").split(",") if x.strip()] or None

    def _run():
        from concurrent.futures import ThreadPoolExecutor
        items = providers_mod.list_all()["providers"]
        if want:
            sel = [p for p in items if p["id"] in set(want)]
        else:
            sel = items
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(providers_mod.test_one, sel))
        bad = [r for r in results if not r["ok"]]
        return {"count": len(results), "ok_count": len(results) - len(bad),
                "bad_count": len(bad), "results": results}

    return await asyncio.to_thread(_run)


@app.post("/providers/delete")
async def delete_provider_endpoint(ids: str = Form(...)):
    """删除指定 provider（先备份再改文件）。"""
    import providers as providers_mod

    wanted = [x for x in ids.split(",") if x.strip()]
    out = []
    for pid in wanted:
        r = await asyncio.to_thread(providers_mod.delete_provider, pid)
        if r.get("ok"):
            providers_mod.set_alias(pid, "", "")  # 清掉已删 provider 的别名记录
        out.append(r)
        await asyncio.sleep(0.05)
    return {"count": len(out), "deleted": sum(1 for r in out if r.get("ok")), "results": out}


@app.post("/providers/rename")
async def rename_provider(req: Request):
    """给 provider 设置自定义显示名 / 备注。存本地 overlay，不改第三方配置文件；两者都空 = 恢复原名。"""
    import providers as providers_mod

    data = await req.json()
    pid = str(data.get("id") or "").strip()
    if not pid:
        return {"ok": False, "error": "缺少 provider id"}
    name = str(data.get("name") or "").strip()
    note = str(data.get("note") or "").strip()
    if len(name) > 64:
        return {"ok": False, "error": "名称最长 64 字符"}
    if len(note) > 128:
        return {"ok": False, "error": "备注最长 128 字符"}
    exists = any(p["id"] == pid for p in providers_mod.list_all()["providers"])
    if not exists:
        return {"ok": False, "error": "找不到该 provider（可能已被删除）"}
    a = providers_mod.set_alias(pid, name, note)
    return {"ok": True, "alias": a["alias"], "note": a["note"]}


# ---------- 媒体缓存管理 ----------

def _media_root():
    import config
    import os as _os
    return config.MEDIA_DIR


@app.get("/cache/stats")
async def media_stats():
    """媒体缓存占用：文件数、总大小、按类型分布。"""
    import os as _os

    root = _media_root()

    def _scan():
        files = 0
        total = 0
        by_type = {}
        newest = 0
        oldest = None
        for dirpath, _dirs, names in _os.walk(root):
            for n in names:
                p = _os.path.join(dirpath, n)
                try:
                    st = _os.stat(p)
                except OSError:
                    continue
                files += 1
                total += st.st_size
                ext = _os.path.splitext(n)[1].lower() or '(无后缀)'
                e = by_type.setdefault(ext, {"files": 0, "bytes": 0})
                e["files"] += 1
                e["bytes"] += st.st_size
                newest = max(newest, st.st_mtime)
                oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
        return {
            "dir": root,
            "files": files,
            "bytes": total,
            "newest": int(newest) if newest else 0,
            "oldest": int(oldest) if oldest else 0,
            "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1]["bytes"])),
        }

    return await asyncio.to_thread(_scan)


@app.post("/cache/purge")
async def media_purge(mode: str = Form("older_than"), days: int = Form(7), keep_mb: int = Form(500)):
    """清理媒体缓存。
    mode=all 全清；mode=older_than 清 N 天前；mode=keep_recent 只保留最近 keep_mb MB。
    """
    import os as _os
    import time as _time

    root = _media_root()

    def _purge():
        now = _time.time()
        items = []
        for dirpath, _dirs, names in _os.walk(root):
            for n in names:
                p = _os.path.join(dirpath, n)
                try:
                    st = _os.stat(p)
                except OSError:
                    continue
                items.append((p, st.st_size, st.st_mtime))
        deleted = kept = freed = 0
        if mode == "all":
            targets = [p for p, _s, _m in items]
        elif mode == "older_than":
            cutoff = now - max(0, days) * 86400
            targets = [p for p, _s, m in items if m < cutoff]
        elif mode == "keep_recent":
            budget = max(0, keep_mb) * 1024 * 1024
            items.sort(key=lambda x: -x[2])
            acc = 0
            targets = []
            for p, s, _m in items:
                acc += s
                if acc > budget:
                    targets.append(p)
                else:
                    kept += 1
        else:
            return {"ok": False, "error": f"未知模式: {mode}"}

        for p in targets:
            try:
                sz = _os.path.getsize(p)
                _os.remove(p)
                deleted += 1
                freed += sz
            except OSError:
                continue
        return {"ok": True, "deleted": deleted, "freed_bytes": freed, "kept": kept}

    res = await asyncio.to_thread(_purge)
    return res


@app.post("/keys/delete")
async def delete_key(key_id: int = Form(...)):
    """删除一个已入库的鸡蛋（Key）。"""
    from db import delete_keys_with_snapshots

    snapshots = await asyncio.to_thread(delete_keys_with_snapshots, [key_id])
    return {"ok": bool(snapshots), "deleted": len(snapshots),
            "undo_token": _store_undo_batch(snapshots)}


@app.post("/keys/restore")
async def restore_deleted_key(payload: dict = Body(...)):
    """Restore one key deleted in the current local UI session (Cmd/Ctrl+Z)."""
    from db import restore_key

    token = payload.get("undo_token") if isinstance(payload, dict) else None
    snapshots = _take_undo_batch(token)
    if snapshots is None:
        return {"ok": False, "restored": 0, "skipped": 0,
                "error": "撤销令牌已过期或不存在"}
    restored = 0
    skipped = 0
    for snapshot in snapshots:
        if await asyncio.to_thread(restore_key, snapshot):
            restored += 1
        else:
            skipped += 1
    return {"ok": restored > 0, "restored": restored, "skipped": skipped,
            "error": None if restored > 0 else "鸡蛋已存在或撤销数据无效"}


@app.post("/keys/delete_batch")
async def delete_keys_batch(key_ids: str = Form(...)):
    """批量删除鸡蛋：key_ids 为逗号分隔的 id；only_invalid=1 时只删失效的。"""
    from db import delete_keys_with_snapshots

    wanted = [int(x) for x in str(key_ids or "").replace("，", ",").split(",") if x.strip().isdigit()]
    if not wanted:
        return {"ok": False, "deleted": 0, "error": "没有传入有效的 key id"}

    snapshots = await asyncio.to_thread(delete_keys_with_snapshots, wanted)
    return {"ok": bool(snapshots), "deleted": len(snapshots), "requested": len(wanted),
            "undo_token": _store_undo_batch(snapshots)}


@app.post("/keys/delete_invalid")
async def delete_invalid_keys():
    """一键清空所有已失效（status != 'valid'）的鸡蛋。"""
    from db import delete_keys_with_snapshots

    snapshots = await asyncio.to_thread(delete_keys_with_snapshots, invalid_only=True)
    return {"ok": True, "deleted": len(snapshots),
            "undo_token": _store_undo_batch(snapshots)}


# ---------- 情报简报系统 ----------

@app.post("/intel/generate")
async def generate_intel(platform: str = Form(None), chat_id: str = Form(None), window_hours: int = Form(6)):
    """为指定群（或所有活跃群）生成简报"""
    if platform and chat_id:
        result = await asyncio.to_thread(intel.generate_summary, platform, chat_id, window_hours)
        return {"count": 1 if result else 0, "summaries": [result] if result else []}
    else:
        results = await asyncio.to_thread(intel.generate_all_summaries, window_hours)
        return {"count": len(results), "summaries": results}


@app.get("/intel/summaries")
async def intel_summaries(limit: int = 20):
    """列出最近的简报"""
    results = await asyncio.to_thread(intel.list_summaries, limit)
    return {"count": len(results), "summaries": results}


@app.post("/intel/detect_events")
async def detect_events():
    """手动触发重要事件 AI 检测（增量，基于 last_event_check 水位线）"""
    events = await asyncio.to_thread(intel.detect_important_events)
    return {"ok": True, "count": len(events), "events": events}


# ---------- 群文件决策（A 方案：≤20MB 自动下，超出待决策 + 通知） ----------

@app.get("/files", response_class=HTMLResponse)
async def files_page():
    with open(os.path.join(os.path.dirname(__file__), "templates", "files.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/files/list")
async def files_list():
    import nsfw as nsfw_mod
    rows = store.pending_files()
    out = []
    for r in rows:
        meta = _parse_file_meta(r.get("raw_json"), r.get("content") or "")
        name = meta.get("name") or meta.get("file") or (r.get("content") or "").replace("[群文件] ", "")
        kind = nsfw_mod.kind_of(name)
        out.append({
            "id": r["id"],
            "name": name,
            "size": _human(int(meta.get("size") or 0)) if int(meta.get("size") or 0) > 0 else "大小未知",
            "size_bytes": int(meta.get("size") or 0),
            "group": r.get("chat_name") or r.get("chat_id"),
            "chat_id": r.get("chat_id"),
            "time": _ts_str(r.get("ts")),
            "title": api_extensions.get_file_title(r["id"]),
            "kind": kind,
            "nsfw": r.get("nsfw"),
            "nsfw_reason": r.get("nsfw_reason") or "",
            "thumb": _thumb_url(f"file:{r['id']}") if r.get("thumb_ref") else "",
            # 图片超过 8MB 时本地不下载、只做关键词判定，避免每次刷新都白跑一次
            "inspectable": kind in ("image", "video")
                           and (kind != "image" or int(meta.get("size") or 0) <= 16 * 1024 * 1024),
            "media_ref": r.get("media_ref") or "",
        })
    return {"count": len(out), "files": out}


def _resolve_media_path(media_ref: str):
    """把 media_ref（相对路径）解析成绝对路径：先试 download_dir，再试 MEDIA_DIR。"""
    import config
    if not media_ref:
        return None
    if os.path.isabs(media_ref):
        return media_ref if os.path.exists(media_ref) else None
    bases = []
    dl = api_extensions.get_setting("download_dir", "") or ""
    if dl:
        bases.append(dl)
    bases.append(config.MEDIA_DIR)
    for b in bases:
        p = os.path.abspath(os.path.join(b, media_ref))
        # 防目录穿越：解析后必须仍在 base 之下
        if os.path.commonpath([os.path.abspath(b), p]) != os.path.abspath(b):
            continue
        if os.path.exists(p):
            return p
    return None


@app.get("/files/recent")
async def files_recent(limit: int = 8):
    """最近已下载落盘的群文件，供「最近下载」条一键打开目录。"""
    rows = store.recent_downloads(min(max(limit, 1), 50))
    out = []
    for r in rows:
        meta = _parse_file_meta(r.get("raw_json"), r.get("content") or "")
        name = meta.get("name") or meta.get("file") or (r.get("content") or "").replace("[群文件] ", "")
        out.append({
            "id": r["id"],
            "name": name,
            "size": _human(int(meta.get("size") or 0)) if int(meta.get("size") or 0) > 0 else "大小未知",
            "group": r.get("chat_name") or r.get("chat_id"),
            "time": _ts_str(r.get("ts")),
            "title": api_extensions.get_file_title(r["id"]),
            "media_ref": r.get("media_ref"),
        })
    return {"count": len(out), "files": out}


@app.post("/files/reveal")
async def files_reveal(id: int = Form(...)):
    """在 Finder 中定位已下载文件（后端本机执行 open -R）。"""
    import subprocess
    row = store.file_by_id(id)
    if not row:
        return {"ok": False, "error": "not found"}
    media_ref = row.get("media_ref")
    path = _resolve_media_path(media_ref)
    if not path:
        return {"ok": False, "error": f"文件不在磁盘上（media_ref={media_ref or '空'}）"}
    try:
        if os.path.isdir(path):
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["open", "-R", path])
    except Exception as e:
        return {"ok": False, "error": f"调用访达失败: {e}"}
    return {"ok": True, "path": path, "dir": os.path.dirname(path) if os.path.isfile(path) else path}


@app.post("/files/action")
async def files_action(id: int = Form(...), action: str = Form(...)):
    row = store.file_by_id(id)
    if not row:
        return {"ok": False, "error": "not found"}
    platform = row["platform"]
    chat_id = row["chat_id"]
    msg_key = row["msg_key"]

    if action == "ignore":
        store.set_file_status(platform, chat_id, msg_key, "ignored")
        return {"ok": True, "action": "ignored"}

    if action == "download":
        if row.get("nsfw"):
            return {"ok": False, "blocked": True,
                    "error": f"疑似不适宜内容，已拦截下载：{row.get('nsfw_reason') or '检测命中'}"}
        meta = _parse_file_meta(row.get("raw_json"), row.get("content") or "")
        file_id = str(meta.get("id") or meta.get("file_id") or "")
        busid = meta.get("busid") or 0
        name = meta.get("name") or meta.get("file") or ""
        url = meta.get("url") or ""
        dl = url
        if not dl and file_id:
            data = napcat.get_group_file_url(chat_id, file_id, busid)
            if isinstance(data, dict):
                dl = data.get("url") or ""
        if not dl:
            return {"ok": False, "error": "no url（NapCat API 未激活或文件已失效）"}
        raw = media.fetch_url(dl)
        if not raw:
            return {"ok": False, "error": "下载失败"}
        # 下载前做一次不适宜内容检测：命中则不落盘
        import nsfw as nsfw_mod
        probe = nsfw_mod.check_image_bytes(raw) if nsfw_mod.kind_of(name) == "image" else None
        if probe and probe.get("nsfw"):
            store.set_msg_nsfw(platform, chat_id, msg_key, True, probe.get("score"), probe.get("reason"))
            return {"ok": False, "blocked": True,
                    "error": f"疑似不适宜内容，已拦截下载：{probe.get('reason')}"}
        ext = os.path.splitext(name)[1] or None
        save_dir = api_extensions.get_setting("download_dir", "") or None
        rel, md5 = media.save_bytes(raw, platform, chat_id, file_id or msg_key, ext=ext, base=save_dir)
        store.attach_media(platform, chat_id, msg_key, rel, md5, note=f"[已下载 {md5[:8]}]")
        store.set_file_status(platform, chat_id, msg_key, "downloaded")
        return {"ok": True, "action": "downloaded", "md5": md5[:8], "path": rel}

    return {"ok": False, "error": "unknown action"}


@app.get("/files/{file_id}/download")
async def file_stream_download(file_id: int, save: int = 0):
    """浏览器流式下载：带 Content-Length 与文件名，支持进度条。
    save=1 时同时把文件落盘到媒体库（download_dir 配置生效）。"""
    from fastapi.responses import StreamingResponse, JSONResponse
    from urllib.parse import quote
    import httpx
    import config

    row = store.file_by_id(file_id)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if row.get("nsfw"):
        return JSONResponse({"ok": False, "blocked": True,
                             "error": f"疑似不适宜内容，已拦截下载：{row.get('nsfw_reason') or '检测命中'}"},
                            status_code=403)
    meta = _parse_file_meta(row.get("raw_json"), row.get("content") or "")
    file_id_str = str(meta.get("id") or meta.get("file_id") or "")
    busid = meta.get("busid") or 0
    name = meta.get("name") or meta.get("file") or f"group_file_{file_id}"
    url = meta.get("url") or ""
    if not url and file_id_str:
        data = napcat.get_group_file_url(row["chat_id"], file_id_str, busid)
        if isinstance(data, dict):
            url = data.get("url") or ""
    if not url:
        return JSONResponse({"ok": False, "error": "no url（NapCat API 未激活或文件已失效）"}, status_code=404)

    try:
        client = httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True)
        req = client.build_request("GET", url)
        resp = client.send(req, stream=True)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"下载连接失败: {e}"}, status_code=502)

    if resp.status_code >= 400:
        resp.close()
        return JSONResponse({"ok": False, "error": f"上游返回 {resp.status_code}"}, status_code=502)

    headers = {}
    clen = resp.headers.get("content-length")
    if clen:
        headers["Content-Length"] = clen
    headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    media_type = resp.headers.get("content-type") or "application/octet-stream"

    import hashlib
    import tempfile

    platform = row["platform"]
    chat_id = row["chat_id"]
    msg_key = row["msg_key"]

    def gen():
        tmpf = None
        if save:
            save_dir = api_extensions.get_setting("download_dir", "") or None
            tmpf = tempfile.NamedTemporaryFile(delete=False, suffix=".part")
            h = hashlib.md5()
        try:
            for chunk in resp.iter_bytes(65536):
                if tmpf is not None:
                    tmpf.write(chunk)
                    h.update(chunk)
                yield chunk
        finally:
            resp.close()
            if tmpf is not None:
                tmpf.close()
                try:
                    ext = os.path.splitext(name)[1] or None
                    rel, md5 = media.save_file(tmpf.name, h.hexdigest(), platform, chat_id,
                                               file_id_str or msg_key, ext=ext, base=save_dir)
                    store.attach_media(platform, chat_id, msg_key, rel, md5,
                                       note=f"[已下载 {md5[:8]}]")
                    store.set_file_status(platform, chat_id, msg_key, "downloaded")
                except Exception as e:
                    print(f"[app] 流式落盘失败: {e}")
                    try:
                        os.remove(tmpf.name)
                    except OSError:
                        pass

    return StreamingResponse(gen(), status_code=resp.status_code,
                             media_type=media_type, headers=headers)


# ========== 新增API端点 ==========

@app.post("/messages/mark_read")
async def mark_read(
    message_ids: str = Form(None),
    platform: str = Form(None),
    chat_id: str = Form(None),
    mark_all: bool = Form(False)
):
    """标记消息为已读
    - message_ids: 逗号分隔的消息ID列表
    - platform + chat_id: 标记指定群的所有消息
    - mark_all: 标记所有消息
    """
    ids = None
    if message_ids:
        ids = [int(x.strip()) for x in message_ids.split(",") if x.strip().isdigit()]
    
    if mark_all:
        affected = await asyncio.to_thread(api_extensions.mark_messages_read)
    elif ids:
        affected = await asyncio.to_thread(api_extensions.mark_messages_read, message_ids=ids)
    elif platform and chat_id:
        affected = await asyncio.to_thread(api_extensions.mark_messages_read, platform=platform, chat_id=chat_id)
    else:
        return {"ok": False, "error": "需要指定 message_ids、platform+chat_id 或 mark_all"}
    
    return {"ok": True, "affected": affected}


@app.get("/important_events")
async def get_important_events(limit: int = 20, unnotified_only: bool = False):
    """列出重要事件"""
    events = await asyncio.to_thread(api_extensions.list_important_events, limit, unnotified_only)
    return {"count": len(events), "events": events}


@app.post("/important_events/notify")
async def notify_event(event_id: int = Form(...)):
    """标记事件已通知"""
    await asyncio.to_thread(api_extensions.mark_event_notified, event_id)
    return {"ok": True}


@app.get("/files/{file_id}/context")
async def file_context(file_id: int, context_window: int = 20):
    """获取文件消息的上下文"""
    ctx = await asyncio.to_thread(api_extensions.get_file_context, file_id, context_window)
    if not ctx:
        return {"ok": False, "error": "文件消息不存在"}
    return {"ok": True, **ctx}


@app.post("/files/{file_id}/summarize")
async def summarize_file_context(file_id: int, context_window: int = Form(20)):
    """AI总结文件上下文"""
    import config
    import requests
    
    ctx = await asyncio.to_thread(api_extensions.get_file_context, file_id, context_window)
    if not ctx:
        return {"ok": False, "error": "文件消息不存在"}
    
    # 构建prompt
    file_msg = ctx["file_message"]
    messages = ctx["context"]
    chat_name = ctx["chat_name"]
    
    prompt = f"""这是群聊【{chat_name}】中的一个文件消息的上下文。

文件消息：
发送者：{file_msg['sender_name']}
内容：{file_msg['content']}
时间：{datetime.datetime.fromtimestamp(file_msg['ts']).strftime('%Y-%m-%d %H:%M:%S')}

上下文对话（共{len(messages)}条）：
"""
    
    for msg in messages:
        ts_str = datetime.datetime.fromtimestamp(msg['ts']).strftime('%H:%M:%S')
        recalled_mark = " [已撤回]" if msg['recalled'] else ""
        prompt += f"\n[{ts_str}] {msg['sender_name']}: {msg['content']}{recalled_mark}"
    
    prompt += "\n\n请完成两个任务：\n1. 给这个文件起一个「智能标题」（≤25字），概括文件是什么、价值何在（如「Gemini 全家桶 API Key 合集」「某开源项目安装包+教程」）；\n2. 总结：这个文件是什么？为什么被发送？群友们在讨论什么？文件的重要性和价值（如果是API key、账号、资源等请标注）。总结300字以内，使用Markdown格式。\n\n严格按 JSON 输出：\n{\"title\": \"智能标题\", \"summary\": \"Markdown 总结\"}"
    
    # 调用LLM
    try:
        resp = requests.post(
            f"{config.GATEWAY_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.GATEWAY_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["choices"][0]["message"]["content"].strip()
        parsed = intel._parse_json(raw) or {}
        title = (parsed.get("title") or "").strip()[:60]
        summary = (parsed.get("summary") or raw).strip()
    except Exception as e:
        return {"ok": False, "error": f"LLM调用失败: {str(e)}"}
    
    # 入库（file_summaries: message_id 主键，upsert）
    try:
        import sqlite3 as _sq
        from config import DB_PATH
        conn = _sq.connect(DB_PATH, timeout=15)
        conn.execute(
            """INSERT INTO file_summaries(message_id, title, summary, context_window, created_at)
               VALUES(?,?,?,?,datetime('now'))
               ON CONFLICT(message_id) DO UPDATE SET
                 title=excluded.title, summary=excluded.summary,
                 context_window=excluded.context_window, created_at=datetime('now')""",
            (int(file_id), title, summary, int(context_window)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[app] file_summaries 入库失败: {e}")
    
    return {
        "ok": True,
        "title": title,
        "summary": summary,
        "file_message": file_msg,
        "context_count": len(messages),
        "chat_name": chat_name,
    }


@app.post("/keys/import_to_dsh")
async def import_key_to_dsh(
    key_id: int = Form(...),
    provider_name: str = Form("custom"),
    selected_models: str = Form(""),
):
    """导入 API key 到 DSH Desktop profile。

    实际写入 ~/.dsh/profiles/desktop/cordis.patch.yml，并将密钥放入
    ~/.dsh/.credentials.yaml 的 refs，通过 apiKeyEnv 关联；不会写入旧的
    ~/.dsh/keys.json，也不会把明文 key 写入 patch 文件。

    - key_id: 本地数据库中的key ID
    - provider_name: 自定义provider名称
    - selected_models: 逗号分隔的模型列表
    """
    # 获取key详情
    rows = list_keys(full=True)
    key_data = None
    for r in rows:
        if r[0] == key_id:
            try:
                stored_models = json.loads(r[5] or "[]")
            except (TypeError, json.JSONDecodeError):
                stored_models = [m.strip() for m in (r[5] or "").split(",") if m.strip()]
            if not isinstance(stored_models, list):
                stored_models = []
            key_data = {
                "id": r[0],
                "base_url": r[2],
                "api_key": r[10],
                "models": [str(m).strip() for m in stored_models if str(m).strip()],
            }
            break
    
    if not key_data:
        return {"ok": False, "error": "Key不存在"}
    
    # 处理模型列表
    if selected_models:
        models = [m.strip() for m in selected_models.split(",") if m.strip()]
    else:
        models = key_data["models"]
    
    # 导入
    result = await asyncio.to_thread(
        api_extensions.import_to_dsh_keys,
        key_data["base_url"],
        key_data["api_key"],
        provider_name,
        models,
    )
    
    return result


# ---------- QQ 登录状态 / 二维码（Web UI 内可直接扫码） ----------

@app.get("/qq/status")
async def qq_status():
    return await asyncio.to_thread(api_extensions.qq_login_status)


@app.get("/qq/qrcode")
async def qq_qrcode():
    path = await asyncio.to_thread(api_extensions.qq_qrcode_path)
    if not path:
        raise HTTPException(404, "暂无二维码，点「重启 NapCat」后重试")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/qq/restart")
async def qq_restart():
    return await asyncio.to_thread(api_extensions.qq_restart_container)


@app.post("/groups/dismiss")
async def dismiss_new_groups(payload: dict = Body(...)):
    """忽略这些“新群”，以后不再提示（稍后处理 / 关闭弹窗都走这里）"""
    items = payload.get("groups") or []
    if not isinstance(items, list):
        return {"ok": False, "detail": "groups must be a list"}
    added = await asyncio.to_thread(api_extensions.dismiss_groups, items)
    return {"ok": True, "dismissed": added, "total": len(items)}


@app.get("/groups/new")
async def get_new_groups():
    """检测新群"""
    new_groups = await asyncio.to_thread(api_extensions.detect_new_groups)
    return {"count": len(new_groups), "groups": new_groups}


@app.post("/groups/add")
async def add_new_group(
    platform: str = Form(...),
    chat_id: str = Form(...),
    chat_name: str = Form(None),
    enabled: bool = Form(True),
):
    """将新群加入追踪"""
    await asyncio.to_thread(api_extensions.add_group_to_tracking, platform, chat_id, chat_name, enabled)
    return {"ok": True, "platform": platform, "chat_id": chat_id}


@app.get("/settings")
async def get_settings():
    """获取所有配置"""
    settings = await asyncio.to_thread(api_extensions.list_all_settings)
    
    # 增加运行时配置
    import config
    runtime_config = {
        "GATEWAY_BASE": config.GATEWAY_BASE,
        "VISION_MODEL": config.VISION_MODEL,
        "SUMMARY_MODEL": config.SUMMARY_MODEL,
        "NAP_HTTP_URL": config.NAP_HTTP_URL,
        "WECHAT_DB_PATH": config.WECHAT_DB_DIR,
    }
    
    # 返回扁平结构，优先runtime_config覆盖user_settings
    result = {**settings, **runtime_config}
    return result


@app.post("/settings/update")
async def update_setting(key: str = Form(...), value: str = Form("")):
    """更新配置项（value 可为空 = 恢复默认）"""
    await asyncio.to_thread(api_extensions.set_setting, key, value)
    return {"ok": True, "key": key}


# ---------- 鸡蛋站（站点库：手工收录 + 自动标题/logo + 分类 + AI 速评 + 导入） ----------

@app.get("/stations")
async def stations_list(type: str = "", q: str = "", source: str = ""):
    items = await asyncio.to_thread(stations.list_stations, type, q, source)
    return {"stations": items, "types": stations.TYPE_LABELS}


@app.post("/stations/preview")
async def stations_preview(url: str = Form(...)):
    meta = await asyncio.to_thread(stations.fetch_meta, url)
    return {"ok": bool(meta.get("ok")), "meta": meta}


@app.post("/stations/add")
async def stations_add(
    url: str = Form(...),
    name: str = Form(""),
    type: str = Form(""),
    reliability: int = Form(3),
    note: str = Form(""),
    auto_meta: str = Form("1"),
):
    sid, err = await asyncio.to_thread(
        stations.add_station, url, name, type, reliability, note, "manual", auto_meta not in ("0", "false", "")
    )
    if not sid:
        return {"ok": False, "error": err or "添加失败"}
    return {"ok": True, "id": sid, "existed": err == "已存在", "station": stations.get_station(sid)}


@app.post("/stations/update")
async def stations_update(
    id: int = Form(...),
    name: str = Form(None),
    type: str = Form(None),
    reliability: int = Form(None),
    note: str = Form(None),
):
    fields = {}
    if name is not None:
        fields["name"] = name
    if type is not None and type in stations.TYPE_LABELS:
        fields["type"] = type
    if reliability is not None:
        fields["reliability"] = max(1, min(5, reliability))
    if note is not None:
        fields["note"] = note
    if not fields:
        return {"ok": False, "error": "没有要更新的字段"}
    await asyncio.to_thread(stations.update_station, id, **fields)
    return {"ok": True, "station": stations.get_station(id)}


@app.post("/stations/delete")
async def stations_delete(id: int = Form(...)):
    st = stations.get_station(id)
    await asyncio.to_thread(stations.delete_station, id)
    return {"ok": True, "deleted": 1, "name": (st or {}).get("name")}


@app.post("/stations/refresh")
async def stations_refresh(id: int = Form(...)):
    return await asyncio.to_thread(stations.refresh_meta, id)


@app.post("/stations/summarize")
async def stations_summarize(id: int = Form(...)):
    return await asyncio.to_thread(stations.summarize_station, id)


@app.post("/stations/import")
async def stations_import(kind: str = Form(...)):
    """kind: safari_reading | safari_favorites | chrome | gicp"""
    if kind in ("safari_reading", "safari_favorites"):
        mode = "reading" if kind.endswith("reading") else "favorites"
        raw = await asyncio.to_thread(stations.import_safari, mode)
        if not raw.get("ok"):
            return raw
        return await asyncio.to_thread(stations.import_items, raw["items"], kind, False)
    if kind == "chrome":
        raw = await asyncio.to_thread(stations.import_chrome)
        if not raw.get("ok"):
            return raw
        return await asyncio.to_thread(stations.import_items, raw["items"], "chrome", False)
    if kind == "gicp":
        return await asyncio.to_thread(stations.seed_from_gicp)
    return {"ok": False, "error": f"未知导入类型: {kind}"}


@app.post("/stations/import_preview")
async def stations_import_preview(kind: str = Form(...)):
    """预览导入候选（不写库）：kind: safari_reading | safari_favorites | chrome | gicp"""
    if kind in ("safari_reading", "safari_favorites"):
        mode = "reading" if kind.endswith("reading") else "favorites"
        return await asyncio.to_thread(stations.preview_safari, mode)
    if kind == "chrome":
        return await asyncio.to_thread(stations.preview_chrome)
    if kind == "gicp":
        return await asyncio.to_thread(stations.preview_gicp)
    return {"ok": False, "error": f"未知导入类型: {kind}", "items": [], "total": 0}


@app.get("/stations/safari_updates")
async def stations_safari_updates():
    """检测首次基线后的阅读列表新增项；只确认候选，不自动入库。"""
    return await asyncio.to_thread(stations.safari_updates)


@app.post("/stations/import_selected")
async def stations_import_selected(payload: dict = Body(...)):
    """按用户勾选入库：{source, items:[{url,title,note}]}"""
    items = payload.get("items") or []
    source = payload.get("source") or "import"
    return await asyncio.to_thread(stations.import_selected, items, source)


@app.post("/stations/import_file_preview")
async def stations_import_file_preview(file: UploadFile = File(...)):
    raw = await file.read()
    text = raw.decode("utf-8", "replace")
    if "<a" not in text.lower():
        return {"ok": False, "error": "没解析到书签，请确认是浏览器导出的 HTML 书签文件", "items": [], "total": 0}
    return await asyncio.to_thread(stations.preview_html_text, text)


@app.post("/stations/enrich")
async def stations_enrich(limit: int = Form(40)):
    return stations.enrich_missing(limit)


@app.post("/stations/summarize_missing")
async def stations_summarize_missing(limit: int = Form(20)):
    return stations.summarize_missing(limit)


@app.get("/stations/summarize_status")
async def stations_summarize_status():
    return stations.summarize_status()


@app.get("/stations/enrich_status")
async def stations_enrich_status():
    return stations.enrich_status()


@app.post("/stations/import_file")
async def stations_import_file(file: UploadFile = File(...)):
    raw = await file.read()
    text = raw.decode("utf-8", "replace")
    if "<a" not in text.lower():
        return {"ok": False, "error": "没解析到书签，请确认是浏览器导出的 HTML 书签文件"}
    return await asyncio.to_thread(stations.import_html_text, text)


@app.get("/daemon/status")
async def get_daemon_status(service: str):
    """获取守护进程状态（macOS launchctl）"""
    import subprocess
    try:
        # 检查服务是否加载
        result = subprocess.run(
            ["launchctl", "list", service],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            # 解析输出获取 PID
            lines = result.stdout.strip().split('\n')
            pid = None
            
            for line in lines:
                if '"PID"' in line:
                    try:
                        pid = int(line.split('=')[1].strip().rstrip(';'))
                    except (ValueError, IndexError):
                        pass
            
            last_start = None
            if pid:
                # 通过 ps 获取进程启动时间
                try:
                    ps_result = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "lstart="],
                        capture_output=True,
                        text=True,
                        timeout=3
                    )
                    if ps_result.returncode == 0:
                        last_start = ps_result.stdout.strip()
                except Exception:
                    pass
            
            return {
                "running": pid is not None and pid > 0,
                "pid": pid,
                "last_start": last_start or "未知"
            }
        else:
            return {"running": False, "pid": None, "last_start": None}
    
    except subprocess.TimeoutExpired:
        return {"running": False, "pid": None, "last_start": None, "error": "超时"}
    except Exception as e:
        return {"running": False, "pid": None, "last_start": None, "error": str(e)}
