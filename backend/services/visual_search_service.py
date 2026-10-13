"""Production-safe visual product search.

For a small local catalogue this module can use the basic image signature
matcher.  For production it stores one vector per product in PostgreSQL with
the `pgvector` HNSW index, so a scan is an indexed nearest-neighbour lookup,
not a loop through every product image.  The local matcher is deliberately
blocked above a small configurable catalogue size to prevent accidental
CPU/disk exhaustion.
"""
from __future__ import annotations

import hashlib
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from sqlalchemy import text

from services import visual_match_service


MODEL_NAME = os.getenv("VISUAL_EMBEDDING_MODEL", "colour-shape-v1").strip() or "colour-shape-v1"
VECTOR_DIMENSIONS = 216
DEV_MAX_CATALOG = int(os.getenv("VISUAL_SEARCH_DEV_MAX_CATALOG", "500"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("VISUAL_SEARCH_RPM", "30"))
CACHE_TTL_SECONDS = int(os.getenv("VISUAL_SEARCH_CACHE_SECONDS", "60"))


class VisualSearchUnavailable(RuntimeError):
    """The app has intentionally refused an unsafe visual-search request."""


@dataclass(frozen=True)
class SearchHit:
    product_id: int
    similarity: float


_local_requests: dict[str, deque[float]] = {}
_local_rate_lock = Lock()
_redis_client = None
_redis_checked = False


def _database_is_postgres(db) -> bool:
    return db.bind.dialect.name == "postgresql"


def _vector_literal(values: tuple[float, ...]) -> str:
    # Decimal text is understood by pgvector and does not rely on a PostgreSQL
    # driver adapter. The values originate from our own image calculation.
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _signature_vector(content: bytes) -> tuple[float, ...]:
    return visual_match_service.image_signature(content).colors


def _redis():
    """Return an optional Redis client; importing redis stays optional locally."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis  # installed in production from requirements.txt
        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None
    return _redis_client


def allow_scan(client_key: str) -> bool:
    """Distributed fixed-window throttling when Redis exists; safe local fallback."""
    key = f"visual-search:rate:{client_key or 'unknown'}:{int(time.time() // 60)}"
    client = _redis()
    if client:
        try:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, 70)
            return count <= RATE_LIMIT_PER_MINUTE
        except Exception:
            pass
    now = time.monotonic()
    with _local_rate_lock:
        entries = _local_requests.setdefault(client_key or "unknown", deque())
        while entries and now - entries[0] >= 60:
            entries.popleft()
        if len(entries) >= RATE_LIMIT_PER_MINUTE:
            return False
        entries.append(now)
        return True


def _cached_hits(content: bytes) -> list[SearchHit] | None:
    client = _redis()
    if not client:
        return None
    key = f"visual-search:match:{MODEL_NAME}:{hashlib.sha256(content).hexdigest()}"
    try:
        raw = client.get(key)
        if not raw:
            return None
        return [SearchHit(int(product_id), float(score)) for product_id, score in (item.split(":", 1) for item in raw.split(",") if ":" in item)]
    except Exception:
        return None


def _cache_hits(content: bytes, hits: list[SearchHit]) -> None:
    client = _redis()
    if not client:
        return
    key = f"visual-search:match:{MODEL_NAME}:{hashlib.sha256(content).hexdigest()}"
    value = ",".join(f"{hit.product_id}:{hit.similarity:.8f}" for hit in hits)
    try:
        client.setex(key, CACHE_TTL_SECONDS, value)
    except Exception:
        pass


def ensure_postgres_index(engine) -> None:
    """Create pgvector structures once; all statements are idempotent.

    HNSW gives high-recall approximate nearest-neighbour search at 200k+
    products without reading 200k images per request.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS product_visual_embeddings (
                product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
                model_name VARCHAR(100) NOT NULL,
                embedding vector({VECTOR_DIMENSIONS}) NOT NULL,
                indexed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_product_visual_embeddings_model ON product_visual_embeddings (model_name)"))
        connection.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_product_visual_embeddings_hnsw
            ON product_visual_embeddings USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 128)
        """))


def index_product_image(db, product_id: int, content: bytes) -> None:
    """Best-effort upsert after photo save; index creation is an explicit task."""
    if not _database_is_postgres(db):
        return
    vector = _vector_literal(_signature_vector(content))
    db.execute(text("""
        INSERT INTO product_visual_embeddings (product_id, model_name, embedding, indexed_at)
        VALUES (:product_id, :model_name, CAST(:embedding AS vector), CURRENT_TIMESTAMP)
        ON CONFLICT (product_id) DO UPDATE
        SET model_name = EXCLUDED.model_name, embedding = EXCLUDED.embedding, indexed_at = CURRENT_TIMESTAMP
    """), {"product_id": product_id, "model_name": MODEL_NAME, "embedding": vector})


def index_is_available(db) -> bool:
    if not _database_is_postgres(db):
        return False
    try:
        ensure_postgres_index(db.bind)
        return True
    except Exception:
        return False


def _postgres_search(db, content: bytes, limit: int) -> list[SearchHit]:
    vector = _vector_literal(_signature_vector(content))
    rows = db.execute(text("""
        SELECT product_id, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM product_visual_embeddings
        WHERE model_name = :model_name
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """), {"embedding": vector, "model_name": MODEL_NAME, "limit": limit}).all()
    return [SearchHit(int(row.product_id), max(0.0, min(1.0, float(row.similarity)))) for row in rows]


def _development_search(db, content: bytes, static_dir: Path, limit: int) -> list[SearchHit]:
    """Bounded fallback for a developer's SQLite catalogue only."""
    from models import Product
    products = db.query(Product).filter(Product.is_deleted == False, Product.image_path.isnot(None)).all()  # noqa: E712
    if len(products) > DEV_MAX_CATALOG:
        raise VisualSearchUnavailable(
            "Visual search is intentionally disabled above the local development limit. "
            "Use PostgreSQL with pgvector and build the visual index before serving this catalogue."
        )
    scan_signature = visual_match_service.image_signature(content)
    hits = []
    for product in products:
        relative = str(product.image_path or "").removeprefix("/static/")
        candidate = (static_dir / relative).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
            score = visual_match_service.score_signatures(scan_signature, visual_match_service.image_signature(candidate.read_bytes()))
            hits.append(SearchHit(product.id, score))
        except (OSError, ValueError, visual_match_service.InvalidProductImage):
            continue
    return sorted(hits, key=lambda item: item.similarity, reverse=True)[:limit]


def search(db, content: bytes, static_dir: Path, limit: int) -> list[SearchHit]:
    """Search an ANN index when available, with a bounded dev-only fallback."""
    cached = _cached_hits(content)
    if cached:
        return cached[:limit]
    if _database_is_postgres(db):
        try:
            hits = _postgres_search(db, content, limit)
        except Exception as exc:
            raise VisualSearchUnavailable(
                "The PostgreSQL visual index is unavailable. Enable pgvector and run the visual-index rebuild worker."
            ) from exc
        if not hits:
            raise VisualSearchUnavailable(
                "The visual index is empty. Run the visual-index rebuild worker after product images are uploaded."
            )
    else:
        hits = _development_search(db, content, static_dir, limit)
    _cache_hits(content, hits)
    return hits
