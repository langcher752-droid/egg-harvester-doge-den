"""共享管线：文本 → 提取 → 测试 → 入库（仅存已验证）。

消息落盘由各 ingest 模块负责（store.save_raw），这里**只管从文本里捞鸡蛋**。
web UI / CLI / OneBot(QQ) / 微信 全部调用 process_text，保证行为一致。
"""
from extract import extract_from_text
from probe import probe, is_plausible_api_url, is_content_url
from db import store_valid_key


def process_text(text: str, source: str = "text"):
    if not text or not text.strip():
        return []
    candidates = extract_from_text(text)
    results = []
    for c in candidates:
        base_url = c.get("base_url")
        # 内容页（视频/公众号文章/分享）不可能是接口，直接判无效，不再浪费一次网络请求
        if base_url and (is_content_url(base_url) or not is_plausible_api_url(base_url)):
            ak = c.get("api_key") or ""
            results.append(
                {
                    "base_url": base_url,
                    "api_key_mask": (ak[:4] + "****") if ak else "****",
                    "latency_ms": 0,
                    "status": "invalid",
                    "error": "疑似视频/文章/分享链接，不是 API 接口",
                }
            )
            continue

        res = probe(base_url, c.get("api_key"))
        if res["ok"]:
            h = store_valid_key(
                source, base_url, c["api_key"], res["provider"], res["models"], res["latency_ms"]
            )
            results.append(
                {
                    "base_url": base_url,
                    "provider": res["provider"],
                    "models": res["models"],
                    "latency_ms": res.get("latency_ms"),
                    "status": "valid",
                    "hash": h,
                    "curl": res.get("curl"),
                    "trace": res.get("trace"),
                }
            )
        else:
            ak = c.get("api_key") or ""
            results.append(
                {
                    "base_url": base_url,
                    "api_key_mask": (ak[:4] + "****") if ak else "****",
                    "latency_ms": res.get("latency_ms"),
                    "status": "invalid",
                    "error": res.get("error"),
                    "curl": res.get("curl"),
                    "trace": res.get("trace"),
                }
            )
    return results
