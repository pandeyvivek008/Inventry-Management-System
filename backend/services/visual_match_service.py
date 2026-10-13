"""SigLIP 2 image embeddings for product-photo retrieval.

The encoder is loaded lazily on CPU. No SKU, product-name, filename, or colour
heuristic participates in the visual score. If the optional model runtime or
weights are unavailable, callers fail closed instead of returning a guessed
nearest product.
"""
from __future__ import annotations

from io import BytesIO
from threading import Lock

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SCAN_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8000
MODEL_ID = "google/siglip2-base-patch16-224"
MODEL_VERSION = "siglip2-base-patch16-224:v1"
VECTOR_DIMENSIONS = 768
_encoder = None
_encoder_lock = Lock()
_inference_lock = Lock()


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


def _load_encoder():
    global _encoder
    if _encoder is not None:
        return _encoder
    with _encoder_lock:
        if _encoder is not None:
            return _encoder
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise ImageModelUnavailable(
                "SigLIP 2 runtime is not installed in this process. Configure the private visual-model service "
                "or install the model-service dependencies, then rebuild the product image index."
            ) from exc
        try:
            processor = AutoProcessor.from_pretrained(MODEL_ID)
            model = AutoModel.from_pretrained(MODEL_ID, low_cpu_mem_usage=True).to("cpu")
            model.eval()
            _encoder = (torch, model, processor)
            return _encoder
        except Exception as exc:
            raise ImageModelUnavailable(
                "SigLIP 2 could not load its model weights. Check Railway memory, outbound access, "
                "and the configured Hugging Face cache volume."
            ) from exc


def image_embedding(content: bytes) -> tuple[float, ...]:
    """Return a normalized 768-dimensional SigLIP 2 image embedding on CPU."""
    image = _open_image(content)
    torch, model, processor = _load_encoder()
    try:
        inputs = processor(images=image, return_tensors="pt")
        with _inference_lock, torch.inference_mode():
            embedding = model.get_image_features(**inputs).float()
            embedding = embedding / embedding.norm(dim=-1, keepdim=True).clamp(min=1e-12)
            values = embedding[0].tolist()
        return tuple(float(value) for value in values)
    except Exception as exc:
        raise ImageModelUnavailable("SigLIP 2 could not create an image embedding.") from exc
