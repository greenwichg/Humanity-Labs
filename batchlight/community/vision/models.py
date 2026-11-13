"""The open-source vision models used to assess photos, pinned to exact versions and checked by SHA-256.

All four run on CPU with ONNX Runtime; no PyTorch is needed. Licences: CLIP (MIT), OWLv2 (Apache-2.0),
CLIPSeg (Apache-2.0), Florence-2 (MIT). Download them with `python -m batchlight.cli community-models download`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Model:
    key: str
    repo: str
    revision: str
    purpose: str
    licence: str
    files: tuple[ModelFile, ...]

    @property
    def label(self) -> str:
        return f"{self.repo}@{self.revision[:10]}"


MODELS: dict[str, Model] = {
    m.key: m
    for m in (
        Model(
            "clip",
            "Xenova/clip-vit-base-patch32",
            "6b7c523703ae26e39be4b03774966f8571c7f523",
            "Image classification: infrastructure type and visible condition (OpenAI CLIP ViT-B/32)",
            "MIT",
            (
                ModelFile(
                    "onnx/vision_model_quantized.onnx",
                    89117001,
                    "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
                ),
                ModelFile(
                    "onnx/text_model_quantized.onnx", 64504507, "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"
                ),
                ModelFile("tokenizer.json", 2224119, "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd"),
                ModelFile("preprocessor_config.json", 520, "6f638fb9401a6d6296feff533ee7efe657b787c49f954f82f5906b36ef2a1b1f"),
            ),
        ),
        Model(
            "owlv2",
            "Xenova/owlv2-base-patch16-ensemble",
            "3e255bc9bf304b3358a8c1945c0a9514eefe7f10",
            "Object detection: visible defects, plus faces and number plates to blur (Google OWLv2)",
            "Apache-2.0",
            (
                ModelFile(
                    "onnx/model_quantized.onnx", 155312754, "af8d6a041e4114b06eafabb03047350c7b8f136670a3f8afc7e73c5a74c2b24b"
                ),
                ModelFile("tokenizer.json", 2224254, "bbce3185c27ac4daf41ea2dfc864785bdcd1a42815e7646324af20edd671a578"),
                ModelFile("preprocessor_config.json", 425, "cf3e396635b797ee1a464e1b2836e98748f8edac19e89aaa2c93b55ac15b0064"),
            ),
        ),
        Model(
            "clipseg",
            "Xenova/clipseg-rd64-refined",
            "924dc94f85f58739f353f94258b33bc47eae4862",
            "Image segmentation: outline and measure visibly damaged areas (CLIPSeg)",
            "Apache-2.0",
            (
                ModelFile(
                    "onnx/model_quantized.onnx", 139115836, "049010b152839f339d6115e668d560860d0031f7b750b24828c07167ba68812a"
                ),
                ModelFile("tokenizer.json", 2224080, "342739d4a4b111d4b1bda83a96f5c518e1b162cc597252ac89ded17681791d31"),
                ModelFile("preprocessor_config.json", 378, "f0ae4c1dd6bffbbfbb9aa2f461489dd72b41f6906743a8bfd258532f09c0332c"),
            ),
        ),
        Model(
            "florence2",
            "onnx-community/Florence-2-base-ft",
            "e88a44eaf3791a35eae0c5a47b3dbcd36e67eb6f",
            "Vision-language model: plain-language description of the photo (Microsoft Florence-2)",
            "MIT",
            (
                ModelFile(
                    "onnx/vision_encoder_quantized.onnx",
                    93746540,
                    "3b79d54f23f666f731549db23cb070c35a979ce19cbd9720e90e67a78dc9768c",
                ),
                ModelFile(
                    "onnx/embed_tokens_quantized.onnx",
                    39390433,
                    "6b2258db1c8ee9b160576ccde3cd3814d83a2edaed0dd1c6ca9ff3c38fa62214",
                ),
                ModelFile(
                    "onnx/encoder_model_quantized.onnx",
                    43651493,
                    "f4ad7a68f1fb875d3bcf735ea14a7021b7ba7e83baf7cf10289881b4ed6d9b85",
                ),
                ModelFile(
                    "onnx/decoder_model_merged_quantized.onnx",
                    98177697,
                    "f22f52f980c33df0efa15932c2f3db6d9d3595ce6387eca938b8cfe23dc4c641",
                ),
                ModelFile("tokenizer.json", 2297961, "d69dcdb2323e124ac4f800cb9863ddccea0d7bb11e16125e8df3bd60f2f8aeac"),
                ModelFile("preprocessor_config.json", 2673, "c892857e34a7082284983a7717717d39c9bf7e574f1f41d80d4c918c97502efa"),
            ),
        ),
    )
}


def model_dir() -> Path:
    from ...config import get_settings

    configured = get_settings().model_dir
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "humanity-labs" / "models"


def file_path(model: Model, name: str) -> Path:
    return model_dir() / model.repo.replace("/", "--") / name


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status() -> dict[str, str]:
    """For each model: 'ready', or what is missing. Checks sizes only (fast); `verify` checks hashes."""
    out = {}
    for key, m in MODELS.items():
        missing = [f.path for f in m.files if not file_path(m, f.path).exists() or file_path(m, f.path).stat().st_size != f.size]
        out[key] = "ready" if not missing else "missing: " + ", ".join(missing)
    return out


def ready() -> bool:
    return all(v == "ready" for v in status().values())


def verify() -> list[str]:
    """Return the files whose SHA-256 does not match the pinned value."""
    return [
        f"{m.key}/{f.path}"
        for m in MODELS.values()
        for f in m.files
        if not file_path(m, f.path).exists() or _sha256(file_path(m, f.path)) != f.sha256
    ]


def download(client: httpx.Client | None = None) -> list[str]:
    """Download any missing file at its pinned revision, check its SHA-256, and only then move it into place."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, read=600.0), follow_redirects=True)
    fetched = []
    try:
        for m in MODELS.values():
            for f in m.files:
                dst = file_path(m, f.path)
                if dst.exists() and dst.stat().st_size == f.size and _sha256(dst) == f.sha256:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(dst.name + ".part")
                h = hashlib.sha256()
                url = f"https://huggingface.co/{m.repo}/resolve/{m.revision}/{f.path}"
                with client.stream("GET", url) as r, tmp.open("wb") as out:
                    r.raise_for_status()
                    for chunk in r.iter_bytes(1 << 20):
                        h.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != f.sha256:
                    tmp.unlink(missing_ok=True)
                    raise ValueError(f"{m.key}/{f.path}: SHA-256 does not match the pinned value; not installed")
                os.replace(tmp, dst)
                fetched.append(f"{m.key}/{f.path}")
                log.info("downloaded %s/%s (%d bytes)", m.key, f.path, f.size)
    finally:
        if own:
            client.close()
    return fetched
