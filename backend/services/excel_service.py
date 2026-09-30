"""
Orders sheet -> inventory deduction pipeline.

The sheet can be .xlsx or .csv and can identify a product in whichever way
the marketplace export does. For each row the matcher tries, in order:

  1. any SKU-like column against a variant's own codes - our variant_code
     ("392-M"), the marketplace "seller sku code" ("TWINS_REHA_WHITE_M") or
     the marketplace's internal "sku code" ("TWLAKASS137693087"). These
     identify one exact size, so no Size column is needed.
  2. otherwise base SKU + a Size column (product.sku, size).

ALGORITHM (why it's built this way, not just "loop and query"):

1. PARSE once into (candidate codes, size, qty) tuples.
2. RESOLVE with batch lookups. All distinct codes in the sheet are looked up
   in a handful of `IN (...)` queries (chunked) and loaded into two hash
   maps - code -> variant and (sku, size) -> variant - so resolving a row is
   O(1) instead of one query per row (the classic N+1 problem).
3. AGGREGATE per resolved variant in one linear pass. A day's export often
   repeats the same size on many rows; 300 rows over 40 variants becomes 40
   stock updates, not 300.
4. DEDUCT ATOMICALLY per variant via inventory_service.apply_stock_change(),
   safe even if another upload or a recount is running at the same time.
5. IDEMPOTENCY: the file's SHA-256 is stored with a unique constraint, so the
   same file uploaded twice is rejected instead of silently double-deducting.
"""
import hashlib
from collections import defaultdict

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from models import Product, Variant, OrderUpload, OrderLine, OrderLineStatus, TransactionType
from services.inventory_service import apply_stock_change, InsufficientStockError
from services.alert_service import check_variant_alert
from services.table_reader import read_table, to_int, clean_code

# Most specific first. All that are present in the sheet are tried per row.
SKU_COLUMN_CANDIDATES = [
    "seller sku code", "seller sku", "seller_sku_code", "variant code", "variant_code",
    "sku code", "sku id", "sku", "style code", "stylecode", "style id", "product sku",
    "ean", "hsn/sku", "item sku", "item sku code", "product code", "barcode",
]
SIZE_COLUMN_CANDIDATES = ["size", "size name", "variant size", "size ordered"]
QTY_COLUMN_CANDIDATES = [
    "quantity", "qty", "qty ordered", "order qty", "order quantity", "units",
    "item quantity", "no of units", "no. of units", "pieces", "final quantity",
]

NOT_COUNTED_NOTE = "Stock for this size was never counted - count it first (Bulk Stock Count)."
AMBIGUOUS_NOTE = "Code matches more than one size - add a Size column."
NO_QTY_COLUMN_NOTE = "No quantity column found - each row was counted as 1 piece."
CHUNK = 500


class DuplicateUploadError(Exception):
    pass


class BadOrderFileError(Exception):
    pass


def _find_columns(columns, candidates):
    """All columns whose (stripped, lowercased) name is a candidate, in candidate order."""
    lower_map = {str(c).strip().lower(): c for c in columns}
    return [lower_map[cand] for cand in candidates if cand in lower_map]


