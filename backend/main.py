import shutil
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, delete as sa_delete
from sqlalchemy.orm import Session, joinedload, selectinload, contains_eager

from database import Base, engine, get_db
import models
import schemas
from services import (
    inventory_service, alert_service, excel_service, reorder_service,
    barcode_service, bulk_stock_service,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Twins Lady Inventory System", version="1.2")

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

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #
def _variant_status(v: models.Variant) -> str:
    if v.last_counted_at is None:
        return "UNCOUNTED"          # stock unknown - NOT the same as out of stock
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
    return {
        "id": p.id, "sku": p.sku, "name": p.name, "brand": p.brand, "category": p.category,
        "image_path": p.image_path,
        "vendor": {"id": p.vendor.id, "name": p.vendor.name, "contact": p.vendor.contact} if p.vendor else None,
        "variants": [_variant_out(v) for v in sorted(p.variants, key=lambda v: (bulk_stock_service.SIZE_ORDER.get(v.size, 99), v.size))],
        "total_stock": sum(v.current_stock for v in p.variants),
    }


def _variant_row(v: models.Variant) -> dict:
    p = v.product
    return {
        "variant_code": v.variant_code, "sku": p.sku, "size": v.size, "product_name": p.name,
        "brand": p.brand, "category": p.category, "vendor": p.vendor.name if p.vendor else None,
        "image_path": p.image_path, "current_stock": v.current_stock,
        "reorder_threshold": v.reorder_threshold, "target_stock_level": v.target_stock_level,
        "status": _variant_status(v), "last_counted_at": v.last_counted_at,
    }


def _get_variant_by_code(db: Session, variant_code: str) -> models.Variant:
    variant = (
        db.query(models.Variant)
        .options(joinedload(models.Variant.product))
        .filter(models.Variant.variant_code == variant_code.strip().upper())
        .first()
    )
    if not variant:
        raise HTTPException(404, f"Variant '{variant_code}' not found")
    return variant


def _make_variant_code(sku: str, size: str) -> str:
    return f"{sku.strip().upper()}-{size.strip().upper()}"


def _apply_variant_filters(query, q, brand, status):
    V = models.Variant
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            models.Product.sku.ilike(like),
            models.Product.name.ilike(like),
            V.variant_code.ilike(like),
        ))
    if brand:
        query = query.filter(models.Product.brand == brand)
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
        elif s == "ATTENTION":   # LOW or OUT
            query = query.filter(counted, V.current_stock <= V.reorder_threshold)
        else:
            raise HTTPException(400, "status must be one of UNCOUNTED, OUT, LOW, OK, ATTENTION")
    return query


