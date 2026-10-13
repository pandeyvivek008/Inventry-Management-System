"""CLIP-backed product-photo indexing with fail-closed match decisions."""
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


MODEL_NAME = f"{visual_match_service.MODEL_ARCH}:{visual_match_service.MODEL_PRETRAINED}:v1"
VECTOR_DIMENSIONS = visual_match_service.VECTOR_DIMENSIONS
TABLE_NAME = "product_visual_embeddings_clip_v1"
DEV_MAX_CATALOG = int(os.getenv("VISUAL_SEARCH_DEV_MAX_CATALOG", "500"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("VISUAL_SEARCH_RPM", "30"))
CACHE_TTL_SECONDS = int(os.getenv("VISUAL_SEARCH_CACHE_SECONDS", "60"))


def _setting(name: str, default: float, lower: float, upper: float) -> float:
    try:
        return max(lower, min(upper, float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


# These are conservative starting values, not calibrated probabilities. Tune
# them with labeled positive and hard-negative garment photos from the catalog.
MIN_SIMILARITY = _setting("VISUAL_MATCH_MIN_SCORE", 0.82, -1.0, 1.0)
MIN_MARGIN = _setting("VISUAL_MATCH_MIN_MARGIN", 0.06, 0.0, 1.0)


class VisualSearchUnavailable(RuntimeError):
    """The app intentionally refused to guess while visual search is unsafe."""


@dataclass(frozen=True)
class SearchHit:
    product_id: int
    similarity: float


@dataclass(frozen=True)
class SearchResult:
    status: str
    matches: tuple[SearchHit, ...]
    message: str


_local_requests: dict[str, deque[float]] = {}
_local_rate_lock = Lock()
_local_embedding_lock = Lock()
_local_embeddings: dict[int, tuple[tuple[str, int, int], tuple[float, ...]]] = {}
_redis_client = None
_redis_checked = False


def _database_is_postgres(db) -> bool:
    return db.bind.dialect.name == "postgresql"


def _vector_literal(values: tuple[float, ...]) -> str:
    if len(values) != VECTOR_DIMENSIONS:
        raise VisualSearchUnavailable(f"Visual model returned {len(values)} dimensions; expected {VECTOR_DIMENSIONS}.")
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None
    return _redis_client


def allow_scan(client_key: str) -> bool:
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
    try:
        client.setex(key, CACHE_TTL_SECONDS, ",".join(f"{hit.product_id}:{hit.similarity:.8f}" for hit in hits))
    except Exception:
        pass


def clear_visual_cache() -> None:
    """Clear cached rankings after an image is replaced or re-indexed."""
    client = _redis()
    if not client:
        return
    try:
        keys = client.scan_iter(match=f"visual-search:match:{MODEL_NAME}:*", count=200)
        for key in keys:
            client.delete(key)
    except Exception:
        pass


def ensure_postgres_index(engine) -> None:
    """Create the versioned pgvector table/index; old color vectors stay untouched."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
                model_name VARCHAR(160) NOT NULL,
                embedding vector({VECTOR_DIMENSIONS}) NOT NULL,
                indexed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{TABLE_NAME}_model ON {TABLE_NAME} (model_name)"))
        connection.execute(text(f"""
            CREATE INDEX IF NOT EXISTS ix_{TABLE_NAME}_hnsw
            ON {TABLE_NAME} USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 128)
        """))


def index_product_image(db, product_id: int, content: bytes) -> None:
    """Upsert CLIP embedding after image save; callers keep photo save independent."""
    if not _database_is_postgres(db):
        return
    embedding = _vector_literal(visual_match_service.image_embedding(content))
    db.execute(text(f"""
        INSERT INTO {TABLE_NAME} (product_id, model_name, embedding, indexed_at)
        VALUES (:product_id, :model_name, CAST(:embedding AS vector), CURRENT_TIMESTAMP)
        ON CONFLICT (product_id) DO UPDATE
        SET model_name = EXCLUDED.model_name, embedding = EXCLUDED.embedding, indexed_at = CURRENT_TIMESTAMP
    """), {"product_id": product_id, "model_name": MODEL_NAME, "embedding": embedding})
def remove_product_embedding(db, product_id: int) -> None:
    """Remove any old vector after a replacement image could not be indexed."""
    if _database_is_postgres(db):
        db.execute(text(f"DELETE FROM {TABLE_NAME} WHERE product_id = :product_id"), {"product_id": product_id})
    with _local_embedding_lock:
        _local_embeddings.pop(product_id, None)
    clear_visual_cache()


def _postgres_search(db, embedding: tuple[float, ...], limit: int) -> list[SearchHit]:
    vector = _vector_literal(embedding)
    rows = db.execute(text(f"""
        SELECT e.product_id, 1 - (e.embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM {TABLE_NAME} AS e
        JOIN products AS p ON p.id = e.product_id
        WHERE e.model_name = :model_name AND p.is_deleted = FALSE AND p.image_path IS NOT NULL
        ORDER BY e.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """), {"embedding": vector, "model_name": MODEL_NAME, "limit": limit}).all()
    return [SearchHit(int(row.product_id), float(row.similarity)) for row in rows]


def _development_search(db, embedding: tuple[float, ...], image_dir: Path, limit: int) -> list[SearchHit]:
    from models import Product
    products = db.query(Product).filter(Product.is_deleted == False, Product.image_path.isnot(None)).all()  # noqa: E712
    if len(products) > DEV_MAX_CATALOG:
        raise VisualSearchUnavailable(
            "Visual search is disabled above the local development limit. Use PostgreSQL with pgvector and build its CLIP index."
        )
    hits: list[SearchHit] = []
    active_ids = {product.id for product in products}
    with _local_embedding_lock:
        for stale_id in set(_local_embeddings) - active_ids:
            _local_embeddings.pop(stale_id, None)
    for product in products:
        candidate = (image_dir / Path(str(product.image_path or "")).name).resolve()
        try:
            candidate.relative_to(image_dir.resolve())
            stat = candidate.stat()
            signature = (str(candidate), stat.st_mtime_ns, stat.st_size)
            cached = _local_embeddings.get(product.id)
            if not cached or cached[0] != signature:
                ref_embedding = visual_match_service.image_embedding(candidate.read_bytes())
                with _local_embedding_lock:
                    _local_embeddings[product.id] = (signature, ref_embedding)
            else:
                ref_embedding = cached[1]
            similarity = sum(left * right for left, right in zip(embedding, ref_embedding))
            hits.append(SearchHit(product.id, similarity))
        except (OSError, ValueError, visual_match_service.InvalidProductImage):
            continue
    return sorted(hits, key=lambda item: item.similarity, reverse=True)[:limit]


def _decide(hits: list[SearchHit]) -> SearchResult:
    if not hits:
        return SearchResult("no_match", (), "No usable product photos are available in the catalog.")
    top = hits[0]
    if top.similarity < MIN_SIMILARITY:
        return SearchResult("no_match", (), "No catalog design was similar enough. Try a clear, front-facing photo.")
    margin = top.similarity - hits[1].similarity if len(hits) > 1 else 1.0
    if margin < MIN_MARGIN:
        return SearchResult("ambiguous", tuple(hits[:3]), "Several designs look similar. Review the photos before choosing a product.")
    return SearchResult("matched", (top,), "A strong visual match was found. Sizes and stock are read from the database.")


def search(db, content: bytes, image_dir: Path, limit: int) -> SearchResult:
    """Rank genuine product photos and return only a verified/uncertain/no-match state."""
    cached = _cached_hits(content)
    if cached is None:
        embedding = visual_match_service.image_embedding(content)
        if _database_is_postgres(db):
            try:
                hits = _postgres_search(db, embedding, max(limit, 5))
            except Exception as exc:
                raise VisualSearchUnavailable(
                    "PostgreSQL CLIP index unavailable. Build product_visual_embeddings_clip_v1 and retry."
                ) from exc
        else:
            hits = _development_search(db, embedding, image_dir, max(limit, 5))
        _cache_hits(content, hits)
    else:
        hits = cached[:max(limit, 5)]
    return _decide(hits)
