import hashlib
import os
import sys

from dotenv import load_dotenv

load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)  # 2026-10-15-11-07-54

# ---------- LLM 网关 ----------
GATEWAY_BASE = os.getenv("GATEWAY_BASE", "https://example.invalid/v1")
GATEWAY_KEY = os.getenv("GATEWAY_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "global:gpt-4o-mini")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "global:gpt-4o-mini")

_DB_DEFAULT = os.path.join(_HERE, "eggs.db")
DB_PATH = os.getenv("DB_PATH") or _DB_DEFAULT

HTTP_PROXY = os.getenv("HTTP_PROXY", "")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))

PROXIES = {"http": HTTP_PROXY, "https": HTTP_PROXY} if HTTP_PROXY else None


def httpx_kwargs():
    """httpx 0.28+ 移除了 proxies 参数；所有 httpx 调用统一走这里。"""
    return {"proxy": HTTP_PROXY} if HTTP_PROXY else {}

# ---------- 外部命令（launchd 下 PATH 极简，需显式兜底） ----------
def _find_bin(name, fallbacks):
    import shutil
    return os.getenv(name.upper() + "_BIN") or shutil.which(name) or next(
        (p for p in fallbacks if os.path.exists(p)), name)


FFMPEG_BIN = _find_bin("ffmpeg", ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"])
FFPROBE_BIN = _find_bin("ffprobe", ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"])

# ---------- 媒体落地 ----------
MEDIA_DIR = os.getenv("MEDIA_DIR") or os.path.join(_HERE, "media")

# ---------- 微信腿 ----------
# V2 图片密钥 = 账号级离线派生（无需内存扫描）。实测微信 4.1.15 复现。
_WXID = os.getenv("WECHAT_WXID", "").strip()
_CODE_RAW = os.getenv("WECHAT_CODE", "").strip()
_CODE = int(_CODE_RAW) if _CODE_RAW.isdigit() else 0


def _clean_wxid(w):
    parts = w.split("_")
    return "_".join(parts[:2]) if w.startswith("wxid_") and len(parts) >= 3 else w


WECHAT_AES_KEY = (
    hashlib.md5(f"{_CODE}{_clean_wxid(_WXID)}".encode()).hexdigest()[:16].encode()
    if _WXID and _CODE
    else b""
)
WECHAT_XOR_KEY = _CODE & 0xFF if _WXID and _CODE else 0

_WX_CONTAINER = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)
_WX_USER = f"{_WXID}_252f"

WECHAT_KEYS_JSON = os.getenv("WECHAT_KEYS_JSON") or os.path.join(_PROJ, "wechat-key-hook", "keys.json")
WECHAT_DECRYPTED = os.getenv("WECHAT_DECRYPTED") or os.path.join(_PROJ, "wechat-key-hook", "decrypted")
WECHAT_DB_DIR = os.getenv("WECHAT_DB_DIR") or os.path.join(_WX_CONTAINER, _WX_USER, "db_storage")
WECHAT_ATTACH_DIR = os.getenv("WECHAT_ATTACH_DIR") or os.path.join(_WX_CONTAINER, _WX_USER, "msg", "attach")

# 解密脚本用的解释器（需 pycryptodome；本机 managed venv 已装）
WECHAT_PY = os.getenv("WECHAT_PY") or sys.executable

# 微信轮询间隔（秒）—— 撤回后的恢复窗口，越小越安全
WECHAT_POLL_INTERVAL = int(os.getenv("WECHAT_POLL_INTERVAL", "30"))

# ---------- QQ（NapCat）腿 ----------
# NapCat OneBot11 HTTP API 地址（主动调用侧）。容器 IP 会变，可用 NAP_HTTP_URL 覆盖。
NAP_HTTP_URL = os.getenv("NAP_HTTP_URL", "http://127.0.0.1:3000")
# 群文件：≤ 此阈值自动下载（保命），超过则进待决策列表 + 弹通知
QQ_AUTO_FILE_BYTES = int(os.getenv("QQ_AUTO_FILE_BYTES", str(20 * 1024 * 1024)))
