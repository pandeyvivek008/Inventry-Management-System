"""
Database connection setup.

Default: SQLite file (inventory.db) - zero setup, perfect for a single-PC /
single-warehouse deployment. To move to PostgreSQL for multi-user / networked
access later, just set an environment variable before starting the server:

    export DATABASE_URL="postgresql://user:password@localhost:5432/inventory"

No code changes needed - SQLAlchemy handles both identically.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./inventory.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency - one DB session per request, always closed after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
