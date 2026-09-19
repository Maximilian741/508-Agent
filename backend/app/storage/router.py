from __future__ import annotations

import mimetypes
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings
from app.storage import get_storage
from app.storage.base import sanitize_storage_key
from app.storage.local import LocalStorage

router = APIRouter()


@router.get("/storage/{key:path}")
async def download_storage_object(key: str) -> FileResponse:
    settings = get_settings()
    storage = get_storage()
    # The kill-switch comes FIRST: a route production has switched off must not
    # run any of its own logic, least of all logic that can raise.
    if settings.environment == "production" and not settings.enable_dev_storage_endpoint:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        clean = sanitize_storage_key(key)
    except ValueError:
        # A traversal or empty key is a key that names nothing — a clean 404,
        # not an unhandled 500 an anonymous caller can trigger at will.
        raise HTTPException(status_code=404, detail="Stored object not found")
    if settings.storage_provider != "local" or not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=404, detail="Storage endpoint is only available for local provider")
    path = storage.resolve_local_path(clean)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored object not found")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(str(path), filename=path.name, media_type=media_type)
