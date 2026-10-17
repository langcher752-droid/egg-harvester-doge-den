"""API扩展模块 - 支持新功能的后端逻辑"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta

import yaml
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ========== 已读标记 ==========

def mark_messages_read(message_ids: List[int] = None, platform: str = None, chat_id: str = None):
    """标记消息为已读"""
    conn = get_conn()
    cur = conn.cursor()
    
    if message_ids:
        # 标记指定消息
        placeholders = ",".join("?" * len(message_ids))
        cur.execute(f"UPDATE raw_messages SET read = 1 WHERE id IN ({placeholders})", message_ids)
    elif platform and chat_id:
        # 标记指定群的所有消息
        cur.execute("UPDATE raw_messages SET read = 1 WHERE platform = ? AND chat_id = ?", (platform, chat_id))
    else:
        # 标记所有消息
        cur.execute("UPDATE raw_messages SET read = 1")
    
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected


# ========== 重要事件检测 ==========

def create_important_event(event_type: str, title: str, summary: str, related_messages: List[int] = None):
    """创建重要事件"""
    conn = get_conn()
    cur = conn.cursor()
    
    msg_json = json.dumps(related_messages or [])
    cur.execute("""
        INSERT INTO important_events (event_type, title, summary, related_messages)
        VALUES (?, ?, ?, ?)
    """, (event_type, title, summary, msg_json))
    
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return event_id


def list_important_events(limit: int = 20, unnotified_only: bool = False):
    """列出重要事件"""
    conn = get_conn()
    cur = conn.cursor()
    
    query = "SELECT * FROM important_events"
    if unnotified_only:
        query += " WHERE notified = 0"
    query += " ORDER BY created_at DESC LIMIT ?"
    
    cur.execute(query, (limit,))
    rows = cur.fetchall()
    conn.close()
    
    events = []
    for r in rows:
        events.append({
            "id": r["id"],
            "event_type": r["event_type"],
            "title": r["title"],
            "summary": r["summary"],
            "related_messages": json.loads(r["related_messages"] or "[]"),
            "created_at": r["created_at"],
            "notified": bool(r["notified"]),
            "dismissed": bool(r["dismissed"]),
        })
    return events


def mark_event_notified(event_id: int):
    """标记事件已通知"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE important_events SET notified = 1 WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


# ========== 群文件上下文总结 ==========

def get_file_context(message_id: int, context_window: int = 20) -> Dict:
    """获取文件消息的上下文（前后N条消息）"""
    conn = get_conn()
    cur = conn.cursor()
    
    # 获取文件消息本身
    cur.execute("SELECT * FROM raw_messages WHERE id = ?", (message_id,))
    file_msg = cur.fetchone()
    if not file_msg:
        conn.close()
        return None
    
    platform = file_msg["platform"]
    chat_id = file_msg["chat_id"]
    ts = file_msg["ts"]
    
    # 获取前后的消息
    cur.execute("""
        SELECT * FROM raw_messages 
        WHERE platform = ? AND chat_id = ? AND id != ?
        ORDER BY ABS(ts - ?) ASC
        LIMIT ?
    """, (platform, chat_id, message_id, ts, context_window))
    
    context_msgs = cur.fetchall()
    conn.close()
    
    messages = []
    for msg in context_msgs:
        messages.append({
            "id": msg["id"],
            "sender_name": msg["sender_name"] or msg["sender_id"] or "未知",
            "content": msg["content"] or "",
            "ts": msg["ts"],
            "msg_type": msg["msg_type"],
            "recalled": bool(msg["recalled"]),
        })
    
    # 按时间排序
    messages.sort(key=lambda x: x["ts"])
    
    return {
        "file_message": {
            "id": file_msg["id"],
            "sender_name": file_msg["sender_name"] or file_msg["sender_id"] or "未知",
            "content": file_msg["content"] or "",
            "ts": file_msg["ts"],
        },
        "context": messages,
        "chat_name": file_msg["chat_name"],
        "platform": platform,
        "chat_id": chat_id,
    }


