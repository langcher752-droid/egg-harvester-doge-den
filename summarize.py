import httpx

from config import GATEWAY_BASE, GATEWAY_KEY, SUMMARY_MODEL, HTTP_TIMEOUT, PROXIES


def summarize(messages_text: str) -> str:
    """把一段时间内的群消息浓缩成中文简报。"""
    if not GATEWAY_KEY:
        return "（未配置 GATEWAY_KEY，无法总结）"
    prompt = (
        "下面是一段时间内的 QQ/微信群消息。请用简体中文输出一份简洁摘要：\n"
        "1) 关键话题；\n2) 提到的资源/链接；\n"
        "3) 任何 API key/接口（只标注『已自动提取』，不要复述密钥本身）。\n"
        "控制在 200 字以内。\n\n" + messages_text
    )
    payload = {
        "model": SUMMARY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        # httpx 0.28+ 移除了请求级 proxies 参数，只有配置了代理才带
        kwargs = dict(
            headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        if PROXIES:
            kwargs["proxies"] = PROXIES
        r = httpx.post(f"{GATEWAY_BASE}/v1/chat/completions", **kwargs)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"（总结失败：{e}）"
