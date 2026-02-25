from __future__ import annotations

from typing import Dict, List


def compute_doc_status(inputs: Dict[str, object]) -> Dict[str, object]:
    reasons: List[str] = []

    latest_job_status = str(inputs.get("latestJobStatus") or "").strip().lower()
    has_jobs = bool(inputs.get("hasJobs", False))
    has_fix_report = bool(inputs.get("hasFixReport", False))
    pending_manual = int(inputs.get("pendingManual", 0) or 0)
    remaining = int(inputs.get("remainingCount", 0) or 0)
    introduced = int(inputs.get("introducedCount", 0) or 0)
    critical_remaining = int(inputs.get("criticalRemaining", 0) or 0)
    preferred_score_status = str(inputs.get("preferredScoreStatus") or "").strip().lower()

    if latest_job_status in {"queued", "running"}:
        reasons.append("job_in_progress")
        return {"status": "in_progress", "reasons": reasons}

    if latest_job_status == "failed":
        reasons.append("job_failed")
        return {"status": "errors", "reasons": reasons}

    if introduced > 0:
        reasons.append("introduced>0")
        return {"status": "errors", "reasons": reasons}

    if critical_remaining > 0:
        reasons.append("critical_remaining>0")
        return {"status": "errors", "reasons": reasons}

    if preferred_score_status == "fail":
        reasons.append("score_fail")
        return {"status": "errors", "reasons": reasons}

    if not has_jobs:
        reasons.append("no_jobs")
        return {"status": "not_run", "reasons": reasons}

    if has_fix_report and remaining == 0 and introduced == 0 and pending_manual == 0:
        reasons.append("delta_clear")
        return {"status": "fixed", "reasons": reasons}

    if pending_manual > 0:
        reasons.append("pending_manual>0")
        return {"status": "needs_review", "reasons": reasons}

    if preferred_score_status == "needs_review":
        reasons.append("score_needs_review")
        return {"status": "needs_review", "reasons": reasons}

    if remaining > 0:
        reasons.append("remaining>0")
        return {"status": "needs_review", "reasons": reasons}

    if not has_fix_report:
        reasons.append("no_fix_report")
        return {"status": "needs_review", "reasons": reasons}

    reasons.append("default_fixed")
    return {"status": "fixed", "reasons": reasons}