def get_file_title(message_id: int) -> str:
    """读取文件的 AI 智能标题（file_summaries.title），无则返回空串。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT title FROM file_summaries WHERE message_id=?",
            (int(message_id),),
        )
        row = cur.fetchone()
        return (row["title"] or "") if row else ""
    except Exception:
        return ""
    finally:
        conn.close()


# ========== DSH Desktop 导入 ==========

DSH_DIR = os.path.expanduser("~/.dsh")
DSH_PATCH_PATH = os.path.join(DSH_DIR, "profiles", "desktop", "cordis.patch.yml")
DSH_CREDENTIALS_PATH = os.path.join(DSH_DIR, ".credentials.yaml")


class _DshLoader(yaml.SafeLoader):
    """允许 DSH patch 中的 !!js 标签通过离线校验。"""


_DshLoader.add_multi_constructor("tag:yaml.org,2002:js", lambda loader, suffix, node: None)
_DshLoader.add_multi_constructor("!js", lambda loader, suffix, node: None)


def _safe_provider_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "custom").strip()).strip("._-")
    if not value or value.lower() in {"true", "false", "null", "yes", "no", "on", "off"} or re.fullmatch(r"[-+]?\d+(\.\d+)?", value):
        return "provider_" + (value or "custom")
    return value


def _credential_ref(name: str) -> str:
    return "EGG_HARVESTER_{}_API_KEY".format(_safe_provider_name(name).upper().replace("-", "_"))


def _atomic_write(path: str, content: str) -> None:
    parent = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".dsh-import-", dir=parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _backup(path: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = "{}.bak-import-{}-{}".format(path, stamp, time.time_ns() % 1_000_000)
    shutil.copy2(path, dst)
    return dst


def _patch_provider_text(text: str, name: str, base_url: str, ref: str, models: List[str]):
    lines = text.splitlines(keepends=True)
    target = next((i for i, line in enumerate(lines) if re.match(r"^- id:\s*llm-pi-ai\s*$", line.rstrip())), None)
    if target is None:
        raise ValueError("desktop patch 缺少 llm-pi-ai 配置")
    end = next((i for i in range(target + 1, len(lines)) if re.match(r"^- id:\s*", lines[i])), len(lines))
    providers = next((i for i in range(target, end) if re.match(r"^\s{4}providers:\s*$", lines[i].rstrip())), None)
    if providers is None:
        raise ValueError("llm-pi-ai 配置缺少 providers")
    provider_indent = 6
    block_start = block_end = None
    key_re = re.compile(r"^\s{6}([A-Za-z0-9_.-]+):\s*$")
    for i in range(providers + 1, end):
        match = key_re.match(lines[i])
        if match and match.group(1) == name:
            block_start = i
            block_end = next((j for j in range(i + 1, end)
                              if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= provider_indent), end)
            break
    model_lines = "".join("          - id: {}\n            name: {}\n".format(json.dumps(m, ensure_ascii=False), json.dumps(m, ensure_ascii=False)) for m in models)
    block = ("      {}:\n"
             "        apiKeyEnv: {}\n"
             "        api: openai-completions\n"
             "        baseURL: {}\n"
             "        models:\n{}".format(
                 name,
                 json.dumps(ref, ensure_ascii=False),
                 json.dumps(base_url, ensure_ascii=False),
                 model_lines,
             ))
    if block_start is not None:
        lines[block_start:block_end] = [block]
        return "".join(lines), "updated"
    insert_at = next((i for i in range(providers + 1, end)
                      if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= 4), end)
    lines[insert_at:insert_at] = [block]
    return "".join(lines), "added"


def _verify_patch(path: str, name: str, base_url: str, ref: str, models: List[str]) -> None:
    with open(path, encoding="utf-8") as handle:
        data = yaml.load(handle, Loader=_DshLoader) or []
    for entry in data if isinstance(data, list) else []:
        if entry.get("id") == "llm-pi-ai":
            provider = ((entry.get("config") or {}).get("providers") or {}).get(name)
            if provider and provider.get("baseURL") == base_url and provider.get("apiKeyEnv") == ref:
                actual = [m.get("id") if isinstance(m, dict) else str(m) for m in provider.get("models") or []]
                if actual == models:
                    return
    raise ValueError("DSH patch 写入后校验失败")


def import_to_dsh_keys(base_url: str, api_key: str, provider_name: str, models: List[str]) -> Dict:
    """导入 DSH Desktop 的 desktop profile；保留旧接口名以兼容前端调用。"""
    if not api_key:
        return {"ok": False, "error": "API key 为空"}
    name = _safe_provider_name(provider_name)
    base_url = (base_url or "").strip().rstrip("/")
    models = [str(m).strip() for m in (models or []) if str(m).strip()]
    patch_path, creds_path = DSH_PATCH_PATH, DSH_CREDENTIALS_PATH
    if not os.path.isfile(patch_path):
        return {"ok": False, "error": "DSH Desktop patch 不存在", "path": patch_path}
    os.makedirs(os.path.dirname(creds_path), exist_ok=True)
    ref = _credential_ref(name)
    backups = []
    creds_existed = os.path.exists(creds_path)
    try:
        with open(patch_path, encoding="utf-8") as handle:
            patch_text = handle.read()
        new_patch, action = _patch_provider_text(patch_text, name, base_url, ref, models)
        creds = {}
        if os.path.exists(creds_path):
            with open(creds_path, encoding="utf-8") as handle:
                creds = yaml.safe_load(handle) or {}
        if not isinstance(creds, dict):
            raise ValueError("DSH credentials 格式无效")
        refs = creds.setdefault("refs", {})
        if not isinstance(refs, dict):
            raise ValueError("DSH credentials.refs 格式无效")
        refs[ref] = api_key
        new_creds = yaml.safe_dump(creds, allow_unicode=True, sort_keys=False)
        if new_patch != patch_text:
            backups.append(_backup(patch_path))
            _atomic_write(patch_path, new_patch)
        if os.path.exists(creds_path):
            with open(creds_path, encoding="utf-8") as handle:
                old_creds = handle.read()
        else:
            old_creds = ""
        if new_creds != old_creds:
            if os.path.exists(creds_path):
                backups.append(_backup(creds_path))
            _atomic_write(creds_path, new_creds)
        _verify_patch(patch_path, name, base_url, ref, models)
        return {"ok": True, "action": action, "path": patch_path, "provider": name,
                "credential_ref": ref, "backup": backups[-1] if backups else None,
                "verified": True}
    except Exception as exc:
        for backup in reversed(backups):
            original = backup.split(".bak-import-", 1)[0]
            shutil.copy2(backup, original)
        if not creds_existed and os.path.exists(creds_path):
            os.unlink(creds_path)
        return {"ok": False, "error": "DSH 导入失败，已回滚: {}".format(exc), "path": patch_path}


# ========== 新群检测 ==========

def detect_new_groups() -> List[Dict]:
    """检测尚未加入 groups 表的新群。

    只针对真正的群聊（qq / wechat）：
    - web 源（baipiao / welfare 等）是信息流，不是群，不参与“新群”检测
    - 用户点过「稍后处理」的会写进 settings.dismissed_groups，永久不再提示
    """
    conn = get_conn()
    cur = conn.cursor()
    
    dismissed = set()
    cur.execute("SELECT value FROM settings WHERE key = 'dismissed_groups'")
    row = cur.fetchone()
    if row:
        try:
            dismissed = {tuple(x) for x in json.loads(row["value"])}
        except (json.JSONDecodeError, TypeError):
            dismissed = set()
    
    # 查找 raw_messages 中存在但 groups 表中不存在的 (platform, chat_id) 组合
    cur.execute("""
        SELECT r.platform, r.chat_id, r.chat_name,
               COUNT(*) as message_count,
               MAX(r.ts) as last_message_ts
        FROM raw_messages r
        LEFT JOIN groups g ON r.platform = g.platform AND r.chat_id = g.chat_id
        WHERE g.platform IS NULL
          AND r.platform IN ('qq', 'wechat')
          AND r.chat_id IS NOT NULL AND r.chat_id != ''
          AND r.chat_name IS NOT NULL AND r.chat_name != ''
        GROUP BY r.platform, r.chat_id, r.chat_name
        ORDER BY last_message_ts DESC
    """)
    
    rows = cur.fetchall()
    conn.close()
    
    new_groups = []
    for r in rows:
        if (r["platform"], r["chat_id"]) in dismissed:
            continue
        new_groups.append({
            "platform": r["platform"],
            "chat_id": r["chat_id"],
            "chat_name": r["chat_name"] or r["chat_id"],
            "message_count": r["message_count"],
            "last_message_ts": r["last_message_ts"],
            "source": "raw_messages",
            "diagnostic": "待用户确认加入追踪",
        })
    
    return new_groups


def add_group_to_tracking(platform: str, chat_id: str, chat_name: str = None, enabled: bool = True):
    """将新群加入追踪。

    注意 groups 表的列名是 name（不是 chat_name）；已存在时把它重新启用并清掉“已忽略”标记。
    """
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    
    cur.execute("""
        INSERT INTO groups (platform, chat_id, name, enabled, auto_discovered, first_seen, last_seen)
        VALUES (?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(platform, chat_id) DO UPDATE SET
            name = excluded.name,
            enabled = excluded.enabled,
            last_seen = excluded.last_seen
    """, (platform, chat_id, chat_name or chat_id, 1 if enabled else 0, now, now))
    
    # 从“已忽略”里移除，避免加进来又不出现在新群提示里造成困惑
    cur.execute("SELECT value FROM settings WHERE key = 'dismissed_groups'")
    row = cur.fetchone()
    if row:
        try:
            items = [x for x in json.loads(row["value"]) if [x[0], x[1]] != [platform, chat_id]]
            cur.execute("UPDATE settings SET value = ? WHERE key = 'dismissed_groups'",
                        (json.dumps(items, ensure_ascii=False),))
        except (json.JSONDecodeError, TypeError, IndexError):
            pass
    
    conn.commit()
    conn.close()


# ========== 设置管理 ==========

def get_setting(key: str, default=None):
    """获取配置项"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    
    if row:
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]
    return default


