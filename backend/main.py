import shutil
import uuid
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, RedirectResponse
from sqlalchemy import case, func, or_, delete as sa_delete, inspect as sa_inspect, text
from sqlalchemy.orm import Session, joinedload, selectinload, contains_eager

from database import Base, engine, get_db
import models
import schemas
from services import (
    inventory_service, alert_service, excel_service, reorder_service,
    barcode_service, bulk_stock_service, bulk_product_service,
)

Base.metadata.create_all(bind=engine)


def remove_legacy_brand_data(target_engine=engine):
    """Migrate older databases after the catalog brand field was removed."""
    columns = {column["name"] for column in sa_inspect(target_engine).get_columns("products")}
    if "brand" not in columns:
        return
    # Preserve product descriptions while removing an identical leading brand
    # prefix (for example, "CATALOG LABEL Women..." becomes "Women...").
    with target_engine.begin() as connection:
        connection.execute(text("""
            UPDATE products
            SET name = LTRIM(SUBSTR(name, LENGTH(brand) + 1))
            WHERE brand IS NOT NULL AND TRIM(brand) != ''
              AND LOWER(SUBSTR(name, 1, LENGTH(brand))) = LOWER(brand)
        """))
        connection.execute(text("ALTER TABLE products DROP COLUMN brand"))


remove_legacy_brand_data()

app = FastAPI(title="Inventory Management System", version="1.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your actual frontend origin before exposing beyond localhost
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
(STATIC_DIR / "product_images").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.get("/", include_in_schema=False)
def app_home():
    """Open the inventory UI directly from the Railway service root."""
    return RedirectResponse(url="/app/", status_code=307)

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #
def _variant_status(v: models.Variant) -> str:
    if v.last_counted_at is None:
        return "UNCOUNTED"
    if v.current_stock <= 0:
        return "OUT"
    if v.current_stock <= v.reorder_threshold:
        return "LOW"
    return "OK"


def _variant_out(v: models.Variant) -> dict:
    return {
        "variant_code": v.variant_code, "size": v.size, "current_stock": v.current_stock,
        "reorder_threshold": v.reorder_threshold, "target_stock_level": v.target_stock_level,
        "status": _variant_status(v),
    }


def _product_out(p: models.Product) -> dict:
    live_variants = [v for v in p.variants if not v.is_deleted]
    return {
        "id": p.id, "sku": p.sku, "name": p.name, "category": p.category,
        "image_path": p.image_path,
        "vendor": {"id": p.vendor.id, "name": p.vendor.name, "contact": p.vendor.contact} if p.vendor else None,
        "variants": [_variant_out(v) for v in sorted(live_variants, key=lambda v: (bulk_stock_service.SIZE_ORDER.get(v.size, 99), v.size))],
        "total_stock": sum(v.current_stock for v in live_variants),
    }


def _variant_row(v: models.Variant) -> dict:
    p = v.product
    return {
        "variant_code": v.variant_code, "sku": p.sku, "size": v.size, "product_name": p.name,
        "category": p.category, "vendor": p.vendor.name if p.vendor else None,
        "image_path": p.image_path, "current_stock": v.current_stock,
        "reorder_threshold": v.reorder_threshold, "target_stock_level": v.target_stock_level,
        "status": _variant_status(v), "last_counted_at": v.last_counted_at,
    }


def _get_product_by_sku(db: Session, sku: str, include_deleted: bool = False) -> models.Product:
    q = db.query(models.Product).options(selectinload(models.Product.variants), joinedload(models.Product.vendor)) \
        .filter(models.Product.sku == sku.strip().upper())
    if not include_deleted:
        q = q.filter(models.Product.is_deleted == False)  # noqa: E712
    product = q.first()
    if not product:
        raise HTTPException(404, f"Product '{sku}' not found")
    return product


def _get_variant_by_code(db: Session, variant_code: str, include_deleted: bool = False) -> models.Variant:
    code = variant_code.strip().upper()
    # Accept both legacy `SKU-SIZE` labels and the canonical `SKU_SIZE`
    # format. New variants are always created with the underscore format.
    candidates = [code]
    if "-" in code:
        candidates.append(code.replace("-", "_"))
    if "_" in code:
        candidates.append(code.rsplit("_", 1)[0] + "-" + code.rsplit("_", 1)[1])
    q = db.query(models.Variant).options(joinedload(models.Variant.product)) \
        .filter(models.Variant.variant_code.in_(list(dict.fromkeys(candidates))))
    if not include_deleted:
        q = q.filter(models.Variant.is_deleted == False)  # noqa: E712
    variant = q.first()
    if not variant:
        raise HTTPException(404, f"Variant '{variant_code}' not found")
    return variant


def _make_variant_code(sku: str, size: str) -> str:
    # Canonical stockable SKU: BASE_SKU_SIZE. Size is always the final token.
    return f"{sku.strip().upper()}_{size.strip().upper()}"


def _apply_variant_filters(query, q, status):
    V = models.Variant
    query = query.filter(V.is_deleted == False, models.Product.is_deleted == False)  # noqa: E712
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(models.Product.sku.ilike(like), models.Product.name.ilike(like), V.variant_code.ilike(like)))
    if status:
        s = status.strip().upper()
        counted = V.last_counted_at.isnot(None)
        if s == "UNCOUNTED":
            query = query.filter(V.last_counted_at.is_(None))
        elif s == "OUT":
            query = query.filter(counted, V.current_stock <= 0)
        elif s == "LOW":
            query = query.filter(counted, V.current_stock > 0, V.current_stock <= V.reorder_threshold)
        elif s == "OK":
            query = query.filter(counted, V.current_stock > V.reorder_threshold)
        elif s == "ATTENTION":
            query = query.filter(counted, V.current_stock <= V.reorder_threshold)
        else:
            raise HTTPException(400, "status must be one of UNCOUNTED, OUT, LOW, OK, ATTENTION")
    return query


