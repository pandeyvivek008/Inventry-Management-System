"""Generate high-contrast printable Code128 barcode labels for each variant."""
from io import BytesIO

import barcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont
import qrcode


def _font(size=18):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _code128_image(value: str) -> Image.Image:
    """Render a large Code128 barcode; scanners/guns can read the raw value."""
    buf = BytesIO()
    code = barcode.get("code128", value, writer=ImageWriter())
    code.write(buf, options={
        "module_width": 0.45,
        "module_height": 22,
        "quiet_zone": 8,
        "font_size": 0,
        "text_distance": 2,
        "write_text": False,
        "dpi": 300,
    })
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def generate_variant_label(variant_code: str, product_name: str, size: str) -> bytes:
    """Barcode is the primary scannable area; all human text stays below it."""
    code_img = _code128_image(variant_code)
    width = max(640, code_img.width + 60)
    height = code_img.height + 125
    canvas = Image.new("RGB", (width, height), "white")
    x = (width - code_img.width) // 2
    canvas.paste(code_img, (x, 12))

    draw = ImageDraw.Draw(canvas)
    title_font = _font(22)
    code_font = _font(24)
    title = f"{product_name} | Size {size}"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, code_img.height + 28), title, fill="black", font=title_font)
    bbox = draw.textbbox((0, 0), variant_code, font=code_font)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, code_img.height + 65), variant_code, fill="black", font=code_font)

    out = BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def generate_product_barcode_label(sku: str, product_name: str) -> bytes:
    """Create one design-level Code128 label; scanner lookup opens all sizes."""
    value = f"PRODUCT:{sku.strip().upper()}"
    code_img = _code128_image(value)
    width = max(680, code_img.width + 60)
    height = code_img.height + 132
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(code_img, ((width - code_img.width) // 2, 10))
    draw = ImageDraw.Draw(canvas)
    title_font, code_font = _font(22), _font(19)
    for text, font, y in ((product_name[:48], title_font, code_img.height + 25), (value, code_font, code_img.height + 66)):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2, y), text, fill="black", font=font)
    out = BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def generate_product_qr_label(sku: str, product_name: str) -> bytes:
    """Create one design-level QR that opens every size in the inventory UI."""
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=3)
    qr.add_data(f"PRODUCT:{sku.strip().upper()}")
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    width = max(520, qr_image.width + 80)
    height = qr_image.height + 150
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(qr_image, ((width - qr_image.width) // 2, 12))
    draw = ImageDraw.Draw(canvas)
    title_font, code_font = _font(22), _font(20)
    title = product_name[:48]
    for text, font, y in ((title, title_font, qr_image.height + 32), (sku.upper(), code_font, qr_image.height + 76)):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2, y), text, fill="black", font=font)
    out = BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def generate_labels_sheet(variants: list[dict]) -> bytes:
    labels = [generate_variant_label(v["variant_code"], v["product_name"], v["size"]) for v in variants]
    images = [Image.open(BytesIO(b)).convert("RGB") for b in labels]
    if not images:
        raise ValueError("No variants to generate labels for.")

    cols = 2
    pad = 24
    cell_w = max(im.width for im in images) + pad
    cell_h = max(im.height for im in images) + pad
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        sheet.paste(im, (c * cell_w + pad // 2, r * cell_h + pad // 2))

    out = BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()
