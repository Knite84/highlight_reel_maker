"""Initialize optional Pillow codecs before any caller opens their files."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _register_heif() -> None:
    try:
        from pillow_heif import register_heif_opener
    except ImportError as exc:
        raise RuntimeError(
            "HEIC/HEIF decoding requires pillow-heif; rebuild the backend environment"
        ) from exc
    register_heif_opener()


def ensure_image_support(path: Path) -> None:
    if path.suffix.lower() in {".heic", ".heif"}:
        _register_heif()


def image_size_sync(path: Path) -> tuple[int, int]:
    from PIL import Image

    ensure_image_support(path)
    with Image.open(path) as image:
        return image.size
