import json
import re
import time
import httpx

from config import HTTP_TIMEOUT, httpx_kwargs


def _mask(key: str) -> str:
    if not key:
        return "sk-***"
    return key[:6] + "***" + key[-4:] if len(key) > 12 else "sk-***"


def _curl(url: str, key: str, method: str = "GET", body: dict = None) -> str:
    """把真实请求还原成可以直接粘贴到终端的 curl（key 脱敏）"""
    parts = [f"curl -sS -m {int(HTTP_TIMEOUT)}", f"-X {method}", f"-H 'Authorization: Bearer {_mask(key)}'"]
    if body is not None:
        parts.append("-H 'Content-Type: application/json'")
        parts.append("-d '" + json.dumps(body, ensure_ascii=False) + "'")
    parts.append(f"'{url}'")
    return " ".join(parts)


def _trace(step: str, **kw) -> dict:
    return {"step": step, **kw}


def _redact(text: str, key: str) -> str:
    value = str(text or "")
    if key:
        value = value.replace(key, "[REDACTED]")
    return re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|x-api-key\s*[:=]\s*)[^\s,;\"']+",
                  r"\1[REDACTED]", value)


def guess_provider(base_url):
    if not base_url:
        return "unknown"
    d = base_url.lower()
    table = [
        ("openai.com", "OpenAI"),
        ("anthropic.com", "Anthropic"),
        ("googleapis.com", "Google"),
        ("deepseek.com", "DeepSeek"),
        ("moonshot", "Moonshot/Kimi"),
        ("dashscope", "Aliyun/Qwen"),
        ("qwen", "Aliyun/Qwen"),
        ("lanche.website", "gateway/lanche"),
        ("freeapi", "gateway/freeapi"),
        ("huangzihao", "gateway/huangzihao"),
    ]
    for frag, name in table:
        if frag in d:
            return name
    host = re.sub(r"https?://", "", d).split("/")[0]
    return "gateway/" + host


# ---------------------------------------------------------------------------
# 地址可信度：防止把「视频 / 公众号文章 / 网盘分享」当成 API 接口地址
# ---------------------------------------------------------------------------

# 内容站点（文章、视频、动态）——这些域名下的链接不可能是 OpenAI 兼容接口
CONTENT_HOSTS = (
    "mp.weixin.qq.com", "weixin.qq.com", "bilibili.com", "b23.tv",
    "douyin.com", "iesdouyin.com", "kuaishou.com", "youtube.com", "youtu.be",
    "x.com", "twitter.com", "t.me", "weibo.com", "weibo.cn", "zhihu.com",
    "t.cn", "jianshu.com", "csdn.net", "juejin.cn", "xiaohongshu.com",
    "douban.com", "sspai.com", "36kr.com", "huxiu.com", "pan.baidu.com",
    "aliyundrive.com", "alipan.com", "115.com", "lanzou.com", "lanzoui.com",
)

# 路径里出现这些片段，基本是内容页而不是 API 根
CONTENT_PATH_RE = re.compile(r"/(s|article|articles|video|videos|watch|share|post|posts|p|note|notes|status)/", re.I)


def is_content_url(base_url: str) -> bool:
    """是不是内容页（视频/文章/分享）而不是 API 接口地址。"""
    if not base_url:
        return False
    u = str(base_url).strip().lower()
    if not re.match(r"^https?://", u):
        return False
    host = re.sub(r"^https?://", "", u).split("/")[0].split(":")[0]
    if any(host == h or host.endswith("." + h) for h in CONTENT_HOSTS):
        return True
    return bool(CONTENT_PATH_RE.search(u))


def is_plausible_api_url(base_url: str) -> bool:
    """接口地址必须带 scheme + 带点的域名；排除内容页。"""
    if not base_url:
        return True  # 空串表示「按默认 api.openai.com」，允许
    u = str(base_url).strip()
    if not re.match(r"^https?://", u):
        return False
    host = re.sub(r"^https?://", "", u).split("/")[0].split(":")[0].lower()
    if "." not in host or host.endswith("."):
        return False
    return not is_content_url(u)


def _is_json_response(r) -> bool:
    """HTTP 200 不代表是 API：很多网页也返回 200。要求 JSON。"""
    ct = (r.headers.get("content-type") or "").lower()
    text = (r.text or "").lstrip()[:1]
    if "json" not in ct and text not in ("{", "["):
        return False
    try:
        r.json()
        return True
    except Exception:
        return False


