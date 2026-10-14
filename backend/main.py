import asyncio
import os
import shutil
import uuid
import re
import time
import logging
from importlib.util import find_spec
from difflib import SequenceMatcher
from collections import defaultdict, deque
from threading import Lock
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, RedirectResponse, Response
from sqlalchemy import case, func, or_, delete as sa_delete, inspect as sa_inspect, text
from sqlalchemy.orm import Session, joinedload, selectinload, contains_eager

from database import Base, engine, get_db, SessionLocal
import models
import schemas
from services import (
    inventory_service, alert_service, excel_service, reorder_service,
    barcode_service, bulk_stock_service, bulk_product_service, assistant_service,
    visual_match_service, visual_search_service,
)

Base.metadata.create_all(bind=engine)
logger = logging.getLogger(__name__)


def _index_product_photo_background(product_id: int, image_id: int, content: bytes, image_path: str) -> None:
    """Index a saved catalog photo after the upload response has been returned.

    SigLIP's first model load can take several seconds on a small Railway
    service. Photo saving and inventory creation must not wait for that work.
    """
    db = SessionLocal()
    path = Path(image_path)
    try:
        visual_search_service.index_product_image(db, product_id, image_id, content, path)
        db.commit()
        visual_search_service.clear_visual_cache()
    except Exception as exc:
        db.rollback()
        try:
            visual_search_service.remove_product_embedding(db, product_id, image_id)
            db.commit()
        except Exception:
            db.rollback()
        logger.warning("Product photo saved; visual indexing queued for later rebuild (%s).", type(exc).__name__)
    finally:
        db.close()


def migrate_legacy_brand_data_explicitly(target_engine=engine):
    """Optional legacy cleanup; never invoke automatically during app startup.

    This changes saved product names and drops a database column. Run it only
    as a separately reviewed migration after taking a restorable backup.
    """
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
PRODUCT_IMAGE_DIR = Path(os.getenv("PRODUCT_IMAGE_DIR") or str(STATIC_DIR / "product_images")).resolve()
PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
# Mount the persistent Railway image directory before the general static mount.
app.mount("/static/product_images", StaticFiles(directory=PRODUCT_IMAGE_DIR), name="product-images")
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
    image_paths = []
    if p.image_path:
        image_paths.append(p.image_path)
    image_paths.extend(
        image.image_path for image in sorted(
            p.reference_images,
            key=lambda image: (not image.is_primary, image.id),
        )
        if image.image_path and image.image_path not in image_paths
    )
    return {
        "id": p.id, "sku": p.sku, "name": p.name, "category": p.category,
        "image_path": p.image_path,
        "images": image_paths,
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
    q = db.query(models.Product).options(
        selectinload(models.Product.variants),
        selectinload(models.Product.reference_images),
        joinedload(models.Product.vendor),
    ) \
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
def _smart_search_tokens(value: str | None) -> list[str]:
    return list(dict.fromkeys(token for token in re.split(r"[^\w]+", (value or "").strip().casefold()) if len(token) > 1))


def _apply_smart_search(query, columns, value: str | None):
    """Match each search word against any identity/name field."""
    for token in _smart_search_tokens(value):
        like = f"%{token}%"
        query = query.filter(or_(*(column.ilike(like) for column in columns)))
    return query


def _fuzzy_product_score(value: str, product) -> float:
    """Score typo-tolerant product-name/SKU matches after exact SQL search misses."""
    wanted = _smart_search_tokens(value)
    candidates = _smart_search_tokens(" ".join((product.sku or "", product.name or "", product.category or "")))
    if not wanted or not candidates:
        return 0.0
    scores = []
    for token in wanted:
        best = 1.0 if any(token in candidate for candidate in candidates) else max(
            (SequenceMatcher(None, token, candidate).ratio() for candidate in candidates),
            default=0.0,
        )
        minimum = 1.0 if len(token) <= 2 else (0.82 if len(token) <= 4 else 0.70)
        if best < minimum:
            return 0.0
        scores.append(best)
    return sum(scores) / len(scores)


@app.get("/api/products")
def list_products(q: str | None = None, limit: int | None = Query(None, ge=1, le=500), offset: int = Query(0, ge=0), paged: bool = False, db: Session = Depends(get_db)):
    base = db.query(models.Product).options(
        selectinload(models.Product.variants),
        selectinload(models.Product.reference_images),
        joinedload(models.Product.vendor),
    ) \
        .filter(models.Product.is_deleted == False)  # noqa: E712
    query = _apply_smart_search(base, (models.Product.sku, models.Product.name, models.Product.category), q)
    # Paged clients need an exact count even when the search box is empty.
    # Without this, Products renders "null designs" and "1–0 of null" while
    # the first page itself contains valid products.
    exact_total = query.count() if paged or (q and q.strip()) else None
    total = exact_total if paged else None
    if q and q.strip() and exact_total == 0:
        fuzzy = [(score, product) for product in base.all() if (score := _fuzzy_product_score(q, product)) > 0]
        fuzzy.sort(key=lambda item: (item[0], item[1].created_at.isoformat() if item[1].created_at else "", item[1].id), reverse=True)
        total = len(fuzzy)
        if limit:
            fuzzy = fuzzy[offset:offset + limit]
        items = [_product_out(product) for _, product in fuzzy]
        return {"items": items, "total": total, "offset": offset, "limit": limit} if paged else items
    query = query.order_by(models.Product.created_at.desc(), models.Product.id.desc())
    if limit:
        query = query.offset(offset).limit(limit)
    items = [_product_out(p) for p in query.all()]
    return {"items": items, "total": total, "offset": offset, "limit": limit} if paged else items


_assistant_request_times: dict[str, deque] = defaultdict(deque)
_assistant_rate_lock = Lock()


def _assistant_rate_limit(client_ip: str) -> bool:
    now = time.monotonic()
    with _assistant_rate_lock:
        recent = _assistant_request_times[client_ip]
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= 12:
            return False
        recent.append(now)
        return True


def _assistant_size(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().upper())
    aliases = {
        "SMALL": "S", "स्मॉल": "S", "छोटा": "S", "छोटी": "S",
        "MEDIUM": "M", "मीडियम": "M", "मध्यम": "M",
        "LARGE": "L", "लार्ज": "L", "बड़ा": "L", "बड़ी": "L", "बड़ी": "L",
        "EXTRA LARGE": "XL", "एक्स्ट्रा लार्ज": "XL",
        "2XL": "XXL", "2X-LARGE": "XXL", "DOUBLE XL": "XXL", "डबल एक्सएल": "XXL", "डबल एक्स्ट्रा लार्ज": "XXL",
        "XXXL": "3XL", "FREE-SIZE": "FREE SIZE", "फ्री साइज": "FREE SIZE", "फ्री साइज़": "FREE SIZE", "ONE SIZE": "FREE SIZE", "एक साइज": "FREE SIZE",
    }
    return aliases.get(normalized, normalized)


def _daily_sales_summary(db: Session) -> dict:
    """Summarize units actually dispatched today in India Standard Time."""
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).date()
    start_utc = datetime.combine(today, datetime.min.time(), tzinfo=ist).astimezone(timezone.utc)
    end_utc = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=ist).astimezone(timezone.utc)
    T, V, P = models.InventoryTransaction, models.Variant, models.Product
    base = db.query(T).filter(
        T.transaction_type == models.TransactionType.ORDER_DEDUCTION,
        T.created_at >= start_utc,
        T.created_at < end_utc,
        T.change_qty < 0,
    )
    units = base.with_entities(func.coalesce(-func.sum(T.change_qty), 0)).scalar()
    line_count = base.with_entities(func.count(T.id)).scalar()
    design_count = (
        db.query(func.count(func.distinct(P.id)))
        .join(V, V.product_id == P.id).join(T, T.variant_id == V.id)
        .filter(
            T.transaction_type == models.TransactionType.ORDER_DEDUCTION,
            T.created_at >= start_utc, T.created_at < end_utc, T.change_qty < 0,
        ).scalar()
    )
    rows = (
        db.query(P.sku, P.name, func.sum(-T.change_qty).label("units"))
        .join(V, V.product_id == P.id)
        .join(T, T.variant_id == V.id)
        .filter(
            T.transaction_type == models.TransactionType.ORDER_DEDUCTION,
            T.created_at >= start_utc,
            T.created_at < end_utc,
            T.change_qty < 0,
        )
        .group_by(P.id, P.sku, P.name)
        .order_by(func.sum(-T.change_qty).desc(), P.sku.asc())
        .limit(5).all()
    )
    return {
        "date": today.isoformat(),
        "timezone": "Asia/Kolkata",
        "dispatched_units": int(units or 0),
        "dispatch_lines": int(line_count or 0),
        "designs_sold": int(design_count or 0),
        "top_products": [{"sku": row.sku, "name": row.name, "units": int(row.units or 0)} for row in rows],
    }


