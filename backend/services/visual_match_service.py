"""CLIP image embedding helpers for the product-photo matcher.

The encoder is loaded lazily on CPU. No SKU, product-name, filename, or colour
heuristic participates in the visual score. If the optional model runtime or
weights are unavailable, callers fail closed instead of returning a guessed
nearest product.
"""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SCAN_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8000
MODEL_ARCH = "ViT-B-32"
MODEL_PRETRAINED = "laion2b_s34b_b79k"
VECTOR_DIMENSIONS = 512


class InvalidProductImage(ValueError):
    """The browser supplied a non-image, damaged, or excessive image."""


class ImageModelUnavailable(RuntimeError):
    """The optional CLIP runtime or model weights are unavailable."""


def _open_image(content: bytes) -> Image.Image:
    if not content or len(content) > MAX_SCAN_BYTES:
        raise InvalidProductImage("Image must be between 1 byte and 10 MB.")
    try:
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise InvalidProductImage("Only JPG, PNG, and WEBP images are supported.")
            if width < 32 or height < 32 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise InvalidProductImage("Image dimensions must be between 32 and 8000 pixels.")
            source.verify()
        with Image.open(BytesIO(content)) as source:
            return ImageOps.exif_transpose(source).convert("RGB")
    except InvalidProductImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidProductImage("Please choose a clear JPG, PNG, or WEBP image.") from exc


def validate_image(content: bytes) -> None:
    """Validate and safely decode an image without loading the model."""
    _open_image(content)


def image_extension(content: bytes) -> str:
    """Return a browser-safe extension based on image bytes, not user filename."""
    _open_image(content)
    try:
        with Image.open(BytesIO(content)) as source:
            return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[source.format]
    except (UnidentifiedImageError, OSError, KeyError) as exc:
        raise InvalidProductImage("Only JPG, PNG, and WEBP images are supported.") from exc


@lru_cache(maxsize=1)
def _load_encoder():
    try:
        import open_clip
        import torch
    except ImportError as exc:
        raise ImageModelUnavailable(
            "Visual AI is not installed on this service. Install requirements-visual-ai.txt, "
            "then rebuild the product image index."
        ) from exc
    try:
        model, _, preprocess = open_clip.create_model_and_transforms(
            MODEL_ARCH,
            pretrained=MODEL_PRETRAINED,
            device="cpu",
        )
        model.eval()
        return torch, model, preprocess
    except Exception as exc:
        raise ImageModelUnavailable(
            "CLIP could not load its model weights. Check Railway memory, outbound access, "
            "and the configured Hugging Face cache volume."
        ) from exc


def image_embedding(content: bytes) -> tuple[float, ...]:
    """Return a normalized 512-dimensional CLIP image embedding on CPU."""
    image = _open_image(content)
    torch, model, preprocess = _load_encoder()
    try:
        tensor = preprocess(image).unsqueeze(0)
        with torch.inference_mode():
            embedding = model.encode_image(tensor).float()
            embedding = embedding / embedding.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        return tuple(float(value) for value in embedding[0].tolist())
    except Exception as exc:
        raise ImageModelUnavailable("CLIP could not create an image embedding.") from exc
