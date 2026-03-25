"""
Shared FastAPI dependencies — database session, model loader.
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy.orm import Session

from dk_ncaab.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a read-only DB session, closed automatically."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
