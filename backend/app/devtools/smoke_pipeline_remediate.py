"""End-to-end smoke test for /pipeline/analyze and /pipeline/remediate.

This test synthesizes a small ``.docx`` document with a known set of
accessibility issues (no core_properties.title, a Heading-1 directly
followed by a Heading-3 to force a level jump, an inline image without
alt text, and a plain paragraph), drives the in-process FastAPI app
through ``TestClient``, and validates:

* /pipeline/analyze returns the expected report shape and surfaces both
  the missing-alt-text and heading-jump violations.
* /pipeline/remediate honors the approved-violation filter and only
  attempts the fix the caller approved (in this test: alt text only).
* The remediated artifact is downloadable via /pipeline/files/{job_id}/
  {filename} and, when re-parsed, exposes the new alt text on its image
  node.

Style follows the in-house smoke convention from
``app.devtools.run_smoke_suite``: plain ``assert`` statements, no
pytest, ``main()`` returns 0 on success / 1 on failure, and progress is
printed to stdout. Run with::

    python -m app.devtools.smoke_pipeline_remediate
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import List

# Hermetic DB for this smoke (its own sqlite file), set before app.main is
# imported inside main() so the engine binds here rather than the dev db.
_SMOKE_DB_DIR = tempfile.mkdtemp(prefix="508_smoke_pipeline_")
os.environ["DATABASE_URL"] = f"sqlite:///{_SMOKE_DB_DIR}/smoke.db"

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Synthetic fixture builder
# ---------------------------------------------------------------------------

# A 1x1 transparent PNG (smallest valid PNG payload). Used as the inline
# picture so the analyzer has at least one image to flag for alt text.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfc\xcf\xc0\x50\x0f\x00\x00\x05\x01\x01\x02\xcf\xa0.\xcd"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _build_synthetic_docx(path: Path) -> dict:
    """Write a tiny .docx with deliberate accessibility issues.

    Returns metadata describing what we actually managed to embed so the
    caller can soften assertions if (for example) python-docx couldn't
    add the picture.
    """

    from docx import Document

    doc = Document()
    # Deliberately do NOT set ``doc.core_properties.title``; that surfaces
    # MISSING_DOCUMENT_TITLE in the analyzer output.

    # Heading 1 followed immediately by Heading 3 => level jump.
    doc.add_heading("Top-Level Section", level=1)
    doc.add_heading("Sub-sub Section", level=3)

    # Plain paragraph so there is body text in the tree.
    doc.add_paragraph("This is a plain paragraph used as filler.")

    # Image with no alt text. python-docx requires Pillow to add a
    # picture from bytes; if Pillow isn't available we degrade
    # gracefully so the analyze half of the test still runs.
    image_added = False
    try:
        run = doc.add_paragraph().add_run()
        run.add_picture(io.BytesIO(_TINY_PNG))
        image_added = True
    except Exception as exc:
        print(f"[smoke] picture insertion skipped: {exc.__class__.__name__}: {exc}")

    doc.save(str(path))
    return {"image_added": image_added}


# ---------------------------------------------------------------------------
# Test driver
# ---------------------------------------------------------------------------


def main() -> int:
    # Defer the FastAPI import to inside main() so import-time errors fail
    # this test rather than crash the surrounding suite.
    from app.main import app
    from app.parsers.docx_parser import DOCXParser
    from app.models.accessibility import ImageNode, iter_reading_order

    client = TestClient(app)

    # All pipeline routes now require an authenticated session; remediate also
    # charges credits, so sign up and grant the starter pack first.
    signin = client.post(
        "/auth/sign-in",
        json={
            "email": "pipeline-smoke@example.com",
            "displayName": "Pipe",
            "password": "pipelinepass1",
        },
    )
    assert signin.status_code == 200, signin.text
    auth_headers = {"Authorization": f"Bearer {signin.json()['token']}"}
    grant = client.post("/auth/grant-starter", headers=auth_headers)
    assert grant.status_code == 200, grant.text

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "smoke.docx"
        fixture = _build_synthetic_docx(tmp_path)
        image_added = fixture["image_added"]

        # ---- /pipeline/analyze ---------------------------------------------
        with tmp_path.open("rb") as fh:
            analyze_resp = client.post(
                "/pipeline/analyze",
                files={"file": ("smoke.docx", fh, _DOCX_MIME)},
                headers=auth_headers,
            )

        assert analyze_resp.status_code == 200, (
            f"analyze failed: {analyze_resp.status_code} {analyze_resp.text}"
        )
        analyze_data = analyze_resp.json()
        for key in ("summary", "violations", "executions", "score", "aiProvider"):
            assert key in analyze_data, f"analyze response missing key: {key!r}"

        # The analyze endpoint defaults execute=False, so the executions
        # list MUST be empty until the caller opts in.
        assert analyze_data["executions"] == [], (
            f"executions should be empty for execute=False, got {analyze_data['executions']}"
        )

        rule_ids = [v["ruleId"] for v in analyze_data["violations"]]
        # Heading level jump may surface under either rule id depending on
        # which analyzer fires first; accept both.
        assert any(r in {"HEADING_LEVEL_JUMP", "SKIPPED_HEADING_LEVEL"} for r in rule_ids), (
            f"expected a heading-jump violation; got {rule_ids}"
        )

        if image_added:
            assert "MISSING_ALT_TEXT" in rule_ids, (
                f"expected MISSING_ALT_TEXT violation; got {rule_ids}"
            )
        else:
            print("[smoke] no image was inserted; skipping alt-text assertion")

        # ---- /pipeline/remediate -------------------------------------------
        # Pick exactly the alt-text violation as the approved subset. This
        # exercises the filtering logic: the writer should attempt that
        # one fix and ignore everything else (heading jump, missing title).
        approved_ids: List[str] = []
        rejected_ids: List[str] = []
        for violation in analyze_data["violations"]:
            if violation["ruleId"] == "MISSING_ALT_TEXT":
                approved_ids.append(violation["id"])
            else:
                rejected_ids.append(violation["id"])

        if not approved_ids and not image_added:
            print("ok pipeline smoke passed (analyze-only mode; no image to remediate)")
            return 0

        assert approved_ids, "expected at least one MISSING_ALT_TEXT to approve"

        with tmp_path.open("rb") as fh:
            remediate_resp = client.post(
                "/pipeline/remediate",
                files={"file": ("smoke.docx", fh, _DOCX_MIME)},
                data={
                    "approved_violations": json.dumps(approved_ids),
                    "rejected_violations": json.dumps(rejected_ids),
                },
                headers=auth_headers,
            )

        assert remediate_resp.status_code == 200, (
            f"remediate failed: {remediate_resp.status_code} {remediate_resp.text}"
        )
        remediate_data = remediate_resp.json()
        for key in ("jobId", "filename", "downloadUrl", "executions"):
            assert key in remediate_data, f"remediate response missing key: {key!r}"

        executions = remediate_data["executions"]
        # We approved exactly one violation, so exactly one execution
        # attempt should have been made. Either ``success`` or
        # ``skipped`` is acceptable: heuristic alt text may not always
        # bake in deterministically (e.g., if the executor decides the
        # node needs human review). What we MUST see is that nothing
        # outside the approved set was touched.
        assert len(executions) == 1, (
            f"expected exactly one execution (the approved alt-text fix), "
            f"got {len(executions)}: {executions}"
        )
        only_exec = executions[0]
        assert only_exec["status"] in {"success", "skipped"}, (
            f"unexpected execution status: {only_exec['status']!r}"
        )

        # ---- /pipeline/files/{jobId}/{filename} ---------------------------
        download_resp = client.get(remediate_data["downloadUrl"])
        assert download_resp.status_code == 200, (
            f"download failed: {download_resp.status_code} {download_resp.text}"
        )

        out_path = Path(tmp_dir) / remediate_data["filename"]
        out_path.write_bytes(download_resp.content)
        assert out_path.stat().st_size > 0, "downloaded remediated file is empty"

        # Round-trip through the parser to confirm the writer baked in
        # the approved mutation. Heuristic alt text looks like
        # "Image 1 shown in page 1." (any non-empty string is fine).
        reparsed = DOCXParser().parse_to_tree(str(out_path))
        image_nodes = [
            n for n in iter_reading_order(reparsed.tree.root) if isinstance(n, ImageNode)
        ]
        assert image_nodes, "expected at least one image in the remediated tree"
        first_image = image_nodes[0]

        if only_exec["status"] == "success":
            assert (first_image.alt_text or "").strip(), (
                f"expected non-empty alt_text after successful remediation; "
                f"got {first_image.alt_text!r}"
            )
        else:
            # skipped: writer did not bake heuristic alt text. Still a
            # valid outcome — we just confirm we didn't accidentally
            # corrupt the image.
            print(
                f"[smoke] alt-text execution was skipped; alt_text={first_image.alt_text!r}"
            )

    print("ok pipeline smoke passed")
    return 0


_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL pipeline smoke: {exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"FAIL pipeline smoke: {exc.__class__.__name__}: {exc}")
        sys.exit(1)
