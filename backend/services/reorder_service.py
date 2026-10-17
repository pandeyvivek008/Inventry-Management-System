"""Marketplace-order shortage picklist.

Pending orders reserve current stock until dispatch. The picklist reports
pending demand, current physical stock, dispatched units for context, and the
remaining quantity to restock. Dispatched units are not subtracted twice.
"""
from datetime import datetime, timezone
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import func, case, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from models import Variant, Product, ReorderBatch, ReorderBatchItem, OrderLine, OrderLineStatus


_COLOR_NAMES = (
    "navy blue", "sky blue", "royal blue", "baby pink", "hot pink", "rani pink", "rose pink",
    "dark green", "light green", "olive green", "sea green", "bottle green", "off white", "off-white", "multi color",
    "multicolor", "mustard", "maroon", "magenta", "turquoise", "teal", "lavender",
    "purple", "violet", "fuchsia", "mauve", "mehendi", "lemon yellow", "pink", "red", "blue", "green", "yellow", "orange", "peach",
    "coral", "rust", "wine", "black", "white", "cream", "ivory", "beige", "brown", "coffee", "lemon", "rama",
    "grey", "gray", "silver", "gold", "golden", "charcoal", "mint", "lime", "cyan",
    "रानी", "गुलाबी", "लाल", "नीला", "नीली", "हरा", "हरी", "पीला", "पीली", "काला",
    "काली", "सफेद", "क्रीम", "बैंगनी", "भूरा", "भूरी",
)
_SIZE_ORDER = (
    "XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "5XL",
    "FREE SIZE", "FREESIZE", "ONE SIZE", "OS",
)


def _identity_words(value: str | None) -> str:
    """Normalize names for matching while retaining their readable spelling elsewhere."""
    value = str(value or "").casefold().replace("_", " ")
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def _product_party(item: dict) -> str:
    party = str(item.get("party") or "").strip()
    if party:
        return party
    # Marketplace codes commonly use BRAND_PARTY_PRODUCT_COLOUR_SIZE. The
    # product SKU has already had its final size suffix separated into Variant.
    sku_parts = [part for part in re.split(r"[_\s]+", str(item.get("sku") or "").strip()) if part]
    return sku_parts[1].replace("-", " ").title() if len(sku_parts) >= 3 else "Unassigned"


def _product_name_and_colour(item: dict) -> tuple[str, str]:
    name = str(item.get("name") or item.get("sku") or "Unknown product").strip()
    normalized_name = _identity_words(name)
    normalized_sku = _identity_words(str(item.get("sku") or ""))
    sku_words = normalized_sku.split()
    sku_product_words = sku_words[2:] if len(sku_words) >= 3 else sku_words
    name_words = normalized_name.split()
    original_name_words = re.findall(r"[\w]+", name.replace("_", " "), flags=re.UNICODE)
    stripped_prefix_words = 0
    if len(sku_words) >= 3 and name_words and name_words[0] == sku_words[0]:
        # Drop a matching brand prefix from names that contain it. For SKU-as-
        # name imports, also drop the following party token. Full variant SKUs
        # stay visible in the code column below.
        stripped_prefix_words = 1
        if len(name_words) > 1 and name_words[1] == sku_words[1]:
            stripped_prefix_words = 2
        name_words = name_words[stripped_prefix_words:]
        original_name_words = original_name_words[stripped_prefix_words:]
        normalized_name = " ".join(name_words)
    found_colour = ""
    colour_pattern = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(colour) for colour in sorted(_COLOR_NAMES, key=len, reverse=True)) + r")(?!\w)",
        re.IGNORECASE,
    )
    match = colour_pattern.search(normalized_name)
    source = normalized_name
    if not match:
        match = colour_pattern.search(" ".join(sku_product_words))
        source = " ".join(sku_product_words)
    if match:
        found_colour = " ".join(match.group(1).casefold().split()).title()
        if source == normalized_name:
            normalized_name = " ".join((normalized_name[:match.start()] + " " + normalized_name[match.end():]).split())

    # Use a clean, stable label in the sheet. If the entire name was a colour,
    # preserve the original name so that the product remains identifiable.
    display_name = " ".join(original_name_words).strip()
    original_colour_match = colour_pattern.search(display_name.casefold())
    if original_colour_match:
        display_name = " ".join((display_name[:original_colour_match.start()] + " " + display_name[original_colour_match.end():]).split())
    if not display_name:
        display_name = name
    colour = found_colour or "—"
    return display_name, colour


def _size_sort_key(size: str):
    normalized = " ".join(str(size or "—").upper().split())
    try:
        return (0, _SIZE_ORDER.index(normalized), "")
    except ValueError:
        if normalized.isdigit():
            return (1, int(normalized), "")
        return (2, 0, normalized)


