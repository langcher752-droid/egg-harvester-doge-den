#!/usr/bin/env python3
"""把 doge 贴纸/素材压缩成 Web 用资产 → static/doge/{stickers,img}/.

- 贴纸：统一 320px 内、webp q=88（原图 60MB → 约 3MB）
- 立绘：512px 内 webp（保留透明度）
"""
import os
import glob
import argparse

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def convert(src, dst, max_side, quality=88):
    im = Image.open(src)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    if im.mode == "RGBA":
        im.save(dst, "WEBP", quality=quality, method=6)
    else:
        im.save(dst, "WEBP", quality=quality, method=6)
    return im.size, os.path.getsize(dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stickers", default=os.getenv("DOGE_STICKERS_DIR", ""))
    parser.add_argument("--images", default=os.getenv("DOGE_IMAGES_DIR", ""))
    parser.add_argument("--output", default=os.getenv("DOGE_OUTPUT_DIR", os.path.join(ROOT, "static", "doge")))
    args = parser.parse_args()
    if not args.stickers or not args.images:
        parser.error("provide --stickers and --images, or set DOGE_STICKERS_DIR/DOGE_IMAGES_DIR")
    stick_src, image_src, dst = args.stickers, args.images, args.output
    stick_dst = os.path.join(dst, "stickers")
    img_dst = os.path.join(dst, "img")
    os.makedirs(stick_dst, exist_ok=True)
    os.makedirs(img_dst, exist_ok=True)

    total = 0
    names = []
    for path in sorted(glob.glob(os.path.join(stick_src, "*"))):
        if os.path.isdir(path):
            continue
        base = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(stick_dst, base + ".webp")
        try:
            size, nbytes = convert(path, out, 320)
            total += nbytes
            names.append(base + ".webp")
        except Exception as e:  # noqa: BLE001
            print("!! 跳过", path, e, file=sys.stderr)

    # 立绘（hero 图）不要透明压缩损失 → 保留 png
    heroes = []
    for path in sorted(glob.glob(os.path.join(image_src, "*"))):
        base = os.path.splitext(os.path.basename(path))[0]
        ext = os.path.splitext(path)[1].lower()
        if ext == ".gif":
            continue
        out = os.path.join(img_dst, base + ".webp")
        try:
            size, nbytes = convert(path, out, 640, quality=90)
            total += nbytes
            heroes.append(base + ".webp")
        except Exception as e:  # noqa: BLE001
            print("!! 跳过 hero", path, e, file=sys.stderr)

    print(f"贴纸 {len(names)} 张 · 立绘 {len(heroes)} 张 · 共 {total/1024/1024:.2f} MB → {DST}")
    print("STICKERS:", names)
    print("HEROES:", heroes)


if __name__ == "__main__":
    main()
