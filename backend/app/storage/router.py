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
    clean = sanitize_storage_key(key)
    if settings.storage_provider != "local" or not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=404, detail="Storage endpoint is only available for local provider")
    path = storage.resolve_local_path(clean)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored object not found")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(str(path), filename=path.name, media_type=media_type)