def _sku_component(value: str | None) -> str:
    """Normalize a display value for the brand-free SKU written to the sheet."""
    return "_".join(re.findall(r"[\w]+", str(value or "").strip().upper(), flags=re.UNICODE))


def _sku_has_size_suffix(sku: str, size: str) -> bool:
    normalized_sku = _sku_component(sku)
    normalized_size = _sku_component(size)
    return bool(normalized_size and (
        normalized_sku == normalized_size or normalized_sku.endswith(f"_{normalized_size}")
    ))


def generate_reorder_list(db: Session, save_batch: bool = True, part: str | None = None):
    """Return one row per stockable size with replenishment demand.

    Pending orders reserve physical stock until dispatch. Dispatched counts are
    shown separately for context, since those quantities have already reduced
    physical stock. Required is based only on pending orders.
    """
    query = (
        db.query(
            Variant,
            func.coalesce(func.sum(case(
                (OrderLine.status == OrderLineStatus.PENDING,
                 OrderLine.qty_ordered - OrderLine.qty_fulfilled), else_=0
            )), 0).label("ordered_qty"),
            func.coalesce(func.sum(OrderLine.qty_fulfilled), 0).label("dispatched_qty"),
        )
        .join(Product, Variant.product_id == Product.id)
        .options(selectinload(Variant.product).joinedload(Product.vendor))
        .outerjoin(
            OrderLine,
            (OrderLine.variant_id == Variant.id)
            & (OrderLine.status.in_([OrderLineStatus.PENDING, OrderLineStatus.FULFILLED])),
        )
        .filter(
            Variant.last_counted_at.isnot(None),
            Variant.is_deleted == False,  # noqa: E712
            Product.is_deleted == False,  # noqa: E712
        )
        .group_by(Variant.id)
    )
    if part and part.strip():
        for token in re.findall(r"[\w]+", part.strip().casefold()):
            like = f"%{token}%"
            query = query.filter(or_(
                Variant.variant_code.ilike(like), Product.sku.ilike(like),
                Product.name.ilike(like), Variant.size.ilike(like),
            ))

    rows = query.all()
    items = []
    selected = []
    for v, ordered_qty, dispatched_qty in rows:
        ordered_qty = int(ordered_qty or 0)
        dispatched_qty = int(dispatched_qty or 0)
        # Only the unfulfilled remainder reserves stock. Dispatched quantities
        # have already reduced physical stock and must not create a second shortage.
        if ordered_qty <= 0 and dispatched_qty <= 0:
            continue
        physical_stock = int(v.current_stock or 0)
        # Negative stock can exist in older databases after an over-dispatch.
        # Carry that deficit forward so the picklist does not hide missing units.
        # A negative balance represents an older stock deficit, so replenishing
        # only pending demand would leave the physical balance short. Include
        # that deficit in the quantity needed to cover pending orders and bring
        # stock back to zero.
        required = max(ordered_qty - physical_stock, 0)
        items.append({
            "sku": v.product.sku,
            "size": v.size.upper(),
            "variant_code": v.variant_code,
            "name": v.product.name,
            "party": v.product.vendor.name if v.product.vendor else None,
            "current_stock": physical_stock,
            "ordered_qty": ordered_qty,
            "dispatched_qty": dispatched_qty,
            "required_qty": required,
            "image_path": v.product.image_path,
            "is_new": False,
        })
        if required > 0:
            selected.append((v, required))

    # Orders that could not be mapped to an existing product must NOT create
    # a product automatically. They are surfaced as NEW in the picklist so
    # the team can identify the SKU and arrange the quantity manually.
    unknown_query = (
        db.query(
            OrderLine.sku, OrderLine.size,
            func.sum(OrderLine.qty_ordered).label("ordered_qty"),
        )
        .filter(OrderLine.status == OrderLineStatus.SKU_NOT_FOUND)
        .group_by(OrderLine.sku, OrderLine.size)
    )
    if part and part.strip():
        for token in re.findall(r"[\w]+", part.strip().casefold()):
            like = f"%{token}%"
            unknown_query = unknown_query.filter(or_(OrderLine.sku.ilike(like), OrderLine.size.ilike(like)))
    for sku, size, ordered_qty in unknown_query.all():
        ordered_qty = int(ordered_qty or 0)
        if ordered_qty <= 0:
            continue
        items.append({
            "sku": str(sku).upper(),
            "size": str(size or "—").upper(),
            "variant_code": str(sku).upper(),
            "name": "NEW / UNMAPPED SKU",
            "party": None,
            "current_stock": 0,
            "ordered_qty": ordered_qty,
            "dispatched_qty": 0,
            "required_qty": ordered_qty,
            "image_path": None,
            "is_new": True,
        })

    items.sort(key=lambda x: (-x["required_qty"], x["current_stock"], x["sku"]))

    if save_batch and selected:
        batch = ReorderBatch()
        db.add(batch)
        db.flush()
        for v, required in selected:
            db.add(ReorderBatchItem(
                batch_id=batch.id,
                variant_id=v.id,
                stock_at_generation=v.current_stock,
                required_qty=required,
            ))
        db.commit()

    return items


