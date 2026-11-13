"""Where community photos are kept.

Local files for development. Production storage is designed as a private S3 bucket (infra/terraform/platform/media.tf)
and is switched on only when that change is approved; until then uploads are off in production.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import get_settings

_KEY = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,250}$")


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key) or ".." in key:
            raise ValueError("invalid storage key")
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def storage() -> LocalStorage:
    settings = get_settings()
    if settings.media_s3_bucket:
        # Never fall back to the container's disk when a bucket is configured: photos would vanish on the next deploy.
        raise RuntimeError(
            "BATCHLIGHT_MEDIA_S3_BUCKET is set, but the S3 photo store is not built yet. "
            "See docs/COMMUNITY.md, 'Turning on uploads in production'."
        )
    return LocalStorage(settings.media_dir)


def keys(post_id) -> dict[str, str]:
    base = f"posts/{post_id}"
    return {"photo": f"{base}/photo.jpg", "thumb": f"{base}/thumb.jpg", "mask": f"{base}/damage-mask.png"}
