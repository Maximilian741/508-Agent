from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import get_settings
from app.persistence.db import get_repo
from app.storage import encode_storage_key, get_storage, parse_artifact_ref

BASE_DIR = Path(__file__).resolve().parents[2]
RUNTIME_DIR = BASE_DIR / ".runtime"
BUNDLES_DIR = RUNTIME_DIR / "bundles"

DEFAULT_BUNDLE_OPTIONS: Dict[str, bool] = {
    "includeOriginal": False,
    "includeFixedIfAvailable": True,
    "includeRebuiltIfAvailable": True,
    "includeRawArtifacts": False,
    "includePiiUnsafe": False,
}
SETTINGS = get_settings()
STORAGE = get_storage()

_SEVERITY_ORDER = {
    "critical": 0,
    "error": 1,
    "serious": 1,
    "warning": 2,
    "moderate": 2,
    "minor": 3,
    "info": 3,
}


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _sanitize_filename(value: str) -> str:
    name = Path(value or "file").name
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    return safe or "file"


def _normalize_options(raw: Optional[Dict[str, object]]) -> Dict[str, bool]:
    options = dict(DEFAULT_BUNDLE_OPTIONS)
    if not raw:
        return options
    for key in DEFAULT_BUNDLE_OPTIONS.keys():
        if key in raw:
            options[key] = bool(raw.get(key))
    options["includePiiUnsafe"] = False
    return options


