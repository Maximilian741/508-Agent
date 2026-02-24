"""Policy pack API routes."""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException

from app.persistence.db import get_repo

router = APIRouter(prefix="/api")
REPO = get_repo()


def _policy_response(policy: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": policy.get("id"),
        "name": policy.get("name"),
        "description": policy.get("description"),
        "version": policy.get("version"),
        "targets": policy.get("targets", []),
        "updated_at": policy.get("updated_at"),
    }


@router.get("/policies")
async def list_policies() -> List[Dict[str, object]]:
    policies = REPO.list_policy_packs()
    return [_policy_response(policy) for policy in policies]


@router.get("/policies/{policy_id}")
async def get_policy(policy_id: str) -> Dict[str, object]:
    policy = REPO.get_policy_pack(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy pack not found")
    return _policy_response(policy)

