"""NSFW 图片分类引擎（ONNX）。

默认使用 ViT-base NSFW 二分类模型（AdamCodd/vit-base-nsfw-detector，Apache-2.0），
int8 量化后约 84MB，纯 CPU 推理；模型文件放 `models/nsfw_vit_int8.onnx`。

调用方只需：
    cls, score, probs = classify_bytes(raw)       # cls: "nsfw" / "sfw"
    cls, score, probs = classify_path(path)

如果模型文件缺失或 onnxruntime 不可用，`available()` 返回 False，调用方退回启发式。
"""
from __future__ import annotations

import io
import json
import os
import threading
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(ROOT, "models", "nsfw_vit_int8.onnx")
DEFAULT_CONFIG = os.path.join(ROOT, "models", "config.json")

_session = None
_session_lock = threading.Lock()
_labels: dict[int, str] = {0: "sfw", 1: "nsfw"}
_input_name = "pixel_values"
_size = 384
_mean = (0.5, 0.5, 0.5)
_std = (0.5, 0.5, 0.5)


def model_path() -> str:
    return os.environ.get("NSFW_MODEL_PATH") or DEFAULT_MODEL


def available() -> bool:
    """模型文件是否就绪（不检查 onnxruntime，导入失败会在 classify 里报）。"""
    return os.path.exists(model_path())


def _load_config() -> None:
    global _labels, _size, _mean, _std, _input_name
    cfg_path = os.environ.get("NSFW_MODEL_CONFIG") or DEFAULT_CONFIG
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        raw_labels = cfg.get("id2label") or {}
        if raw_labels:
            _labels = {int(k): str(v).lower() for k, v in raw_labels.items()}
    except Exception:
        pass
    try:
        pre = os.path.join(os.path.dirname(cfg_path), "preprocessor_config.json")
        with open(pre, "r", encoding="utf-8") as fh:
            pc = json.load(fh)
        size = pc.get("size") or {}
        if isinstance(size, dict):
            _size = int(size.get("height") or size.get("shortest_edge") or _size)
        elif isinstance(size, int):
            _size = size
        _mean = tuple(pc.get("image_mean") or _mean)
        _std = tuple(pc.get("image_std") or _std)
    except Exception:
        pass


def _get_session():
    global _session, _input_name
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        import onnxruntime as ort  # 延迟导入：没装依赖时不影响启发式兜底

        _load_config()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2)))
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(model_path(), sess_options=opts,
                                    providers=["CPUExecutionProvider"])
        _input_name = sess.get_inputs()[0].name
        _session = sess
        return _session


def _softmax(x):
    import numpy as np

    e = np.exp(x - np.max(x))
    return e / e.sum()


def _preprocess(data: bytes):
    """RGB → 384×384 → [-1,1] → NCHW float32。"""
    from PIL import Image
    import numpy as np

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    # 长边缩放 + 居中裁剪（等价 torchvision Resize(size) + CenterCrop）
    from PIL import ImageOps

    img = ImageOps.fit(img, (_size, _size), method=Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - np.array(_mean, dtype=np.float32)) / np.array(_std, dtype=np.float32)
    return arr.transpose(2, 0, 1)[None, ...]


def classify_bytes(data: bytes, warm: bool = True) -> tuple[str, float, dict[str, float]]:
    """返回 (top_label, nsfw_score, {label: prob})。"""
    import numpy as np

    sess = _get_session()
    x = _preprocess(data)
    logits = sess.run(None, {_input_name: x})[0][0].astype(np.float32)
    probs = _softmax(logits)
    result: dict[str, float] = {}
    for idx, p in enumerate(probs):
        result[_labels.get(idx, str(idx))] = float(p)
    nsfw_score = result.get("nsfw", 0.0)
    top = max(result, key=result.get) if result else "sfw"
    return top, nsfw_score, result


def classify_path(path: str) -> tuple[str, float, dict[str, float]]:
    with open(path, "rb") as fh:
        return classify_bytes(fh.read())


def classify_file(path: str) -> dict[str, Any]:
    """给 /media-nsfw 之类的接口用的统一返回。"""
    label, score, probs = classify_path(path)
    return {"label": label, "score": score, "probs": probs}
