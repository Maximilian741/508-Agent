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
- The response discloses whether real vision AI is configured. Without it the
  answer is an EMPTY ``altText`` plus a plain ``message`` — a picture with no
  caption cannot be described by rules, and a placeholder to paste would be
  worse than nothing.
"""

from __future__ import annotations

import base64
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from app.ai.offline_rules import vet_alt_text
from app.ai.semantic_inference import SemanticInferenceClient
from app.api.deps import require_user_id

router = APIRouter(prefix="/tools")

# Generous for a single image, far below the document upload cap. Images are
# read fully into memory (then base64-encoded for the vision API), so keep a lid
# on it.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB
_CHUNK = 64 * 1024

# Per-USER hourly cap on the free vision-AI alt-text tool. The per-request cost
# cap (SemanticInferenceClient.max_cost_usd) bounds a single call, and the IP
# rate limiter bounds bursts, but neither bounds a single authenticated user
# making many calls over time (the IP limit is per-IP, not per-account). This
# caps cumulative free-AI spend per user. In-memory / single-instance (same
# caveat as security/rate_limit.py — swap for Redis under horizontal scale).
_ALT_TEXT_WINDOW_S = 3600.0
_ALT_TEXT_MAX_PER_USER = 40
_alt_text_calls: Dict[str, Deque[float]] = {}
_alt_text_lock = threading.Lock()


def _alt_text_rate_ok(user_id: str) -> bool:
    now = time.monotonic()
    cutoff = now - _ALT_TEXT_WINDOW_S
    with _alt_text_lock:
        dq = _alt_text_calls.setdefault(user_id, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _ALT_TEXT_MAX_PER_USER:
            return False
        dq.append(now)
        return True


class AltTextToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty when no honest description could be written; ``message`` says why
    # in words a customer can act on. Never a placeholder to copy.
    altText: str
    provider: str
    confidence: float
    # True when a real vision provider (claude/openai) is configured.
    aiConfigured: bool
    message: Optional[str] = None


# Customer-facing: no provider names, no settings, nothing only an operator
# could act on.
_NO_VISION_MESSAGE = (
    "Automatic image descriptions aren't available right now, so we can't describe this "
    "picture for you. Write one sentence saying what it shows, as you would describe it "
    "to someone over the phone."
)
_NO_ANSWER_MESSAGE = (
    "We couldn't write a useful description of this picture. Please try again in a minute, "
    "or write one sentence saying what it shows."
)


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


class AltTextAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    # Customer words for why not; never a provider or setting name.
    message: Optional[str] = None


@router.get("/alt-text/availability", response_model=AltTextAvailability)
async def alt_text_availability() -> AltTextAvailability:
    """Whether the image tool can describe pictures on this deployment.

    Lets the page say so BEFORE someone signs in and uploads, instead of
    promising "a ready-to-paste description in seconds" and then answering
    every image with "not available". No auth: it discloses one boolean (no
    provider name, no configuration), and makes no provider call.
    """
    from app.ai.semantic_inference import build_default_provider

    try:
        available = build_default_provider().name != "heuristic"
    except Exception:
        available = False
    return AltTextAvailability(available=available, message=None if available else _NO_VISION_MESSAGE)


@router.post("/alt-text", response_model=AltTextToolResponse)
async def generate_alt_text(
    file: UploadFile,
    user_id: str = Depends(require_user_id),
) -> AltTextToolResponse:
    """Generate alternative text for a single uploaded image. Free (0 credits)."""
    if not _alt_text_rate_ok(user_id):
        raise HTTPException(
            status_code=429,
            detail=(
                f"rate_limited: the free alt-text tool is capped at "
                f"{_ALT_TEXT_MAX_PER_USER} images/hour per account. Try again later."
            ),
        )
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

    client = SemanticInferenceClient()
    if client.provider_name == "heuristic":
        # Nothing on this deployment can LOOK at a picture, and a lone image
        # has no caption to borrow words from. This used to answer 200 with
        # "Uploaded image shown in image." — a string our own analyzer calls a
        # placeholder — under a "Suggested alt text" heading with a Copy
        # button. Say so instead, and never hand back text to paste.
        return AltTextToolResponse(
            altText="",
            provider=client.provider_name,
            confidence=0.0,
            aiConfigured=False,
            message=_NO_VISION_MESSAGE,
        )

    b64 = base64.b64encode(data).decode("ascii")
    result = client.suggest_alt_text(
        label="image",
        location="image",
        image_b64=b64,
        image_mime=mime,
    )
    text = (result.text or "").strip()
    # The vision call can fall back to the offline rules (provider error, or
    # the per-request budget), and even a real model can answer with a
    # placeholder or an address. Same gate the document executor uses.
    if not text or result.provider == "heuristic" or vet_alt_text(text):
        return AltTextToolResponse(
            altText="",
            provider=result.provider,
            confidence=0.0,
            aiConfigured=True,
            message=_NO_ANSWER_MESSAGE,
        )

    return AltTextToolResponse(
        altText=text,
        provider=result.provider,
        confidence=round(float(result.confidence), 3),
        aiConfigured=True,
    )


__all__ = ["router"]
