from __future__ import annotations

from app.repositories.base import Repository
from app.repositories.repo_sqlalchemy import SqlAlchemyRepository

_REPO: Repository | None = None


def get_repository() -> Repository:
    global _REPO
    if _REPO is None:
        _REPO = SqlAlchemyRepository()
    return _REPO

