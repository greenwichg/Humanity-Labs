"""Image handling that needs no model: decoding, metadata stripping, quality checks, duplicate hashes and blurring.

Runs in the web process when a photo is uploaded, so it uses only Pillow and NumPy and finishes in well under a second.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 40_000_000
STORED_MAX_SIDE = 2048  # photos are stored at most this size, metadata removed
ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

# Quality thresholds, measured on a 1024-pixel working copy. "reject" stops the upload; "warn" keeps the post but
# leaves the photo out of scores, because the evidence in it cannot be judged.
MIN_SIDE_REJECT, MIN_SIDE_WARN = 320, 480
SHARPNESS_REJECT, SHARPNESS_WARN = 8.0, 30.0
BRIGHTNESS_REJECT, BRIGHTNESS_WARN = 18.0, 45.0
OVEREXPOSED_WARN = 235.0
CONTRAST_WARN = 18.0

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageRejected(ValueError):
    """The file is not a usable photo; the message is shown to the person uploading it."""


@dataclass
class Quality:
    width: int
    height: int
    sharpness: float  # variance of the Laplacian; low means blurred
    brightness: float  # mean luminance 0-255
    contrast: float  # luminance standard deviation
    clipped_dark: float  # share of nearly black pixels
    clipped_bright: float  # share of nearly white pixels
    problems: list[str]  # plain-language problems found
    usable: bool  # good enough to be scored

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Prepared:
    data: bytes  # re-encoded JPEG, metadata removed
    mime: str
    width: int
    height: int
    sha256: str  # of the uploaded file, for exact duplicates
    pixel_sha256: str  # of the decoded pixels, so a re-saved copy of the same photo is caught too
    phash: int  # perceptual hash (64-bit DCT)
    dhash: int  # difference hash (64-bit)
    quality: Quality
    taken_lat: float | None  # GPS position from the photo's metadata, offered to the uploader, never stored with the image
    taken_lng: float | None


def _signed64(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_DCT32 = _dct_matrix(32)


def phash(img: Image.Image) -> int:
    """64-bit perceptual hash: low frequencies of a 32x32 DCT, compared with their median."""
    g = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT32 @ g @ _DCT32.T)[:8, :8].flatten()
    bits = low > np.median(low[1:])
    return _signed64(int("".join("1" if b else "0" for b in bits), 2))


def dhash(img: Image.Image) -> int:
    g = np.asarray(img.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return _signed64(int("".join("1" if b else "0" for b in bits), 2))


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


def _laplacian_variance(gray: np.ndarray) -> float:
    g = gray.astype(np.float64)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def assess_quality(img: Image.Image) -> Quality:
    w, h = img.size
    work = img.convert("L")
    work.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    g = np.asarray(work, dtype=np.uint8)
    sharp = _laplacian_variance(g)
    bright = float(g.mean())
    contrast = float(g.std())
    dark = float((g < 12).mean())
    light = float((g > 243).mean())
    problems: list[str] = []
    if min(w, h) < MIN_SIDE_WARN:
        problems.append(f"Low resolution ({w} × {h} pixels): small details cannot be judged.")
    if sharp < SHARPNESS_WARN:
        problems.append("The photo looks blurred or out of focus.")
    if bright < BRIGHTNESS_WARN:
        problems.append("The photo is too dark to see the surface clearly.")
    elif bright > OVEREXPOSED_WARN or light > 0.45:
        problems.append("The photo is overexposed: much of it is washed out.")
    if contrast < CONTRAST_WARN:
        problems.append("The photo has very little contrast.")
    return Quality(
        w, h, round(sharp, 1), round(bright, 1), round(contrast, 1), round(dark, 3), round(light, 3), problems, not problems
    )


def _gps(img: Image.Image) -> tuple[float | None, float | None]:
    try:
        gps = img.getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None, None

        def deg(v) -> float:
            d, m, s = (float(x) for x in v)
            return d + m / 60 + s / 3600

        lat, lng = deg(gps[2]), deg(gps[4])
        if gps.get(1) == "S":
            lat = -lat
        if gps.get(3) == "W":
            lng = -lng
        if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0.0, 0.0):
            return round(lat, 6), round(lng, 6)
    except Exception:  # noqa: BLE001 - malformed metadata is common; the photo is still usable
        return None, None
    return None, None


def prepare(raw: bytes) -> Prepared:
    """Decode, check and normalise an uploaded photo. Raises ImageRejected with a plain-language reason."""
    if len(raw) > MAX_BYTES:
        raise ImageRejected(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Please send a smaller photo.")
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise ImageRejected("This file is not a photo we can read. Please send a JPEG, PNG or WebP image.") from None
    if fmt not in ALLOWED_FORMATS:
        raise ImageRejected("Please send a JPEG, PNG or WebP photo.")
    if getattr(img, "n_frames", 1) > 1:
        raise ImageRejected("Animated images are not accepted. Please send a single photo.")
    lat, lng = _gps(img)
    img = ImageOps.exif_transpose(img).convert("RGB")  # apply the camera's rotation, then drop all metadata
    w, h = img.size
    if min(w, h) < MIN_SIDE_REJECT:
        raise ImageRejected(
            f"The photo is too small ({w} × {h} pixels). Please send one at least {MIN_SIDE_WARN} pixels on its shorter side."
        )
    quality = assess_quality(img)
    if quality.sharpness < SHARPNESS_REJECT:
        raise ImageRejected("The photo is too blurred to show anything clearly. Please take it again, holding the camera still.")
    if quality.brightness < BRIGHTNESS_REJECT:
        raise ImageRejected("The photo is too dark to show anything. Please take it again in daylight or with more light.")
    pixel_sha = hashlib.sha256(np.asarray(img, dtype=np.uint8).tobytes()).hexdigest()
    ph, dh = phash(img), dhash(img)
    stored = img.copy()
    stored.thumbnail((STORED_MAX_SIDE, STORED_MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    stored.save(buf, "JPEG", quality=88, optimize=True, progressive=True)  # no EXIF, no GPS, no camera details
    return Prepared(
        buf.getvalue(),
        "image/jpeg",
        stored.width,
        stored.height,
        hashlib.sha256(raw).hexdigest(),
        pixel_sha,
        ph,
        dh,
        quality,
        lat,
        lng,
    )


def blur_regions(data: bytes, boxes: list[tuple[float, float, float, float]]) -> bytes:
    """Blur rectangles given as fractions of the image (x, y, width, height). Returns a new JPEG."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    W, H = img.size
    for x, y, w, h in boxes:
        # Detector boxes around small things like plates are often a little off, so the margin is generous.
        pad_x, pad_y = max(w * 0.25, 0.006), max(h * 0.4, 0.006)
        left, top = max(0, int((x - pad_x) * W)), max(0, int((y - pad_y) * H))
        right, bottom = min(W, int((x + w + pad_x) * W)), min(H, int((y + h + pad_y) * H))
        if right - left < 2 or bottom - top < 2:
            continue
        region = img.crop((left, top, right, bottom))
        radius = max(6, int(max(right - left, bottom - top) / 6))
        img.paste(region.filter(ImageFilter.GaussianBlur(radius)), (left, top))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True, progressive=True)
    return buf.getvalue()


def thumbnail(data: bytes, max_side: int = 640) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    return buf.getvalue()
