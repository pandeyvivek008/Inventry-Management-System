"""
Bulk stock counting: a physical count done on paper/Excel, loaded in one go.

  build_count_sheet()  -> an .xlsx listing every size of every design, with
                          a blank "Count" column to fill in.
  bulk_set_stock()     -> reads that sheet (or any sheet with a Variant Code /
                          SKU+Size column and a Count column) and SETS stock.

Setting (not deducting) goes through inventory_service.set_absolute_stock, so
every row still gets a ledger entry and an alert check, and the variant is
stamped as counted - which is what lets it start taking part in alerts and
reorder lists (a freshly imported catalog is "not counted" until this runs).
Blank Count cells are skipped: stock stays exactly as it was.
"""
from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from sqlalchemy import case, insert, update
from sqlalchemy.orm import Session, joinedload

from models import Product, Variant, Alert, InventoryTransaction, TransactionType
from services.table_reader import read_table, to_int, clean_code

VARIANT_CODE_CANDIDATES = ["variant code", "variant_code", "code"]
SKU_COLUMN_CANDIDATES = ["sku", "style code", "stylecode"]
SIZE_COLUMN_CANDIDATES = ["size"]
COUNT_COLUMN_CANDIDATES = ["count", "counted", "actual count", "current stock", "stock", "qty"]
CHUNK = 500

SIZE_ORDER = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5, "3XL": 6, "4XL": 7, "5XL": 8}


def size_rank_expr():
    """SQL CASE so sizes sort S, M, L, XL, XXL, 3XL... instead of alphabetically."""
    return case(*[(Variant.size == s, r) for s, r in SIZE_ORDER.items()], else_=99)


