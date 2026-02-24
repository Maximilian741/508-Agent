from __future__ import annotations

from app.repositories.base import Repository
from app.persistence.db import get_repo

_REPO: Repository | None = None


def get_repository() -> Repository:
    global _REPO
    if _REPO is None:
        _REPO = get_repo()
    return _REPO
