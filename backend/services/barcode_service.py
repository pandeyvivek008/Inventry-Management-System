"""
Generates a QR code per variant (product + size), encoding just the
variant_code (e.g. "392-M"). Scanning it with a phone camera - via the
in-browser scanner on the Manual Recount screen - fills in the SKU field
instantly instead of someone typing it.

Why QR over a traditional 1D barcode: any phone camera can read a QR code
without a dedicated scanner or a scanning app; a small business's staff
almost certainly have phones already. If a real barcode scanner is bought
later, it can be pointed at the same QR code's payload just as easily, or
Code128 labels can be generated the same way by swapping the library call
below - the rest of the system (variant_code as the lookup key) doesn't
change either way.
"""
from io import BytesIO

import qrcode
from PIL import Image, ImageDraw, ImageFont


def generate_variant_label(variant_code: str, product_name: str, size: str) -> bytes:
    """Returns a PNG: QR code on top, human-readable text below (so a
    misprint or a torn QR can still be read and typed in by hand)."""
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(variant_code)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    try:
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except OSError:
        font_bold = ImageFont.load_default()
        font_small = ImageFont.load_default()

    measurer = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    code_text = variant_code
    name_text = f"{product_name[:34]} - {size}"
    code_w = measurer.textbbox((0, 0), code_text, font=font_bold)[2]
    name_w = measurer.textbbox((0, 0), name_text, font=font_small)[2]

    label_w = int(max(qr_img.width, code_w, name_w) + 16)  # +16 side padding
    text_h = 46
    canvas = Image.new("RGB", (label_w, qr_img.height + text_h), "white")
    canvas.paste(qr_img, ((label_w - qr_img.width) // 2, 0))

    draw = ImageDraw.Draw(canvas)
    draw.text(((label_w - code_w) / 2, qr_img.height + 4), code_text, fill="black", font=font_bold)
    draw.text(((label_w - name_w) / 2, qr_img.height + 24), name_text, fill="#444444", font=font_small)

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def generate_labels_sheet(variants: list[dict]) -> bytes:
    """variants: list of {"variant_code", "product_name", "size"}.
    Lays labels out in a grid on one PNG sheet, ready to print and cut -
    the practical way to print a batch of shelf/bin tags in one go."""
    labels = [
        generate_variant_label(v["variant_code"], v["product_name"], v["size"])
        for v in variants
    ]
    images = [Image.open(BytesIO(b)) for b in labels]
    if not images:
        raise ValueError("No variants to generate labels for.")

    cols = 4
    pad = 14
    cell_w = max(im.width for im in images) + pad
    cell_h = max(im.height for im in images) + pad
    rows = (len(images) + cols - 1) // cols

    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        sheet.paste(im, (c * cell_w + pad // 2, r * cell_h + pad // 2))

    buf = BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()
