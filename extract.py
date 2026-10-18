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
_GROUP_RE = re.compile(r"(?m)^\s*(?:【\s*第\s*\d+\s*组\s*】|第\s*\d+\s*组\s*(?:[：:]|$))")
_MASKED_KEY_RE = re.compile(r"(?:sk(?:-ant)?|gsk|AIza|pk_live_|xai-|AKIA)[A-Za-z0-9_-]*\*{2,}[A-Za-z0-9_*.-]*", re.I)
_EXPIRED_RE = re.compile(r"用不了|不能用|已失效|失效|G\s*了|挂了|跑路|封了|废了|过期", re.I)
_B64_MARKER_RE = re.compile(r"(?:jia\s*mi\s*base\s*64|加密\s*base\s*64|加密base64|base\s*64(?:编码)?)", re.I)
_BASE_MARKER_RE = re.compile(r"(?:base\s*url|baseurl|base_url|接口地址|api地址|api\s*地址|endpoint)", re.I)


def _strict_digest_key(raw: str):
    value = re.sub(r"\s+", "", raw or "")
    if not value or len(value) % 4 or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value):
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None
    if _MASKED_KEY_RE.search(decoded) or not any(re.fullmatch(p, decoded) for p in KEY_PATTERNS):
        return None
    return decoded


def _next_nonempty(lines, index):
    index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return (lines[index].strip(), index + 1) if index < len(lines) else ("", index)


def _extract_long_group(body: str):
    lines = body.splitlines()
    base_url = encoded = source_note = None
    models = []
    metadata = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = _BASE_MARKER_RE.search(line)
        if match:
            inline = line[match.end():].lstrip(" \t:：=|")
            value, i = (inline, i + 1) if inline else _next_nonempty(lines, i)
            url = BASEURL_RE.search(value)
            if url:
                base_url = url.group(0).rstrip(".,;:，。；、)】>")
            continue
        match = _B64_MARKER_RE.search(line)
        if match:
            inline = line[match.end():].lstrip(" \t:：=|")
            encoded, i = (inline, i + 1) if inline else _next_nonempty(lines, i)
            continue
        if re.search(r"可用模型|models?", line, re.I):
            value = re.sub(r"^.*?(?:可用模型|models?)\s*[：:]?", "", line, flags=re.I).strip()
            if not value:
                value, i = _next_nonempty(lines, i)
            if value:
                models.extend(x.strip(" ，,、") for x in re.split(r"[,，、\s]+", value) if x.strip(" ，,、"))
            continue
        for label, key in (("截止日期", "expires"), ("账号余额", "balance"), ("获取时间", "observed_at"), ("贡献者", "contributor")):
            if line.startswith(label):
                value = re.sub(rf"^{label}\s*[：:]?", "", line).strip()
                if not value:
                    value, i = _next_nonempty(lines, i)
                metadata[key] = value
                break
        if any(line.startswith(label) for label, _ in (("截止日期", "expires"), ("账号余额", "balance"), ("获取时间", "observed_at"), ("贡献者", "contributor"))):
            continue
        if not source_note and re.search(r"备注|说明|note|remark|失效|过期|用不了|不能用|G\s*了", line, re.I):
            source_note = line
        i += 1
    key = _strict_digest_key(encoded)
    if not key:
        return None
    result = {"base_url": base_url, "api_key": key}
    if models:
        result["models"] = list(dict.fromkeys(models))
    result.update(metadata)
    if source_note:
        result["note"] = source_note
        if _EXPIRED_RE.search(source_note):
            result["reported_status"] = "possibly_expired"
    return result


def _extract_long_digest(text: str):
    matches = list(_GROUP_RE.finditer(text))
    chunks = [text[m.end():(matches[n + 1].start() if n + 1 < len(matches) else len(text))]
              for n, m in enumerate(matches)] if matches else [text]
    records, encoded_values = [], set()
    for chunk in chunks:
        record = _extract_long_group(chunk)
        if record:
            records.append(record)
        lines = chunk.splitlines()
        for n, line in enumerate(lines):
            if _B64_MARKER_RE.search(line):
                value, _ = _next_nonempty(lines, n)
                if value:
                    encoded_values.add(value)
    return records, encoded_values


def extract_from_text(text: str):
    """从文本提取候选 (base_url, api_key) 对。

    规则：仅当 base_url 与 key 出现在同一行（显式分隔符或同行邻近）才绑定，
    避免把裸 key 错配到无关的地址。
    """
    long_candidates, encoded_values = _extract_long_digest(text)
    candidates = list(long_candidates)

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
        line_keys += [t for t in GENERIC_TOKEN_RE.findall(line) if t not in blob and t not in encoded_values]
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
