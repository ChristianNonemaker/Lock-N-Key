"""
Database session factory.

Usage:
    from dk_ncaab.db.session import get_engine, SessionLocal

    with SessionLocal() as session:
        session.execute(...)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, Engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import make_url

from dk_ncaab.config.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    cfg = get_settings().database
    url = make_url(cfg.url)
    connect_args = {}
    if url.drivername.startswith("sqlite"):
        if url.database and url.database not in {":memory:", ""}:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        connect_args["timeout"] = 30

    engine = create_engine(
        cfg.url,
        echo=cfg.echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if url.drivername.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


# Convenience alias
SessionLocal = get_session_factory()
