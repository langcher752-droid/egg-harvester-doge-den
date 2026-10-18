"""供应商体检：把 DSH / WorkBuddy / CCswitch 里配置的 provider 统一列出来，逐个测连通性，
失败的可删（删除前自动备份，改完用 yaml/json 重新解析校验）。

三类来源：
  dsh        ~/.dsh/profiles/<profile>/cordis.patch.yml   真正的 provider 清单（DSH Desktop 就在这里，
                                                          一条 patch 的 config.providers.{name} = {displayName, apiKeyEnv, baseURL, api, models}）
             ~/.dsh/settings*.yaml                        老格式兼容（providers: {name: {...}}）
  workbuddy  ~/.workbuddy/models.json       [{id, name, url, apiKey}]（按 url+key 归组）
  ccswitch   ~/.cc-switch/cc-switch.db      providers 表，settings_config 里 env/config 带 base_url + key
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from typing import Dict, List, Optional

import httpx
import yaml

HOME = os.path.expanduser("~")
DSH_DIR = os.path.join(HOME, ".dsh")
WORKBUDDY_MODELS = os.path.join(HOME, ".workbuddy", "models.json")
CCSWITCH_DB = os.path.join(HOME, ".cc-switch", "cc-switch.db")
DSH_CREDS = os.path.join(DSH_DIR, ".credentials.yaml")

TIMEOUT = 8.0

# 很多中转站挂在 Cloudflare 后面，默认 python-httpx 的 UA 会直接吃 403 HTML 假页面，
# 这里伪装成普通浏览器，避免「明明能用的 key 被判定失效」。
_UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}


def _models_url(base: str) -> str:
    """把 baseURL 拼成 /models 地址。

    兼容三种写法：
      https://x.com/v1   → https://x.com/v1/models
      https://x.com/v1/  → https://x.com/v1/models   （旧实现会拼成 /v1//v1/models）
      https://x.com      → https://x.com/v1/models
      https://x.com/api/paas/v4 → https://x.com/api/paas/v4/models
    """
    b = (base or "").strip().rstrip("/")
    if not b:
        return ""
    low = b.lower()
    if low.endswith("/models"):
        return b
    # 路径里已经带版本号（/v1 /v2 /v4 /v1beta ...）就只补 /models
    if re.search(r"/v\d+[a-z0-9]*$", low):
        return b + "/models"
    return b + "/v1/models"

DSH_PROFILES = os.path.join(DSH_DIR, "profiles")


class _DshLoader(yaml.SafeLoader):
    """DSH 的 cordis.patch.yml 里有 !!js / tag:yaml.org,2002:js 表达式，SafeLoader 会直接报错。"""


_DshLoader.add_multi_constructor("tag:yaml.org,2002:js", lambda loader, suffix, node: None)
_DshLoader.add_multi_constructor("!js", lambda loader, suffix, node: None)


def _load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=_DshLoader)


def _mask(key: Optional[str]) -> str:
    if not key:
        return ""
    k = str(key)
    if len(k) <= 10:
        return k[:2] + "***"
    return k[:6] + "***" + k[-4:]


def _clean_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    # ccswitch 有时填的是完整接口地址
    u = re.sub(r"/chat/completions$", "", u)
    u = re.sub(r"/messages$", "", u)
    return u


def _dsh_creds() -> Dict[str, str]:
    try:
        with open(DSH_CREDS, "r", encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        refs = d.get("refs") or {}
        return {str(k): str(v) for k, v in refs.items() if v}
    except Exception:
        return {}


# ---------------- 列出来源 ----------------

def _dsh_provider_entry(name: str, cfg: Dict, creds: Dict[str, str],
                        profile: str, file_path: str, kind: str) -> Optional[Dict]:
    if not isinstance(cfg, dict):
        return None
    base = cfg.get("baseURL") or cfg.get("base_url") or ""
    if not base:
        return None
    key = cfg.get("apiKey") or ""
    env_name = cfg.get("apiKeyEnv") or ""
    if env_name and not key:
        key = os.getenv(env_name) or creds.get(env_name, "")
    api = str(cfg.get("api") or "")
    models = [m.get("id") if isinstance(m, dict) else str(m)
              for m in (cfg.get("models") or [])]
    display = cfg.get("displayName") or name

    if kind == "patch":
        pid = f"dsh:{profile}:{name}"
        label = f"DSH · {profile}"
    else:
        pid = f"dsh:{profile}:{name}"
        label = f"DSH · {profile}"

    return {
        "id": pid,
        "source": "dsh",
        "source_label": label,
        "profile": profile,
        "name": str(name),
        "display_name": str(display),
        "base_url": _clean_base(str(base)),
        "api_key": key,
        "api_key_masked": _mask(key),
        "api_style": "anthropic" if "anthropic" in api.lower() else "openai",
        "key_env": env_name,
        "models": models[:12],
        "model_count": len(models),
        "file": file_path,
        "kind": kind,
        "deletable": True,
    }


def _walk_providers(node, hits: List[Dict]):
    """在任意嵌套结构里找 providers: {...}，记录所在的 patch 条目名。"""
    if isinstance(node, dict):
        provs = node.get("providers")
        if isinstance(provs, dict):
            hits.append({"providers": provs, "name": node.get("name") or "", "config": node})
        for k, v in node.items():
            if k == "providers":
                continue
            _walk_providers(v, hits)
    elif isinstance(node, list):
        for v in node:
            _walk_providers(v, hits)


def list_dsh() -> List[Dict]:
    """DSH 的 provider 有两个来源：
    1) profiles/<name>/cordis.patch.yml —— DSH Desktop 真正在用的清单（每个 profile 一份）
    2) settings*.yaml —— 老版单文件配置，保留兼容
    """
    out: List[Dict] = []
    creds = _dsh_creds()
    seen = set()

    if os.path.isdir(DSH_PROFILES):
        for profile in sorted(os.listdir(DSH_PROFILES)):
            patch = os.path.join(DSH_PROFILES, profile, "cordis.patch.yml")
            if not os.path.isfile(patch):
                continue
            try:
                data = _load_yaml(patch)
            except Exception:
                continue
            hits: List[Dict] = []
            _walk_providers(data, hits)
            for hit in hits:
                for name, cfg in hit["providers"].items():
                    entry = _dsh_provider_entry(name, cfg, creds, profile, patch, "patch")
                    if not entry:
                        continue
                    dedup = (entry["base_url"], entry["api_key"], entry["name"])
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    out.append(entry)

    if os.path.isdir(DSH_DIR):
        for fn in sorted(os.listdir(DSH_DIR)):
            if not (fn.startswith("settings") and fn.endswith((".yaml", ".yml"))):
                continue
            path = os.path.join(DSH_DIR, fn)
            try:
                data = _load_yaml(path)
            except Exception:
                continue
            providers = (data or {}).get("providers") or {}
            if not isinstance(providers, dict):
                continue
            for name, cfg in providers.items():
                entry = _dsh_provider_entry(name, cfg, creds, fn, path, "settings")
                if not entry:
                    continue
                dedup = (entry["base_url"], entry["api_key"], entry["name"])
                if dedup in seen:
                    continue
                seen.add(dedup)
                out.append(entry)

    # desktop profile 排在最前（DSH Desktop 当前在用的就是它）
    out.sort(key=lambda p: (0 if p.get("profile") == "desktop" else 1, p.get("profile") or "", p["name"]))
    return out


def list_workbuddy() -> List[Dict]:
    out = []
    if not os.path.isfile(WORKBUDDY_MODELS):
        return out
    try:
        with open(WORKBUDDY_MODELS, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return out
    if not isinstance(items, list):
        return out

    groups: Dict[tuple, List[Dict]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        base = _clean_base(it.get("url") or "")
        key = it.get("apiKey") or ""
        if not base:
            continue
        groups.setdefault((base, key), []).append(it)

    for (base, key), group in groups.items():
        out.append({
            "id": f"workbuddy:{abs(hash((base, key))) % 10**10}",
            "source": "workbuddy",
            "source_label": "WorkBuddy · models.json",
            "name": f"{len(group)} 个模型",
            "base_url": base,
            "api_key": key,
            "api_key_masked": _mask(key),
            "api_style": "openai",
            "models": [g.get("id") for g in group][:12],
            "model_count": len(group),
            "file": WORKBUDDY_MODELS,
            "deletable": True,
        })
    return out


def _ccswitch_rows():
    if not os.path.isfile(CCSWITCH_DB):
        return []
    con = sqlite3.connect(CCSWITCH_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, app_type, name, settings_config, website_url FROM providers"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _parse_ccswitch(settings_config: str):
    """从 ccswitch 的 settings_config 里抠出 base_url / api_key。"""
    base = key = ""
    try:
        cfg = json.loads(settings_config or "{}")
    except Exception:
        cfg = {}
    env = cfg.get("env") or {}
    for k, v in env.items():
        kl = k.upper()
        if "BASE_URL" in kl or kl.endswith("_URL"):
            base = base or str(v)
        if "API_KEY" in kl or "AUTH_TOKEN" in kl:
            key = key or str(v)
    if not base:
        raw = cfg.get("config") or ""
        m = re.search(r'base_url\s*=\s*"([^"]+)"', raw)
        if m:
            base = m.group(1)
    if not key:
        auth = cfg.get("auth") or {}
        for k, v in auth.items():
            if "API_KEY" in k.upper() or "TOKEN" in k.upper():
                key = key or str(v)
                break
    return _clean_base(base), key


def list_ccswitch() -> List[Dict]:
    out = []
    for r in _ccswitch_rows():
        base, key = _parse_ccswitch(r.get("settings_config") or "")
        if not base:
            continue
        out.append({
            "id": f"ccswitch:{r['id']}",
            "source": "ccswitch",
            "source_label": f"CCswitch · {r.get('app_type')}",
            "name": r.get("name") or r.get("id"),
            "base_url": base,
            "api_key": key,
            "api_key_masked": _mask(key),
            "api_style": "anthropic" if "anthropic" in (r.get("app_type") or "") else "openai",
            "models": [],
            "file": CCSWITCH_DB,
            "row_id": r["id"],
            "deletable": True,
        })
    return out


def list_all() -> Dict:
    diagnostics = []
    loaders = (("dsh", list_dsh, DSH_DIR), ("workbuddy", list_workbuddy, WORKBUDDY_MODELS),
               ("ccswitch", list_ccswitch, CCSWITCH_DB))
    loaded = {}
    for source, loader, path in loaders:
        try:
            loaded[source] = loader()
            exists = os.path.exists(path)
            diagnostics.append({"source": source, "path": path, "status": "ok" if exists else "missing",
                                "count": len(loaded[source]), "error": None if exists else "配置来源不存在"})
        except Exception as exc:
            loaded[source] = []
            diagnostics.append({"source": source, "path": path, "status": "error", "count": 0,
                                "error": f"{type(exc).__name__}: 解析失败"})
    items = loaded["dsh"] + loaded["workbuddy"] + loaded["ccswitch"]
    apply_aliases(items)
    return {
        "count": len(items),
        "providers": items,
        "sources": {k: len(v) for k, v in loaded.items()},
        "diagnostics": diagnostics,
    }


# ---------------- 测连通性 ----------------

def _pick_models(p: Dict) -> List[str]:
    models = [m for m in (p.get("models") or []) if m]
    if models:
        return models
    return []


def test_one(p: Dict) -> Dict:
    """返回 ok / status / latency_ms / error / models / curl。"""
    base = p.get("base_url") or ""
    key = p.get("api_key") or ""
    style = p.get("api_style") or "openai"
    if not base:
        return {**p, "ok": False, "error": "没有 baseURL"}
    if not key:
        env = p.get("key_env") or ""
        hint = f"（{env} 在 ~/.dsh/.credentials.yaml 里没有值）" if env else ""
        return {**p, "ok": False, "status": 0, "latency_ms": 0,
                "error": f"未配置 API Key{hint}", "curl": "", "trace": []}
    url = _models_url(base)
    if style == "anthropic":
        headers = {**_UA_HEADERS, "x-api-key": key, "anthropic-version": "2023-06-01"}
        curl = f"curl -sS -m {int(TIMEOUT)} '{url}' -H 'x-api-key: {_mask(key)}' -H 'anthropic-version: 2023-06-01'"
    else:
        headers = {**_UA_HEADERS, "Authorization": f"Bearer {key}"}
        curl = f"curl -sS -m {int(TIMEOUT)} '{url}' -H 'Authorization: Bearer {_mask(key)}'"

    t0 = time.time()
    try:
        r = httpx.get(url, headers=headers, timeout=TIMEOUT, follow_redirects=True)
        ms = int((time.time() - t0) * 1000)
        body = (r.text or "")[:400]
        ok = r.status_code == 200
        models = []
        if ok:
            try:
                data = r.json()
                raw = data.get("data") if isinstance(data, dict) else data
                if isinstance(raw, list):
                    models = [(m.get("id") if isinstance(m, dict) else str(m)) for m in raw][:30]
            except Exception:
                pass
        return {**p, "ok": ok, "status": r.status_code, "latency_ms": ms,
                "models_live": models or _pick_models(p), "error": "" if ok else body[:200],
                "curl": curl}
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return {**p, "ok": False, "status": 0, "latency_ms": ms,
                "error": f"{type(e).__name__}: {e}"[:200], "curl": curl}


def test_all(ids: Optional[List[str]] = None) -> Dict:
    items = list_all()["providers"]
    if ids:
        want = set(ids)
        items = [p for p in items if p["id"] in want]
    results = [test_one(p) for p in items]
    bad = [r for r in results if not r["ok"]]
    return {
        "count": len(results),
        "ok_count": len(results) - len(bad),
        "bad_count": len(bad),
        "results": results,
    }


# ---------------- 删除 ----------------

def _backup(path: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-provider-{ts}"
    shutil.copy2(path, dst)
    return dst


def _count_providers(data) -> int:
    hits: List[Dict] = []
    _walk_providers(data, hits)
    return sum(len(h["providers"]) for h in hits)


def _has_provider(data, name: str) -> bool:
    hits: List[Dict] = []
    _walk_providers(data, hits)
    return any(name in h["providers"] for h in hits)


def _delete_dsh(prov_name: str, file_path: str) -> Dict:
    """按缩进块删掉 YAML 里的 providers.<name>。

    同时支持两种布局：
      settings*.yaml        providers:            (缩进 0)
      cordis.patch.yml      ... config: → providers:  (缩进 4)
    删完用带 !!js 支持的 loader 重新解析，provider 数必须正好少 1，否则回滚。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    try:
        before = _count_providers(_load_yaml(file_path))
    except Exception:
        before = None

    out: List[str] = []
    i = 0
    removed = False
    providers_indent = None
    while i < len(lines):
        line = lines[i]
        m_prov = re.match(r"^(\s*)providers:\s*$", line)
        if m_prov:
            providers_indent = len(m_prov.group(1))
            out.append(line)
            i += 1
            continue

        if providers_indent is not None:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= providers_indent:
                # 离开 providers 块
                providers_indent = None
            else:
                key_indent = providers_indent + 2
                m_key = re.match(r"^(\s+)([A-Za-z0-9_.\-]+):\s*$", line)
                if m_key and len(m_key.group(1)) == key_indent and m_key.group(2) == prov_name:
                    # 吃掉整个 provider 块
                    i += 1
                    while i < len(lines):
                        nxt = lines[i]
                        if nxt.strip() == "":
                            i += 1
                            continue
                        nindent = len(nxt) - len(nxt.lstrip())
                        if nindent > key_indent:
                            i += 1
                            continue
                        break
                    removed = True
                    continue

        out.append(line)
        i += 1

    if not removed:
        return {"ok": False, "error": f"没找到 provider: {prov_name}"}

    backup = _backup(file_path)
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(out)

    try:
        after = _count_providers(_load_yaml(file_path))
        if _has_provider(_load_yaml(file_path), prov_name):
            raise ValueError("provider 仍然存在")
        if before is not None and after != before - 1:
            raise ValueError(f"provider 数量不对（{before} → {after}）")
    except Exception as e:
        shutil.copy2(backup, file_path)
        return {"ok": False, "error": f"改完 YAML 校验失败，已回滚: {e}"}

    return {"ok": True, "file": file_path, "backup": backup, "deleted": 1}


