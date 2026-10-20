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
    return display_image_metadata_sync(path)["size"]


def load_display_rgb(path: Path):
    """Return detached RGB pixels in display orientation, with no EXIF transform left."""
    from PIL import Image, ImageOps

    ensure_image_support(path)
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def display_image_metadata_sync(path: Path) -> dict:
    from PIL import Image, ImageOps

    ensure_image_support(path)
    with Image.open(path) as image:
        orientation = int(image.getexif().get(274, 1))
        return {"size": ImageOps.exif_transpose(image).size, "orientation": orientation}


def prepare_display_photo(source: Path, output: Path) -> None:
    # PNG inputs carry already oriented pixels, shared with analysis/focus detection.
    image = load_display_rgb(source)
    image.save(output, format="PNG")
