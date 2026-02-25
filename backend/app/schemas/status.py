from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel


class JobRef(BaseModel):
    jobId: str
    status: str
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None


class PolicyRef(BaseModel):
    policyPackId: Optional[str] = None
    name: str
    version: int


class ScorePassSummary(BaseModel):
    scoreTotal: int
    status: str
    createdAt: Optional[str] = None


class ScoreSummary(BaseModel):
    baseline: Optional[ScorePassSummary] = None
    postFix: Optional[ScorePassSummary] = None
    postManual: Optional[ScorePassSummary] = None


class SeverityCounts(BaseModel):
    total: int
    bySeverity: Dict[str, int]


class DeltaCounts(BaseModel):
    fixed: int
    remaining: int
    introduced: int


class ManualReviewCounts(BaseModel):
    pending: int
    approved: int
    rejected: int


class DocumentStatusCounts(BaseModel):
    before: Optional[SeverityCounts] = None
    after: Optional[SeverityCounts] = None
    delta: Optional[DeltaCounts] = None
    manualReview: ManualReviewCounts


class DocStatusSummary(BaseModel):
    docId: str
    filename: str
    docType: str
    createdAt: Optional[str] = None
    latestJob: Optional[JobRef] = None
    policy: Optional[PolicyRef] = None
    score: ScoreSummary
    counts: DocumentStatusCounts
    status: str
    reasons: List[str]


class DocStatusListResponse(BaseModel):
    items: List[DocStatusSummary]
    total: Optional[int] = None
    limit: int
    offset: int
