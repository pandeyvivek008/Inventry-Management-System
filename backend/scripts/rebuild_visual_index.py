"""Build/rebuild the pgvector product-image index outside web requests.

Run this as a one-off worker after moving a catalog to PostgreSQL:
    cd backend && python scripts/rebuild_visual_index.py
"""
from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal, engine  # noqa: E402
import models  # noqa: E402
from services import visual_search_service  # noqa: E402


def main() -> None:
    if engine.dialect.name != "postgresql":
        raise SystemExit("Visual index rebuild requires PostgreSQL with pgvector; SQLite is local-development only.")
    visual_search_service.ensure_postgres_index(engine)
    static_dir = BASE_DIR / "static"
    image_dir = Path(os.getenv("PRODUCT_IMAGE_DIR") or str(static_dir / "product_images")).resolve()
    db = SessionLocal()
    indexed = skipped = 0
    try:
        products = db.query(models.Product).filter(models.Product.is_deleted == False, models.Product.image_path.isnot(None)).yield_per(250)  # noqa: E712
        for product in products:
            path = (image_dir / Path(str(product.image_path)).name).resolve()
            try:
                path.relative_to(image_dir)
                visual_search_service.index_product_image(db, product.id, path.read_bytes())
                indexed += 1
            except Exception as exc:
                skipped += 1
                print(f"SKIP {product.sku}: {type(exc).__name__}")
                try:
                    visual_search_service.remove_product_embedding(db, product.id)
                    db.commit()
                except Exception:
                    db.rollback()
            if (indexed + skipped) % 250 == 0:
                db.commit()
                print(f"Processed {indexed + skipped}: indexed={indexed}, skipped={skipped}")
        db.commit()
    finally:
        db.close()
    visual_search_service.clear_visual_cache()
    print(f"Visual index complete: indexed={indexed}, skipped={skipped}")
    if skipped:
        raise SystemExit("Some product photos were skipped. Review the messages above before relying on visual search.")


if __name__ == "__main__":
    main()
