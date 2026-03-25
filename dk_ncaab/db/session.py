"""
Database session factory.

Usage:
    from dk_ncaab.db.session import get_engine, SessionLocal

    with SessionLocal() as session:
        session.execute(...)
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session

from dk_ncaab.config.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    cfg = get_settings().database
    return create_engine(cfg.url, echo=cfg.echo, pool_pre_ping=True)


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


# Convenience alias
SessionLocal = get_session_factory()