@app.get("/api/analytics/daily-sales")
def daily_sales_summary(db: Session = Depends(get_db)):
    return _daily_sales_summary(db)


@app.post("/api/assistant/chat")
async def inventory_assistant_chat(payload: schemas.AssistantChatRequest, request: Request, db: Session = Depends(get_db)):
    """Interpret a chat turn, ground it in catalog data, and prepare a safe action.

    This route never changes stock. Inventory writes still require the existing
    explicit stock-adjustment confirmation flow.
    """
    if not assistant_service.api_key_configured():
        raise HTTPException(status_code=503, detail="Disha की AI सेवा सेट नहीं है। AI_PROVIDER और संबंधित सेवा URL/की जाँचें।")
    client_ip = request.client.host if request.client else "unknown"
    if not _assistant_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Assistant request limit reached. Please wait a minute and try again.")
    try:
        intent = await assistant_service.parse_inventory_request(
            payload.message,
            [item.model_dump() for item in payload.history],
        )
    except assistant_service.AssistantProviderError as exc:
        detail = exc.user_message or "Gemini assistant temporarily unavailable; try again later."
        raise HTTPException(status_code=503, detail=detail) from exc

    action = intent["intent"]
    last_assistant_message = next(
        (str(item.content) for item in reversed(payload.history) if item.role == "assistant"),
        "",
    )
    all_sizes_phrase = bool(re.search(r"\b(?:sab(?:hi)?|saare?|sare|all|every|har(?:\s+ek)?)\b.{0,24}\b(?:size|sizes|stock)\b", payload.message, flags=re.IGNORECASE))
    answered_size_question = bool(re.search(r"kaunsa\s+size|which\s+size|what\s+size", last_assistant_message, flags=re.IGNORECASE))
    wants_all_sizes = all_sizes_phrase or (answered_size_question and bool(re.fullmatch(r"\s*(?:sab|sabhi|saare|sare|all)\s*[.!?]*\s*", payload.message, flags=re.IGNORECASE)))
    if wants_all_sizes:
        if not intent.get("product_query") and not payload.selected_sku:
            prior_design = re.search(r"^\s*(.+?)\s+mil\s+gaya\b", last_assistant_message, flags=re.IGNORECASE)
            if prior_design:
                intent["product_query"] = prior_design.group(1).strip(" .:-")[:160]
        # Treat this follow-up as a complete size summary, even if the intent
        # model repeats its previous "which size?" clarification. Never turn
        # an unqualified request into a search that could return random items.
        if intent.get("product_query") or payload.selected_sku:
            action = "product_search"
    if action == "daily_sales":
        sales_summary = _daily_sales_summary(db)
        if not sales_summary["dispatched_units"]:
            reply = f"Aaj ({sales_summary['date']}) abhi tak koi dispatched sale record nahi hai. Pending orders sale mein count nahi kiye jaate."
        else:
            top = ", ".join(f"{item['name']} ({item['sku']}): {item['units']}" for item in sales_summary["top_products"][:3])
            reply = f"Aaj ({sales_summary['date']}) {sales_summary['dispatched_units']} units dispatch hue, {sales_summary['designs_sold']} designs mein. Top: {top}. Ye dispatched units hain; revenue/rupee sales app mein record nahi hote."
        return {"status": "daily_sales", "reply": reply, "summary": sales_summary}
    if action == "app_status":
        dashboard = summary(db)
        pending_line_count, pending_units = db.query(
            func.count(models.OrderLine.id),
            func.coalesce(func.sum(models.OrderLine.qty_ordered), 0),
        ).filter(models.OrderLine.status == models.OrderLineStatus.PENDING).one()
        total_stock = db.query(func.coalesce(func.sum(models.Variant.current_stock), 0)).join(models.Product).filter(
            models.Variant.is_deleted == False, models.Product.is_deleted == False  # noqa: E712
        ).scalar()
        sales = _daily_sales_summary(db)
        reply = (
            f"App ka live status: {dashboard['products']} designs aur {dashboard['variants']} sizes. "
            f"Counted stock total {int(total_stock or 0)} pieces hai; {dashboard['ok']} sizes in stock, "
            f"{dashboard['low']} low stock, {dashboard['out']} out of stock aur {dashboard['uncounted']} abhi count nahi hue. "
            f"{dashboard['open_alerts']} open alerts hain. Pending orders {int(pending_line_count or 0)} lines / "
            f"{int(pending_units or 0)} pieces; aaj {sales['dispatched_units']} pieces dispatch hue."
        )
        return {"status": "app_status", "reply": reply, "summary": {**dashboard, "total_stock": int(total_stock or 0), "pending_order_lines": int(pending_line_count or 0), "pending_order_units": int(pending_units or 0), "today_dispatched_units": sales["dispatched_units"]}}
    if action == "product_search":
        query = db.query(models.Product).options(selectinload(models.Product.variants)).filter(models.Product.is_deleted == False)  # noqa: E712
        if payload.selected_sku:
            query = query.filter(models.Product.sku == payload.selected_sku.strip().upper())
        else:
            query = _apply_smart_search(query, (models.Product.sku, models.Product.name, models.Product.category), intent.get("product_query"))
        products = query.order_by(models.Product.name.asc()).limit(8).all()
        if not products and intent.get("product_query"):
            candidates = db.query(models.Product).options(selectinload(models.Product.variants)).filter(
                models.Product.is_deleted == False  # noqa: E712
            ).all()
            ranked = [
                (_fuzzy_product_score(intent["product_query"], product), product)
                for product in candidates
            ]
            ranked = [(score, product) for score, product in ranked if score > 0]
            ranked.sort(key=lambda item: (item[0], item[1].name.casefold(), item[1].sku), reverse=True)
            products = [product for _, product in ranked[:8]]
        if not products:
            return {"status": "not_found", "reply": f"{intent.get('product_query') or 'Is search'} ke matching designs nahi mile. SKU, colour, ya naam ka koi aur hissa boliye."}
        details = []
        for product in products:
            variants = [v for v in product.variants if not v.is_deleted]
            size_rows = [{"size": v.size, "stock": v.current_stock, "counted": v.last_counted_at is not None} for v in variants]
            details.append({"sku": product.sku, "name": product.name, "category": product.category, "image_path": product.image_path, "sizes": size_rows})
        reply = ("Mujhe milte-julte designs mile. Agar rang alag hai, sahi colour wala design choose karein.\n" if len(details) > 1 else "") + "\n".join(
            f"{product['name']}: " + ", ".join(
                f"size {size['size']}, {size['stock'] if size['counted'] else 'stock not counted'} pieces" for size in product["sizes"]
            ) for product in details
        )
        return {"status": "product_search", "reply": reply, "products": details}
    if action == "alerts_summary":
        alerts = _collect_alerts(db, intent.get("product_query") or None)
        if not alerts:
            return {"status": "alerts_summary", "reply": "Is search ke liye abhi koi open stock ya order-shortage alert nahi hai.", "total": 0}
        lines = [f"{a['product_name']} ({a['sku']}), {a['size']}: {a['alert_type'].replace('_', ' ').lower()}, stock {a['stock_at_alert']}" + (f", orders {a['ordered_qty']}, restock {a['required_qty']}" if a['alert_type'] == "ORDER_SHORTAGE" else "") for a in alerts[:8]]
        more = f" Aur {len(alerts) - 8} alerts hain." if len(alerts) > 8 else ""
        return {"status": "alerts_summary", "reply": f"{len(alerts)} open size alerts hain. " + "; ".join(lines) + "." + more, "total": len(alerts)}
    if action == "reorder_summary":
        items = reorder_service.generate_reorder_list(db, save_batch=False, part=intent.get("product_query") or None)
        needed = [item for item in items if item["required_qty"] > 0]
        if not needed:
            return {"status": "reorder_summary", "reply": "Abhi pending orders ke liye koi restock quantity required nahi hai.", "total": 0}
        lines = [f"{i['name']} ({i['sku']}), {i['size']}: stock {i['current_stock']}, pending orders {i['ordered_qty']}, mangwana {i['required_qty']}" for i in needed[:8]]
        more = f" Aur {len(needed) - 8} sizes hain." if len(needed) > 8 else ""
        return {"status": "reorder_summary", "reply": f"{len(needed)} sizes mein restock chahiye. " + "; ".join(lines) + "." + more, "total": len(needed)}
    if action == "pending_orders":
        pending_lines, pending_units = db.query(func.count(models.OrderLine.id), func.coalesce(func.sum(models.OrderLine.qty_ordered), 0)).filter(models.OrderLine.status == models.OrderLineStatus.PENDING).one()
        batches = db.query(models.OrderUpload).join(models.OrderLine).filter(models.OrderLine.status == models.OrderLineStatus.PENDING).distinct().order_by(models.OrderUpload.uploaded_at.desc()).limit(5).all()
        batch_text = ", ".join(f"batch {b.id} ({b.filename or 'orders'})" for b in batches) or "koi pending batch nahi"
        return {"status": "pending_orders", "reply": f"{int(pending_lines or 0)} pending order lines hain, total {int(pending_units or 0)} pieces. Recent batches: {batch_text}. Dispatch se pehle stock validate hota hai."}
    if action == "recent_activity":
        T, V, P = models.InventoryTransaction, models.Variant, models.Product
        query = db.query(T).join(V, T.variant_id == V.id).join(P, V.product_id == P.id)
        if intent.get("product_query"):
            query = _apply_smart_search(query, (P.sku, P.name, V.variant_code, V.size), intent["product_query"])
        rows = query.options(contains_eager(T.variant).contains_eager(V.product)).order_by(T.created_at.desc(), T.id.desc()).limit(8).all()
        if not rows:
            return {"status": "recent_activity", "reply": "Is search ke liye inventory history nahi mili."}
        lines = [f"{t.variant.product.name}, {t.variant.size}: {t.change_qty:+d} ({t.transaction_type.value}), balance {t.balance_after}" for t in rows]
        return {"status": "recent_activity", "reply": "Recent stock changes: " + "; ".join(lines) + "."}
    if action == "product_create":
        sku = intent["sku"].strip().upper()
        name = intent["product_name"].strip()
        # Accept natural voice lists such as "S M L", comma-separated lists,
        # and multi-word size labels without splitting FREE SIZE into two.
        size_text = intent["sizes"].upper()
        for hindi_size, latin_size in (("डबल एक्सएल", "XXL"), ("एक्स्ट्रा लार्ज", "XL"), ("फ्री साइज", "FREE SIZE"), ("फ्री साइज़", "FREE SIZE"), ("एक्सएल", "XL"), ("एस", "S"), ("एम", "M"), ("एल", "L")):
            size_text = size_text.replace(hindi_size.upper(), latin_size)
        raw_sizes = re.findall(r"FREE\s+SIZE|ONE\s+SIZE|[A-Za-z0-9-]+", size_text, flags=re.IGNORECASE)
        sizes = [_assistant_size(value) for value in raw_sizes if value.strip()]
        sizes = list(dict.fromkeys(size for size in sizes if size))
        if not sku or not name or not sizes:
            return {"status": "clarify", "reply": "नया प्रोडक्ट बनाने के लिए उसका SKU, नाम और साइज़ बताइए। उदाहरण: SKU DRESS-21, नाम नीली ड्रेस, साइज़ एस एम एल। सेव करने से पहले मैं आपसे पुष्टि करूँगी।"}
        if len(sku) > 64 or len(name) > 300:
            return {"status": "clarify", "reply": "SKU या प्रोडक्ट का नाम बहुत लंबा है। कृपया छोटा SKU और नाम बताइए।"}
        existing = db.query(models.Product).filter(models.Product.sku == sku).first()
        if existing:
            return {"status": "clarify", "reply": f"SKU {sku} पहले से कैटलॉग या ट्रैश में मौजूद है। मौजूदा प्रोडक्ट को एडिट या रिस्टोर करें, या दूसरा SKU बताइए।"}
        return {"status": "confirm_action", "reply": f"मैं {name} प्रोडक्ट, SKU {sku}, साइज़ {', '.join(sizes)} के साथ बनाऊँगी। हर साइज़ का शुरुआती स्टॉक शून्य रहेगा। क्या सेव कर दूँ?", "action": {"type": "product_create", "sku": sku, "name": name, "category": intent["category"] or None, "sizes": sizes}}
    if action in {"product_update", "product_archive", "product_restore"}:
        product_query = payload.selected_sku.strip().upper() if payload.selected_sku else intent.get("product_query", "").strip()
        if not product_query:
            return {"status": "clarify", "reply": "Kaunsa design? Product name ya SKU batayein."}
        include_deleted = action == "product_restore"
        query = db.query(models.Product).filter(models.Product.is_deleted == include_deleted)
        if payload.selected_sku:
            query = query.filter(models.Product.sku == product_query)
        else:
            query = _apply_smart_search(query, (models.Product.sku, models.Product.name, models.Product.category), product_query)
        matches = query.order_by(models.Product.name.asc()).limit(9).all()
        if not matches:
            state = "Trash mein" if include_deleted else "active catalog mein"
            return {"status": "not_found", "reply": f"{product_query} {state} nahi mila."}
        if len(matches) > 1:
            return {"status": "choose_product", "reply": "Kai designs match hue. Sahi design chunein:", "choices": [{"sku": p.sku, "name": p.name} for p in matches[:8]]}
        product = matches[0]
        if action == "product_update":
            changes = {}
            if intent["new_sku"]: changes["sku"] = intent["new_sku"]
            if intent["new_name"]: changes["name"] = intent["new_name"]
            if intent["new_category"]: changes["category"] = intent["new_category"]
            if not changes:
                return {"status": "clarify", "reply": "Kya badalna hai? Naya product name, SKU, ya category clearly batayein."}
            if changes.get("sku"):
                conflict = db.query(models.Product).filter(models.Product.sku == changes["sku"], models.Product.id != product.id).first()
                if conflict:
                    return {"status": "clarify", "reply": f"SKU {changes['sku']} doosre design mein already use ho raha hai."}
            description = ", ".join(f"{key}: {getattr(product, key) or '—'} → {value}" for key, value in changes.items())
            return {"status": "confirm_action", "reply": f"{product.name} ({product.sku}) mein ye change hoga: {description}. Confirm karun?", "action": {"type": "product_update", "sku": product.sku, "changes": changes}}
        kind = "product_archive" if action == "product_archive" else "product_restore"
        verb = "Trash mein move" if action == "product_archive" else "restore"
        return {"status": "confirm_action", "reply": f"{product.name} ({product.sku}) aur uske sabhi sizes ko {verb} karungi. Stock history preserve rahegi. Confirm?", "action": {"type": kind, "sku": product.sku}}
    if action == "order_dispatch":
        order_id = intent.get("order_id")
        if not order_id or order_id < 1:
            return {"status": "clarify", "reply": "Dispatch se pehle batch ID chahiye. 'Pending orders batao' bolkar batch ID dekhein, phir 'batch 123 dispatch karo' kahe."}
        batch = db.query(models.OrderUpload).filter(models.OrderUpload.id == order_id).first()
        if not batch:
            return {"status": "not_found", "reply": f"Order batch {order_id} nahi mila."}
        lines = db.query(models.OrderLine).filter(models.OrderLine.order_upload_id == order_id, models.OrderLine.status == models.OrderLineStatus.PENDING).all()
        if not lines:
            return {"status": "clarify", "reply": f"Batch {order_id} mein koi pending order line nahi hai."}
        units = sum(line.qty_ordered for line in lines)
        return {"status": "confirm_action", "reply": f"Batch {order_id} ({batch.filename or 'orders'}) ke {len(lines)} pending lines, total {units} pieces dispatch honge. Dispatch ke waqt har size ka stock validate hoga; shortage hui to poora batch rukega. Confirm?", "action": {"type": "order_dispatch", "order_id": order_id, "filename": batch.filename, "lines": len(lines), "units": units}}
    if action not in {"stock_add", "stock_set", "stock_check"}:
        reply = intent.get("reply") or "Main Disha hoon. Aap mujhse normal baat kar sakte hain, ya inventory ke baare mein pooch sakte hain."
        return {"status": "reply", "reply": reply}

    product_query = payload.selected_sku.strip().upper() if payload.selected_sku else intent["product_query"]
    if not product_query:
        return {"status": "clarify", "reply": "Kaunsa design ya SKU? Product ka naam, rang, ya SKU batayein."}
    products_query = db.query(models.Product).options(selectinload(models.Product.variants)).filter(models.Product.is_deleted == False)  # noqa: E712
    if payload.selected_sku:
        products_query = products_query.filter(models.Product.sku == payload.selected_sku.strip().upper())
    else:
        products_query = _apply_smart_search(products_query, (models.Product.sku, models.Product.name, models.Product.category), product_query)
    products = products_query.order_by(models.Product.created_at.desc(), models.Product.id.desc()).limit(30).all()
    if not products:
        return {"status": "not_found", "reply": f"'{product_query}' naam ya SKU ka design catalog mein nahi mila. SKU, rang, ya product name dobara batayein."}

    requested_size = _assistant_size(intent["size"])
    operation = "set" if action == "stock_set" else "add"
    quantity = intent["quantity"]
    if action in {"stock_add", "stock_set"} and quantity is None:
        return {"status": "clarify", "reply": "Kitne pieces? Quantity batayein—jaise '5 add karo' ya 'total stock 12 set karo'."}

    live_products = []
    for product in products:
        live_variants = [variant for variant in product.variants if not variant.is_deleted]
        if live_variants:
            live_products.append((product, live_variants))
    if not requested_size:
        if len(live_products) == 1:
            product, variants = live_products[0]
            if wants_all_sizes:
                size_rows = [{"size": variant.size, "stock": variant.current_stock, "counted": variant.last_counted_at is not None} for variant in variants]
                size_summary = ", ".join(f"{row['size']} size mein {row['stock'] if row['counted'] else 'stock count nahi hua'} pieces" for row in size_rows)
                return {
                    "status": "product_search",
                    "reply": f"{product.name} ke sabhi sizes ka stock: {size_summary}.",
                    "products": [{"sku": product.sku, "name": product.name, "category": product.category, "image_path": product.image_path, "sizes": size_rows}],
                }
            sizes = ", ".join(variant.size for variant in variants)
            return {"status": "clarify", "reply": f"{product.name} mein ye sizes available hain: {sizes}. Aap chahein to 'sabhi sizes ka stock' bol sakte hain, ya ek size batayein."}
        choices = [{"sku": product.sku, "name": product.name} for product, _ in live_products[:8]]
        return {"status": "choose_product", "reply": "Kaunsa size aur kaunsa design? Sahi product chunein:", "choices": choices}

    matches = []
    for product, variants in live_products:
        variant = next((item for item in variants if _assistant_size(item.size) == requested_size), None)
        if variant:
            matches.append((product, variant))

    if not matches:
        if len(live_products) == 1:
            product, variants = live_products[0]
            sizes = ", ".join(variant.size for variant in variants) or "koi active size nahi"
            return {"status": "missing_size", "reply": f"{product.name} / {product.sku} mil gaya, lekin {requested_size} size nahi hai. Available sizes: {sizes}.", "available_sizes": [variant.size for variant in variants]}
        choices = [{"sku": product.sku, "name": product.name, "sizes": [variant.size for variant in variants]} for product, variants in live_products[:8]]
        return {"status": "choose_product", "reply": f"In designs mein {requested_size} size nahi mila. Sahi design chunein:", "choices": choices}

    if len(matches) > 1:
        choices = [{"sku": product.sku, "name": product.name, "size": variant.size, "current_stock": variant.current_stock} for product, variant in matches[:8]]
        return {"status": "choose_product", "reply": f"{requested_size} size ke kai matching designs mile. Sahi design chunein:", "choices": choices}

    product, variant = matches[0]
    counted = variant.last_counted_at is not None
    if action == "stock_check":
        if not counted:
            reply = f"{product.name}, size {variant.size} ka stock abhi physical count nahi hua hai. Pehle count karke enter karna hoga."
            return {"status": "reply", "reply": reply}
        return {"status": "reply", "reply": f"{product.name}, size {variant.size} mein abhi {variant.current_stock} pieces hain.", "stock": {"sku": product.sku, "name": product.name, "size": variant.size, "current_stock": variant.current_stock}}
    if operation == "add" and not counted:
        return {"status": "clarify", "reply": f"{product.name}, size {variant.size} ka purana stock abhi count nahi hua. Galat total se bachne ke liye pehle is size ka kul physical count batayein."}

    current_stock = int(variant.current_stock) if counted else 0
    proposed_stock = current_stock + int(quantity) if operation == "add" else int(quantity)
    return {
        "status": "confirm_stock",
        "reply": f"{product.name}, size {variant.size}: abhi {current_stock}, update ke baad {proposed_stock} pieces honge. Kya confirm karun?",
        "action": {
            "sku": product.sku, "name": product.name, "size": variant.size,
            "variant_code": variant.variant_code, "current_stock": current_stock,
            "proposed_stock": proposed_stock, "quantity": int(quantity),
            "operation": operation, "counted": counted,
        },
    }


