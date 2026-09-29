"""
Turns "which variants are low" into the daily list sent to parties/vendors:
current stock + how much is needed to top back up to a healthy level -
size-wise, since a design can be short in M while fine in XL.

Sorted by current_stock ascending (most urgent / closest to zero first) -
an O(n log n) sort, cheap even at catalog sizes far larger than a small
apparel business will ever have, so no need for a fancier structure here.
"""
from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session, joinedload

from models import Variant, Product, ReorderBatch, ReorderBatchItem


def generate_reorder_list(db: Session, save_batch: bool = True):
    low_stock_variants = (
        db.query(Variant)
        .options(joinedload(Variant.product).joinedload(Product.vendor))
        .filter(
            Variant.last_counted_at.isnot(None),  # never-counted sizes must not be reordered
            Variant.current_stock <= Variant.reorder_threshold,
        )
        .order_by(Variant.current_stock.asc())
        .all()
    )

    items = []
    for v in low_stock_variants:
        required = max(v.target_stock_level - v.current_stock, 0)
        items.append({
            "sku": v.product.sku,
            "size": v.size,
            "variant_code": v.variant_code,
            "name": v.product.name,
            "current_stock": v.current_stock,
            "required_qty": required,
            "vendor": v.product.vendor.name if v.product.vendor else None,
            "image_path": v.product.image_path,
        })

    if save_batch and items:
        batch = ReorderBatch()
        db.add(batch)
        db.flush()
        for v in low_stock_variants:
            required = max(v.target_stock_level - v.current_stock, 0)
            db.add(ReorderBatchItem(
                batch_id=batch.id, variant_id=v.id,
                stock_at_generation=v.current_stock, required_qty=required,
            ))
        db.commit()

    return items


def reorder_list_to_excel(items: list[dict]) -> bytes:
    """Build the sheet that gets sent to a party/vendor each morning."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Reorder List"

    headers = ["SKU", "Size", "Product Name", "Current Stock", "Required Qty", "Vendor"]
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F3B52", end_color="2F3B52", fill_type="solid")
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill

    for row_idx, item in enumerate(items, start=2):
        ws.cell(row=row_idx, column=1, value=item["sku"]).font = Font(name="Arial")
        ws.cell(row=row_idx, column=2, value=item["size"]).font = Font(name="Arial")
        ws.cell(row=row_idx, column=3, value=item["name"]).font = Font(name="Arial")
        ws.cell(row=row_idx, column=4, value=item["current_stock"]).font = Font(name="Arial")
        ws.cell(row=row_idx, column=5, value=item["required_qty"]).font = Font(name="Arial")
        ws.cell(row=row_idx, column=6, value=item.get("vendor") or "").font = Font(name="Arial")

    widths = [14, 8, 38, 14, 14, 22]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    ws.cell(row=1, column=8, value=f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