# ------------------------------------------------------------------ #
# Live inventory (flat, filterable, paginated)
# ------------------------------------------------------------------ #
@app.get("/api/variants", response_model=schemas.VariantPage)
def list_variants(
    q: str | None = None, status: str | None = None,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    V = models.Variant
    filtered = _apply_variant_filters(db.query(V).join(V.product), q, status)
    total = filtered.with_entities(func.count(V.id)).scalar()
    rows = (
        filtered.options(contains_eager(V.product).joinedload(models.Product.vendor))
        .order_by(models.Product.sku, case(bulk_stock_service.SIZE_ORDER, value=func.upper(V.size), else_=99), V.size)
        .offset(offset).limit(limit).all()
    )
    return {"total": total, "limit": limit, "offset": offset, "items": [_variant_row(v) for v in rows]}


def _open_alert_count(db: Session) -> int:
    physical_codes = {
        code for (code,) in db.query(models.Variant.variant_code)
        .join(models.Alert, models.Alert.variant_id == models.Variant.id)
        .join(models.Product, models.Variant.product_id == models.Product.id)
        .filter(
            models.Alert.resolved == False,
            models.Variant.is_deleted == False,
            models.Product.is_deleted == False,
        ).all()
    }
    shortage_codes = {
        item["variant_code"] for item in reorder_service.generate_reorder_list(db, save_batch=False)
        if item["required_qty"] > 0
    }
    return len(physical_codes | shortage_codes)


@app.get("/api/summary", response_model=schemas.SummaryOut)
def summary(db: Session = Depends(get_db)):
    V, P = models.Variant, models.Product
    live = (V.is_deleted == False, P.is_deleted == False)  # noqa: E712
    counted = V.last_counted_at.isnot(None)

    def count(*conds):
        return db.query(func.count(V.id)).join(P).filter(*live, *conds).scalar()

    return {
        "products": db.query(func.count(P.id)).filter(P.is_deleted == False).scalar(),  # noqa: E712
        "variants": count(),
        "uncounted": count(V.last_counted_at.is_(None)),
        "out": count(counted, V.current_stock <= 0),
        "low": count(counted, V.current_stock > 0, V.current_stock <= V.reorder_threshold),
        "ok": count(counted, V.current_stock > V.reorder_threshold),
        # One visible alert per affected variant. Physical low-stock alerts
        # and pending-order shortages can refer to the same variant, so do not
        # double-count them in the badge.
        "open_alerts": _open_alert_count(db),
    }


# ------------------------------------------------------------------ #
# Products (+ their size variants)
# ------------------------------------------------------------------ #
@app.get("/api/products", response_model=list[schemas.ProductOut])
def list_products(q: str | None = None, limit: int | None = Query(None, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = db.query(models.Product).options(selectinload(models.Product.variants), joinedload(models.Product.vendor)) \
        .filter(models.Product.is_deleted == False)  # noqa: E712
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(models.Product.sku.ilike(like), models.Product.name.ilike(like)))
    query = query.order_by(models.Product.created_at.desc(), models.Product.id.desc())
    if limit:
        query = query.offset(offset).limit(limit)
    return [_product_out(p) for p in query.all()]


@app.get("/api/products/bulk-template")
def bulk_product_template():
    content = bulk_product_service.build_bulk_product_template()
    return StreamingResponse(
        BytesIO(content), media_type=XLSX_MEDIA,
        headers={"Content-Disposition": "attachment; filename=bulk_product_template.xlsx"},
    )


@app.post("/api/products/bulk", response_model=schemas.BulkProductImportResult)
async def bulk_create_products(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read(bulk_product_service.MAX_FILE_BYTES + 1)
    try:
        result = bulk_product_service.import_bulk_products(db, content, file.filename or "")
        if result.get("products_created") or result.get("variants_created"):
            excel_service.reconcile_unmapped_order_lines(db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.get("/api/products/{sku}", response_model=schemas.ProductOut)
def get_product(sku: str, db: Session = Depends(get_db)):
    return _product_out(_get_product_by_sku(db, sku))


@app.post("/api/products", response_model=schemas.ProductOut)
def create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    sku = payload.sku.strip().upper()
    existing = db.query(models.Product).filter(models.Product.sku == sku).first()
    if existing and not existing.is_deleted:
        raise HTTPException(400, f"SKU '{sku}' already exists.")
    if existing and existing.is_deleted:
        raise HTTPException(409, f"SKU '{sku}' exists in Trash. Restore it instead of creating a new one.")

    sizes_seen = set()
    for v in payload.variants:
        if v.size.strip().upper() in sizes_seen:
            raise HTTPException(400, f"Duplicate size '{v.size}' in request.")
        sizes_seen.add(v.size.strip().upper())

    product = models.Product(sku=sku, name=payload.name, category=payload.category, vendor_id=payload.vendor_id)
    db.add(product)
    db.flush()

    now = datetime.now(timezone.utc)
    for v in payload.variants:
        variant = models.Variant(
            product_id=product.id, size=v.size.strip().upper(), variant_code=_make_variant_code(sku, v.size),
            current_stock=0, reorder_threshold=v.reorder_threshold, target_stock_level=v.target_stock_level,
            last_counted_at=now,
        )
        db.add(variant)
        db.flush()
        if v.initial_stock:
            inventory_service.apply_stock_change(db, variant, v.initial_stock, transaction_type=models.TransactionType.INITIAL_STOCK, reference="product created")
        alert_service.check_variant_alert(db, variant)

    db.commit()
    excel_service.reconcile_unmapped_order_lines(db)
    db.refresh(product)
    return _product_out(product)


@app.patch("/api/products/{sku}", response_model=schemas.ProductOut)
def update_product(sku: str, payload: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = _get_product_by_sku(db, sku)
    data = payload.model_dump(exclude_unset=True)

    # SKU is the single canonical identity used by Products, Bulk Stock,
    # Reorder and new marketplace-order matching. If the user edits it,
    # regenerate every size's stockable Variant code from the new SKU too.
    if "sku" in data:
        new_sku = str(data.pop("sku") or "").strip().upper()
        if not new_sku:
            raise HTTPException(400, "SKU cannot be empty.")
        if new_sku != product.sku:
            conflict = db.query(models.Product).filter(
                models.Product.sku == new_sku, models.Product.id != product.id
            ).first()
            if conflict:
                raise HTTPException(409, f"SKU '{new_sku}' already exists.")
            for variant in product.variants:
                if variant.is_deleted:
                    continue
                variant.variant_code = _make_variant_code(new_sku, variant.size)
            product.sku = new_sku

    for field, value in data.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return _product_out(product)


@app.post("/api/products/{sku}/variants", response_model=schemas.ProductOut)
def add_size(sku: str, payload: schemas.VariantCreate, db: Session = Depends(get_db)):
    """Adds one more size to a design that already exists - for a size that
    was missed when the product was first added."""
    product = _get_product_by_sku(db, sku)
    size = payload.size.strip().upper()
    code = _make_variant_code(sku, size)

    existing = db.query(models.Variant).filter(models.Variant.product_id == product.id, models.Variant.size == size).first()
    if existing and not existing.is_deleted:
        raise HTTPException(400, f"Size '{size}' already exists on {sku}.")
    if existing and existing.is_deleted:
        existing.is_deleted = False
        existing.deleted_at = None
        variant = existing
    else:
        variant = models.Variant(
            product_id=product.id, size=size, variant_code=code, current_stock=0,
            reorder_threshold=payload.reorder_threshold, target_stock_level=payload.target_stock_level,
        )
        db.add(variant)
    db.flush()
    if payload.initial_stock:
        inventory_service.apply_stock_change(db, variant, payload.initial_stock, transaction_type=models.TransactionType.INITIAL_STOCK, reference="size added")
        variant.last_counted_at = datetime.now(timezone.utc)
    alert_service.check_variant_alert(db, variant)
    db.commit()
    excel_service.reconcile_unmapped_order_lines(db)
    db.refresh(product)
    return _product_out(product)


@app.patch("/api/variants/{variant_code}", response_model=schemas.VariantOut)
def update_variant(variant_code: str, payload: schemas.VariantUpdate, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(variant, field, value)
    db.flush()
    alert_service.check_variant_alert(db, variant)
    db.commit()
    db.refresh(variant)
    return _variant_out(variant)


@app.post("/api/products/{sku}/image")
def upload_product_image(sku: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    product = _get_product_by_sku(db, sku)
    ext = Path(file.filename).suffix or ".jpg"
    fname = f"{product.sku}_{uuid.uuid4().hex[:8]}{ext}"
    dest = STATIC_DIR / "product_images" / fname
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    old_path = product.image_path
    product.image_path = f"/static/product_images/{fname}"
    db.commit()
    if old_path:
        old_file = STATIC_DIR.parent / old_path.lstrip("/").replace("static/", "static/", 1)
        try:
            (BASE_DIR / old_path.lstrip("/")).unlink(missing_ok=True)
        except Exception:
            pass
    return {"image_path": product.image_path}


# ------------------------------------------------------------------ #
# Trash: soft delete + restore
# ------------------------------------------------------------------ #
@app.delete("/api/products/{sku}", response_model=schemas.DeleteResult)
def delete_product(sku: str, db: Session = Depends(get_db)):
    """Moves a design and all its sizes to Trash. Stock, alerts and reorder
    lines stop counting it immediately; full history is kept and it can be
    restored later from the Trash view."""
    product = _get_product_by_sku(db, sku)
    now = datetime.now(timezone.utc)
    count = 0
    for v in product.variants:
        if not v.is_deleted:
            v.is_deleted = True
            v.deleted_at = now
            open_alert = db.query(models.Alert).filter(models.Alert.variant_id == v.id, models.Alert.resolved == False).first()  # noqa: E712
            if open_alert:
                open_alert.resolved = True
                open_alert.resolved_at = now
            count += 1
    product.is_deleted = True
    product.deleted_at = now
    db.commit()
    return {"deleted": product.sku, "trashed": True, "sizes_removed": count}


@app.post("/api/products/{sku}/restore", response_model=schemas.RestoreResult)
def restore_product(sku: str, db: Session = Depends(get_db)):
    product = _get_product_by_sku(db, sku, include_deleted=True)
    if not product.is_deleted:
        raise HTTPException(400, f"'{sku}' is not in Trash.")
    product.is_deleted = False
    product.deleted_at = None
    count = 0
    for v in product.variants:
        if v.is_deleted:
            v.is_deleted = False
            v.deleted_at = None
            count += 1
        alert_service.check_variant_alert(db, v)
    db.commit()
    excel_service.reconcile_unmapped_order_lines(db)
    return {"restored": product.sku, "variants_restored": count}


@app.delete("/api/variants/{variant_code}", response_model=schemas.DeleteResult)
def delete_variant(variant_code: str, db: Session = Depends(get_db)):
    """Moves a single size to Trash. If it was the design's last remaining
    size, the whole design goes to Trash too."""
    variant = _get_variant_by_code(db, variant_code)
    product = variant.product
    now = datetime.now(timezone.utc)
    variant.is_deleted = True
    variant.deleted_at = now
    open_alert = db.query(models.Alert).filter(models.Alert.variant_id == variant.id, models.Alert.resolved == False).first()  # noqa: E712
    if open_alert:
        open_alert.resolved = True
        open_alert.resolved_at = now
    db.flush()

    remaining = db.query(func.count(models.Variant.id)).filter(models.Variant.product_id == product.id, models.Variant.is_deleted == False).scalar()  # noqa: E712
    product_also_deleted = remaining == 0
    if product_also_deleted:
        product.is_deleted = True
        product.deleted_at = now
    db.commit()
    return {"deleted": variant.variant_code, "trashed": True, "product_also_deleted": product_also_deleted}


@app.post("/api/variants/{variant_code}/restore", response_model=schemas.RestoreResult)
def restore_variant(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code, include_deleted=True)
    if not variant.is_deleted:
        raise HTTPException(400, f"'{variant_code}' is not in Trash.")
    variant.is_deleted = False
    variant.deleted_at = None
    if variant.product.is_deleted:
        variant.product.is_deleted = False
        variant.product.deleted_at = None
    alert_service.check_variant_alert(db, variant)
    db.commit()
    excel_service.reconcile_unmapped_order_lines(db)
    return {"restored": variant.variant_code}


@app.get("/api/trash")
def list_trash(limit: int | None = Query(None, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = (
        db.query(models.Product).options(selectinload(models.Product.variants))
        .filter(models.Product.is_deleted == True)  # noqa: E712
        .order_by(models.Product.deleted_at.desc())
    )
    total = query.count()
    products = query.offset(offset).limit(limit).all() if limit else query.all()
    items = [{
        "sku": p.sku, "name": p.name, "image_path": p.image_path,
        "variant_count": len(p.variants), "deleted_at": p.deleted_at,
    } for p in products]
    return {"total": total, "limit": limit, "offset": offset, "items": items} if limit is not None else items


@app.delete("/api/trash/{sku}")
def permanently_delete(sku: str, db: Session = Depends(get_db)):
    """Empties one item from Trash for good - no further undo. Use only to
    clean up Trash itself; everyday removal is the regular delete above."""
    product = _get_product_by_sku(db, sku, include_deleted=True)
    if not product.is_deleted:
        raise HTTPException(400, f"'{sku}' must be in Trash before it can be permanently deleted.")
    variant_ids = [v.id for v in db.query(models.Variant.id).filter(models.Variant.product_id == product.id)]
    if variant_ids:
        db.execute(sa_delete(models.OrderLine).where(models.OrderLine.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.ReorderBatchItem).where(models.ReorderBatchItem.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.Alert).where(models.Alert.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.InventoryTransaction).where(models.InventoryTransaction.variant_id.in_(variant_ids)))
    # Variant rows themselves are left to the ORM cascade below (Product.variants
    # is configured cascade="all, delete-orphan") - deleting them here too would
    # just make SQLAlchemy attempt the same DELETE twice and warn about it.
    db.delete(product)
    db.commit()
    return {"permanently_deleted": sku.strip().upper()}


# ------------------------------------------------------------------ #
# Variant lookup (scan screen) + counting / adjustment
# ------------------------------------------------------------------ #
@app.get("/api/variants/{variant_code}", response_model=schemas.VariantLookupOut)
def lookup_variant(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code)
    return {
        "variant_code": variant.variant_code, "sku": variant.product.sku, "size": variant.size,
        "product_name": variant.product.name, "current_stock": variant.current_stock,
        "counted": variant.last_counted_at is not None, "image_path": variant.product.image_path,
    }


@app.get("/api/variants/{variant_code}/product", response_model=schemas.ProductOut)
def product_for_variant(variant_code: str, db: Session = Depends(get_db)):
    """Scanning or looking up one size resolves the WHOLE design, so every
    size can be seen and updated together, not just the one that was scanned."""
    variant = _get_variant_by_code(db, variant_code)
    return _product_out(_get_product_by_sku(db, variant.product.sku))


@app.post("/api/inventory/adjust", response_model=schemas.VariantOut)
def adjust_stock(payload: schemas.StockAdjustment, db: Session = Depends(get_db)):
    if payload.new_count < 0:
        raise HTTPException(400, "Count cannot be negative.")
    variant = _get_variant_by_code(db, payload.variant_code)
    inventory_service.set_absolute_stock(db, variant, payload.new_count, note=payload.note)
    alert_service.check_variant_alert(db, variant)
    db.commit()
    db.refresh(variant)
    return _variant_out(variant)


@app.get("/api/inventory/count-sheet")
def count_sheet(only_uncounted: bool = False, db: Session = Depends(get_db)):
    xlsx_bytes = bulk_stock_service.build_count_sheet(db, only_uncounted=only_uncounted)
    return StreamingResponse(BytesIO(xlsx_bytes), media_type=XLSX_MEDIA, headers={"Content-Disposition": "attachment; filename=count_sheet.xlsx"})


@app.post("/api/inventory/bulk-set", response_model=schemas.BulkSetResult)
async def bulk_set(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        return bulk_stock_service.bulk_set_stock(db, content, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/variants/{variant_code}/transactions", response_model=list[schemas.TransactionOut])
def variant_transactions(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code, include_deleted=True)
    return (
        db.query(models.InventoryTransaction).filter(models.InventoryTransaction.variant_id == variant.id)
        .order_by(models.InventoryTransaction.created_at.desc(), models.InventoryTransaction.id.desc()).limit(200).all()
    )


@app.get("/api/transactions", response_model=list[schemas.TransactionLogOut])
def global_transactions(
    q: str | None = None, transaction_type: str | None = None,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Every stock change across the whole catalog, newest first - the
    'who/what/when/how much changed' activity log."""
    T, V, P = models.InventoryTransaction, models.Variant, models.Product
    query = db.query(T).join(V, T.variant_id == V.id).join(P, V.product_id == P.id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(P.sku.ilike(like), P.name.ilike(like), V.variant_code.ilike(like)))
    if transaction_type:
        query = query.filter(T.transaction_type == transaction_type)
    rows = query.options(contains_eager(T.variant).contains_eager(V.product)) \
        .order_by(T.created_at.desc(), T.id.desc()).offset(offset).limit(limit).all()
    return [{
        "id": t.id, "variant_code": t.variant.variant_code, "sku": t.variant.product.sku, "size": t.variant.size,
        "product_name": t.variant.product.name, "change_qty": t.change_qty, "transaction_type": t.transaction_type.value,
        "reference": t.reference, "balance_after": t.balance_after, "created_by": t.created_by, "created_at": t.created_at,
    } for t in rows]


# ------------------------------------------------------------------ #
# Order history / batches
# ------------------------------------------------------------------ #
@app.get("/api/orders/history")
def order_history(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    uploads = (
        db.query(models.OrderUpload)
        .options(
            selectinload(models.OrderUpload.lines).joinedload(models.OrderLine.variant).joinedload(models.Variant.product)
        )
        .order_by(models.OrderUpload.uploaded_at.desc(), models.OrderUpload.id.desc())
        .offset(offset).limit(limit).all()
    )
    result = []
    for u in uploads:
        pending = sum(1 for line in u.lines if line.status == models.OrderLineStatus.PENDING)
        fulfilled = sum(1 for line in u.lines if line.status == models.OrderLineStatus.FULFILLED)
        insufficient = sum(1 for line in u.lines if line.status == models.OrderLineStatus.INSUFFICIENT_STOCK)
        not_found = sum(1 for line in u.lines if line.status == models.OrderLineStatus.SKU_NOT_FOUND)
        if pending:
            status = "PENDING"
        elif fulfilled:
            status = "DISPATCHED"
        elif insufficient:
            status = "INSUFFICIENT STOCK"
        else:
            status = "PROCESSED"
        lines = []
        for line in sorted(u.lines, key=lambda x: x.id):
            product_name = line.variant.product.name if line.variant and line.variant.product else None
            variant_code = line.variant.variant_code if line.variant else None
            lines.append({
                "id": line.id, "sku": line.sku, "size": line.size, "variant_code": variant_code,
                "product_name": product_name, "qty_ordered": line.qty_ordered,
                "qty_fulfilled": line.qty_fulfilled, "status": line.status.value,
            })
        result.append({
            "order_upload_id": u.id, "filename": u.filename, "uploaded_at": u.uploaded_at,
            "total_rows": u.total_rows, "unique_skus": u.unique_skus,
            "pending_count": pending, "fulfilled_count": fulfilled,
            "insufficient_count": insufficient, "not_found_count": not_found,
            "status": status, "lines": lines,
        })
    return result


# ------------------------------------------------------------------ #
# Orders (Excel / CSV upload)
# ------------------------------------------------------------------ #
@app.post("/api/orders/upload", response_model=schemas.OrderUploadResult)
async def upload_orders(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        return excel_service.process_order_excel(db, content, file.filename or "")
    except excel_service.DuplicateUploadError as e:
        raise HTTPException(409, str(e))
    except (excel_service.BadOrderFileError, ValueError) as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ #
# Alerts
# ------------------------------------------------------------------ #
def _collect_alerts(db: Session, q: str | None = None):
    alerts = alert_service.get_open_alerts(db, limit=None, offset=0, q=q)
    result = [{
        "id": a.id, "variant_code": a.variant.variant_code, "sku": a.variant.product.sku, "size": a.variant.size,
        "product_name": a.variant.product.name, "alert_type": a.alert_type, "stock_at_alert": a.stock_at_alert,
        "created_at": a.created_at, "ordered_qty": None, "required_qty": None,
    } for a in alerts]

    # Pending marketplace orders can create a real purchase shortage even when
    # physical stock is still above the normal low-stock threshold. Show that
    # shortage in Alerts as a virtual alert; the database remains the source of
    # truth for physical-stock alerts.
    shortages = reorder_service.generate_reorder_list(db, save_batch=False)
    existing_codes = {a["variant_code"] for a in result}
    for item in shortages:
        if item.get("is_new") or item["required_qty"] <= 0:
            continue
        if item["variant_code"] in existing_codes:
            continue
        result.append({
            "id": -item["variant_code"].__hash__(),
            "variant_code": item["variant_code"],
            "sku": item["sku"],
            "size": item["size"],
            "product_name": item["name"],
            "alert_type": "ORDER_SHORTAGE",
            "stock_at_alert": item["current_stock"],
            "created_at": datetime.now(timezone.utc),
            "ordered_qty": item["ordered_qty"],
            "required_qty": item["required_qty"],
        })
    result.sort(key=lambda a: (a["alert_type"] != "OUT_OF_STOCK", a["stock_at_alert"], a["variant_code"]))
    if q and q.strip():
        needle = q.strip().casefold()
        result = [a for a in result if any(
            needle in str(a.get(key) or "").casefold()
            for key in ("variant_code", "sku", "size", "product_name", "alert_type")
        )]
    return result


@app.get("/api/alerts", response_model=list[schemas.AlertOut])
def list_alerts(limit: int = Query(1000, ge=1, le=5000), offset: int = Query(0, ge=0), q: str | None = None, db: Session = Depends(get_db)):
    alerts = _collect_alerts(db, q)
    return alerts[offset:offset + limit]


@app.get("/api/alerts/groups", response_model=schemas.AlertGroupPage)
def list_alert_groups(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), q: str | None = None, db: Session = Depends(get_db)):
    alerts = _collect_alerts(db, q)
    rank = {"OUT_OF_STOCK": 0, "ORDER_SHORTAGE": 1, "LOW_STOCK": 2}
    size_rank = {name: index for index, name in enumerate(("FREE SIZE", "XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL"))}
    groups = {}
    for alert in alerts:
        group = groups.setdefault(alert["sku"], {
            "sku": alert["sku"], "product_name": alert["product_name"], "items": [],
            "highest_severity": alert["alert_type"], "_severity": 99, "_stock": alert["stock_at_alert"],
        })
        group["items"].append(alert)
        severity = rank.get(alert["alert_type"], 3)
        group["_severity"] = min(group["_severity"], severity)
        group["_stock"] = min(group["_stock"], alert["stock_at_alert"])
        if severity == group["_severity"]:
            group["highest_severity"] = alert["alert_type"]
    page_groups = list(groups.values())
    for group in page_groups:
        group["items"].sort(key=lambda a: (size_rank.get(a["size"].upper(), 100), a["size"].upper()))
        group["alert_count"] = len(group["items"])
        del group["_severity"], group["_stock"]
    page_groups.sort(key=lambda g: (rank.get(g["highest_severity"], 3), min(a["stock_at_alert"] for a in g["items"]), g["sku"].casefold()))
    total = len(page_groups)
    return {"total": total, "limit": limit, "offset": offset, "items": page_groups[offset:offset + limit]}


# ------------------------------------------------------------------ #
# Dispatch pending orders
# ------------------------------------------------------------------ #
@app.post("/api/orders/{order_upload_id}/dispatch")
def dispatch_order_upload(order_upload_id: int, db: Session = Depends(get_db)):
    """Confirm a marketplace order batch is dispatched and deduct physical stock."""
    lines = (
        db.query(models.OrderLine)
        .options(joinedload(models.OrderLine.variant).joinedload(models.Variant.product))
        .filter(
            models.OrderLine.order_upload_id == order_upload_id,
            models.OrderLine.status == models.OrderLineStatus.PENDING,
            models.OrderLine.variant_id.isnot(None),
        )
        .all()
    )
    if not lines:
        raise HTTPException(status_code=404, detail="No pending order lines found for this upload.")

    # Validate the whole batch before changing anything.
    by_variant = {}
    for line in lines:
        by_variant[line.variant_id] = by_variant.get(line.variant_id, 0) + line.qty_ordered

    for variant_id, qty in by_variant.items():
        variant = next(line.variant for line in lines if line.variant_id == variant_id)
        if variant.last_counted_at is None:
            raise HTTPException(
                status_code=400,
                detail=f"{variant.variant_code}: physical stock has never been counted. Count it before dispatch.",
            )
        # Do not block dispatch when the order is larger than physical stock.
        # The net stock is allowed to go negative so the shortage is visible
        # immediately in Inventory + Alerts and the Picklist tells the team
        # how much must be arranged.

    try:
        for line in lines:
            variant = line.variant
            apply = inventory_service.apply_stock_change
            apply(
                db, variant, -line.qty_ordered,
                transaction_type=models.TransactionType.ORDER_DEDUCTION,
                reference=f"order_upload:{order_upload_id}:dispatch",
                created_by="dispatch",
                allow_negative=True,
            )
            line.qty_fulfilled = line.qty_ordered
            line.status = models.OrderLineStatus.FULFILLED
            alert_service.check_variant_alert(db, variant)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "order_upload_id": order_upload_id,
        "dispatched_lines": len(lines),
        "dispatched_units": sum(line.qty_ordered for line in lines),
        "status": "DISPATCHED",
    }


# ------------------------------------------------------------------ #
# Reorder list
# ------------------------------------------------------------------ #
@app.get("/api/reorder-list")
def reorder_list(part: str | None = None, limit: int | None = Query(None, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    items = reorder_service.generate_reorder_list(db, save_batch=False, part=part)
    return {"total": len(items), "limit": limit, "offset": offset, "items": items[offset:offset + limit]} if limit is not None else items


@app.get("/api/reorder-list/export")
def reorder_list_export(part: str | None = None, db: Session = Depends(get_db)):
    items = reorder_service.generate_reorder_list(db, save_batch=True, part=part)
    items = [item for item in items if item["required_qty"] > 0 or item.get("is_new")]
    xlsx_bytes = reorder_service.reorder_list_to_excel(items)
    return StreamingResponse(BytesIO(xlsx_bytes), media_type=XLSX_MEDIA, headers={"Content-Disposition": "attachment; filename=reorder_list.xlsx"})


# ------------------------------------------------------------------ #
# Printable barcode labels
# ------------------------------------------------------------------ #
@app.get("/api/variants/{variant_code}/label")
def variant_label(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code)
    png_bytes = barcode_service.generate_variant_label(variant.variant_code, variant.product.name, variant.size)
    return StreamingResponse(BytesIO(png_bytes), media_type="image/png")


@app.get("/api/labels/sheet")
def labels_sheet(variant_codes: str = Query(...), db: Session = Depends(get_db)):
    codes = [c.strip().upper() for c in variant_codes.split(",") if c.strip()]
    variants = (
        db.query(models.Variant).options(joinedload(models.Variant.product))
        .filter(models.Variant.variant_code.in_(codes), models.Variant.is_deleted == False).all()  # noqa: E712
    )
    if not variants:
        raise HTTPException(404, "No matching variants found for the given codes.")
    variants.sort(key=lambda v: (v.product.sku, bulk_stock_service.SIZE_ORDER.get(v.size, 99), v.size))
    png_bytes = barcode_service.generate_labels_sheet([{"variant_code": v.variant_code, "product_name": v.product.name, "size": v.size} for v in variants])
    return StreamingResponse(BytesIO(png_bytes), media_type="image/png", headers={"Content-Disposition": "attachment; filename=labels.png"})


# ------------------------------------------------------------------ #
# Analytics: movers, for the dashboard chart
# ------------------------------------------------------------------ #
@app.get("/api/analytics/movers", response_model=list[schemas.MoverOut])
def movers(days: int = Query(30, ge=1, le=365), limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    """Top ordered designs. Pending and dispatched orders both count as movement."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            models.Product.sku, models.Product.name, models.Product.image_path,
            func.sum(models.OrderLine.qty_ordered).label("units"),
        )
        .join(models.Variant, models.OrderLine.variant_id == models.Variant.id)
        .join(models.Product, models.Variant.product_id == models.Product.id)
        .join(models.OrderUpload, models.OrderLine.order_upload_id == models.OrderUpload.id)
        .filter(
            models.OrderUpload.uploaded_at >= since,
            models.Variant.is_deleted == False,  # noqa: E712
            models.Product.is_deleted == False,  # noqa: E712
            models.OrderLine.status.in_([models.OrderLineStatus.PENDING, models.OrderLineStatus.FULFILLED]),
        )
        .group_by(models.Product.sku, models.Product.name, models.Product.image_path)
        .order_by(func.sum(models.OrderLine.qty_ordered).desc())
        .limit(limit).all()
    )
    return [{"sku": r.sku, "name": r.name, "image_path": r.image_path, "units_moved": int(r.units)} for r in rows]


@app.get("/api/health")
def health():
    return {"status": "ok"}
