"""Run the pinned vision models with ONNX Runtime on CPU.

Preprocessing follows each model's published preprocessor_config.json. Models load lazily, once per process.
"""

from __future__ import annotations

import io
import json
import os
import threading
from dataclasses import dataclass
from functools import cached_property

import numpy as np
from PIL import Image

from .models import MODELS, file_path

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _session(model_key: str, name: str):
    import onnxruntime as ort

    from ...config import get_settings

    ort.disable_telemetry_events()  # ONNX Runtime sends usage events to Microsoft by default; nothing leaves our servers
    opts = ort.SessionOptions()
    threads = get_settings().vision_threads or max(1, (os.cpu_count() or 2))
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(file_path(MODELS[model_key], name)), opts, providers=["CPUExecutionProvider"])


def _tokenizer(model_key: str):
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(file_path(MODELS[model_key], "tokenizer.json")))


def _to_chw(img: Image.Image, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    a = (np.asarray(img, dtype=np.float32) / 255.0 - mean) / std
    return a.transpose(2, 0, 1)[None].astype(np.float32)


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _encode(tok, texts: list[str], pad_id: int, max_len: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    ids = [tok.encode(t).ids for t in texts]
    if max_len:
        ids = [i[: max_len - 1] + [i[-1]] if len(i) > max_len else i for i in ids]
    n = max_len or max(len(i) for i in ids)
    arr = np.full((len(ids), n), pad_id, dtype=np.int64)
    mask = np.zeros((len(ids), n), dtype=np.int64)
    for r, i in enumerate(ids):
        arr[r, : len(i)] = i
        mask[r, : len(i)] = 1
    return arr, mask


# ------------------------------------------------------------------------------------------------ CLIP (classification)
class Clip:
    def __init__(self):
        self.vision = _session("clip", "onnx/vision_model_quantized.onnx")
        self.text = _session("clip", "onnx/text_model_quantized.onnx")
        self.tok = _tokenizer("clip")
        self._cache: dict[str, np.ndarray] = {}

    def image_embedding(self, img: Image.Image) -> np.ndarray:
        w, h = img.size
        s = 224 / min(w, h)
        r = img.convert("RGB").resize((max(224, round(w * s)), max(224, round(h * s))), Image.Resampling.BICUBIC)
        left, top = (r.width - 224) // 2, (r.height - 224) // 2
        r = r.crop((left, top, left + 224, top + 224))
        e = self.vision.run(None, {"pixel_values": _to_chw(r, CLIP_MEAN, CLIP_STD)})[0][0]
        return e / np.linalg.norm(e)

    def text_embeddings(self, prompts: list[str]) -> np.ndarray:
        todo = [p for p in prompts if p not in self._cache]
        if todo:
            ids, _ = _encode(self.tok, todo, pad_id=49407)
            embs = self.text.run(None, {"input_ids": ids})[0]
            for p, e in zip(todo, embs, strict=True):
                self._cache[p] = e / np.linalg.norm(e)
        return np.stack([self._cache[p] for p in prompts])

    def probabilities(self, image_emb: np.ndarray, prompts: list[str]) -> np.ndarray:
        return _softmax(100.0 * (self.text_embeddings(prompts) @ image_emb))


# ------------------------------------------------------------------------------------------------ OWLv2 (detection)
@dataclass
class Box:
    query: int
    score: float
    x: float  # fractions of the image width/height, top-left origin
    y: float
    w: float
    h: float


def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


class Owl:
    SIZE = 960

    def __init__(self):
        self.session = _session("owlv2", "onnx/model_quantized.onnx")
        self.tok = _tokenizer("owlv2")

    def detect(self, img: Image.Image, queries: list[str], threshold: float, max_per_query: int = 8) -> list[Box]:
        img = img.convert("RGB")
        w, h = img.size
        side = max(w, h)
        canvas = Image.new("RGB", (side, side), (128, 128, 128))  # the processor pads bottom/right with 0.5 grey
        canvas.paste(img, (0, 0))
        pixels = _to_chw(canvas.resize((self.SIZE, self.SIZE), Image.Resampling.BILINEAR), CLIP_MEAN, CLIP_STD)
        ids, mask = _encode(self.tok, queries, pad_id=0, max_len=16)
        logits, boxes = self.session.run(
            ["logits", "pred_boxes"], {"input_ids": ids, "attention_mask": mask, "pixel_values": pixels}
        )[:2]
        scores = _sigmoid(logits[0])  # (3600, queries)
        found: list[Box] = []
        for q in range(len(queries)):
            idx = np.where(scores[:, q] >= threshold)[0]
            cand = []
            for i in idx[np.argsort(-scores[idx, q])]:
                cx, cy, bw, bh = boxes[0, i] * side
                x0, y0 = max(0.0, cx - bw / 2), max(0.0, cy - bh / 2)
                x1, y1 = min(float(w), cx + bw / 2), min(float(h), cy + bh / 2)
                if x1 - x0 < 4 or y1 - y0 < 4:
                    continue
                b = Box(q, float(scores[i, q]), float(x0 / w), float(y0 / h), float((x1 - x0) / w), float((y1 - y0) / h))
                if b.w * b.h > 0.9:  # a box covering the whole photo is not a defect location
                    continue
                if all(_iou(b, k) < 0.3 for k in cand):
                    cand.append(b)
                if len(cand) >= max_per_query:
                    break
            found.extend(cand)
        return found


# ------------------------------------------------------------------------------------------------ CLIPSeg (segmentation)
class ClipSeg:
    SIZE = 352

    def __init__(self):
        self.session = _session("clipseg", "onnx/model_quantized.onnx")
        self.tok = _tokenizer("clipseg")

    def probabilities(self, img: Image.Image, prompts: list[str]) -> np.ndarray:
        """Per-prompt probability maps, shape (len(prompts), 352, 352)."""
        pixels = _to_chw(
            img.convert("RGB").resize((self.SIZE, self.SIZE), Image.Resampling.BILINEAR), IMAGENET_MEAN, IMAGENET_STD
        )
        ids, mask = _encode(self.tok, prompts, pad_id=49407)
        logits = self.session.run(
            None, {"input_ids": ids, "attention_mask": mask, "pixel_values": np.repeat(pixels, len(prompts), axis=0)}
        )[0]
        if logits.ndim == 2:
            logits = logits[None]
        return _sigmoid(logits)


# ------------------------------------------------------------------------------------------------ Florence-2 (descriptions)
class Florence:
    SIZE = 768
    PROMPTS = {
        "<MORE_DETAILED_CAPTION>": "Describe with a paragraph what is shown in the image.",
        "<DETAILED_CAPTION>": "Describe in detail what is shown in the image.",
        "<CAPTION>": "What does the image describe?",
    }
    DECODER_START, BOS, EOS, LAYERS = 2, 0, 2, 6

    def __init__(self):
        self.vision = _session("florence2", "onnx/vision_encoder_quantized.onnx")
        self.embed = _session("florence2", "onnx/embed_tokens_quantized.onnx")
        self.encoder = _session("florence2", "onnx/encoder_model_quantized.onnx")
        self.decoder = _session("florence2", "onnx/decoder_model_merged_quantized.onnx")
        self.tok = _tokenizer("florence2")

    def _embed(self, ids: list[int]) -> np.ndarray:
        return self.embed.run(None, {"input_ids": np.array([ids], dtype=np.int64)})[0]

    def describe(self, img: Image.Image, task: str = "<MORE_DETAILED_CAPTION>", max_new_tokens: int = 120) -> str:
        pixels = _to_chw(img.convert("RGB").resize((self.SIZE, self.SIZE), Image.Resampling.BICUBIC), IMAGENET_MEAN, IMAGENET_STD)
        image_features = self.vision.run(None, {"pixel_values": pixels})[0]
        prompt_ids = self.tok.encode(self.PROMPTS[task]).ids
        enc_in = np.concatenate([image_features, self._embed(prompt_ids)], axis=1).astype(np.float32)
        enc_mask = np.ones(enc_in.shape[:2], dtype=np.int64)
        hidden = self.encoder.run(None, {"inputs_embeds": enc_in, "attention_mask": enc_mask})[0]

        empty = np.zeros((1, 12, 0, 64), dtype=np.float32)
        past = {
            f"past_key_values.{i}.{k}.{v}": empty
            for i in range(self.LAYERS)
            for k in ("decoder", "encoder")
            for v in ("key", "value")
        }
        out_names = [o.name for o in self.decoder.get_outputs()]
        tokens = [self.DECODER_START]
        generated: list[int] = []
        for step in range(max_new_tokens):
            feed = {
                "inputs_embeds": self._embed([tokens[-1]]).astype(np.float32),
                "encoder_hidden_states": hidden,
                "encoder_attention_mask": enc_mask,
                "use_cache_branch": np.array([step > 0]),
                **past,
            }
            outs = dict(zip(out_names, self.decoder.run(None, feed), strict=True))
            logits = outs["logits"][0, -1].astype(np.float64)
            if step == 0:
                nxt = self.BOS  # forced_bos_token_id
            else:
                seq = tokens[1:]
                if len(seq) >= 2:  # no_repeat_ngram_size = 3
                    prefix = tuple(seq[-2:])
                    for i in range(len(seq) - 2):
                        if tuple(seq[i : i + 2]) == prefix:
                            logits[seq[i + 2]] = -np.inf
                nxt = int(np.argmax(logits))
            tokens.append(nxt)
            if nxt == self.EOS and step > 0:
                break
            generated.append(nxt)
            for i in range(self.LAYERS):
                for v in ("key", "value"):
                    past[f"past_key_values.{i}.decoder.{v}"] = outs[f"present.{i}.decoder.{v}"]
                    if step == 0:
                        past[f"past_key_values.{i}.encoder.{v}"] = outs[f"present.{i}.encoder.{v}"]
        text = self.tok.decode(generated, skip_special_tokens=True)
        return " ".join(text.replace("<pad>", " ").split())


# ------------------------------------------------------------------------------------------------ all models, loaded once
class VisionModels:
    _instance: VisionModels | None = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> VisionModels:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @cached_property
    def clip(self) -> Clip:
        return Clip()

    @cached_property
    def owl(self) -> Owl:
        return Owl()

    @cached_property
    def clipseg(self) -> ClipSeg:
        return ClipSeg()

    @cached_property
    def florence(self) -> Florence:
        return Florence()

    @staticmethod
    def versions() -> dict[str, str]:
        return {k: m.label for k, m in MODELS.items()}


def load_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def mask_png(mask: np.ndarray, color: tuple[int, int, int] = (220, 38, 38), alpha: int = 120) -> bytes:
    """A transparent PNG with the mask painted in, for overlaying on the photo."""
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[mask] = (*color, alpha)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))
