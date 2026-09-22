"""Legacy single-action remediation route: retired (410 Gone).

``POST /remediate`` ran one action against the caller's last ``POST /scan``
tree with no credit check and no rate limit, and every request built fresh AI
clients with a fresh per-job cost cap. That made it a free, unthrottled proxy
to the paid inference provider, handing out fixes nobody paid for.

Documents are remediated only through ``POST /pipeline/remediate`` (the Audit
screen), which prices the job before any work and charges only for fixes that
persist into the output file. The route answers 410 instead of disappearing,
so an old client fails with a message that says where the feature went.
``POST /scan`` stays: it only runs the analyzers and never calls a model.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_user_id

router = APIRouter()
logger = logging.getLogger(__name__)

RETIRED_DETAIL = (
    "POST /remediate has been retired. Fix documents on the Audit screen "
    "(POST /pipeline/remediate), where each fix is priced before you pay."
)


@router.post("/remediate")
async def remediate(user_id: str = Depends(require_user_id)) -> None:
    # Auth still runs first, so an anonymous caller gets 401, not a hint.
    logger.info("POST /remediate called; route is retired (410)")
    raise HTTPException(status_code=410, detail=RETIRED_DETAIL)