@app.post("/api/assistant/transcribe")
async def assistant_transcribe(request: Request, file: UploadFile = File(...)):
    """Transcribe one short Hindi/English utterance without restarting the mic."""
    client_ip = request.client.host if request.client else "unknown"
    if not _assistant_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Voice request limit reached. Please wait a minute.")
    audio = await file.read(8 * 1024 * 1024 + 1)
    if len(audio) > 8 * 1024 * 1024:
        raise HTTPException(413, "Audio clip is too large. Please speak in a shorter sentence.")
    if not audio:
        raise HTTPException(400, "No speech audio was captured.")
    try:
        transcript = await assistant_service.transcribe_audio(
            audio, file.filename or "disha-utterance.webm", file.content_type or "audio/webm"
        )
    except assistant_service.AssistantProviderError as exc:
        raise HTTPException(503, detail=exc.user_message) from exc
    if not transcript:
        raise HTTPException(422, "Speech was not clear. Please say it again.")
    return {"text": transcript}


@app.post("/api/assistant/speech")
async def assistant_speech(payload: schemas.AssistantSpeechRequest, request: Request):
    """Generate a warm built-in Disha voice; provider key never leaves the server."""
    client_ip = request.client.host if request.client else "unknown"
    if not _assistant_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Voice request limit reached. Please wait a minute.")
    try:
        audio = await assistant_service.synthesize_speech(payload.text)
    except assistant_service.AssistantProviderError as exc:
        raise HTTPException(503, detail=exc.user_message) from exc
    media_type = "audio/wav" if audio.startswith(b"RIFF") else "audio/mpeg"
    return Response(content=audio, media_type=media_type, headers={"Cache-Control": "no-store"})


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
def upload_product_image(
    sku: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    product = _get_product_by_sku(db, sku)
    content = file.file.read(visual_match_service.MAX_SCAN_BYTES + 1)
    try:
        visual_match_service.validate_image(content)
        ext = visual_match_service.image_extension(content)
    except visual_match_service.InvalidProductImage as exc:
        raise HTTPException(400, str(exc)) from exc
    fname = f"{product.sku}_{uuid.uuid4().hex[:8]}{ext}"
    dest = PRODUCT_IMAGE_DIR / fname
    dest.write_bytes(content)
    old_path = product.image_path
    primary_ref = db.query(models.ProductImage).filter(
        models.ProductImage.product_id == product.id, models.ProductImage.is_primary == True  # noqa: E712
    ).first()
    product.image_path = f"/static/product_images/{fname}"
    if primary_ref:
        primary_ref.image_path = product.image_path
    else:
        primary_ref = models.ProductImage(product_id=product.id, image_path=product.image_path, is_primary=True)
        db.add(primary_ref)
    try:
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        dest.unlink(missing_ok=True)
        raise
    background_tasks.add_task(_index_product_photo_background, product.id, primary_ref.id, content, str(dest))
    if old_path:
        try:
            old_name = Path(old_path).name
            (PRODUCT_IMAGE_DIR / old_name).unlink(missing_ok=True)
        except Exception:
            pass
    return {"image_path": product.image_path}


@app.post("/api/products/{sku}/images")
async def upload_product_reference_images(
    sku: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Attach up to eight genuine extra views to this product, never to stock rows."""
    product = _get_product_by_sku(db, sku)
    if not files or len(files) > 8:
        raise HTTPException(400, "Choose between 1 and 8 reference photos.")
    saved: list[tuple[models.ProductImage, bytes, Path]] = []
    try:
        for file in files:
            content = await file.read(visual_match_service.MAX_SCAN_BYTES + 1)
            visual_match_service.validate_image(content)
            ext = visual_match_service.image_extension(content)
            fname = f"{product.sku}_{uuid.uuid4().hex[:8]}{ext}"
            dest = PRODUCT_IMAGE_DIR / fname
            dest.write_bytes(content)
            image = models.ProductImage(
                product_id=product.id,
                image_path=f"/static/product_images/{fname}",
                is_primary=False,
            )
            db.add(image)
            saved.append((image, content, dest))
        db.flush()
        db.commit()
    except visual_match_service.InvalidProductImage as exc:
        db.rollback()
        for _, _, dest in saved:
            dest.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        db.rollback()
        for _, _, dest in saved:
            dest.unlink(missing_ok=True)
        raise

    for image, content, dest in saved:
        background_tasks.add_task(_index_product_photo_background, product.id, image.id, content, str(dest))
    return {"saved": len(saved), "queued_for_indexing": len(saved), "message": "Photos saved. Visual indexing will finish in the background."}


@app.post("/api/products/visual-match")
async def visual_product_match(
    request: Request,
    file: UploadFile = File(...),
    limit: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """Return a verified, ambiguous, or no-match result; this route never changes stock."""
    client_key = request.client.host if request.client else "unknown"
    if not visual_search_service.allow_scan(client_key):
        raise HTTPException(429, "Too many image searches. Please wait a minute and try again.")
    content = await file.read(visual_match_service.MAX_SCAN_BYTES + 1)
    try:
        visual_match_service.validate_image(content)
        result = await asyncio.to_thread(visual_search_service.search, db, content, PRODUCT_IMAGE_DIR, limit)
    except visual_match_service.InvalidProductImage as exc:
        raise HTTPException(400, str(exc)) from exc
    except visual_search_service.VisualSearchUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except visual_match_service.ImageModelUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    product_ids = [hit.product_id for hit in result.matches]
    products = db.query(models.Product).options(selectinload(models.Product.variants)).filter(
        models.Product.id.in_(product_ids), models.Product.is_deleted == False  # noqa: E712
    ).all() if product_ids else []
    by_id = {product.id: product for product in products}
    matches = []
    for hit in result.matches:
        product = by_id.get(hit.product_id)
        if product:
            matches.append({
                "similarity": round(hit.similarity, 4),
                "reference_image_path": hit.reference_image_path,
                "product": _product_out(product),
            })
    status = result.status if matches else "no_match"
    message = result.message if matches else "No active catalog match is available. Check the product photos or try another photo."
    return {
        "ranked_designs": len(product_ids),
        "status": status,
        "matches": matches,
        "message": message,
        "score_type": "cosine_similarity_not_probability",
        "notice": "Only a matched result is a strong visual match. Ambiguous results are suggestions; photo search never changes stock.",
    }


@app.get("/api/visual-search/status")
def visual_search_status(db: Session = Depends(get_db)):
    """Report safe setup diagnostics for photo search without exposing secrets."""
    dialect = engine.dialect.name
    try:
        photo_count = db.query(models.ProductImage).join(models.Product).filter(
            models.Product.is_deleted == False  # noqa: E712
        ).count()
    except Exception:
        photo_count = 0

    pgvector_index_exists = False
    pgvector_extension_installed = False
    indexed_photo_count = 0
    postgres_connected = False
    if dialect == "postgresql":
        try:
            db.execute(text("SELECT 1"))
            postgres_connected = True
        except Exception:
            db.rollback()
    if postgres_connected:
        try:
            pgvector_extension_installed = bool(db.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )).scalar())
            pgvector_index_exists = bool(db.execute(
                text("SELECT to_regclass(:table_name) IS NOT NULL"),
                {"table_name": visual_search_service.TABLE_NAME},
            ).scalar())
            if pgvector_index_exists:
                indexed_photo_count = int(db.execute(
                    text(f"SELECT count(*) FROM {visual_search_service.TABLE_NAME} WHERE model_name = :model"),
                    {"model": visual_search_service.MODEL_NAME},
                ).scalar() or 0)
        except Exception:
            db.rollback()
            pgvector_index_exists = False

    runtime_installed = bool(find_spec("torch") and find_spec("transformers"))
    model_weights_loaded = visual_match_service._encoder is not None
    external_model_service = bool(os.getenv("VISUAL_AI_URL", "").strip())
    needs_rebuild = dialect == "postgresql" and (
        not pgvector_index_exists or indexed_photo_count < photo_count
    )
    model_available = runtime_installed or external_model_service
    index_ready = (
        dialect == "postgresql"
        and pgvector_extension_installed
        and pgvector_index_exists
        and photo_count > 0
        and indexed_photo_count >= photo_count
    )
    if dialect != "postgresql":
        if not os.getenv("DATABASE_URL", "").strip():
            message = f"Database: SQLite (DATABASE_URL is not set). Visual search requires PostgreSQL with pgvector. Catalog photos: {photo_count}."
        else:
            message = f"Database: {dialect}. Visual search requires PostgreSQL with pgvector; check the app service DATABASE_URL."
    elif not postgres_connected:
        message = "Database URL is PostgreSQL, but the app cannot connect to it. Check the Railway PostgreSQL service and DATABASE_URL reference."
    elif not pgvector_extension_installed:
        message = "Database: PostgreSQL connected; pgvector extension is missing. Enable vector on this database, then rebuild the image index."
    elif photo_count == 0:
        message = "Database: PostgreSQL with pgvector connected; no catalog reference photos are saved yet. Add clear product photos first."
    elif needs_rebuild:
        message = f"Database: PostgreSQL with pgvector connected; {indexed_photo_count} of {photo_count} photos are indexed. Rebuild the image index from this app service."
    elif not model_weights_loaded and not external_model_service:
        message = "Catalog photo index is ready. SigLIP weights will load the first time you search a photo."
    else:
        message = "Visual model runtime and catalog photo index are ready."
    if not model_available:
        message += " SigLIP runtime is not installed and no private model service is configured."

    return {
        "status": "ready" if index_ready and model_available else "unavailable",
        "database": dialect,
        "database_url_configured": bool(os.getenv("DATABASE_URL", "").strip()),
        "postgres_connected": postgres_connected,
        "pgvector_extension_installed": pgvector_extension_installed,
        "index_ready": index_ready,
        "runtime_installed": runtime_installed,
        "model_weights_loaded": model_weights_loaded,
        "external_model_service_configured": external_model_service,
        "pgvector_index_exists": pgvector_index_exists,
        "catalog_photos": photo_count,
        "indexed_photos": indexed_photo_count,
        "needs_rebuild": needs_rebuild,
        "message": message,
    }


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
    """Resolve a scanned size to its parent design; the client limits barcode
    editing to that size while product QR/manual search show the full design."""
    variant = _get_variant_by_code(db, variant_code)
    return _product_out(_get_product_by_sku(db, variant.product.sku))


@app.post("/api/inventory/adjust", response_model=schemas.VariantOut)
def adjust_stock(payload: schemas.StockAdjustment, db: Session = Depends(get_db)):
    if payload.operation not in {"set", "delta"}:
        raise HTTPException(400, "Operation must be 'set' or 'delta'.")
    if payload.operation == "set":
        if payload.new_count is None:
            raise HTTPException(400, "Enter the final physical stock count.")
        if payload.new_count < 0:
            raise HTTPException(400, "Count cannot be negative.")
    else:
        if payload.delta is None or payload.delta == 0:
            raise HTTPException(400, "Enter a non-zero quantity to add or remove.")
        if payload.expected_current_stock is None:
            raise HTTPException(400, "Current stock is required for a safe add/remove update.")
    variant = _get_variant_by_code(db, payload.variant_code)
    variant = db.query(models.Variant).filter(models.Variant.id == variant.id).populate_existing().with_for_update().one()
    if payload.expected_current_stock is not None and variant.current_stock != payload.expected_current_stock:
        raise HTTPException(409, f"Stock changed since this update was prepared. Current stock is {variant.current_stock}; refresh and try again.")
    try:
        if payload.operation == "set":
            inventory_service.set_absolute_stock(
                db, variant, payload.new_count, note=payload.note,
                expected_current_stock=payload.expected_current_stock,
            )
        else:
            new_count = variant.current_stock + payload.delta
            if new_count < 0:
                raise HTTPException(400, f"Cannot remove {abs(payload.delta)} units; only {variant.current_stock} are in stock.")
            inventory_service.apply_stock_change(
                db,
                variant,
                payload.delta,
                transaction_type=(models.TransactionType.RESTOCK if payload.delta > 0 else models.TransactionType.MANUAL_ADJUSTMENT),
                reference=payload.note,
                created_by="stock adjustment",
                expected_current_stock=payload.expected_current_stock,
            )
            variant.last_counted_at = datetime.now(timezone.utc)
            db.flush()
    except inventory_service.StaleStockError as exc:
        raise HTTPException(409, f"Stock changed since this update was prepared. Current stock is {exc.current}; refresh and try again.") from exc
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


@app.get("/api/transactions")
def global_transactions(
    q: str | None = None, transaction_type: str | None = None,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    paged: bool = False,
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
    total = query.order_by(None).count() if paged else None
    rows = query.options(contains_eager(T.variant).contains_eager(V.product)) \
        .order_by(T.created_at.desc(), T.id.desc()).offset(offset).limit(limit).all()
    items = [{
        "id": t.id, "variant_code": t.variant.variant_code, "sku": t.variant.product.sku, "size": t.variant.size,
        "product_name": t.variant.product.name, "change_qty": t.change_qty, "transaction_type": t.transaction_type.value,
        "reference": t.reference, "balance_after": t.balance_after, "created_by": t.created_by, "created_at": t.created_at,
    } for t in rows]
    return {"items": items, "total": total, "offset": offset, "limit": limit} if paged else items


# ------------------------------------------------------------------ #
# Order history / batches
# ------------------------------------------------------------------ #
@app.get("/api/orders/history")
def order_history(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), paged: bool = False, db: Session = Depends(get_db)):
    total = db.query(func.count(models.OrderUpload.id)).scalar() if paged else None
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
    return {"items": result, "total": total, "offset": offset, "limit": limit} if paged else result


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
        "current_stock": a.variant.current_stock,
        "created_at": a.created_at, "ordered_qty": None, "required_qty": None,
    } for a in alerts]

    # Pending marketplace orders can create a real purchase shortage even when
    # physical stock is still above the normal low-stock threshold. Show that
    # shortage in Alerts as a virtual alert; the database remains the source of
    # truth for physical-stock alerts.
    shortages = reorder_service.generate_reorder_list(db, save_batch=False, part=q)
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
            "current_stock": item["current_stock"],
            "created_at": datetime.now(timezone.utc),
            "ordered_qty": item["ordered_qty"],
            "required_qty": item["required_qty"],
        })
    result.sort(key=lambda a: (a["alert_type"] != "OUT_OF_STOCK", a["stock_at_alert"], a["variant_code"]))
    if q and q.strip():
        tokens = _smart_search_tokens(q)
        result = [a for a in result if all(
            any(token in str(a.get(key) or "").casefold() for key in ("variant_code", "sku", "size", "product_name", "alert_type"))
            for token in tokens
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
        # Serialize concurrent attempts to dispatch the same batch on databases
        # that support row locks (PostgreSQL). The guarded stock update below
        # remains the final protection against a simultaneous recount/dispatch.
        .with_for_update(of=models.OrderLine)
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
        if qty > variant.current_stock:
            raise HTTPException(
                status_code=409,
                detail=f"{variant.variant_code}: {qty} ordered, but only {variant.current_stock} in stock. Restock/count the size before dispatch.",
            )

    try:
        for line in lines:
            variant = line.variant
            apply = inventory_service.apply_stock_change
            apply(
                db, variant, -line.qty_ordered,
                transaction_type=models.TransactionType.ORDER_DEDUCTION,
                reference=f"order_upload:{order_upload_id}:dispatch",
                created_by="dispatch",
            )
            line.qty_fulfilled = line.qty_ordered
            line.status = models.OrderLineStatus.FULFILLED
            alert_service.check_variant_alert(db, variant)
        db.commit()
    except inventory_service.InsufficientStockError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{exc.variant_code}: stock changed during dispatch; requested {exc.requested}, only {exc.available} remain. The batch was not dispatched.",
        ) from exc
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


@app.get("/api/products/{sku}/qr")
def product_qr_label(sku: str, db: Session = Depends(get_db)):
    """One scannable design QR; scanning opens the product with every size."""
    product = _get_product_by_sku(db, sku)
    png_bytes = barcode_service.generate_product_qr_label(product.sku, product.name)
    return StreamingResponse(
        BytesIO(png_bytes), media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{product.sku}_product_qr.png"'},
    )


@app.get("/api/products/{sku}/barcode")
def product_barcode_label(sku: str, db: Session = Depends(get_db)):
    """One design-level barcode; the PRODUCT: prefix routes scanners to all sizes."""
    product = _get_product_by_sku(db, sku)
    png_bytes = barcode_service.generate_product_barcode_label(product.sku, product.name)
    return StreamingResponse(
        BytesIO(png_bytes), media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{product.sku}_product_barcode.png"'},
    )


@app.get("/api/products/{sku}/label")
def product_combo_label(sku: str, db: Session = Depends(get_db)):
    """One compact product label containing both QR and Code128 symbols."""
    product = _get_product_by_sku(db, sku)
    png_bytes = barcode_service.generate_product_combo_label(product.sku, product.name)
    return StreamingResponse(
        BytesIO(png_bytes), media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{product.sku}_product_label.png"'},
    )


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
