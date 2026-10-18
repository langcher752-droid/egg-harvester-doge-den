"""wxgf 解码器（2026-10-17 按研究规范重写，实测通过）。

wxgf = WXAM：微信自研图片格式，内部是标准 HEVC/H.265 Annex B 裸流（静态图也是视频帧）。
解码链：剥私有 Header → 扫 NAL 起始码 → 按 VPS 分组 → 主图流写 raw.h265 → ffmpeg 解帧。

研究结论（.workbuddy/memory/2026-10-17.md）与本机实测：
- 私有 Header（~31B 起，含 wxgf 魔数）无需解析，直接跳过
- 起始码混合：参数集（VPS/SPS/PPS）用 4B `00 00 00 01`，帧 NAL 用 3B `00 00 01`
- NAL type = (byte >> 1) & 0x3F：32=VPS 33=SPS 34=PPS 16/17/18=BLA 19/20=IDR 21=CRA
- 按 VPS 分组两路流：
  - 流 A（第一路）= hevc Rext, gray  → 遮罩（透明度通道，仅在需要透明 PNG 时用）
  - 流 B（第二路/最后一路）= hevc Main, yuv420p/yuvj420p → 彩色主图
- 单 VPS：仅主图一路（无遮罩）
- 动态表情：多 partition = 多帧 + 遮罩交替 → 逐帧解后合成 GIF
- ffmpeg 直接解 .wxgf 会失败（Invalid data）——必须先剥私有 Header

旧实现三个 bug（已修复）：起始码 pattern 错（\x00\x00\x01\x00 不存在）、
把数据喂给 pillow-heif（应喂 ffmpeg）、没按 VPS 分流。
"""
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

START4 = b"\x00\x00\x00\x01"
START3 = b"\x00\x00\x01"

# launchd 守护进程环境下 PATH 可能极简，允许通过环境变量覆盖并优先使用 PATH。
try:  # 统一走 config（launchd 下 PATH 不含 /opt/homebrew/bin）
    from config import FFMPEG_BIN, FFPROBE_BIN
except Exception:
    FFMPEG_BIN = os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    FFPROBE_BIN = os.getenv("FFPROBE_BIN") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

NAL_VPS = 32
NAL_SPS = 33
NAL_PPS = 34
# 帧类 NAL（slice / IDR / BLA / CRA / TRAIL）
FRAME_TYPES = {0, 1, 16, 17, 18, 19, 20, 21}


def _scan_nals(data: bytes, min_off: int = 0) -> List[Tuple[int, int, int]]:
    """扫描全部 NAL 起始码（4B 与 3B 混合，4B 优先去重）。

    返回 [(起始码偏移, NAL header 偏移, nal_type), ...] 按偏移排序。
    帧 NAL 用 3B 起始码、参数集用 4B，需合并扫描；落在 4B 起始码
    内部的 3B 匹配（pos+1）剔除。
    """
    pos_len = {}
    i = min_off
    while True:
        p = data.find(START4, i)
        if p < 0:
            break
        pos_len[p] = 4
        i = p + 1
    i = min_off
    while True:
        p = data.find(START3, i)
        if p < 0:
            break
        if p not in pos_len and p + 1 not in pos_len:
            pos_len[p] = 3
        i = p + 1

    nals = []
    n = len(data)
    for p in sorted(pos_len):
        hdr = p + pos_len[p]
        if hdr >= n:
            continue
        nal_type = (data[hdr] >> 1) & 0x3F
        nals.append((p, hdr, nal_type))
    return nals


def _split_by_vps(data: bytes, nals: List[Tuple[int, int, int]]) -> List[bytes]:
    """按 VPS 起始码把字节流切分为多个 partition（每路一个流）。

    每个 partition = [该 VPS 起始码 .. 下一个 VPS 起始码)，最后一区到 EOF。
    无 VPS 时整体作为一路（从第一个 NAL 起）。
    """
    vps_offsets = [p for p, _, t in nals if t == NAL_VPS]
    if not vps_offsets:
        # 无 VPS：从第一个 NAL 起始码开始整流
        start = nals[0][0] if nals else 0
        return [data[start:]]

    parts = []
    for k, off in enumerate(vps_offsets):
        end = vps_offsets[k + 1] if k + 1 < len(vps_offsets) else len(data)
        parts.append(data[off:end])
    return parts