def _sha256_bytes(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_snippet(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True)
        except Exception:
            text = str(value)
    compact = " ".join(text.split())
    return compact[:200]


def _issue_signature(issue: Dict[str, object]) -> str:
    issue_id = issue.get("id")
    if isinstance(issue_id, str) and issue_id.strip():
        return issue_id.strip()
    basis = "|".join(
        [
            str(issue.get("ruleId") or "").strip().lower(),
            str(issue.get("severity") or "").strip().lower(),
            str(issue.get("locationHint") or "").strip().lower(),
            _evidence_snippet(issue.get("evidence")),
        ]
    )
    return f"sig-{_sha256_bytes(basis.encode('utf-8'))[:16]}"


def _count_by_severity(issues: List[Dict[str, object]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for issue in issues:
        severity = str(issue.get("severity") or "info").lower()
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _policy_override_for_rule(policy_json: Dict[str, object], rule_id: str, doc_type: str) -> Dict[str, object]:
    rules = policy_json.get("rules", {}) if isinstance(policy_json.get("rules"), dict) else {}
    overrides = rules.get("overrides", {}) if isinstance(rules.get("overrides"), dict) else {}
    normalized_rule = str(rule_id or "").strip()
    normalized_doc = str(doc_type or "").strip().upper()
    candidates = [
        normalized_rule,
        normalized_rule.upper(),
        f"{normalized_doc}.{normalized_rule}",
        f"{normalized_doc}.{normalized_rule.upper()}",
    ]
    for key in candidates:
        if key in overrides and isinstance(overrides.get(key), dict):
            return overrides.get(key)  # type: ignore[return-value]
    return {}


def _apply_policy_to_issue(issue: Dict[str, object], policy_json: Dict[str, object], doc_type: str) -> Dict[str, object]:
    out = dict(issue)
    rule_id = str(issue.get("ruleId") or "")
    override = _policy_override_for_rule(policy_json, rule_id, doc_type)
    original = str(issue.get("severity") or "")
    override_severity = override.get("severity")
    if isinstance(override_severity, str) and override_severity.strip():
        out["severity"] = override_severity.strip().lower()
        if original and original.lower() != str(out["severity"]):
            out["originalSeverity"] = original
    return out


def _compute_delta(before: List[Dict[str, object]], after: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    before_map = {_issue_signature(issue): issue for issue in before}
    after_map = {_issue_signature(issue): issue for issue in after}

    fixed = []
    remaining = []
    introduced = []

    for signature in sorted(before_map.keys()):
        issue = before_map[signature]
        if signature in after_map:
            remaining.append(
                {
                    "signature": signature,
                    "ruleId": issue.get("ruleId"),
                    "severity": issue.get("severity"),
                    "locationHint": issue.get("locationHint"),
                }
            )
        else:
            fixed.append(
                {
                    "signature": signature,
                    "ruleId": issue.get("ruleId"),
                    "severity": issue.get("severity"),
                    "locationHint": issue.get("locationHint"),
                }
            )

    for signature in sorted(after_map.keys()):
        if signature not in before_map:
            issue = after_map[signature]
            introduced.append(
                {
                    "signature": signature,
                    "ruleId": issue.get("ruleId"),
                    "severity": issue.get("severity"),
                    "locationHint": issue.get("locationHint"),
                }
            )

    return {"fixed": fixed, "remaining": remaining, "introduced": introduced}


def _manual_summary(items: List[Dict[str, object]]) -> Dict[str, int]:
    out = {"pending": 0, "approved": 0, "rejected": 0, "other": 0}
    for item in items:
        status = str(item.get("status") or "pending").strip().lower()
        if status in out:
            out[status] += 1
        else:
            out["other"] += 1
    return out


def _policy_fallback() -> Dict[str, object]:
    repo = get_repo()
    pack = repo.get_policy_pack("policy-508-wcag20-aa")
    if pack is None:
        packs = repo.list_policy_packs()
        if packs:
            pack = packs[0]
    if pack is None:
        return {
            "policyPackId": None,
            "policyName": "fallback-default",
            "policyVersion": 1,
            "policyJson": {},
        }
    return {
        "policyPackId": pack.get("id"),
        "policyName": pack.get("name"),
        "policyVersion": pack.get("version"),
        "policyJson": pack.get("policy_json") if isinstance(pack.get("policy_json"), dict) else {},
    }


def _collect_inputs(job_id: str, options: Dict[str, bool]) -> Dict[str, object]:
    repo = get_repo()
    job = repo.get_job(job_id)
    if not job:
        raise ValueError("Job not found")
    doc_id = str(job.get("docId") or "")
    if not doc_id:
        raise ValueError("Job has no document context")
    doc = repo.get_document(doc_id)
    if not doc:
        raise ValueError("Document not found")

    notes: List[str] = []
    policy_snapshot = repo.get_job_policy_snapshot(job_id)
    if not policy_snapshot:
        policy_snapshot = _policy_fallback()
        notes.append("WARNING: job policy snapshot missing; used fallback policy pack.")

    scores = repo.get_job_scores(job_id)
    if not scores:
        notes.append("Score unavailable: no persisted job scoring rows found.")

    before_issues = repo.get_issues(doc_id, "before")
    after_issues = repo.get_issues(doc_id, "after")
    if not before_issues:
        notes.append("No before-pass issues found.")
    if not after_issues:
        notes.append("No after-pass issues found.")

    fix_report = repo.get_fix_report(doc_id)
    if not fix_report:
        notes.append("Fix report missing (expected until apply-fixes completes).")

    manual_review = repo.list_manual_review_items_for_doc(doc_id, include_resolved=True)
    if not manual_review:
        notes.append("No manual review items for this document.")

    return {
        "job": job,
        "doc": doc,
        "docId": doc_id,
        "jobId": job_id,
        "policySnapshot": policy_snapshot,
        "scores": scores,
        "beforeIssues": before_issues,
        "afterIssues": after_issues,
        "fixReport": fix_report,
        "manualReview": manual_review,
        "notes": notes,
        "options": options,
    }


def _git_commit() -> str:
    env_value = os.getenv("GIT_COMMIT", "").strip()
    if env_value:
        return env_value
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), stderr=subprocess.DEVNULL, timeout=1.0)
        commit = out.decode("utf-8", errors="ignore").strip()
        return commit or "unknown"
    except Exception:
        return "unknown"


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ruleId",
        "severity",
        "status",
        "pass",
        "locationHint",
        "evidenceSnippet",
        "recommendation",
        "createdAt",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _pdf_escape(value: str) -> str:
    safe = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe.encode("latin-1", errors="ignore").decode("latin-1")


def _simple_pdf_bytes(lines: List[str]) -> bytes:
    y = 760
    content_parts = ["BT", "/F1 16 Tf", "72 780 Td", "(508-Agent Evidence Summary) Tj", "ET"]
    content_parts.append("BT")
    content_parts.append("/F1 11 Tf")
    content_parts.append(f"72 {y} Td")
    first = True
    for line in lines[:40]:
        escaped = _pdf_escape(line)
        if first:
            content_parts.append(f"({escaped}) Tj")
            first = False
        else:
            content_parts.append("0 -14 Td")
            content_parts.append(f"({escaped}) Tj")
    content_parts.append("ET")
    content_stream = "\n".join(content_parts).encode("latin-1", errors="ignore")

    objects: List[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>")
    objects.append(b"<< /Length " + str(len(content_stream)).encode("ascii") + b" >>\nstream\n" + content_stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    current = len(chunks[0])
    for idx, obj in enumerate(objects, start=1):
        header = f"{idx} 0 obj\n".encode("ascii")
        footer = b"\nendobj\n"
        block = header + obj + footer
        offsets.append(current)
        chunks.append(block)
        current += len(block)

    xref_offset = current
    xref = [f"xref\n0 {len(objects) + 1}\n".encode("ascii")]
    xref.append(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        xref.append(f"{off:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return b"".join(chunks + xref + [trailer])


def _write_summary_pdf(path: Path, report_json: Dict[str, object], manifest: Dict[str, object], delta_json: Dict[str, object], manual_json: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scores = report_json.get("scoring") if isinstance(report_json.get("scoring"), list) else []
    score_bits = []
    for entry in scores:
        if not isinstance(entry, dict):
            continue
        score_bits.append(f"{entry.get('passType')}: {entry.get('scoreTotal')} ({entry.get('status')})")
    if not score_bits:
        score_bits = ["score unavailable"]
    lines = [
        f"Document: {report_json.get('doc', {}).get('filename', 'unknown')} ({manifest.get('docId')})",
        f"Job: {manifest.get('jobId')}",
        f"Policy: {manifest.get('policy', {}).get('name', 'unknown')} v{manifest.get('policy', {}).get('version', 'unknown')}",
        f"Created: {manifest.get('createdAt')}",
        f"Scores: {' | '.join(score_bits)}",
        f"Before issues: {manifest.get('counts', {}).get('before', {}).get('total', 0)}",
        f"After issues: {manifest.get('counts', {}).get('after', {}).get('total', 0)}",
        f"Delta fixed/remaining/introduced: {len(delta_json.get('fixed', []))}/{len(delta_json.get('remaining', []))}/{len(delta_json.get('introduced', []))}",
        f"Manual review pending/approved/rejected: {manual_json.get('summary', {}).get('pending', 0)}/{manual_json.get('summary', {}).get('approved', 0)}/{manual_json.get('summary', {}).get('rejected', 0)}",
    ]
    remaining = delta_json.get("remaining", []) if isinstance(delta_json.get("remaining"), list) else []
    if remaining:
        lines.append("Top remaining issues:")
        for item in remaining[:10]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('ruleId', 'unknown')}: {item.get('locationHint', '')}"
            )
    lines.append("Generated by 508-Agent Evidence Bundle v1")
    path.write_bytes(_simple_pdf_bytes(lines))


def _build_checks_rows(
    before_issues: List[Dict[str, object]],
    after_issues: List[Dict[str, object]],
    manual_items: List[Dict[str, object]],
) -> List[Dict[str, str]]:
    before_map = {_issue_signature(issue): issue for issue in before_issues}
    after_map = {_issue_signature(issue): issue for issue in after_issues}
    rows: List[Dict[str, str]] = []

    for signature, issue in before_map.items():
        status = "open" if signature in after_map else "fixed"
        rows.append(
            {
                "ruleId": str(issue.get("ruleId") or ""),
                "severity": str(issue.get("severity") or ""),
                "status": status,
                "pass": "before",
                "locationHint": str(issue.get("locationHint") or ""),
                "evidenceSnippet": _evidence_snippet(issue.get("evidence")),
                "recommendation": str(issue.get("recommendation") or ""),
                "createdAt": str(issue.get("createdAt") or ""),
            }
        )

    for signature, issue in after_map.items():
        if signature in before_map:
            continue
        rows.append(
            {
                "ruleId": str(issue.get("ruleId") or ""),
                "severity": str(issue.get("severity") or ""),
                "status": "introduced",
                "pass": "after",
                "locationHint": str(issue.get("locationHint") or ""),
                "evidenceSnippet": _evidence_snippet(issue.get("evidence")),
                "recommendation": str(issue.get("recommendation") or ""),
                "createdAt": str(issue.get("createdAt") or ""),
            }
        )

    for item in manual_items:
        decision = str(item.get("status") or "pending").strip().lower()
        mapped = {
            "approved": "approved",
            "rejected": "rejected",
            "pending": "pending-review",
            "accepted-risk": "accepted-risk",
            "waived": "waived",
        }.get(decision, "pending-review")
        rows.append(
            {
                "ruleId": str(item.get("issueId") or item.get("ruleId") or "manual_review"),
                "severity": str(item.get("severity") or ""),
                "status": mapped,
                "pass": "delta",
                "locationHint": str(item.get("locationHint") or item.get("targetNodeId") or ""),
                "evidenceSnippet": _evidence_snippet(item.get("evidence") or item.get("reason")),
                "recommendation": str(item.get("instructions") or item.get("suggestedFix") or ""),
                "createdAt": str(item.get("createdAt") or ""),
            }
        )

    rows.sort(
        key=lambda row: (
            _SEVERITY_ORDER.get(str(row.get("severity") or "").lower(), 99),
            str(row.get("ruleId") or ""),
            str(row.get("locationHint") or ""),
            str(row.get("status") or ""),
        )
    )
    return rows


def _safe_relative_path(path: Path, base: Path) -> str:
    rel = path.relative_to(base).as_posix()
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise ValueError(f"Unsafe relative path: {rel}")
    return rel


def _make_zip(source_dir: Path, out_zip: Path) -> None:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            arcname = _safe_relative_path(path, source_dir)
            info = zipfile.ZipInfo(filename=arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            data = path.read_bytes()
            zf.writestr(info, data)


def _copy_artifact_if_present(src_path: object, dest_dir: Path) -> Optional[str]:
    if not isinstance(src_path, str) or not src_path.strip():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    ref = parse_artifact_ref(src_path)
    if ref.type == "storage_key":
        if not STORAGE.exists(ref.value):
            return None
        src_name = Path(ref.value).name
        dest_name = _sanitize_filename(src_name)
        dest = dest_dir / dest_name
        with STORAGE.open_stream(ref.value) as stream, dest.open("wb") as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        return dest_name

    src = Path(ref.value)
    if not src.exists() or not src.is_file():
        return None
    dest_name = _sanitize_filename(src.name)
    dest = dest_dir / dest_name
    shutil.copy2(src, dest)
    return dest_name


def build_evidence_bundle(job_id: str, options: Optional[Dict[str, object]] = None) -> Tuple[str, str, Dict[str, object]]:
    repo = get_repo()
    normalized_options = _normalize_options(options if isinstance(options, dict) else None)
    inputs = _collect_inputs(job_id=job_id, options=normalized_options)

    doc = inputs["doc"]
    doc_id = str(inputs["docId"])
    policy_snapshot = inputs["policySnapshot"] if isinstance(inputs["policySnapshot"], dict) else {}
    policy_json = policy_snapshot.get("policyJson", {}) if isinstance(policy_snapshot.get("policyJson"), dict) else {}
    doc_type = str(doc.get("docType") or "pdf").lower()

    before_raw = inputs["beforeIssues"] if isinstance(inputs["beforeIssues"], list) else []
    after_raw = inputs["afterIssues"] if isinstance(inputs["afterIssues"], list) else []
    before_issues = [_apply_policy_to_issue(issue, policy_json, doc_type) for issue in before_raw if isinstance(issue, dict)]
    after_issues = [_apply_policy_to_issue(issue, policy_json, doc_type) for issue in after_raw if isinstance(issue, dict)]

    delta_json = _compute_delta(before_issues, after_issues)
    if not after_issues and before_issues:
        delta_json = {
            "fixed": [],
            "remaining": [
                {
                    "signature": _issue_signature(issue),
                    "ruleId": issue.get("ruleId"),
                    "severity": issue.get("severity"),
                    "locationHint": issue.get("locationHint"),
                }
                for issue in before_issues
            ],
            "introduced": [],
        }
        notes = inputs.get("notes") if isinstance(inputs.get("notes"), list) else []
        notes.append("After-pass issues missing; delta treated as remaining=before.")

    manual_items = [item for item in inputs["manualReview"] if isinstance(item, dict)] if isinstance(inputs["manualReview"], list) else []
    manual_json = {
        "docId": doc_id,
        "jobId": job_id,
        "items": manual_items,
        "summary": _manual_summary(manual_items),
    }

    before_counts = _count_by_severity(before_issues)
    after_counts = _count_by_severity(after_issues)

    fix_report = inputs["fixReport"] if isinstance(inputs["fixReport"], dict) else None

    doc_path = Path(str(doc.get("path") or ""))
    doc_size = doc_path.stat().st_size if doc_path.exists() else None
    doc_hash = _sha256_file(doc_path) if doc_path.exists() else None

    report_json = {
        "doc": {
            "id": doc_id,
            "filename": doc.get("filename"),
            "type": doc.get("docType"),
            "sizeBytes": doc_size,
            "hashes": {"sha256": doc_hash} if doc_hash else {},
            "createdAt": doc.get("createdAt"),
        },
        "job": {
            "id": job_id,
            "status": inputs["job"].get("status") if isinstance(inputs["job"], dict) else None,
            "startedAt": inputs["job"].get("startedAt") if isinstance(inputs["job"], dict) else None,
            "finishedAt": inputs["job"].get("finishedAt") if isinstance(inputs["job"], dict) else None,
        },
        "policySnapshot": policy_json,
        "scoring": inputs["scores"] if isinstance(inputs["scores"], list) else [],
        "issues": {
            "before": before_issues,
            "after": after_issues,
            "note": "after issues unavailable" if not after_issues else None,
        },
        "fixReport": fix_report,
        "fixReportNote": None if fix_report else "fix report unavailable",
        "manualReview": manual_items,
        "delta": delta_json,
        "notes": inputs["notes"],
    }

    created_at = _utc_now()
    bundle_id = f"bundle-{uuid.uuid4().hex[:12]}"
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    bundle_name = f"508-agent-evidence_{doc_id}_{job_id}_{stamp}.zip"
    bundle_path = BUNDLES_DIR / bundle_name

    BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="evidence-build-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)

        _write_json(temp_dir / "policy" / "policy.json", policy_json)
        _write_json(temp_dir / "reports" / "report.json", report_json)
        _write_json(temp_dir / "reports" / "delta.json", delta_json)
        _write_json(temp_dir / "reports" / "manual-review.json", manual_json)

        checks_rows = _build_checks_rows(before_issues=before_issues, after_issues=after_issues, manual_items=manual_items)
        _write_csv(temp_dir / "reports" / "checks.csv", checks_rows)

        manifest: Dict[str, object] = {
            "schemaVersion": 1,
            "docId": doc_id,
            "jobId": job_id,
            "createdAt": created_at,
            "appVersion": os.getenv("APP_VERSION", "unknown"),
            "policy": {
                "id": policy_snapshot.get("policyPackId"),
                "name": policy_snapshot.get("policyName"),
                "version": policy_snapshot.get("policyVersion"),
            },
            "scores": [
                {
                    "pass_type": score.get("passType"),
                    "score_total": score.get("scoreTotal"),
                    "status": score.get("status"),
                }
                for score in (inputs["scores"] if isinstance(inputs["scores"], list) else [])
                if isinstance(score, dict)
            ],
            "counts": {
                "before": {"total": len(before_issues), "bySeverity": before_counts},
                "after": {"total": len(after_issues), "bySeverity": after_counts},
                "manualReview": manual_json["summary"],
            },
            "includedFiles": [],
            "notes": inputs["notes"],
        }

        _write_summary_pdf(
            path=temp_dir / "reports" / "summary.pdf",
            report_json=report_json,
            manifest=manifest,
            delta_json=delta_json,
            manual_json=manual_json,
        )

        artifact_notes: List[str] = []
        if normalized_options.get("includeOriginal"):
            name = _copy_artifact_if_present(doc.get("path"), temp_dir / "artifacts" / "original")
            if not name:
                artifact_notes.append("Original artifact requested but source file was not found.")
        if normalized_options.get("includeFixedIfAvailable"):
            fixed_source = doc.get("fixedPath")
            if not fixed_source and fix_report:
                fixed_source = fix_report.get("fixedPath")
            name = _copy_artifact_if_present(fixed_source, temp_dir / "artifacts" / "fixed")
            if not name:
                artifact_notes.append("Fixed artifact requested but fixed file was not found.")
        if normalized_options.get("includeRebuiltIfAvailable"):
            rebuilt_source = doc.get("rebuiltPath")
            if not rebuilt_source and fix_report:
                rebuilt_source = fix_report.get("rebuiltPath")
            name = _copy_artifact_if_present(rebuilt_source, temp_dir / "artifacts" / "rebuilt")
            if not name:
                artifact_notes.append("Rebuilt artifact requested but rebuilt file was not found.")

        if artifact_notes:
            notes = manifest.get("notes") if isinstance(manifest.get("notes"), list) else []
            notes.extend(artifact_notes)
            manifest["notes"] = notes

        included_files = [
            _safe_relative_path(path, temp_dir)
            for path in sorted(temp_dir.rglob("*"))
            if path.is_file()
        ]
        manifest["includedFiles"] = included_files + ["provenance/tool.json", "provenance/hashes.json", "manifest.json"]

        _write_json(temp_dir / "manifest.json", manifest)

        tool_json = {
            "app": "508-Agent",
            "version": os.getenv("APP_VERSION", "unknown"),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "timestamp": created_at,
            "gitCommit": _git_commit(),
        }
        _write_json(temp_dir / "provenance" / "tool.json", tool_json)

        file_hashes: Dict[str, str] = {}
        for path in sorted(temp_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = _safe_relative_path(path, temp_dir)
            if rel == "provenance/hashes.json":
                continue
            file_hashes[rel] = _sha256_file(path)

        hashes_payload = {
            "files": file_hashes,
            "zipSha256": "",
            "note": "zipSha256 is from pre-final packaging pass; final hash is returned by API and DB.",
        }
        hashes_path = temp_dir / "provenance" / "hashes.json"
        _write_json(hashes_path, hashes_payload)

        _make_zip(temp_dir, bundle_path)
        prefinal_zip_hash = _sha256_file(bundle_path)
        hashes_payload["zipSha256"] = prefinal_zip_hash
        _write_json(hashes_path, hashes_payload)
        _make_zip(temp_dir, bundle_path)

    bundle_hash = _sha256_file(bundle_path)
    stored_bundle_ref = str(bundle_path)
    bundle_key = f"bundles/{bundle_id}/evidence.zip"
    try:
        STORAGE.save_file(key=bundle_key, src_path=str(bundle_path), content_type="application/zip")
        if SETTINGS.storage_provider == "s3":
            stored_bundle_ref = encode_storage_key(bundle_key)
        else:
            stored_bundle_ref = str(bundle_path)
    except Exception:
        stored_bundle_ref = str(bundle_path)

    repo.create_evidence_bundle_record(
        bundle_id=bundle_id,
        job_id=job_id,
        doc_id=doc_id,
        bundle_path=stored_bundle_ref,
        bundle_hash=bundle_hash,
        options=normalized_options,
        status="created",
    )

    return bundle_id, bundle_hash, {
        "bundlePath": stored_bundle_ref,
        "createdAt": created_at,
        "docId": doc_id,
        "jobId": job_id,
        "manifest": manifest,
    }
