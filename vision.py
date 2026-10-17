"""视觉理解层：全量理解 + 分级。

策略（用户拍板）：
  每张图都过视觉模型，输出分两档：
    - 表情包/无信息量 → 一行语义标签（情绪 + 语境）
    - 截图/表格/二维码/文档 → 分类 + 详细记录（进简报）

鸡蛋提取内嵌：识别到的 key 走 probe 连通性测试，通过才入库。
"""
import base64
import json
import re

import httpx

from config import GATEWAY_BASE, GATEWAY_KEY, VISION_MODEL, HTTP_TIMEOUT, httpx_kwargs

# 单图上限（base64 后体积膨胀 1.33 倍，超大 gif 直接跳过）
MAX_IMAGE_BYTES = 6 * 1024 * 1024

PROMPT = """分析这张群聊图片，只输出 JSON（无代码块标记、无解释）：
{"category":"...","label":"...","detail":"...","keys":[...]}

category 取值：
  sticker   表情包/动画表情/梗图（无信息量，只表情绪）
  key       含 API key / 接口地址 / 密钥的截图（最高价值）
  info      其他有信息量截图（模型清单、教程、优惠、公告、代码、配置等）
  qrcode    二维码
  other     以上都不是

label：一句话。表情包写「情绪+语境」（如「大笑，回应上文翻车」）；截图写内容概述。
detail：仅 info/key 类需要——把图中关键信息完整抄录（模型列表、教程步骤、优惠内容、接口地址、密钥等），中文。
keys：图中若有 API key，逐个列出 {"base_url":"接口地址或空串","api_key":"密钥"}；没有则 []。"""


def analyze_image(image_bytes: bytes, mime: str = "image/png"):
    """一次视觉调用：分级 + 语义标签 + key 提取。失败返回 None。"""
    if not GATEWAY_KEY:
        return None
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return {"category": "other", "label": f"[超大图 {len(image_bytes)//1024//1024}MB 未识别]", "detail": "", "keys": []}
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}],
        "temperature": 0,
    }
    try:
        r = httpx.post(
            f"{GATEWAY_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            json=payload, timeout=HTTP_TIMEOUT, **httpx_kwargs(),
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return _parse_json(content)
    except Exception:
        return None


def _parse_json(content: str):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def mime_of(rel_path: str) -> str:
    p = str(rel_path).lower()
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".gif"):
        return "image/gif"
    if p.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


# category → 中文标签前缀
TAG = {"sticker": "表情包", "key": "KEY截图", "info": "情报截图", "qrcode": "二维码", "other": "图片"}


def process_media(platform, chat_id, msg_key, rel_path):
    """对已落盘媒体做视觉理解 + key 提取验证入库。QQ/微信共用。"""
    import os
    from config import MEDIA_DIR
    import store as _store
    from probe import probe
    from db import store_valid_key
    path = os.path.join(MEDIA_DIR, rel_path)
    if not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return
    res = analyze_image(data, mime_of(rel_path))
    if not res:
        return
    cat = res.get("category") or "other"
    label = (res.get("label") or "").strip()
    if label:
        _store.append_content(platform, str(chat_id), str(msg_key), f"[{TAG.get(cat, '图片')}: {label}]")
    for c in res.get("keys") or []:
        if not isinstance(c, dict) or not c.get("api_key"):
            continue
        try:
            r2 = probe(c.get("base_url"), c.get("api_key"))
        except Exception:
            continue
        if r2.get("ok"):
            try:
                store_valid_key(f"{platform}:{chat_id}", c.get("base_url"), c["api_key"],
                                r2["provider"], r2["models"], r2["latency_ms"])
            except Exception:
                pass