# ------------------------------------------------------------------ #
# Live inventory (flat, filterable, paginated - built for a ~8,000-row catalog)
# ------------------------------------------------------------------ #
@app.get("/api/variants", response_model=schemas.VariantPage)
def list_variants(
    q: str | None = None,
    brand: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    V = models.Variant
    filtered = _apply_variant_filters(db.query(V).join(V.product), q, brand, status)
    total = filtered.with_entities(func.count(V.id)).scalar()
    rows = (
        filtered.options(contains_eager(V.product).joinedload(models.Product.vendor))
        .order_by(models.Product.sku, bulk_stock_service.size_rank_expr(), V.size)
        .offset(offset).limit(limit).all()
    )
    return {"total": total, "limit": limit, "offset": offset, "items": [_variant_row(v) for v in rows]}


@app.get("/api/summary", response_model=schemas.SummaryOut)
def summary(db: Session = Depends(get_db)):
    V = models.Variant
    counted = V.last_counted_at.isnot(None)

    def count(*conds):
        return db.query(func.count(V.id)).filter(*conds).scalar()

    return {
        "products": db.query(func.count(models.Product.id)).scalar(),
        "variants": count(),
        "uncounted": count(V.last_counted_at.is_(None)),
        "out": count(counted, V.current_stock <= 0),
        "low": count(counted, V.current_stock > 0, V.current_stock <= V.reorder_threshold),
        "ok": count(counted, V.current_stock > V.reorder_threshold),
        "open_alerts": db.query(func.count(models.Alert.id)).filter(models.Alert.resolved == False).scalar(),  # noqa: E712
    }


@app.get("/api/brands", response_model=list[str])
def list_brands(db: Session = Depends(get_db)):
    rows = db.query(models.Product.brand).filter(models.Product.brand.isnot(None)).distinct().order_by(models.Product.brand).all()
    return [r[0] for r in rows]


# ------------------------------------------------------------------ #
# Products (+ their size variants)
# ------------------------------------------------------------------ #
@app.get("/api/products", response_model=list[schemas.ProductOut])
def list_products(
    q: str | None = None,
    limit: int | None = Query(None, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(models.Product).options(
        selectinload(models.Product.variants), joinedload(models.Product.vendor)
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(models.Product.sku.ilike(like), models.Product.name.ilike(like)))
    query = query.order_by(models.Product.sku)
    if limit:
        query = query.limit(limit)
    return [_product_out(p) for p in query.all()]


@app.post("/api/products", response_model=schemas.ProductOut)
def create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    sku = payload.sku.strip().upper()
    if db.query(models.Product).filter(models.Product.sku == sku).first():
        raise HTTPException(400, f"SKU '{sku}' already exists.")

    sizes_seen = set()
    for v in payload.variants:
        if v.size.strip().upper() in sizes_seen:
            raise HTTPException(400, f"Duplicate size '{v.size}' in request.")
        sizes_seen.add(v.size.strip().upper())

    product = models.Product(
        sku=sku, name=payload.name, brand=payload.brand,
        category=payload.category, vendor_id=payload.vendor_id,
    )
    db.add(product)
    db.flush()

    now = datetime.now(timezone.utc)
    for v in payload.variants:
        variant = models.Variant(
            product_id=product.id,
            size=v.size.strip().upper(),
            variant_code=_make_variant_code(sku, v.size),
            current_stock=0,
            reorder_threshold=v.reorder_threshold,
            target_stock_level=v.target_stock_level,
            last_counted_at=now,   # the starting stock was typed in on purpose, so it counts as counted
        )
        db.add(variant)
        db.flush()
        if v.initial_stock:
            inventory_service.apply_stock_change(
                db, variant, v.initial_stock,
                transaction_type=models.TransactionType.INITIAL_STOCK,
                reference="product created",
            )
        alert_service.check_variant_alert(db, variant)

    db.commit()
    db.refresh(product)
    return _product_out(product)


@app.patch("/api/products/{sku}", response_model=schemas.ProductOut)
def update_product(sku: str, payload: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.sku == sku.strip().upper()).first()
    if not product:
        raise HTTPException(404, "Product not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
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


@app.delete("/api/products/{sku}")
def delete_product(sku: str, db: Session = Depends(get_db)):
    """Permanently removes a design and every size under it, including its
    full history (ledger, alerts, order lines, reorder lines) - there is no
    undo. Meant for cleaning up a mistaken/duplicate entry; if you need to
    stop selling a real design while keeping its records, leave it as is and
    just let its stock run to 0 instead."""
    product = db.query(models.Product).filter(models.Product.sku == sku.strip().upper()).first()
    if not product:
        raise HTTPException(404, "Product not found")

    variant_ids = [v.id for v in db.query(models.Variant.id).filter(models.Variant.product_id == product.id)]
    if variant_ids:
        db.execute(sa_delete(models.OrderLine).where(models.OrderLine.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.ReorderBatchItem).where(models.ReorderBatchItem.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.Alert).where(models.Alert.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.InventoryTransaction).where(models.InventoryTransaction.variant_id.in_(variant_ids)))
        db.execute(sa_delete(models.Variant).where(models.Variant.id.in_(variant_ids)))
    db.delete(product)
    db.commit()
    return {"deleted": sku.strip().upper(), "sizes_removed": len(variant_ids)}


@app.delete("/api/variants/{variant_code}")
def delete_variant(variant_code: str, db: Session = Depends(get_db)):
    """Remove a single size from a design (e.g. a size you never actually
    stock) without touching its other sizes. If it's the design's only
    remaining size, the design is removed too."""
    variant = _get_variant_by_code(db, variant_code)
    product = variant.product
    db.execute(sa_delete(models.OrderLine).where(models.OrderLine.variant_id == variant.id))
    db.execute(sa_delete(models.ReorderBatchItem).where(models.ReorderBatchItem.variant_id == variant.id))
    db.execute(sa_delete(models.Alert).where(models.Alert.variant_id == variant.id))
    db.execute(sa_delete(models.InventoryTransaction).where(models.InventoryTransaction.variant_id == variant.id))
    db.delete(variant)
    db.flush()
    remaining = db.query(func.count(models.Variant.id)).filter(models.Variant.product_id == product.id).scalar()
    product_also_deleted = remaining == 0
    if product_also_deleted:
        db.delete(product)
    db.commit()
    return {"deleted": variant_code.strip().upper(), "product_also_deleted": product_also_deleted}


@app.post("/api/products/{sku}/image")
def upload_product_image(sku: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.sku == sku.strip().upper()).first()
    if not product:
        raise HTTPException(404, "Product not found")
    ext = Path(file.filename).suffix or ".jpg"
    fname = f"{product.sku}_{uuid.uuid4().hex[:8]}{ext}"
    dest = STATIC_DIR / "product_images" / fname
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    product.image_path = f"/static/product_images/{fname}"
    db.commit()
    return {"image_path": product.image_path}


# ------------------------------------------------------------------ #
# Variant lookup (scan screen) + counting / adjustment
# ------------------------------------------------------------------ #
@app.get("/api/variants/{variant_code}", response_model=schemas.VariantLookupOut)
def lookup_variant(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code)
    return {
        "variant_code": variant.variant_code, "sku": variant.product.sku, "size": variant.size,
        "product_name": variant.product.name, "current_stock": variant.current_stock,
        "counted": variant.last_counted_at is not None,
        "image_path": variant.product.image_path,
    }


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
def count_sheet(brand: str | None = None, only_uncounted: bool = False, db: Session = Depends(get_db)):
    xlsx_bytes = bulk_stock_service.build_count_sheet(db, brand=brand or None, only_uncounted=only_uncounted)
    return StreamingResponse(
        BytesIO(xlsx_bytes), media_type=XLSX_MEDIA,
        headers={"Content-Disposition": "attachment; filename=count_sheet.xlsx"},
    )


@app.post("/api/inventory/bulk-set", response_model=schemas.BulkSetResult)
async def bulk_set(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        return bulk_stock_service.bulk_set_stock(db, content, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/variants/{variant_code}/transactions", response_model=list[schemas.TransactionOut])
def variant_transactions(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code)
    return (
        db.query(models.InventoryTransaction)
        .filter(models.InventoryTransaction.variant_id == variant.id)
        .order_by(models.InventoryTransaction.created_at.desc(), models.InventoryTransaction.id.desc())
        .limit(200)
        .all()
    )


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
@app.get("/api/alerts", response_model=list[schemas.AlertOut])
def list_alerts(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    alerts = alert_service.get_open_alerts(db, limit=limit, offset=offset)
    return [
        {
            "id": a.id, "variant_code": a.variant.variant_code, "sku": a.variant.product.sku,
            "size": a.variant.size, "product_name": a.variant.product.name,
            "alert_type": a.alert_type, "stock_at_alert": a.stock_at_alert, "created_at": a.created_at,
        }
        for a in alerts
    ]


# ------------------------------------------------------------------ #
# Reorder list  (viewing is read-only; a snapshot is saved when it is exported to send out)
# ------------------------------------------------------------------ #
@app.get("/api/reorder-list", response_model=list[schemas.ReorderItemOut])
def reorder_list(db: Session = Depends(get_db)):
    return reorder_service.generate_reorder_list(db, save_batch=False)


@app.get("/api/reorder-list/export")
def reorder_list_export(db: Session = Depends(get_db)):
    items = reorder_service.generate_reorder_list(db, save_batch=True)
    xlsx_bytes = reorder_service.reorder_list_to_excel(items)
    return StreamingResponse(
        BytesIO(xlsx_bytes), media_type=XLSX_MEDIA,
        headers={"Content-Disposition": "attachment; filename=reorder_list.xlsx"},
    )


# ------------------------------------------------------------------ #
# Printable QR labels
# ------------------------------------------------------------------ #
@app.get("/api/variants/{variant_code}/label")
def variant_label(variant_code: str, db: Session = Depends(get_db)):
    variant = _get_variant_by_code(db, variant_code)
    png_bytes = barcode_service.generate_variant_label(variant.variant_code, variant.product.name, variant.size)
    return StreamingResponse(BytesIO(png_bytes), media_type="image/png")


@app.get("/api/labels/sheet")
def labels_sheet(
    variant_codes: str = Query(..., description="Comma-separated variant codes, e.g. 392-M,392-L,393-S"),
    db: Session = Depends(get_db),
):
    codes = [c.strip().upper() for c in variant_codes.split(",") if c.strip()]
    variants = (
        db.query(models.Variant)
        .options(joinedload(models.Variant.product))
        .filter(models.Variant.variant_code.in_(codes))
        .all()
    )
    if not variants:
        raise HTTPException(404, "No matching variants found for the given codes.")
    variants.sort(key=lambda v: (v.product.sku, bulk_stock_service.SIZE_ORDER.get(v.size, 99), v.size))
    png_bytes = barcode_service.generate_labels_sheet([
        {"variant_code": v.variant_code, "product_name": v.product.name, "size": v.size}
        for v in variants
    ])
    return StreamingResponse(
        BytesIO(png_bytes), media_type="image/png",
        headers={"Content-Disposition": "attachment; filename=labels.png"},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}