def set_setting(key: str, value):
    """设置配置项"""
    conn = get_conn()
    cur = conn.cursor()
    
    value_json = json.dumps(value) if not isinstance(value, str) else value
    
    cur.execute("""
        INSERT OR REPLACE INTO settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
    """, (key, value_json))
    
    conn.commit()
    conn.close()


def list_all_settings() -> Dict:
    """列出所有配置"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    rows = cur.fetchall()
    conn.close()
    
    settings = {}
    for r in rows:
        try:
            settings[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            settings[r["key"]] = r["value"]
    
    return settings


# ========== 新群忽略 ==========

def dismiss_groups(items: List[Dict]) -> int:
    """把用户「稍后处理」的群写进 settings.dismissed_groups，永久不再提示。

    items: [{"platform": "qq", "chat_id": "123"}, ...]
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = 'dismissed_groups'")
    row = cur.fetchone()
    current = []
    if row:
        try:
            current = json.loads(row["value"])
        except json.JSONDecodeError:
            current = []
    
    seen = {tuple(x) for x in current if isinstance(x, (list, tuple)) and len(x) >= 2}
    added = 0
    for it in items:
        key = (it.get("platform"), it.get("chat_id"))
        if key[0] and key[1] and key not in seen:
            current.append([key[0], key[1]])
            seen.add(key)
            added += 1
    
    payload = json.dumps(current, ensure_ascii=False)
    cur.execute("""
        INSERT INTO settings (key, value) VALUES ('dismissed_groups', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (payload,))
    conn.commit()
    conn.close()
    return added


# ========== QQ 登录状态 / 二维码 ==========

import subprocess
import time

_QR_CACHE = {"path": None, "ts": 0.0}
_QR_TMP = "/tmp/groupintel-qr.png"
NAP_CONTAINER = os.getenv("NAP_CONTAINER", "napcat")
DOCKER_BIN = os.getenv("DOCKER_BIN", "/Users/rancho/.orbstack/bin/docker")


def _docker(*args, timeout: int = 20):
    try:
        return subprocess.run([DOCKER_BIN, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess([], 1, "", str(exc))


def qq_login_status() -> Dict:
    """登录状态：优先问 OneBot get_login_info；未登录时看容器里的二维码是否新鲜。"""
    import urllib.request

    base = os.getenv("NAP_HTTP_URL", "http://127.0.0.1:3000").rstrip("/")
    online, uin, nickname = False, 0, ""
    try:
        with urllib.request.urlopen(f"{base}/get_login_info", timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = payload.get("data") or {}
        uin = int(data.get("user_id") or 0)
        nickname = data.get("nickname") or ""
        online = uin > 0 and payload.get("retcode") == 0
    except Exception:
        online = False

    qr_age = None
    res = _docker("exec", NAP_CONTAINER, "sh", "-c",
                  "stat -c %Y /app/napcat/cache/qrcode.png 2>/dev/null")
    if res.returncode == 0 and res.stdout.strip().isdigit():
        qr_age = int(time.time()) - int(res.stdout.strip())

    return {
        "online": online,
        "uin": uin,
        "nickname": nickname,
        "qr_age": qr_age,
        "qr_fresh": qr_age is not None and 0 <= qr_age <= 300,
        "container": NAP_CONTAINER,
    }


def qq_qrcode_path(max_age: int = 5) -> Optional[str]:
    """把容器里的二维码拷到临时文件，返回路径（5 秒缓存，避免每次请求都 docker cp）。"""
    now = time.time()
    if _QR_CACHE["path"] and now - _QR_CACHE["ts"] < max_age and os.path.exists(_QR_TMP):
        return _QR_TMP
    res = _docker("cp", f"{NAP_CONTAINER}:/app/napcat/cache/qrcode.png", _QR_TMP, timeout=20)
    if res.returncode != 0 or not os.path.exists(_QR_TMP) or os.path.getsize(_QR_TMP) == 0:
        return None
    _QR_CACHE.update(path=_QR_TMP, ts=now)
    return _QR_TMP


def qq_restart_container(wait: int = 8) -> Dict:
    """重启 NapCat 容器（二维码过期/卡住时用）。"""
    res = _docker("restart", NAP_CONTAINER, timeout=120)
    ok = res.returncode == 0
    if ok and wait:
        time.sleep(wait)
    return {"ok": ok, "detail": (res.stderr or res.stdout or "").strip()[:300]}
