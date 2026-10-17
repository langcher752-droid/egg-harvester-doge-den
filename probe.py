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


def probe(base_url: str, api_key: str):
    """OpenAI 兼容连通性测试。优先 /v1/models，失败降级 /v1/chat/completions。"""
    if not api_key:
        return {"ok": False, "provider": guess_provider(base_url), "models": [], "latency_ms": 0}
    base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
    models_url = base_url if base_url.endswith("/v1") else base_url + "/v1"
    headers = {"Authorization": f"Bearer {api_key}"}
    start = time.time()
    trace = []
    curl_models = _curl(f"{models_url}/models", api_key)

    try:
        r = httpx.get(
            f"{models_url}/models", headers=headers, timeout=HTTP_TIMEOUT, **httpx_kwargs()
        )
        trace.append(_trace("GET /v1/models", status=r.status_code,
                            ms=int((time.time() - start) * 1000),
                            body=(r.text or "")[:300]))
        if r.status_code == 200:
            try:
                models = [m.get("id") for m in r.json().get("data", [])]
            except Exception:
                models = []
            return {
                "ok": True,
                "provider": guess_provider(base_url),
                "models": models,
                "latency_ms": int((time.time() - start) * 1000),
                "curl": curl_models,
                "trace": trace,
            }
    except Exception as e:
        trace.append(_trace("GET /v1/models", error=str(e)[:200]))

    body = {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
    curl_chat = _curl(f"{models_url}/chat/completions", api_key, "POST", body)
    try:
        r = httpx.post(
            f"{models_url}/chat/completions",
            headers={**headers, "Content-Type": "application/json"},
            json=body,
            timeout=HTTP_TIMEOUT,
            **httpx_kwargs(),
        )
        trace.append(_trace("POST /v1/chat/completions", status=r.status_code,
                            ms=int((time.time() - start) * 1000),
                            body=(r.text or "")[:300]))
        if r.status_code == 200:
            return {
                "ok": True,
                "provider": guess_provider(base_url),
                "models": [],
                "latency_ms": int((time.time() - start) * 1000),
                "curl": curl_chat,
                "trace": trace,
            }
    except Exception as e:
        trace.append(_trace("POST /v1/chat/completions", error=str(e)[:200]))

    return {
        "ok": False,
        "provider": guess_provider(base_url),
        "models": [],
        "latency_ms": int((time.time() - start) * 1000),
        "curl": curl_chat,
        "trace": trace,
    }