def _models_from(r):
    try:
        data = r.json()
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("data")
    if isinstance(data, list):
        return [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
    return []


def probe(base_url: str, api_key: str):
    """OpenAI 兼容连通性测试。

    双重校验，避免把网页/视频/公众号链接误判成可用接口：
    1. 地址形态必须像 API（排除内容页）；
    2. 响应必须是 JSON（/models 的 data 列表，或 chat/completions 的 choices）。
    """
    if not api_key:
        return {"ok": False, "provider": guess_provider(base_url), "models": [],
                "latency_ms": 0, "error": "缺少 api_key"}
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return {"ok": False, "provider": "unknown", "models": [],
                "latency_ms": 0, "error": "请填写自定义 Base URL；不会默认连接 OpenAI 官方 endpoint"}
    if not is_plausible_api_url(base_url):
        return {"ok": False, "provider": guess_provider(base_url), "models": [],
                "latency_ms": 0, "error": "接口地址不像 API（疑似视频/文章/分享链接）"}
    endpoint_suffix = "/chat/completions"
    if base_url.lower().endswith(endpoint_suffix):
        api_root = base_url[:-len(endpoint_suffix)].rstrip("/")
    else:
        api_root = base_url
    if not re.search(r"/v\d+[a-z0-9]*$", api_root, re.I):
        api_root += "/v1"
    models_url = api_root + "/models"
    chat_url = base_url if base_url.lower().endswith(endpoint_suffix) else api_root + endpoint_suffix
    headers = {"Authorization": f"Bearer {api_key}"}
    start = time.time()
    trace = []
    last_error = ""
    curl_models = _curl(models_url, api_key)

    try:
        r = httpx.get(
            models_url, headers=headers, timeout=HTTP_TIMEOUT, **httpx_kwargs()
        )
        trace.append(_trace("GET /v1/models", status=r.status_code,
                            ms=int((time.time() - start) * 1000),
                            body=_redact((r.text or "")[:300], api_key)))
        if r.status_code == 200:
            if _is_json_response(r):
                models = _models_from(r)
                data = r.json() if isinstance(r.json(), dict) else {}
                if "data" in data or models:
                    return {
                        "ok": True,
                        "provider": guess_provider(base_url),
                        "models": models,
                        "latency_ms": int((time.time() - start) * 1000),
                        "curl": curl_models,
                        "trace": trace,
                    }
                last_error = "响应是 JSON 但没有 models 列表"
            else:
                last_error = "响应是网页/非 JSON（HTTP 200 但不是 API）"
        else:
            last_error = f"HTTP {r.status_code}"
    except Exception as e:
        last_error = _redact(str(e), api_key)[:200]
        trace.append(_trace("GET /v1/models", error=last_error))

    body = {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
    curl_chat = _curl(chat_url, api_key, "POST", body)
    try:
        r = httpx.post(
            chat_url,
            headers={**headers, "Content-Type": "application/json"},
            json=body,
            timeout=HTTP_TIMEOUT,
            **httpx_kwargs(),
        )
        trace.append(_trace("POST /v1/chat/completions", status=r.status_code,
                            ms=int((time.time() - start) * 1000),
                            body=_redact((r.text or "")[:300], api_key)))
        if r.status_code == 200 and _is_json_response(r):
            obj = r.json()
            if isinstance(obj, dict) and ("choices" in obj or "error" not in obj):
                return {
                    "ok": True,
                    "provider": guess_provider(base_url),
                    "models": [],
                    "latency_ms": int((time.time() - start) * 1000),
                    "curl": curl_chat,
                    "trace": trace,
                }
            last_error = "响应是 JSON 但不是 chat/completions 结构"
        elif r.status_code == 200:
            last_error = "响应是网页/非 JSON（HTTP 200 但不是 API）"
        else:
            last_error = f"HTTP {r.status_code}"
    except Exception as e:
        last_error = _redact(str(e), api_key)[:200]
        trace.append(_trace("POST /v1/chat/completions", error=last_error))

    return {
        "ok": False,
        "provider": guess_provider(base_url),
        "models": [],
        "latency_ms": int((time.time() - start) * 1000),
        "curl": curl_chat,
        "trace": trace,
        "error": last_error or "接口不可用",
    }
