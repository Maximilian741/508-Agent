from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from app.api.deps import require_user_id
from app.config import get_settings
from app.evidence.bundle_builder import DEFAULT_BUNDLE_OPTIONS, build_evidence_bundle
from app.persistence.db import get_repo
from app.storage import get_storage, parse_artifact_ref

logger = logging.getLogger(__name__)
router = APIRouter()
REPO = get_repo()
SETTINGS = get_settings()
STORAGE = get_storage()


class EvidenceBundleOptions(BaseModel):
    includeOriginal: bool = False
    includeFixedIfAvailable: bool = True
    includeRebuiltIfAvailable: bool = True
    includeRawArtifacts: bool = False
    includePiiUnsafe: bool = False


def _require_owned_doc(doc_id: Optional[str], user_id: str) -> None:
    """404 unless ``doc_id`` resolves to a document owned by ``user_id``."""
    doc = REPO.get_document(str(doc_id)) if doc_id else None
    if doc is None or (doc.get("ownerId") or None) != user_id:
        raise HTTPException(status_code=404, detail="not_found")


@router.post("/jobs/{job_id}/evidence-bundle")
async def create_evidence_bundle(
    job_id: str,
    options: Optional[EvidenceBundleOptions] = None,
    user_id: str = Depends(require_user_id),
) -> Dict[str, object]:
    job = REPO.get_job(job_id)
    _job_doc = (job or {}).get("docId")
    if _job_doc:
        _require_owned_doc(_job_doc, user_id)
    payload = options.model_dump() if options is not None else dict(DEFAULT_BUNDLE_OPTIONS)
    try:
        bundle_id, bundle_hash, meta = build_evidence_bundle(job_id=job_id, options=payload)
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    except Exception as exc:
        logger.exception("evidence bundle generation failed for job %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="evidence_bundle_failed")

    return {
        "bundleId": bundle_id,
        "jobId": meta.get("jobId"),
        "docId": meta.get("docId"),
        "createdAt": meta.get("createdAt"),
        "bundleHash": bundle_hash,
        "downloadUrl": f"/evidence-bundles/{bundle_id}/download",
    }


@router.get("/documents/{doc_id}/evidence-bundles")
async def list_document_evidence_bundles(
    doc_id: str,
    user_id: str = Depends(require_user_id),
) -> List[Dict[str, object]]:
    _require_owned_doc(doc_id, user_id)
    bundles = REPO.list_evidence_bundles_for_doc(doc_id)
    return [
        {
            "bundleId": item.get("bundleId"),
            "createdAt": item.get("createdAt"),
            "bundleHash": item.get("bundleHash"),
            "options": item.get("options", {}),
            "downloadUrl": f"/evidence-bundles/{item.get('bundleId')}/download",
        }
        for item in bundles
        if str(item.get("status") or "") == "created"
    ]


@router.get("/evidence-bundles/{bundle_id}/download")
async def download_evidence_bundle(
    bundle_id: str,
    user_id: str = Depends(require_user_id),
):
    bundle = REPO.get_evidence_bundle(bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Evidence bundle not found")
    _bundle_doc = bundle.get("docId")
    if _bundle_doc:
        _require_owned_doc(_bundle_doc, user_id)
    if str(bundle.get("status") or "") != "created":
        raise HTTPException(status_code=404, detail="Evidence bundle not available")

    bundle_ref = parse_artifact_ref(bundle.get("bundlePath"))
    if bundle_ref.type == "storage_key":
        if SETTINGS.storage_provider == "s3":
            return RedirectResponse(
                url=STORAGE.get_download_url(bundle_ref.value, expires_seconds=SETTINGS.presign_expires_seconds),
                status_code=302,
            )
        if not STORAGE.exists(bundle_ref.value):
            raise HTTPException(status_code=404, detail="Evidence bundle file not found")
        local_path = STORAGE.resolve_local_path(bundle_ref.value)
        return FileResponse(
            str(local_path),
            media_type="application/zip",
            filename=local_path.name,
            headers={"Content-Disposition": f'attachment; filename=\"{local_path.name}\"'},
        )

    bundle_path = Path(bundle_ref.value)
    if not bundle_path.exists() or not bundle_path.is_file():
        raise HTTPException(status_code=404, detail="Evidence bundle file not found")
    return FileResponse(str(bundle_path), media_type="application/zip", filename=bundle_path.name)