def reorder_list_to_excel(items: list[dict]) -> bytes:
    """Export shortages in the picklist's SKU / Size / Quantity layout.

    Matching brand SKUs are grouped by party, product and colour. Their
    quantities are added by size, and the output SKU is built from those
    brand-free product details plus the size. No grand-total row is written.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Reorder"

    grouped: dict[tuple[str, str, str, str], dict] = {}
    for item in items:
        party = _product_party(item)
        product_name, colour = _product_name_and_colour(item)
        size = " ".join(str(item.get("size") or "—").upper().split())
        sku = str(item.get("sku") or "").strip()
        if item.get("is_new"):
            # Keep separate unmapped designs apart until they are reconciled.
            key = ("__unmapped__", sku.casefold(), "", "")
            party, product_name, colour = "Unmapped", sku or "Unknown SKU", "—"
        else:
            unassigned_guard = sku.casefold() if party == "Unassigned" else ""
            key = (_identity_words(party), _identity_words(product_name), _identity_words(colour), unassigned_guard)
        row = grouped.setdefault(key, {
            "party": party,
            "product": product_name,
            "colour": colour,
            "quantities": {},
            "source_skus": {},
            "is_new": bool(item.get("is_new")),
        })
        row["quantities"][size] = row["quantities"].get(size, 0) + int(item.get("required_qty") or 0)
        source_sku = str(item.get("sku") or "").strip()
        if source_sku:
            row["source_skus"].setdefault(size, set()).add(source_sku)

    rows = sorted(grouped.values(), key=lambda row: (
        _identity_words(row["party"]), _identity_words(row["product"]), _identity_words(row["colour"])
    ))
    headers = ["SKU", "Size", "Quantity"]
    header_font = Font(name="Aptos", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="24466C", end_color="24466C", fill_type="solid")
    thin_line = Side(style="thin", color="D8E2EC")
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill

    row_idx = 2
    for index, item in enumerate(rows):
        product_words = _identity_words(item["product"]).split()
        party_words = _identity_words(item["party"]).split()
        # Some imported product names still start with the party token. Keep it
        # only once in the generated SKU.
        if party_words and product_words[:len(party_words)] == party_words:
            product_words = product_words[len(party_words):]

        if not item["is_new"]:
            sku_parts = [
                _sku_component(item["party"] if item["party"] != "Unassigned" else ""),
                _sku_component(" ".join(product_words)),
                _sku_component(item["colour"] if item["colour"] != "—" else ""),
            ]
            base_sku = "_".join(part for part in sku_parts if part)
            if not base_sku:
                base_sku = next(iter(sorted(next(iter(item["source_skus"].values()), {"UNKNOWN"}))))

        sizes = sorted(item["quantities"], key=_size_sort_key)
        for size in sizes:
            quantity = item["quantities"][size]
            size_sku = _sku_component(size)
            if item["is_new"]:
                # Unmapped lines have no safe product identity yet. Preserve
                # the source SKU for that size so it can be reconciled.
                sku = next(iter(sorted(item["source_skus"].get(size) or {"UNKNOWN"})))
            else:
                sku = base_sku
            if size_sku and not _sku_has_size_suffix(sku, size):
                sku = f"{sku}_{size_sku}" if sku else size_sku
            values = [sku, size, quantity]
            for col, value in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.font = Font(name="Aptos", size=10, color="172B4D")
                cell.alignment = Alignment(vertical="center", horizontal="right" if col == 3 else "left")
                cell.border = Border(bottom=thin_line)
                if index % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F2F7FC")
                if col == 3:
                    cell.font = Font(name="Aptos", size=11, bold=True, color="173B63")
            ws.row_dimensions[row_idx].height = 22
            row_idx += 1
        # Keep each product / colour block visually separate in Excel.
        if index < len(rows) - 1:
            row_idx += 1

    for column, width in {"A": 48, "B": 16, "C": 18}.items():
        ws.column_dimensions[column].width = width
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.orientation = "landscape"
    ws.print_title_rows = "1:1"
    ws.sheet_properties.outlinePr.summaryBelow = True
    ws.auto_filter.ref = f"A1:C{max(1, ws.max_row)}"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
