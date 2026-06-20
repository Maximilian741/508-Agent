"""Standalone accessibility utilities ("tools").

These are small, self-contained helpers that don't require uploading a whole
document — the first is an AI alt-text generator: drop in an image, get a
ready-to-paste description. It directly answers the most common piece of busywork
("no one wants to look at an image to write alt text").

Design:
- Auth required (a signed-in user) so the vision-AI call isn't an open,
  anonymous cost/abuse surface. It costs 0 credits — a free perk that reduces
  work and drives sign-ups.
- The path is rate-limited (see ``security/rate_limit.py`` ``/tools`` prefix).
- Bytes are validated as a real image by magic number, capped in size, and
  never written to disk.
- The response discloses which provider answered and whether real vision AI is
  configured, so the UI can be honest when only the heuristic fallback is
  available (which produces weak, generic text for a context-free image).
"""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from app.ai.semantic_inference import SemanticInferenceClient
from app.api.deps import require_user_id

router = APIRouter(prefix="/tools")

# Generous for a single image, far below the document upload cap. Images are
# read fully into memory (then base64-encoded for the vision API), so keep a lid
# on it.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB
_CHUNK = 64 * 1024


class AltTextToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    altText: str
    provider: str
    confidence: float
    # True when a real vision provider (claude/openai) is configured; False when
    # only the offline heuristic answered (a context-free image yields weak text).
    aiConfigured: bool


def _detect_image_mime(head: bytes) -> Optional[str]:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    return None


@router.post("/alt-text", response_model=AltTextToolResponse)
async def generate_alt_text(
    file: UploadFile,
    user_id: str = Depends(require_user_id),
) -> AltTextToolResponse:
    """Generate alternative text for a single uploaded image. Free (0 credits)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"image_too_large: limit={_MAX_IMAGE_BYTES} bytes",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="empty_upload")

    mime = _detect_image_mime(data[:16])
    if mime is None:
        raise HTTPException(
            status_code=400,
            detail="unsupported_image: expected PNG, JPEG, GIF, WebP, or BMP",
        )

    b64 = base64.b64encode(data).decode("ascii")
    client = SemanticInferenceClient()
    result = client.suggest_alt_text(
        label="Uploaded image",
        location="image",
        image_b64=b64,
        image_mime=mime,
    )
    text = (result.text or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="alt_text_generation_failed")

    return AltTextToolResponse(
        altText=text,
        provider=result.provider,
        confidence=round(float(result.confidence), 3),
        aiConfigured=(client.provider_name != "heuristic"),
    )


__all__ = ["router"]
