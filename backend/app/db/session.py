from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


def _default_sqlite_url() -> str:
    return "sqlite:///./.runtime/508_agent.db"


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", _default_sqlite_url())


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_database_url()
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        directory = os.path.dirname(raw)
        if directory:
            os.makedirs(directory, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


ENGINE = build_engine()
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, future=True)