def _chunks(items, size=CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def process_order_excel(db: Session, file_bytes: bytes, filename: str, created_by: str = "excel_upload"):
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    if db.query(OrderUpload).filter(OrderUpload.file_hash == file_hash).first():
        raise DuplicateUploadError(
            "This exact file was already processed before (matched by content hash). "
            "If today's orders genuinely repeat yesterday's file by mistake, rename/re-export it."
        )

    df = read_table(file_bytes, filename)
    if df.empty:
        raise BadOrderFileError("The file has no rows.")

    sku_cols = _find_columns(df.columns, SKU_COLUMN_CANDIDATES)
    size_cols = _find_columns(df.columns, SIZE_COLUMN_CANDIDATES)
    qty_cols = _find_columns(df.columns, QTY_COLUMN_CANDIDATES)
    if not sku_cols:
        raise BadOrderFileError(
            f"Could not find a SKU/code column. Found columns: {list(df.columns)}. "
            f"Expected one of {SKU_COLUMN_CANDIDATES}."
        )
    size_col = size_cols[0] if size_cols else None
    # No quantity column is a real, common shape too: an order/shipment manifest
    # where every row IS one physical piece, so there's nothing to count in a
    # column - the row itself is the unit. Assume qty=1 per row rather than
    # rejecting the whole file for a column that was never meant to exist.
    qty_col = qty_cols[0] if qty_cols else None
    used_default_qty = qty_col is None

    # --- Step 1: parse rows ---
    parsed = []  # (candidate codes, size or None, qty)
    for rec in df.to_dict("records"):
        qty = 1 if used_default_qty else to_int(rec.get(qty_col))
        if qty is None or qty <= 0:
            continue
        cands = [c for c in (clean_code(rec.get(col)) for col in sku_cols) if c]
        if not cands:
            continue
        size = clean_code(rec.get(size_col)) if size_col else None
        parsed.append((cands, size, qty))
    if not parsed:
        raise BadOrderFileError("No usable rows: every row was missing a SKU or a positive quantity.")

    # --- Step 2: batch-resolve every distinct code into two hash maps ---
    all_codes = sorted({c for cands, _, _ in parsed for c in cands})
    code_map: dict[str, dict[int, Variant]] = defaultdict(dict)   # code -> {variant_id: Variant}
    pair_map: dict[tuple[str, str], Variant] = {}                  # (product sku, size) -> Variant

    for chunk in _chunks(all_codes):
        by_code = (
            db.query(Variant).options(joinedload(Variant.product))
            .filter(or_(
                Variant.variant_code.in_(chunk),
                Variant.seller_sku_code.in_(chunk),
                Variant.marketplace_sku_code.in_(chunk),
            ))
            .all()
        )
        for v in by_code:
            for key in (v.variant_code, v.seller_sku_code, v.marketplace_sku_code):
                if key:
                    code_map[key.upper()][v.id] = v

        by_product = (
            db.query(Variant).options(joinedload(Variant.product))
            .join(Product).filter(Product.sku.in_(chunk)).all()
        )
        for v in by_product:
            pair_map[(v.product.sku.upper(), v.size.upper())] = v

    def resolve(cands, size):
        ambiguous = False
        for c in cands:
            hits = list(code_map.get(c, {}).values())
            if len(hits) == 1:
                return hits[0], False
            if len(hits) > 1:
                if size:
                    narrowed = [v for v in hits if v.size.upper() == size]
                    if len(narrowed) == 1:
                        return narrowed[0], False
                ambiguous = True
        if size:
            for c in cands:
                v = pair_map.get((c, size))
                if v:
                    return v, False
        return None, ambiguous

    # --- Step 3: aggregate per resolved variant (or per unresolved code) ---
    resolved_qty: dict[int, int] = defaultdict(int)
    resolved_obj: dict[int, Variant] = {}
    unresolved_qty: dict[tuple[str, str | None], list] = {}   # (code, size) -> [qty, ambiguous]
    for cands, size, qty in parsed:
        variant, ambiguous = resolve(cands, size)
        if variant:
            resolved_qty[variant.id] += qty
            resolved_obj[variant.id] = variant
        else:
            entry = unresolved_qty.setdefault((cands[0], size), [0, ambiguous])
            entry[0] += qty

    order_upload = OrderUpload(
        filename=filename, file_hash=file_hash,
        total_rows=len(parsed), unique_skus=len(resolved_qty) + len(unresolved_qty),
    )
    db.add(order_upload)
    db.flush()

    fulfilled = insufficient = not_found = 0
    new_alerts, line_results = [], []

    # --- Step 4: deduct once per variant ---
    for vid, qty in resolved_qty.items():
        variant = resolved_obj[vid]
        base = {"sku": variant.product.sku, "size": variant.size, "qty_ordered": qty,
                "product_name": variant.product.name, "note": None}
        try:
            apply_stock_change(
                db, variant, -qty, transaction_type=TransactionType.ORDER_DEDUCTION,
                reference=f"order_upload:{order_upload.id}", created_by=created_by,
            )
            fulfilled += 1
            status, got, remaining = OrderLineStatus.FULFILLED, qty, variant.current_stock
        except InsufficientStockError as e:
            insufficient += 1
            status, got, remaining = OrderLineStatus.INSUFFICIENT_STOCK, 0, e.available
            if variant.last_counted_at is None:
                base["note"] = NOT_COUNTED_NOTE

        db.add(OrderLine(
            order_upload_id=order_upload.id, sku=variant.product.sku, size=variant.size,
            variant_id=variant.id, qty_ordered=qty, qty_fulfilled=got, status=status,
        ))
        line_results.append({**base, "qty_fulfilled": got, "remaining_stock": remaining, "status": status.value})

        alert = check_variant_alert(db, variant)
        if alert:
            new_alerts.append(variant.variant_code)

    for (code, size), (qty, ambiguous) in unresolved_qty.items():
        not_found += 1
        db.add(OrderLine(
            order_upload_id=order_upload.id, sku=code, size=size, variant_id=None,
            qty_ordered=qty, qty_fulfilled=0, status=OrderLineStatus.SKU_NOT_FOUND,
        ))
        line_results.append({
            "sku": code, "size": size, "qty_ordered": qty, "qty_fulfilled": 0,
            "remaining_stock": None, "status": "SKU_NOT_FOUND", "product_name": None,
            "note": AMBIGUOUS_NOTE if ambiguous else None,
        })

    order_upload.fulfilled_count = fulfilled
    order_upload.insufficient_count = insufficient
    order_upload.not_found_count = not_found

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateUploadError("This file was already processed (detected at commit).")

    return {
        "order_upload_id": order_upload.id, "filename": filename,
        "total_rows": len(parsed), "unique_skus": len(resolved_qty) + len(unresolved_qty),
        "fulfilled_count": fulfilled, "insufficient_count": insufficient, "not_found_count": not_found,
        "lines": line_results, "new_alerts": new_alerts,
        "file_note": NO_QTY_COLUMN_NOTE if used_default_qty else None,
    }
