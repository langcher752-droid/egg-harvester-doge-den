import re
import json
import base64
import httpx

from config import GATEWAY_BASE, GATEWAY_KEY, VISION_MODEL, HTTP_TIMEOUT, httpx_kwargs
from probe import is_content_url, is_plausible_api_url

# 常见密钥前缀
KEY_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"AIza[A-Za-z0-9_-]{20,}",
    r"pk_live_[A-Za-z0-9]{16,}",
    r"xai-[A-Za-z0-9]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"gsk_[A-Za-z0-9]{20,}",
]
BASEURL_RE = re.compile(r"https?://[^\s|#]+", re.I)
GENERIC_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
PAIR_RE = re.compile(r"(https?://\S+?)\s*[\|#:@=]{1,3}\s*([A-Za-z0-9_\-]{16,})")


def extract_from_text(text: str):
    """从文本提取候选 (base_url, api_key) 对。

    规则：仅当 base_url 与 key 出现在同一行（显式分隔符或同行邻近）才绑定，
    避免把裸 key 错配到无关的地址。
    """
    candidates = []

    for line in text.splitlines():
        # 1) 显式配对：base_url|key / base_url###key / base_url::key / base_url=key
        m = PAIR_RE.search(line)
        if m:
            candidates.append({"base_url": m.group(1).rstrip("/"), "api_key": m.group(2)})
            continue

        # 2) 同行内的 key + 邻近 url
        line_urls = BASEURL_RE.findall(line)
        line_keys = []
        for p in KEY_PATTERNS:
            line_keys += re.findall(p, line)
        blob = " ".join(line_urls)
        line_keys += [t for t in GENERIC_TOKEN_RE.findall(line) if t not in blob]
        for k in line_keys:
            paired = line_urls[0] if line_urls else None
            candidates.append({"base_url": paired, "api_key": k})

    # 去重 + 丢掉明显不是接口的地址（视频/公众号文章/网盘分享）
    seen = set()
    out = []
    for c in candidates:
        if not c.get("api_key"):
            continue
        base = c.get("base_url")
        if base and (is_content_url(base) or not is_plausible_api_url(base)):
            continue
        sig = (base, c["api_key"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(c)
    return out


def extract_from_image(image_bytes: bytes, mime: str = "image/png"):
    """用视觉 LLM 从截图提取 {base_url, api_key} 列表。失败返回 []。"""
    if not GATEWAY_KEY:
        return []
    b64 = base64.b64encode(image_bytes).decode()
    data_uri = f"data:{mime};base64,{b64}"
    prompt = (
        "从这张图片中提取所有 API key / 接口地址对。"
        "返回 JSON 数组，每项格式 {\"base_url\": 接口地址或空字符串, \"api_key\": 密钥}。"
        "只输出 JSON，不要解释或代码块标记。如果没有，返回 []。"
    )
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "temperature": 0,
    }
    try:
        r = httpx.post(
            f"{GATEWAY_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GATEWAY_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=HTTP_TIMEOUT,
            **httpx_kwargs(),
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        candidates = _parse_json_array(content)
        return [
            c for c in candidates
            if isinstance(c, dict) and c.get("api_key")
            and (not c.get("base_url") or is_plausible_api_url(c.get("base_url")))
        ]
    except Exception:
        return []


def _parse_json_array(content: str):
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\[.*\]", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return []
        return []
