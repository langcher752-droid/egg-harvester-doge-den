"""不适宜内容（NSFW）检测 —— 纯本地方案，零费用、零外网依赖。

设计取舍（为什么不用现成模型）：
  · opennsfw2 / nsfwjs 需要 TensorFlow 或 25MB+ 权重，打包进原生 App 代价太高；
  · 调网关视觉模型要花 token，不满足「免费」；
  · 所以这里用**肤色像素占比 + 关键词**两级启发式，宁可误报也不漏报，
    命中后只做「标记 + 不显示预览」，允许用户在界面上显式点开。

检测口径：
  image  → 解码成 128×128，按 RGB/YCbCr 规则数肤色像素，
           整体占比 ≥ 0.34 且中心区占比 ≥ 0.30，或整体 ≥ 0.45 → 判定 NSFW
  video  → ffmpeg 抽 1s 处的帧再走 image 逻辑（没装 ffmpeg 就只做关键词判断）
  text   → 文件名 / 消息文本里的敏感词（拉丁词按词边界，中文按具体词组）

返回值统一为：
  {"nsfw": bool, "score": float, "reason": str, "kind": "image|video|text|none", "checked": bool}
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile

try:
    from config import FFMPEG_BIN
except Exception:  # pragma: no cover
    import shutil as _sh
    FFMPEG_BIN = _sh.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - Pillow 是内置依赖
    Image = None
    ImageOps = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".wxgf"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".flv", ".ts"}

# 拉丁关键词按词边界匹配，避免 "sussex" 之类误伤
_LATIN_WORDS = [
    "porn", "porno", "xxx", "nsfw", "hentai", "nude", "nudes", "naked",
    "onlyfans", "jav", "r18", "r18+", "18+", "adult", "milf", "boobs",
    "pussy", "cumshot", "blowjob", "hentai", "ecchi", "rule34", "futanari",
]
_LATIN_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in _LATIN_WORDS) + r")(?![a-z0-9])", re.I)

# 中文用具体词组，避免「裸机」「成人教育」这种正常词被误伤
_CN_WORDS = [
    "色情", "成人视频", "成人影片", "黄片", "黄图", "裸照", "裸聊", "裸体",
    "全裸", "半裸", "做爱", "性爱", "口交", "性交", "淫秽", "淫乱", "里番",
    "无码", "有码", "福利姬", "约炮", "一夜情", "下海", "脱衣舞", "成人网站",
    "18禁", "少儿不宜",
]

DEFAULT_THRESHOLD = 0.34


def kind_of(name: str) -> str:
    ext = os.path.splitext(str(name or ""))[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
        return "archive"
    if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}:
        return "doc"
    return "other"


def keyword_hit(text: str):
    """返回 (hit, matched_word)。空文本返回 (False, "")。"""
    t = str(text or "")
    if not t:
        return False, ""
    for w in _CN_WORDS:
        if w in t:
            return True, w
    m = _LATIN_RE.search(t)
    if m:
        return True, m.group(1)
    return False, ""


STICKER_MARKERS = ("表情包", "动画表情", "[表情", "[贴纸", "[图片 [动画表情]]")


def looks_like_sticker(context: str) -> bool:
    t = str(context or "")
    if any(k in t for k in STICKER_MARKERS):
        return True
    # QQ 表情包形如：[图片 [哈哈哈哈]] / [图片 [啊]]
    return bool(re.search(r"\[图片\s*\[[^\]]+\]\]", t))


def _unique_colors(img) -> int:
    """缩到 64×64 数不同颜色：照片上千，卡通/表情包只有几十~几百。"""
    try:
        small = img.convert("RGB").resize((64, 64))
        return len(set(small.getdata()))
    except Exception:
        return 0


def _is_skin(r: int, g: int, b: int) -> bool:
    """收紧的肤色判定：只要 RGB 规则（YCbCr 规则会把米色 UI 背景也算成肤色）。

    附加约束（都是为了压掉截图/表情包误报）：
      · r > g >= b，且 r-g >= 15、g-b >= 5（米色墙纸 r-g 只有 10 左右）
      · 饱和度 0.15~0.70（纯橙、荧光色饱和度接近 1，排除）
      · 明度 100~250，色差 15~120
    """
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == 0:
        return False
    if not (r > g >= b):
        return False
    if r - g < 15 or g - b < 5:
        return False
    diff = mx - mn
    if diff < 15 or diff > 120:
        return False
    if mx < 100 or mx > 250:
        return False
    sat = diff / mx
    return 0.15 <= sat <= 0.70


def _ratio_skin(img) -> tuple:
    """返回 (整体肤色占比, 中心区肤色占比)。img 已转 RGB。"""
    img = img.convert("RGB")
    w, h = img.size
    # 太小的图先放大，避免采样点过少
    if max(w, h) < 64:
        k = 64 / max(1, max(w, h))
        img = img.resize((max(1, int(w * k)), max(1, int(h * k))))
        w, h = img.size
    step = max(1, max(w, h) // 128)
    px = img.load()
    total = 0
    skin = 0
    ctotal = 0
    cskin = 0
    x0, x1 = int(w * 0.2), int(w * 0.8)
    y0, y1 = int(h * 0.2), int(h * 0.8)
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y][:3]
            s = _is_skin(r, g, b)
            total += 1
            if s:
                skin += 1
            if x0 <= x <= x1 and y0 <= y <= y1:
                ctotal += 1
                if s:
                    cskin += 1
    return (skin / total if total else 0.0), (cskin / ctotal if ctotal else 0.0)


# 分级阈值：明确命中 / 擦边（暴露、低俗）
EXPLICIT_THRESHOLD = float(os.environ.get("NSFW_EXPLICIT_THRESHOLD", "0.70"))
BORDERLINE_THRESHOLD = float(os.environ.get("NSFW_BORDERLINE_THRESHOLD", "0.40"))
# 肤色兜底：模型漏掉的「大面积裸露但画面不露骨」也按擦边处理
# 纯肤色兜底：模型分不高、但画面大面积肤色（照片里常见「露但不露骨」）
BORDERLINE_SKIN_CENTER = float(os.environ.get("NSFW_BORDERLINE_SKIN_CENTER", "0.60"))
BORDERLINE_SKIN_OVERALL = float(os.environ.get("NSFW_BORDERLINE_SKIN_OVERALL", "0.55"))
SKIN_ONLY_MODEL_MIN = float(os.environ.get("NSFW_SKIN_ONLY_MODEL_MIN", "0.25"))
# 擦边档的肤色门槛：模型分够、但画面完全没肤色时，多半是截图/表情包误报
BORDERLINE_SKIN_GATE = float(os.environ.get("NSFW_BORDERLINE_SKIN_GATE", "0.22"))

_model_ready = None


def model_available() -> bool:
    """ONNX 模型是否可用（缺文件/缺依赖时静默退回启发式）。"""
    global _model_ready
    if _model_ready is None:
        try:
            import nsfw_model  # noqa: F401
            _model_ready = nsfw_model.available()
        except Exception:
            _model_ready = False
    return bool(_model_ready)


def _model_score(data: bytes):
    try:
        import nsfw_model
        label, score, probs = nsfw_model.classify_bytes(data)
        return float(score), probs
    except Exception:
        return None, None


def check_image_bytes(data: bytes, threshold: float = DEFAULT_THRESHOLD, context: str = "") -> dict:
    """图片内容检测。

    优先用 ONNX 视觉模型（ViT-NSFW）打分，并按「明确不适宜 / 疑似擦边」两级拦截；
    模型不可用时退回肤色启发式。表情包/贴纸不做内容判定。
    """
    if Image is None:
        return {"nsfw": False, "score": 0.0, "reason": "未安装 Pillow，跳过", "kind": "image", "checked": False}
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        overall, center = _ratio_skin(img)
        colors = _unique_colors(img)
    except Exception as e:
        return {"nsfw": False, "score": 0.0, "reason": f"图片解码失败: {e}", "kind": "image", "checked": False}

    if looks_like_sticker(context) or looks_like_sticker(os.path.basename(str(context))):
        return {"nsfw": False, "score": 0.0, "reason": "表情包/贴纸，跳过内容检测", "kind": "image",
                "checked": True, "skin": round(overall, 3), "skin_center": round(center, 3)}

    # ---- 模型判定（主路径） ----
    if model_available():
        model_score, probs = _model_score(data)
        if model_score is not None:
            explicit = model_score >= EXPLICIT_THRESHOLD
            borderline = (not explicit) and model_score >= BORDERLINE_THRESHOLD \
                and center >= BORDERLINE_SKIN_GATE
            skin_border = (not explicit) and model_score >= SKIN_ONLY_MODEL_MIN \
                and center >= BORDERLINE_SKIN_CENTER and overall >= BORDERLINE_SKIN_OVERALL
            if explicit:
                reason = f"模型判定明确不适宜（p={model_score:.2f}），肤色占比 {overall:.0%}"
                tier = "explicit"
            elif borderline or skin_border:
                if borderline:
                    reason = f"模型判定疑似擦边/暴露（p={model_score:.2f}），肤色占比 {overall:.0%}（中心 {center:.0%}）"
                else:
                    reason = f"大面积肤色暴露（占比 {overall:.0%}，中心 {center:.0%}），按擦边拦截"
                tier = "borderline"
            else:
                reason = ""
                tier = None
            return {"nsfw": tier is not None, "score": round(model_score, 4), "reason": reason,
                    "kind": "image", "checked": True, "tier": tier, "model": True,
                    "skin": round(overall, 3), "skin_center": round(center, 3), "colors": colors,
                    "probs": probs}

    # ---- 启发式兜底（模型缺失时） ----
    score = round(0.5 * overall + 0.5 * center, 3)
    is_cartoon = colors and colors < 1200
    if is_cartoon:
        # 卡通/插画：只在几乎整幅都是肤色时才判（避免把动漫头像、表情包当违规）
        hit = overall >= 0.75 and center >= 0.60
        reason = f"插画类（{colors} 色）肤色占比 {overall:.0%}，疑似不适宜内容" if hit else ""
    else:
        hit = (overall >= threshold and center >= 0.38) or overall >= 0.55
        reason = f"肤色像素占比 {overall:.0%}（中心 {center:.0%}），疑似不适宜内容" if hit else ""
    return {"nsfw": hit, "score": score, "reason": reason, "kind": "image", "checked": True,
            "tier": "explicit" if hit else None, "model": False,
            "skin": round(overall, 3), "skin_center": round(center, 3), "colors": colors}


def _video_first_frame(path: str) -> bytes:
    """用 ffmpeg 抽 1 秒处的帧，返回 PNG 字节；没装 ffmpeg 返回 b''。"""
    ff = FFMPEG_BIN if os.path.exists(FFMPEG_BIN) else shutil.which("ffmpeg")
    if not ff:
        return b""
    out = None
    try:
        fd, out = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        subprocess.run(
            [ff, "-v", "error", "-ss", "1", "-i", path, "-frames:v", "1", "-y", out],
            capture_output=True, timeout=20,
        )
        if os.path.exists(out) and os.path.getsize(out) > 0:
            with open(out, "rb") as f:
                return f.read()
    except Exception:
        return b""
    finally:
        if out and os.path.exists(out):
            try:
                os.unlink(out)
            except OSError:
                pass
    return b""


def check_video_path(path: str, threshold: float = DEFAULT_THRESHOLD) -> dict:
    frame = _video_first_frame(path)
    if not frame:
        return {"nsfw": False, "score": 0.0, "reason": "没有 ffmpeg 或抽帧失败，仅做关键词判断",
                "kind": "video", "checked": False}
    res = check_image_bytes(frame, threshold)
    res["kind"] = "video"
    res["reason"] = ("视频首帧" + (res["reason"] or "")) if res["nsfw"] else ""
    return res


def check_path(path: str, threshold: float = DEFAULT_THRESHOLD, context: str = "") -> dict:
    """对本地文件做检测（图片 / 视频 / 其它）。"""
    if not path or not os.path.exists(path):
        return {"nsfw": False, "score": 0.0, "reason": "文件不存在", "kind": "none", "checked": False}
    k = kind_of(path)
    name_hit, word = keyword_hit(os.path.basename(path))
    if name_hit:
        return {"nsfw": True, "score": 0.99, "reason": f"文件名命中敏感词「{word}」", "kind": k, "checked": True}
    if k == "image":
        if path.lower().endswith(".wxgf"):
            try:
                import wxgf_decoder
                res = wxgf_decoder.decode_wxgf(path)
                if res:
                    return check_image_bytes(res[0], threshold, context)
            except Exception:
                pass
        try:
            with open(path, "rb") as f:
                return check_image_bytes(f.read(), threshold, context)
        except Exception as e:
            return {"nsfw": False, "score": 0.0, "reason": f"读取失败: {e}", "kind": k, "checked": False}
    if k == "video":
        return check_video_path(path, threshold)
    return {"nsfw": False, "score": 0.0, "reason": "", "kind": k, "checked": False}


def make_thumb(path: str, max_side: int = 520, quality: int = 78) -> bytes:
    """生成 JPEG 缩略图（图片直接缩放；视频抽首帧）。失败返回 b''。"""
    if Image is None or not path or not os.path.exists(path):
        return b""
    k = kind_of(path)
    if k == "video":
        frame = _video_first_frame(path)
        if not frame:
            return b""
        try:
            img = Image.open(io.BytesIO(frame))
        except Exception:
            return b""
    elif k == "image":
        if path.lower().endswith(".wxgf"):
            try:
                import wxgf_decoder
                res = wxgf_decoder.decode_wxgf(path)
                if not res:
                    return b""
                img = Image.open(io.BytesIO(res[0]))
            except Exception:
                return b""
        else:
            try:
                img = Image.open(path)
            except Exception:
                return b""
    else:
        return b""
    try:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return b""


def thumb_cache_name(key: str) -> str:
    return hashlib.md5(str(key).encode()).hexdigest() + ".jpg"
