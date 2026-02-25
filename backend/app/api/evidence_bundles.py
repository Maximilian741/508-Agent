from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from app.config import get_settings
from app.evidence.bundle_builder import DEFAULT_BUNDLE_OPTIONS, build_evidence_bundle
from app.persistence.db import get_repo
from app.storage import get_storage, parse_artifact_ref

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


@router.post("/jobs/{job_id}/evidence-bundle")
async def create_evidence_bundle(job_id: str, options: Optional[EvidenceBundleOptions] = None) -> Dict[str, object]:
    payload = options.model_dump() if options is not None else dict(DEFAULT_BUNDLE_OPTIONS)
    try:
        bundle_id, bundle_hash, meta = build_evidence_bundle(job_id=job_id, options=payload)
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate evidence bundle: {exc}")

    return {
        "bundleId": bundle_id,
        "jobId": meta.get("jobId"),
        "docId": meta.get("docId"),
        "createdAt": meta.get("createdAt"),
        "bundleHash": bundle_hash,
        "downloadUrl": f"/evidence-bundles/{bundle_id}/download",
    }


@router.get("/documents/{doc_id}/evidence-bundles")
async def list_document_evidence_bundles(doc_id: str) -> List[Dict[str, object]]:
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
async def download_evidence_bundle(bundle_id: str):
    bundle = REPO.get_evidence_bundle(bundle_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Evidence bundle not found")
    if str(bundle.get("status") or "") != "created":
        raise HTTPException(status_code=404, detail="Evidence bundle not available")

    bundle_ref = parse_artifact_ref(bundle.get("bundlePath"))
    if bundle_ref.type == "storage_key":
        if SETTINGS.storage_provider == "s3":
            return RedirectResponse(url=STORAGE.get_download_url(bundle_ref.value, expires_seconds=3600), status_code=302)
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
