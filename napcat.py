"""NapCat OneBot11 HTTP API 客户端（主动调用侧）。

事件靠 httpClients 单向推过来；这里负责反向调用 NapCat 的 HTTP API：
  get_group_file_url  → 拿群文件新鲜下载链接（file_id+busid 可事后重取）
  get_group_file_list → 拉群文件清单
  get_image           → 按 message_id 取图片（兜底）

QQ 群文件与图片的关键区别：群文件链接可再生（file_id 长期有效），
图片 CDN 链接带 rkey 会过期 → 所以群文件可以"先问再下"。
"""
import httpx

from config import NAP_HTTP_URL, HTTP_TIMEOUT, httpx_kwargs


def call(action, **params):
    """POST /<action>，返回 data 或 None。"""
    if not NAP_HTTP_URL:
        return None
    try:
        r = httpx.post(f"{NAP_HTTP_URL.rstrip('/')}/{action}", json=params,
                       timeout=HTTP_TIMEOUT, **httpx_kwargs())
        r.raise_for_status()
    except Exception:
        return None
    try:
        j = r.json()
    except Exception:
        return None
    if not isinstance(j, dict) or j.get("status") not in ("ok", "async"):
        return None
    return j.get("data")


def get_group_file_url(group_id, file_id, busid):
    try:
        busid = int(busid)
    except (TypeError, ValueError):
        busid = 0
    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        group_id = 0
    return call("get_group_file_url", group_id=group_id, file_id=str(file_id), busid=busid)


def get_group_file_list(group_id):
    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        group_id = 0
    return call("get_group_file_list", group_id=group_id)


def get_image(message_id):
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        message_id = 0
    return call("get_image", message_id=message_id)
