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
import re
from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from models import Product, Variant, OrderUpload, OrderLine, OrderLineStatus, TransactionType
from services.inventory_service import apply_stock_change, InsufficientStockError
from services.alert_service import check_variant_alert
from services.table_reader import read_table, to_int, clean_code

# Most specific first. All that are present in the sheet are tried per row.
SKU_COLUMN_CANDIDATES = [
    "master sku", "master_sku", "master sku code", "master_sku_code",
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


def _code_aliases(value: str | None) -> set[str]:
    """Return stable aliases for legacy `SKU-SIZE` and canonical `SKU_SIZE`."""
    if not value:
        return set()
    code = str(value).strip().upper()
    aliases = {code}
    if "-" in code:
        aliases.add(code.rsplit("-", 1)[0] + "_" + code.rsplit("-", 1)[1])
    if "_" in code:
        aliases.add(code.rsplit("_", 1)[0] + "-" + code.rsplit("_", 1)[1])
    return aliases


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    import re
    raw = re.split(r"[^A-Z0-9]+", str(value).upper())
    return {x for x in raw if len(x) >= 2}


def _infer_size_from_code(code: str | None, explicit_size: str | None = None):
    """Return (base SKU, size) for marketplace Master SKU values.

    The real marketplace export contains many size formats: S/M/L/XL/XXL,
    3XL/5XL, numeric trouser sizes (28/30/32), and kids sizes such as
    13-14Y/15-16Y. When the sheet already has a Size column, that value is
    authoritative and a trailing ``_<size>`` / ``-<size>`` suffix is removed
    from the Master SKU before Product SKU + Size matching.
    """
    value = str(code or "").strip().upper()
    if explicit_size:
        size = str(explicit_size).strip().upper()
        # Explicit Size is the source of truth. Strip only a separator-bound
        # trailing size so product SKU matching can work with full Master SKU.
        suffix = re.escape(size)
        stripped = re.sub(rf"(?:[_\-\s]+){suffix}$", "", value, flags=re.IGNORECASE)
        return stripped or value, size

    # Without a Size column, support the common apparel + kids + numeric sizes.
    m = re.search(
        r"(?:^|[_\-\s])(5XL|4XL|3XL|XXXL|XXL|XL|XS|XXS|L|M|S|\d{1,3}(?:-\d{1,3})?Y|\d{1,3})$",
        value,
    )
    if not m:
        return value, None
    return value[:m.start()].rstrip("_- "), m.group(1).upper()


def _fuzzy_score(candidate: str, variant) -> int:
    """Score a marketplace code against a product/variant identity.

    Fuzzy matching is only a fallback. It supports common marketplace noise
    such as a channel prefix or a one-character typo (MAROOM -> MAROON), while
    size is resolved separately so all five sizes cannot tie with each other.
    """
    c = str(candidate or "").upper()
    ct = _tokens(c)
    if not ct:
        return 0
    fields = [variant.product.sku, variant.product.name, variant.variant_code, variant.seller_sku_code, variant.marketplace_sku_code]
    best = 0
    normalized_c = "".join(ch for ch in c if ch.isalnum())
    for field in fields:
        ft = _tokens(field)
        shared = ct & ft
        score = len(shared) * 10
        if len(shared) >= 2:
            score += min(8, len("".join(sorted(shared, key=len, reverse=True))))
        normalized_f = "".join(ch for ch in str(field or "").upper() if ch.isalnum())
        if len(normalized_c) >= 6 and (normalized_c in normalized_f or normalized_f in normalized_c):
            score += 12
        if len(normalized_c) >= 8 and len(normalized_f) >= 8:
            ratio = SequenceMatcher(None, normalized_c, normalized_f).ratio()
            if ratio >= 0.90:
                score += 18
            elif ratio >= 0.82:
                score += 8
        best = max(best, score)
    return best


def reconcile_unmapped_order_lines(db: Session) -> int:
    """Repair older SKU_NOT_FOUND lines after the catalog/matcher is fixed.

    Only previously-unmapped lines are touched. They become normal PENDING
    order lines; no physical stock is deducted. This is safe for old batches
    because a SKU_NOT_FOUND line could never have been dispatched by this app.
    """
    lines = (
        db.query(OrderLine)
        .filter(OrderLine.status == OrderLineStatus.SKU_NOT_FOUND, OrderLine.variant_id.is_(None))
        .all()
    )
    if not lines:
        return 0

    variants = (
        db.query(Variant).options(joinedload(Variant.product))
        .join(Product)
        .filter(Variant.is_deleted == False, Product.is_deleted == False)  # noqa: E712
        .all()
    )
    exact = {}
    pair = {}
    for v in variants:
        for key in (v.variant_code, v.seller_sku_code, v.marketplace_sku_code):
            for alias in _code_aliases(key):
                exact.setdefault(alias, {})[v.id] = v
        pair[(v.product.sku.upper(), v.size.upper())] = v

    changed = 0
    for line in lines:
        raw = str(line.sku or "").strip().upper()
        base, inferred_size = _infer_size_from_code(raw, line.size)
        hits = {}
        for alias in _code_aliases(raw):
            hits.update(exact.get(alias, {}))
        candidates = list(hits.values())
        if inferred_size:
            candidates = [v for v in candidates if v.size.upper() == inferred_size] or candidates
        if len(candidates) == 1:
            v = candidates[0]
        else:
            v = pair.get((base, inferred_size)) if inferred_size else None
            if not v:
                scored = []
                for candidate in variants:
                    if inferred_size and candidate.size.upper() != inferred_size:
                        continue
                    score = _fuzzy_score(base, candidate)
                    if score >= 28:
                        scored.append((score, candidate))
                scored.sort(key=lambda x: x[0], reverse=True)
                v = scored[0][1] if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]) else None
        if not v:
            continue
        line.variant_id = v.id
        line.sku = v.product.sku
        line.size = v.size.upper()
        line.status = OrderLineStatus.PENDING
        line.qty_fulfilled = 0
        changed += 1

    if changed:
        db.commit()
    return changed


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
    # For the real marketplace export, Master SKU normally contains the size
    # already (e.g. TWINS_V_ATHIYA_BLUE_M) while Product SKU in our catalog is
    # the base (TWINS_V_ATHIYA_BLUE). Build the Product-SKU lookup from both
    # the raw code and the size-stripped base.
    all_product_skus = set(all_codes)
    for cands, size, _ in parsed:
        for raw in cands:
            base, _ = _infer_size_from_code(raw, size)
            if base:
                all_product_skus.add(base.upper())
    all_product_skus = sorted(all_product_skus)

    code_map: dict[str, dict[int, Variant]] = defaultdict(dict)   # code -> {variant_id: Variant}
    pair_map: dict[tuple[str, str], Variant] = {}                  # (product sku, size) -> Variant

    for chunk in _chunks(all_codes):
        by_code = (
            db.query(Variant).options(joinedload(Variant.product))
            .filter(
                Variant.is_deleted == False,  # noqa: E712
                or_(
                    Variant.variant_code.in_(chunk),
                    Variant.seller_sku_code.in_(chunk),
                    Variant.marketplace_sku_code.in_(chunk),
                ),
            )
            .all()
        )
        for v in by_code:
            for key in (v.variant_code, v.seller_sku_code, v.marketplace_sku_code):
                for alias in _code_aliases(key):
                    code_map[alias][v.id] = v

    # The code-map query above is intentionally chunked by raw marketplace
    # codes. Now query the expanded base Product SKU list as well.
    for product_chunk in _chunks(all_product_skus):
        by_product = (
            db.query(Variant).options(joinedload(Variant.product))
            .join(Product).filter(Product.sku.in_(product_chunk), Variant.is_deleted == False, Product.is_deleted == False).all()  # noqa: E712
        )
        for v in by_product:
            pair_map[(v.product.sku.upper(), v.size.upper())] = v

    # Fuzzy fallback index. The catalog is loaded once, not queried per order row.
    fuzzy_variants = (
        db.query(Variant).options(joinedload(Variant.product))
        .join(Product)
        .filter(Variant.is_deleted == False, Product.is_deleted == False)  # noqa: E712
        .all()
    )

    def resolve(cands, size):
        ambiguous = False
        prepared = []
        for raw in cands:
            base, inferred = _infer_size_from_code(raw, size)
            effective_size = inferred or (str(size).strip().upper() if size else None)
            prepared.append((raw, base, effective_size))

        # 1) Exact variant/seller/marketplace code.
        for raw, base, effective_size in prepared:
            hits_by_id = {}
            for alias in _code_aliases(raw):
                hits_by_id.update(code_map.get(alias, {}))
            hits = list(hits_by_id.values())
            if len(hits) == 1:
                return hits[0], False
            if len(hits) > 1 and effective_size:
                narrowed = [v for v in hits if v.size.upper() == effective_size]
                if len(narrowed) == 1:
                    return narrowed[0], False
                ambiguous = True

        # 2) Exact Product SKU + Size. Also accept BASE_SIZE / BASE-SIZE.
        for raw, base, effective_size in prepared:
            if not effective_size:
                continue
            for candidate_base in (raw.upper(), base.upper()):
                v = pair_map.get((candidate_base, effective_size))
                if v:
                    return v, False

        # 3) Fuzzy marketplace matching. Size is inferred from the trailing
        # token when possible, so M cannot tie with L/XL/XXL. This fixes
        # common real-world variants such as MAROOM-M -> MAROON, M.
        best = None
        best_score = 0
        tied = False
        for raw, base, effective_size in prepared:
            for v in fuzzy_variants:
                if effective_size and v.size.upper() != effective_size:
                    continue
                score = _fuzzy_score(base, v)
                if score > best_score:
                    best, best_score, tied = v, score, False
                elif score and score == best_score:
                    tied = True
        if best and best_score >= 28 and not tied:
            return best, False
        return None, ambiguous or bool(best_score >= 20)

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

    fulfilled = insufficient = not_found = pending = 0
    new_alerts, line_results = [], []

    # --- Step 4: orders are RECEIVED/RESERVED, not deducted immediately ---
    # Physical inventory must remain unchanged until the order is actually
    # dispatched.  Reorder/Picklist uses pending order quantities to calculate
    # the shortage that must be arranged from production/vendor stock.
    for vid, qty in resolved_qty.items():
        variant = resolved_obj[vid]
        base = {"sku": variant.product.sku, "size": variant.size, "qty_ordered": qty,
                "product_name": variant.product.name, "note": NOT_COUNTED_NOTE if variant.last_counted_at is None else None}
        status = OrderLineStatus.PENDING
        pending += 1
        got = 0
        remaining = variant.current_stock

        db.add(OrderLine(
            order_upload_id=order_upload.id, sku=variant.product.sku, size=variant.size,
            variant_id=variant.id, qty_ordered=qty, qty_fulfilled=0, status=status,
        ))
        line_results.append({**base, "qty_fulfilled": got, "remaining_stock": remaining, "status": status.value})

    
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
        "fulfilled_count": fulfilled, "pending_count": pending, "insufficient_count": insufficient, "not_found_count": not_found,
        "lines": line_results, "new_alerts": new_alerts,
        "file_note": NO_QTY_COLUMN_NOTE if used_default_qty else None,
    }