def _find_column(columns, candidates):
    lower_map = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _chunks(items, size=CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def build_count_sheet(db: Session, brand: str | None = None, only_uncounted: bool = False) -> bytes:
    query = (
        db.query(Variant).join(Product).options(joinedload(Variant.product))
    )
    if brand:
        query = query.filter(Product.brand == brand)
    if only_uncounted:
        query = query.filter(Variant.last_counted_at.is_(None))
    variants = query.order_by(Product.sku, size_rank_expr(), Variant.size).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Count Sheet"

    headers = ["Variant Code", "SKU", "Size", "Product", "Brand", "System Stock", "Count"]
    head_font = Font(name="Arial", bold=True, color="FFFFFF")
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = head_font
        cell.fill = PatternFill(
            start_color="A6415B" if h == "Count" else "2F3B52",
            end_color="A6415B" if h == "Count" else "2F3B52", fill_type="solid",
        )
    ws.cell(row=1, column=7).comment = Comment(
        "Type the number of pieces you physically counted. Leave blank to skip "
        "(stock stays as it is). Enter 0 if you counted and there are none.",
        "Inventory System",
    )

    body_font = Font(name="Arial")
    for r, v in enumerate(variants, start=2):
        ws.cell(row=r, column=1, value=v.variant_code).font = body_font
        ws.cell(row=r, column=2, value=v.product.sku).font = body_font
        ws.cell(row=r, column=3, value=v.size).font = body_font
        ws.cell(row=r, column=4, value=v.product.name).font = body_font
        ws.cell(row=r, column=5, value=v.product.brand).font = body_font
        # blank System Stock = never counted (so 0 isn't mistaken for a real zero)
        ws.cell(row=r, column=6, value=None if v.last_counted_at is None else v.current_stock).font = body_font

    for i, w in enumerate([34, 30, 8, 50, 18, 14, 10], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:G{max(len(variants) + 1, 2)}"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def bulk_set_stock(db: Session, file_bytes: bytes, filename: str = "", created_by: str = "bulk_upload"):
    """Set stock from a counted sheet.

    PERFORMANCE: done as set-based bulk statements (one executemany INSERT for
    the ledger, one bulk UPDATE by primary key for the stock, one INSERT for
    new alerts, one UPDATE for resolved alerts) instead of per-row ORM calls.
    A first version that looped set_absolute_stock() row by row needed ~140 s
    for a 7,862-size catalog - too slow for a web request. Semantics are the
    same as the single-row path: every row leaves a ledger entry, the variant
    is stamped as counted, and alerts open/resolve by the same rule
    (stock <= threshold)."""
    df = read_table(file_bytes, filename)
    if df.empty:
        raise ValueError("The file has no rows.")

    variant_code_col = _find_column(df.columns, VARIANT_CODE_CANDIDATES)
    sku_col = _find_column(df.columns, SKU_COLUMN_CANDIDATES)
    size_col = _find_column(df.columns, SIZE_COLUMN_CANDIDATES)
    count_col = _find_column(df.columns, COUNT_COLUMN_CANDIDATES)

    if not count_col or not (variant_code_col or (sku_col and size_col)):
        raise ValueError(
            "Need a Count column, plus either a Variant Code column or both SKU and Size columns. "
            f"Found columns: {list(df.columns)}"
        )

    records = df.to_dict("records")

    # Batch-load every referenced variant (chunked IN queries) -> O(1) lookup per row.
    # FOR UPDATE (ignored by SQLite, honoured by PostgreSQL) keeps a concurrent order
    # upload from changing these rows between our read and our write.
    variant_by_key: dict = {}
    if variant_code_col:
        codes = sorted({c for c in (clean_code(r.get(variant_code_col)) for r in records) if c})
        for chunk in _chunks(codes):
            for v in db.query(Variant).filter(Variant.variant_code.in_(chunk)).with_for_update(of=Variant).all():
                variant_by_key[v.variant_code.upper()] = v
    else:
        skus = sorted({c for c in (clean_code(r.get(sku_col)) for r in records) if c})
        for chunk in _chunks(skus):
            rows = (
                db.query(Variant).options(joinedload(Variant.product))
                .join(Product).filter(Product.sku.in_(chunk)).with_for_update(of=Variant).all()
            )
            for v in rows:
                variant_by_key[(v.product.sku.upper(), v.size.upper())] = v

    targets: dict[int, tuple[Variant, int]] = {}   # variant id -> (variant, new count); first row wins
    not_found, skipped_blank, duplicates = [], 0, 0
    for rec in records:
        count = to_int(rec.get(count_col))
        if count is None or count < 0:
            skipped_blank += 1
            continue
        if variant_code_col:
            key = clean_code(rec.get(variant_code_col))
        else:
            key = (clean_code(rec.get(sku_col)), clean_code(rec.get(size_col)))
        variant = variant_by_key.get(key)
        if not variant:
            not_found.append(str(key))
            continue
        if variant.id in targets:
            duplicates += 1
            continue
        targets[variant.id] = (variant, count)

    now = datetime.now(timezone.utc)
    ledger_rows, stock_rows = [], []
    for variant, new_count in targets.values():
        ledger_rows.append({
            "variant_id": variant.id, "change_qty": new_count - variant.current_stock,
            "transaction_type": TransactionType.MANUAL_ADJUSTMENT, "reference": "bulk stock upload",
            "balance_after": new_count, "created_by": created_by, "created_at": now,
        })
        stock_rows.append({"id": variant.id, "current_stock": new_count, "last_counted_at": now, "updated_at": now})

    # Alerts: same rule as alert_service.check_variant_alert (open iff stock <= threshold),
    # evaluated for all touched variants in one pass against the currently open alerts.
    open_alert_by_variant: dict[int, int] = {}
    ids = list(targets.keys())
    for chunk in _chunks(ids):
        for alert_id, variant_id in (
            db.query(Alert.id, Alert.variant_id)
            .filter(Alert.variant_id.in_(chunk), Alert.resolved == False)  # noqa: E712
            .all()
        ):
            open_alert_by_variant[variant_id] = alert_id

    new_alert_rows, resolve_ids = [], []
    for variant, new_count in targets.values():
        is_low = new_count <= variant.reorder_threshold
        has_open = variant.id in open_alert_by_variant
        if is_low and not has_open:
            new_alert_rows.append({
                "variant_id": variant.id, "alert_type": "OUT_OF_STOCK" if new_count <= 0 else "LOW_STOCK",
                "stock_at_alert": new_count, "created_at": now, "resolved": False,
            })
        elif not is_low and has_open:
            resolve_ids.append(open_alert_by_variant[variant.id])

    if ledger_rows:
        db.execute(insert(InventoryTransaction), ledger_rows)
        db.execute(update(Variant), stock_rows)
    if new_alert_rows:
        db.execute(insert(Alert), new_alert_rows)
    for chunk in _chunks(resolve_ids):
        db.execute(update(Alert).where(Alert.id.in_(chunk)).values(resolved=True, resolved_at=now))

    db.commit()
    return {
        "rows_processed": len(records),
        "updated_count": len(targets),
        "not_found_count": len(not_found),
        "not_found_sample": not_found[:20],
        "skipped_blank_count": skipped_blank,
        "duplicate_rows_ignored": duplicates,
    }