def _run(cmd: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def _probe(h265_path: str) -> dict:
    """ffprobe 第一路视频流的 pix_fmt / width / height / nb_read_frames。"""
    r = _run([
        FFPROBE_BIN, "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=pix_fmt,width,height,nb_read_frames",
        "-of", "csv=p=0", h265_path,
    ])
    out = r.stdout.decode("utf-8", "replace").strip()
    fields = out.split(",") if out else []
    try:
        return {
            "pix_fmt": fields[0] if len(fields) > 0 else "unknown",
            "width": int(fields[1]) if len(fields) > 1 else 0,
            "height": int(fields[2]) if len(fields) > 2 else 0,
            "frames": int(fields[3]) if len(fields) > 3 else 0,
        }
    except (ValueError, IndexError):
        return {"pix_fmt": "unknown", "width": 0, "height": 0, "frames": 0}


def _ffmpeg_decode(h265_bytes: bytes, out_prefix: str, frames_only: bool = True) -> Tuple[int, dict]:
    """把 Annex B 字节流解码为 PNG 序列。

    返回 (帧数, probe 信息)。PNG 文件写入 out_prefix + "%03d.png"。
    """
    with tempfile.NamedTemporaryFile(suffix=".h265", delete=False) as tmp:
        tmp.write(h265_bytes)
        h265_path = tmp.name

    info = _probe(h265_path)
    cmd = [FFMPEG_BIN, "-y", "-v", "error", "-f", "hevc", "-i", h265_path, "-vsync", "0"]
    if frames_only:
        cmd += ["-frames:v", "1"]
    cmd += [f"{out_prefix}%03d.png"]
    _run(cmd)
    os.unlink(h265_path)

    frames = []
    if frames_only:
        if os.path.exists(f"{out_prefix}001.png"):
            frames.append(f"{out_prefix}001.png")
    else:
        k = 1
        while os.path.exists(f"{out_prefix}{k:03d}.png"):
            frames.append(f"{out_prefix}{k:03d}.png")
            k += 1
    return len(frames), info


def _compose_gif(frame_paths: List[str], fps: int = 10) -> Optional[bytes]:
    """多帧 PNG 合成 GIF（ffmpeg 两遍 palette 方案）。"""
    import shutil
    if not frame_paths:
        return None
    fps = max(1, fps)
    with tempfile.TemporaryDirectory(prefix="wxgf_gif_") as tmpdir:
        # 统一编号（多 partition 前缀不同）
        seq = []
        for k, p in enumerate(frame_paths):
            dst = os.path.join(tmpdir, f"seq_{k:03d}.png")
            shutil.copy(p, dst)
            seq.append(dst)
        palette = os.path.join(tmpdir, "palette.png")
        out_gif = os.path.join(tmpdir, "out.gif")
        r1 = _run([
            FFMPEG_BIN, "-y", "-v", "error",
            "-framerate", str(fps), "-i", os.path.join(tmpdir, "seq_%03d.png"),
            "-vf", "palettegen=stats_mode=diff", palette,
        ])
        if r1.returncode != 0:
            return None
        r2 = _run([
            FFMPEG_BIN, "-y", "-v", "error",
            "-framerate", str(fps), "-i", os.path.join(tmpdir, "seq_%03d.png"),
            "-i", palette,
            "-lavfi", "paletteuse=dither=bayer", "-loop", "0", out_gif,
        ])
        if r2.returncode != 0:
            return None
        with open(out_gif, "rb") as f:
            return f.read()


def _apply_mask(png_bytes: bytes, mask_bytes: bytes) -> Optional[bytes]:
    """把遮罩灰度图作为 alpha 通道合成透明 PNG（ffmpeg alphamerge）。"""
    with tempfile.TemporaryDirectory(prefix="wxgf_alpha_") as tmpdir:
        main_p = os.path.join(tmpdir, "main.png")
        mask_p = os.path.join(tmpdir, "mask.png")
        out_p = os.path.join(tmpdir, "out.png")
        with open(main_p, "wb") as f:
            f.write(png_bytes)
        with open(mask_p, "wb") as f:
            f.write(mask_bytes)
        r = _run([
            FFMPEG_BIN, "-y", "-v", "error",
            "-i", main_p, "-i", mask_p,
            "-filter_complex",
            "[1:v]format=gray[g];[g][0:v]scale2ref[m][ref];[ref][m]alphamerge",
            out_p,
        ])
        if r.returncode != 0:
            return None
        with open(out_p, "rb") as f:
            return f.read()


def decode_wxgf(
    wxgf_path: str,
    prefer_format: str = "png",
    with_alpha: bool = False,
    fps: int = 10,
) -> Optional[Tuple[bytes, str]]:
    """解码 wxgf 文件为标准图片格式。

    返回 (图片 bytes, 扩展名 ".png"/".gif"/".jpg")，失败返回 None。

    with_alpha=True 时：若存在遮罩流，输出带 alpha 通道的透明 PNG；
    动图合成 GIF（GIF 不支持半透明，遮罩仅静态图生效）。
    """
    if not os.path.exists(wxgf_path):
        return None

    with open(wxgf_path, "rb") as f:
        data = f.read()

    # 快速魔数检查（部分文件无 wxgf 头，直接按 NAL 处理）
    nals = _scan_nals(data)
    if not nals:
        return None

    # 按 VPS 分组两路流
    parts = _split_by_vps(data, nals)
    if not parts:
        return None

    # 识别各路：gray/Rext = 遮罩，yuv420p/yuvj420p/Main = 主图
    color_parts: List[bytes] = []
    mask_parts: List[bytes] = []
    for part in parts:
        with tempfile.NamedTemporaryFile(suffix=".h265", delete=False) as tmp:
            tmp.write(part)
            tmp_path = tmp.name
        info = _probe(tmp_path)
        os.unlink(tmp_path)
        fmt = info["pix_fmt"]
        if fmt and "gray" in fmt:
            mask_parts.append(part)
        elif fmt and "yuv" in fmt:
            color_parts.append(part)

    if not color_parts:
        # 没有探测到彩色流：回退为最后一路
        color_parts = [parts[-1]]

    # 解码：每个主图 partition 解全部帧（多 partition = 多帧动图），按序拼接
    with tempfile.TemporaryDirectory(prefix="wxgf_") as tmpdir:
        all_frames: List[str] = []
        for idx, part in enumerate(color_parts):
            prefix = os.path.join(tmpdir, f"f{idx}_")
            n, info = _ffmpeg_decode(part, prefix, frames_only=False)
            all_frames += [f"{prefix}{k:03d}.png" for k in range(1, n + 1)]
        if not all_frames:
            return None

        ext = ".png"
        if len(all_frames) == 1:
            with open(all_frames[0], "rb") as f:
                out = f.read()
            # 透明 PNG：遮罩合成（取最后一个遮罩路）
            if with_alpha and mask_parts:
                with tempfile.TemporaryDirectory(prefix="wxgf_mask_") as mdir:
                    mprefix = os.path.join(mdir, "m_")
                    mn, _ = _ffmpeg_decode(mask_parts[-1], mprefix, frames_only=True)
                    if mn == 1:
                        with open(f"{mprefix}001.png", "rb") as f:
                            mask_png = f.read()
                        composed = _apply_mask(out, mask_png)
                        if composed:
                            out = composed
        else:
            out = _compose_gif(all_frames, fps)
            if out is None:
                # PIL 不可用：退回第一帧 PNG
                with open(all_frames[0], "rb") as f:
                    out = f.read()
            else:
                ext = ".gif"

        # 转 JPEG（ffmpeg）
        if prefer_format in ("jpg", "jpeg") and ext == ".png":
            with tempfile.TemporaryDirectory(prefix="wxgf_jpg_") as jdir:
                src = os.path.join(jdir, "src.png")
                dst = os.path.join(jdir, "out.jpg")
                with open(src, "wb") as f:
                    f.write(out)
                r = _run([FFMPEG_BIN, "-y", "-v", "error", "-i", src, "-q:v", "2", dst])
                if r.returncode == 0 and os.path.exists(dst):
                    with open(dst, "rb") as f:
                        out = f.read()
                    ext = ".jpg"

        return out, ext


def batch_decode_wxgf(media_dir: str, output_dir: str = None, with_alpha: bool = False):
    """批量解码目录下所有 wxgf 文件。

    media_dir: 媒体根目录（递归扫描 *.wxgf）
    output_dir: 输出目录（默认原地替换扩展名）
    """
    count_total = 0
    count_success = 0

    for root, _dirs, files in os.walk(media_dir):
        for fname in files:
            if not fname.endswith(".wxgf"):
                continue
            count_total += 1
            wxgf_path = os.path.join(root, fname)

            result = decode_wxgf(wxgf_path, with_alpha=with_alpha)
            if not result:
                print(f"[wxgf] 解码失败: {wxgf_path}")
                continue

            data, ext = result
            if output_dir:
                rel_path = os.path.relpath(wxgf_path, media_dir)
                out_path = os.path.join(output_dir, rel_path[:-5] + ext)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
            else:
                out_path = wxgf_path[:-5] + ext

            with open(out_path, "wb") as f:
                f.write(data)
            count_success += 1
            print(f"[wxgf] 已解码: {wxgf_path} -> {out_path}")

    print(f"[wxgf] 批量解码完成：{count_success}/{count_total}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python wxgf_decoder.py <wxgf文件> [--alpha] [--jpg]  # 单文件解码")
        print("  python wxgf_decoder.py --batch <media目录> [--alpha] # 批量解码")
        sys.exit(1)

    if sys.argv[1] == "--batch":
        if len(sys.argv) < 3:
            print("错误：需要指定 media 目录")
            sys.exit(1)
        batch_decode_wxgf(sys.argv[2], with_alpha="--alpha" in sys.argv)
    else:
        wxgf_path = sys.argv[1]
        result = decode_wxgf(
            wxgf_path,
            prefer_format="jpg" if "--jpg" in sys.argv else "png",
            with_alpha="--alpha" in sys.argv,
        )
        if result:
            data, ext = result
            out_path = wxgf_path[:-5] + ext
            with open(out_path, "wb") as f:
                f.write(data)
            print(f"[wxgf] 已解码: {wxgf_path} -> {out_path} ({len(data)} B)")
        else:
            print(f"[wxgf] 解码失败: {wxgf_path}")
            sys.exit(1)