def _delete_workbuddy(base_url: str, api_key: str) -> Dict:
    with open(WORKBUDDY_MODELS, "r", encoding="utf-8") as f:
        items = json.load(f)
    keep = [it for it in items
            if not (isinstance(it, dict)
                    and _clean_base(it.get("url") or "") == base_url
                    and (it.get("apiKey") or "") == api_key)]
    if len(keep) == len(items):
        return {"ok": False, "error": "没找到对应 provider"}
    backup = _backup(WORKBUDDY_MODELS)
    with open(WORKBUDDY_MODELS, "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, indent=2)
    return {"ok": True, "deleted": len(items) - len(keep), "backup": backup}


def _delete_ccswitch(row_id: str) -> Dict:
    backup = _backup(CCSWITCH_DB)
    con = sqlite3.connect(CCSWITCH_DB)
    try:
        cur = con.execute("DELETE FROM providers WHERE id=?", (row_id,))
        con.commit()
        n = cur.rowcount
    finally:
        con.close()
    return {"ok": n > 0, "deleted": n, "backup": backup}


def delete_provider(pid: str) -> Dict:
    items = {p["id"]: p for p in list_all()["providers"]}
    p = items.get(pid)
    if not p:
        return {"ok": False, "error": "provider 不存在（可能已被删）"}
    src = p["source"]
    if src == "dsh":
        return _delete_dsh(p["name"], p["file"])
    if src == "workbuddy":
        return _delete_workbuddy(p["base_url"], p.get("api_key") or "")
    if src == "ccswitch":
        return _delete_ccswitch(p["row_id"])
    return {"ok": False, "error": f"未知来源: {src}"}


# ---------------- 别名 / 备注（存本地，不动第三方配置文件） ----------------

ALIAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "provider_aliases.json")


def _read_aliases() -> Dict:
    try:
        with open(ALIAS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_aliases(d: Dict) -> None:
    os.makedirs(os.path.dirname(ALIAS_FILE), exist_ok=True)
    tmp = ALIAS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ALIAS_FILE)


def apply_aliases(items: List[Dict]) -> List[Dict]:
    """给列表附上 alias/note/original_name；有别名时覆盖 display_name。"""
    aliases = _read_aliases()
    for p in items:
        a = aliases.get(p.get("id")) or {}
        alias = str(a.get("alias") or "").strip()
        note = str(a.get("note") or "").strip()
        p["original_name"] = p.get("display_name") or p.get("name") or ""
        p["alias"] = alias
        p["note"] = note
        if alias:
            p["display_name"] = alias
    return items


def set_alias(provider_id: str, alias: str, note: str) -> Dict:
    """设置/清除别名与备注；两者都空则删除记录。"""
    d = _read_aliases()
    alias = (alias or "").strip()
    note = (note or "").strip()
    if alias or note:
        d[provider_id] = {"alias": alias, "note": note}
    else:
        d.pop(provider_id, None)
    _write_aliases(d)
    return {"alias": alias, "note": note}
