"""Fast, local visual matching for the product-photo catalog.

This is deliberately a *matching* service, not a claim that a photograph can
identify every garment with certainty.  It compares the scan against the
photos already saved on products and always returns a ranked shortlist for a
person to confirm.  It has no cloud/API dependency and is suitable for the
small-to-medium catalogs this app starts with.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SCAN_BYTES = 10 * 1024 * 1024
_RESAMPLE = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)


class InvalidProductImage(ValueError):
    """The browser supplied a non-image or a damaged image."""


@dataclass(frozen=True)
class ImageSignature:
    colors: tuple[float, ...]
    dhash: int


def _open_image(content: bytes) -> Image.Image:
    if not content or len(content) > MAX_SCAN_BYTES:
        raise InvalidProductImage("Image must be between 1 byte and 10 MB.")
    try:
        with Image.open(BytesIO(content)) as source:
            source.verify()
        with Image.open(BytesIO(content)) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidProductImage("Please choose a clear JPG, PNG, WEBP, or other valid image.") from exc


def image_signature(content: bytes) -> ImageSignature:
    """Build a compact colour + shape signature without a heavy ML runtime."""
    image = _open_image(content)
    # A six-bin RGB histogram gives useful colour/design separation while
    # dHash still rewards matching embroidery/outline shapes.
    small = ImageOps.fit(image, (96, 96), method=_RESAMPLE, centering=(0.5, 0.5))
    histogram = [0] * 216
    pixels = small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()
    for red, green, blue in pixels:
        histogram[(red // 43) * 36 + (green // 43) * 6 + (blue // 43)] += 1
    total = float(96 * 96)
    colors = tuple(value / total for value in histogram)

    grey = ImageOps.fit(image.convert("L"), (9, 8), method=_RESAMPLE, centering=(0.5, 0.5))
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | int(grey.getpixel((x, y)) > grey.getpixel((x + 1, y)))
    return ImageSignature(colors=colors, dhash=bits)


def score_signatures(scan: ImageSignature, reference: ImageSignature) -> float:
    """Return an explainable 0..1 similarity score.

    Histogram cosine similarity is intentionally weighted more than dHash:
    shop photos often have a different crop/background, but colour remains a
    very useful discriminator for this catalogue workflow.
    """
    dot = sum(a * b for a, b in zip(scan.colors, reference.colors))
    scan_norm = sum(a * a for a in scan.colors) ** 0.5
    ref_norm = sum(a * a for a in reference.colors) ** 0.5
    color_score = dot / (scan_norm * ref_norm) if scan_norm and ref_norm else 0.0
    hash_distance = (scan.dhash ^ reference.dhash).bit_count()
    shape_score = 1.0 - hash_distance / 64.0
    return max(0.0, min(1.0, color_score * 0.70 + shape_score * 0.30))


def score_image_files(scan_content: bytes, image_path: Path) -> float:
    """Compare a browser-uploaded scan to one locally stored reference image."""
    return score_signatures(image_signature(scan_content), image_signature(image_path.read_bytes()))
