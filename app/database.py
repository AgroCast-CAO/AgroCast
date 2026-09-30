"""Database engine + session dependency.

Default: SQLite file (zero setup). For PostGIS set
    AGROCAST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/agrocast
Parcels store plain latitude/longitude (+ optional GeoJSON text), so the same models
work on both backends. See README for upgrading to a native PostGIS geometry column.
"""
from __future__ import annotations

from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, echo=False)


def init_db() -> None:
    """Create all tables (idempotent)."""
    from app.models import tables  # noqa: F401  (import registers the tables)
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with Session(engine) as session:
        yield session
