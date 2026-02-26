from __future__ import annotations

import argparse
import io
import json
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from pypdf import PdfWriter


REQUIRED_BUNDLE_FILES = {
    "manifest.json",
    "policy/policy.json",
    "reports/report.json",
    "reports/checks.csv",
    "reports/delta.json",
    "reports/manual-review.json",
    "reports/summary.pdf",
    "provenance/hashes.json",
}


def _request_json(method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url=url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read()
    if not payload:
        return {}
    decoded = json.loads(payload.decode("utf-8"))
    if isinstance(decoded, dict):
        return decoded
    raise RuntimeError(f"Expected JSON object from {url}")


def _request_list(url: str) -> list:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read()
    decoded = json.loads(payload.decode("utf-8"))
    if isinstance(decoded, list):
        return decoded
    raise RuntimeError(f"Expected JSON list from {url}")


def _multipart_upload(url: str, file_path: Path) -> dict:
    boundary = f"----508agent{int(time.time() * 1000)}"
    file_bytes = file_path.read_bytes()
    disposition = f'form-data; name="file"; filename="{file_path.name}"'
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: {disposition}\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return _request_json("POST", url, body=body, headers=headers)


def _wait_job(base_url: str, job_id: str, timeout_sec: int = 120) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        payload = _request_json("GET", f"{base_url}/jobs/{job_id}")
        status = str(payload.get("status") or "").lower()
        if status in {"done", "completed"}:
            return payload
        if status in {"error", "failed"}:
            raise RuntimeError(f"Scan job failed: {payload}")
        time.sleep(1)
    raise RuntimeError("Timed out waiting for scan job completion")


def _download_bytes(url: str) -> bytes:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _make_temp_pdf() -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    fd = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    path = Path(fd.name)
    fd.close()
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def run(base_url: str, file_path: Path) -> None:
    base_url = base_url.rstrip("/")
    print(f"[e2e] base_url={base_url}")
    upload = _multipart_upload(f"{base_url}/documents/upload", file_path)
    doc_id = str(upload.get("docId") or "")
    if not doc_id:
        raise RuntimeError(f"Upload failed: {upload}")
    print(f"[e2e] upload doc_id={doc_id}")

    scan = _request_json("POST", f"{base_url}/documents/{doc_id}/scan")
    job_id = str(scan.get("jobId") or "")
    if not job_id:
        raise RuntimeError(f"Scan start failed: {scan}")
    _wait_job(base_url, job_id)
    print(f"[e2e] scan complete job_id={job_id}")

    apply = _request_json("POST", f"{base_url}/documents/{doc_id}/apply-fixes")
    if not bool(apply.get("fixed", False)):
        raise RuntimeError(f"Apply fixes failed: {apply}")
    print("[e2e] apply fixes complete")

    fix_report = _request_json("GET", f"{base_url}/documents/{doc_id}/fix-report")
    print(f"[e2e] fix report delta keys={list((fix_report.get('delta') or {}).keys())}")

    bundle = _request_json("POST", f"{base_url}/jobs/{job_id}/evidence-bundle", body=b"{}", headers={"Content-Type": "application/json"})
    bundle_id = str(bundle.get("bundleId") or "")
    if not bundle_id:
        raise RuntimeError(f"Bundle creation failed: {bundle}")
    print(f"[e2e] bundle created bundle_id={bundle_id}")

    bundle_bytes = _download_bytes(f"{base_url}/evidence-bundles/{bundle_id}/download")
    with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as zf:
        names = set(zf.namelist())
        missing = REQUIRED_BUNDLE_FILES - names
        if missing:
            raise RuntimeError(f"Bundle missing files: {sorted(missing)}")
    print("[e2e] bundle download + contents verified")

    statuses = _request_json("GET", f"{base_url}/documents/status")
    items = statuses.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("Invalid /documents/status payload")
    found = any(isinstance(item, dict) and str(item.get("docId") or "") == doc_id for item in items)
    if not found:
        raise RuntimeError(f"Doc {doc_id} not present in /documents/status")
    print("[e2e] status listing verified")
    print("[e2e] PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end smoke against a running 508-agent backend.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--file", default="")
    args = parser.parse_args()

    path = Path(args.file).resolve() if args.file else _make_temp_pdf()
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    run(args.base_url, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

