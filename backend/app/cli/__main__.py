"""Headless CLI for the 508 Agent audit pipeline.

Run with ``python -m app.cli`` from inside ``backend/``.

Subcommands
-----------

``audit``
    Parse a document (or every supported document inside a folder), run the
    deterministic analyzer suite, and print a colored issue table.  Optional
    flags persist the report as JSON or printable HTML, and ``--apply`` runs
    the executors and writes a remediated copy of the source file.

Design notes
------------

* The CLI talks to the same code the FastAPI route does — :func:`parse_to_tree`,
  :class:`RemediationEngine`, and :func:`write_remediated` — without any HTTP
  hop.  This keeps batch runs fast and lets CI invoke it without spinning up
  the web server.
* Only the Python standard library is used: ``argparse`` for argument
  parsing, plain ANSI escape sequences for color.  No third-party CLI
  helpers (click / typer / rich) are imported.
* Anything that crashes when auditing one file inside a directory is caught
  and logged so the rest of the batch still runs.
* Exit codes follow the familiar convention used by linters:

  =====  ============================================
  Code   Meaning
  =====  ============================================
  0      No issues found
  1      Issues found (CI gate trips)
  2      Bad input — file missing, unsupported, parse error
  =====  ============================================
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from html import escape as _html_escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("app.cli")


# ---------------------------------------------------------------------------
# ANSI / color helpers
# ---------------------------------------------------------------------------


_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
    "grey": "\033[90m",
}


class _Colorizer:
    """Tiny ANSI helper.

    Behaves as a no-op when ``--no-color`` is passed, when stdout isn't a TTY,
    or when the ``NO_COLOR`` environment variable is set (per
    https://no-color.org/).
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        prefix = "".join(_ANSI.get(s, "") for s in styles)
        if not prefix:
            return text
        return f"{prefix}{text}{_ANSI['reset']}"


def _make_colorizer(no_color: bool) -> _Colorizer:
    enabled = (
        not no_color
        and "NO_COLOR" not in os.environ
        and hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
    )
    return _Colorizer(enabled)


# ---------------------------------------------------------------------------
# Supported formats
# ---------------------------------------------------------------------------


_SUPPORTED_EXTS = {".pdf", ".docx", ".pptx", ".html", ".htm"}


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in _SUPPORTED_EXTS


def _iter_supported_files(root: Path) -> List[Path]:
    """Recursively collect every supported file under ``root``.

    Hidden files / directories (leading dot) are skipped to avoid descending
    into ``.git`` and similar.
    """

    if root.is_file():
        return [root] if _is_supported(root) else []
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        # Skip dot-prefixed directories anywhere in the path.
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_file() and _is_supported(path):
            files.append(path)
    return files


# ---------------------------------------------------------------------------
# Score calculation (mirrors app.api.pipeline._build_score)
# ---------------------------------------------------------------------------


def _grade_letter(score: float) -> str:
    if score >= 95:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _build_score_dict(violations: Sequence[Any], executions: Sequence[Any]) -> Dict[str, Any]:
    """Compute a score payload mirroring :func:`app.api.pipeline._build_score`.

    Returns a plain dict so the CLI can dump it to JSON without depending on
    pydantic models.
    """

    from app.models.accessibility import Severity  # local import: keep startup cheap

    initial = len(violations)
    if initial == 0:
        return {
            "initialIssues": 0,
            "fixedAutomatically": 0,
            "pendingManual": 0,
            "score": 100.0,
            "grade": "A+",
        }

    fixed = sum(1 for e in executions if getattr(e.status, "value", e.status) == "success")
    pending = sum(1 for e in executions if getattr(e.status, "value", e.status) == "skipped")
    error_weight = sum(2 for v in violations if v.severity == Severity.ERROR.value)
    warning_weight = sum(1 for v in violations if v.severity == Severity.WARNING.value)
    total_weight = max(error_weight + warning_weight, 1)
    fixed_weight = sum(
        2 for e in executions if getattr(e.status, "value", e.status) == "success"
    )
    score = max(0.0, 100.0 * (fixed_weight / (total_weight * 2 + 0.01)))
    score = min(100.0, score + 10.0 * (fixed / max(initial, 1)))
    return {
        "initialIssues": initial,
        "fixedAutomatically": fixed,
        "pendingManual": pending,
        "score": round(score, 2),
        "grade": _grade_letter(score),
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _violation_to_dict(v: Any) -> Dict[str, Any]:
    """Best-effort serialization of a :class:`Violation` to plain dict."""

    location = v.location
    return {
        "id": v.violation_id,
        "ruleId": v.rule_id,
        "severity": v.severity,
        "description": v.description,
        "nodeId": location.node_id,
        "path": list(getattr(location, "path", []) or []),
        "page": (v.evidence or {}).get("page"),
        "evidence": dict(v.evidence or {}),
    }


def _execution_to_dict(e: Any) -> Dict[str, Any]:
    return {
        "actionCode": getattr(e.action_code, "value", str(e.action_code)),
        "targetNodeId": e.target_node_id,
        "status": getattr(e.status, "value", str(e.status)),
        "notes": e.notes,
    }


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


_SEV_GLYPH = {"error": "x", "warning": "!", "info": "i"}
_SEV_COLORS = {"error": ("red",), "warning": ("yellow",), "info": ("cyan",)}


def _print_issue_table(violations: Sequence[Any], color: _Colorizer) -> None:
    if not violations:
        print(color("  No issues found.", "green"))
        return

    rule_width = min(28, max((len(v.rule_id) for v in violations), default=12))
    page_width = 8

    for v in violations:
        glyph = _SEV_GLYPH.get(v.severity, "-")
        sev_styles = _SEV_COLORS.get(v.severity, ("magenta",))
        sev_label = v.severity.upper().ljust(7)
        rule = v.rule_id.ljust(rule_width)
        page_val = (v.evidence or {}).get("page")
        page = ("page " + str(page_val)) if page_val is not None else "—"
        page = page.ljust(page_width)
        line = f"  {glyph} {color(sev_label, *sev_styles, 'bold')}  {color(rule, 'bold')}  {color(page, 'dim')}  {v.description}"
        print(line)


def _print_summary_line(
    *,
    label: str,
    score: Dict[str, Any],
    violation_count: int,
    source_format: str,
    color: _Colorizer,
) -> None:
    grade = score.get("grade", "?")
    score_val = score.get("score", 0.0)

    if violation_count == 0:
        score_styled = color(f"{score_val}", "green", "bold")
    elif score_val >= 80:
        score_styled = color(f"{score_val}", "yellow", "bold")
    else:
        score_styled = color(f"{score_val}", "red", "bold")

    parts = [
        f"Score: {score_styled} (Grade: {color(grade, 'bold')})",
        f"{violation_count} issue(s)",
        f"format: {source_format}",
    ]
    prefix = color(label, "magenta", "bold") if label else ""
    if prefix:
        print(f"{prefix}  " + "  -  ".join(parts))
    else:
        print("  -  ".join(parts))


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def _build_html_report(
    *,
    document_label: str,
    source_format: str,
    violations: Sequence[Dict[str, Any]],
    executions: Sequence[Dict[str, Any]],
    score: Dict[str, Any],
) -> str:
    """Render a minimal printable HTML report.

    The frontend's ``audit.tsx`` ``_buildReportHtml`` renders a much fancier
    document with CSS variables and signature blocks.  Reproducing that
    template verbatim from the CLI would couple the backend to the React app.
    Instead, this generates a stylistically similar standalone page using
    only inlined CSS so it works offline.
    """

    def _row(v: Dict[str, Any]) -> str:
        sev_class = f"sev-{_html_escape(v['severity'])}"
        page = v.get("page")
        page_str = _html_escape(str(page)) if page is not None else "&mdash;"
        return (
            "<tr>"
            f"<td class=\"{sev_class}\">{_html_escape(v['severity'].upper())}</td>"
            f"<td><code>{_html_escape(v['ruleId'])}</code></td>"
            f"<td>{page_str}</td>"
            f"<td>{_html_escape(v['description'])}</td>"
            f"<td><code class=\"muted\">{_html_escape(v['nodeId'])}</code></td>"
            "</tr>"
        )

    rows_html = "\n".join(_row(v) for v in violations) or (
        '<tr><td colspan="5" class="muted">No issues found.</td></tr>'
    )

    exec_rows = "\n".join(
        "<tr>"
        f"<td><code>{_html_escape(e['actionCode'])}</code></td>"
        f"<td>{_html_escape(e['status'])}</td>"
        f"<td><code class=\"muted\">{_html_escape(e['targetNodeId'])}</code></td>"
        f"<td>{_html_escape(e['notes'])}</td>"
        "</tr>"
        for e in executions
    )

    exec_section = (
        f"""
    <h2>Applied actions</h2>
    <table>
      <thead><tr><th>Action</th><th>Status</th><th>Target</th><th>Notes</th></tr></thead>
      <tbody>
        {exec_rows}
      </tbody>
    </table>
    """
        if exec_rows
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>508 Agent Report &mdash; {_html_escape(document_label)}</title>
<style>
  :root {{ --accent:#2D5BFF; --ok:#16A34A; --warn:#F59E0B; --err:#DC2626; --bg:#F6F7FB; --fg:#0F172A; --muted:#5B6475; --border:#E2E8F0; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background: var(--bg); color: var(--fg); margin: 0; padding: 32px 16px; }}
  .page {{ max-width: 960px; margin: 0 auto; background: white; border-radius: 16px; box-shadow: 0 8px 32px rgba(15, 23, 42, 0.08); overflow: hidden; }}
  .hero {{ background: linear-gradient(135deg, var(--accent) 0%, #1E3A8A 100%); color: white; padding: 32px 40px; }}
  .hero .eyebrow {{ font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; opacity: 0.85; }}
  .hero h1 {{ font-size: 28px; margin: 6px 0 4px; letter-spacing: -0.5px; }}
  .hero .filename {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; opacity: 0.85; }}
  .score-block {{ display: flex; align-items: baseline; gap: 12px; margin-top: 18px; }}
  .score-num {{ font-size: 56px; font-weight: 800; letter-spacing: -2px; }}
  .score-grade {{ display: inline-block; background: rgba(255,255,255,0.2); border: 1.5px solid rgba(255,255,255,0.4); padding: 4px 12px; border-radius: 999px; font-weight: 700; font-size: 16px; }}
  .body {{ padding: 28px 40px 16px; }}
  .body h2 {{ font-size: 18px; border-bottom: 2px solid var(--border); padding-bottom: 6px; margin-top: 28px; }}
  .body h2:first-child {{ margin-top: 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ background: var(--bg); font-weight: 700; font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; color: var(--muted); }}
  td.sev-error {{ color: var(--err); font-weight: 700; }}
  td.sev-warning {{ color: var(--warn); font-weight: 700; }}
  td.sev-info {{ color: #0369A1; }}
  .muted {{ color: var(--muted); }}
  code {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }}
  .footer {{ background: var(--bg); padding: 14px 40px; font-size: 11px; color: var(--muted); text-align: center; }}
  @media print {{
    body {{ background: white; padding: 0; }}
    .page {{ box-shadow: none; border-radius: 0; max-width: none; }}
    h2 {{ page-break-after: avoid; }}
    tr {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="page">
  <header class="hero">
    <div class="eyebrow">Accessibility Audit Report</div>
    <h1>{_html_escape(document_label)}</h1>
    <div class="filename">{_html_escape(source_format.upper())} &middot; generated by 508 Agent CLI</div>
    <div class="score-block">
      <div class="score-num">{score.get("score", 0):.0f}</div>
      <div>
        <div class="score-grade">Grade {_html_escape(str(score.get("grade", "?")))}</div>
        <div style="margin-top:6px; font-size:13px; opacity:0.85;">
          {len(violations)} finding(s) &middot; {score.get("fixedAutomatically", 0)} auto-fixed &middot; {score.get("pendingManual", 0)} pending
        </div>
      </div>
    </div>
  </header>
  <div class="body">
    <h2>Findings</h2>
    <table>
      <thead><tr><th>Severity</th><th>Rule</th><th>Page</th><th>Description</th><th>Node</th></tr></thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
    {exec_section}
  </div>
  <div class="footer">508 Agent CLI &middot; report is informational; final conformance requires human review.</div>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Audit core
# ---------------------------------------------------------------------------


class _AuditOutcome:
    """Aggregate result returned by :func:`_audit_single_file`."""

    __slots__ = ("ok", "violations", "score", "report_data", "tree", "result")

    def __init__(
        self,
        *,
        ok: bool,
        violations: List[Any],
        score: Dict[str, Any],
        report_data: Dict[str, Any],
        tree: Any,
        result: Any,
    ) -> None:
        self.ok = ok
        self.violations = violations
        self.score = score
        self.report_data = report_data
        self.tree = tree
        self.result = result


def _audit_single_file(
    file_path: Path,
    *,
    apply: bool,
    approve_all_errors: bool,
) -> _AuditOutcome:
    """Run the full pipeline on one file.

    Returns an :class:`_AuditOutcome`.  Raises on parse failure so the caller
    can decide whether to surface it (CI exit 2) or continue iterating in a
    directory walk.
    """

    from app.parsers import parse_to_tree
    from app.services.remediation_engine import RemediationEngine
    from app.services.remediation_planner import plan_remediations, RemediationPolicy
    from app.services.remediators.registry import execute_plans

    result = parse_to_tree(str(file_path))
    tree = result.tree

    engine = RemediationEngine()
    violations = engine.detect_violations(tree)

    executions: List[Any] = []

    if apply:
        # Apply mode runs the fixes the user opted into (--apply / --approve-*),
        # so allow every recommended action — including AI/heuristic alt-text —
        # rather than the conservative preview default (which no-ops alt-text and
        # the auto-apply fixes).
        plans = plan_remediations(
            tree,
            RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False),
        )

        if approve_all_errors:
            # Only execute plans whose flag severity is "error".
            error_violation_keys = {
                (v.location.node_id, v.rule_id)
                for v in violations
                if v.severity == "error"
            }
            selected_plans = [
                p
                for p in plans
                if (p.target_node_id, p.flag.code.value) in error_violation_keys
            ]
            executions = execute_plans(tree, selected_plans) if selected_plans else []
        else:
            executions = engine.execute(tree)

    score = _build_score_dict(violations, executions)

    report_data: Dict[str, Any] = {
        "schema": "508-agent-audit-v1",
        "source": {
            "path": str(file_path),
            "format": result.format,
            "documentId": result.document_id,
        },
        "summary": {
            "title": (
                tree.root.metadata.properties.get("title")
                if tree.root.metadata.properties
                else None
            ),
            "language": tree.root.metadata.language,
        },
        "violations": [_violation_to_dict(v) for v in violations],
        "executions": [_execution_to_dict(e) for e in executions],
        "score": score,
    }

    return _AuditOutcome(
        ok=True,
        violations=violations,
        score=score,
        report_data=report_data,
        tree=tree,
        result=result,
    )


# ---------------------------------------------------------------------------
# Subcommand: audit
# ---------------------------------------------------------------------------


def _default_remediated_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}-remediated{source.suffix}")


def _run_audit(args: argparse.Namespace) -> int:
    color = _make_colorizer(no_color=args.no_color)

    target = Path(args.path)
    if not target.exists():
        print(color(f"error: path does not exist: {target}", "red", "bold"), file=sys.stderr)
        return 2

    files = _iter_supported_files(target)
    if not files:
        print(
            color(
                f"error: no supported files (.pdf .docx .pptx .html .htm) under: {target}",
                "red",
                "bold",
            ),
            file=sys.stderr,
        )
        return 2

    is_directory_run = target.is_dir() or len(files) > 1

    # Validate single-file-only flags up front.
    if is_directory_run and args.output:
        print(
            color(
                "error: --output is only valid when auditing a single file.",
                "red",
                "bold",
            ),
            file=sys.stderr,
        )
        return 2
    if is_directory_run and (args.json or args.html):
        # We allow it but warn — the report will reflect only the *last* file.
        # Most users want per-file outputs so we choose to error instead.
        print(
            color(
                "error: --json and --html are only valid when auditing a single file.",
                "red",
                "bold",
            ),
            file=sys.stderr,
        )
        return 2

    if args.policy:
        # Policy is currently a stub — acknowledge but ignore.
        print(
            color(
                f"note: --policy {args.policy!r} is a placeholder; default policy is in effect.",
                "dim",
            )
        )

    aggregate_violation_count = 0
    aggregate_score_sum = 0.0
    files_audited = 0
    files_failed = 0

    overall_exit_code = 0

    from app.writers import write_remediated  # imported lazily so --help is fast

    for file_path in files:
        rel_label = (
            str(file_path.relative_to(target)) if target.is_dir() else file_path.name
        )

        try:
            outcome = _audit_single_file(
                file_path,
                apply=args.apply,
                approve_all_errors=args.approve_all_errors,
            )
        except FileNotFoundError as exc:
            print(color(f"error: {file_path}: {exc}", "red", "bold"), file=sys.stderr)
            files_failed += 1
            overall_exit_code = max(overall_exit_code, 2)
            continue
        except ValueError as exc:
            # Unsupported format raised by parse_to_tree.
            print(color(f"error: {file_path}: {exc}", "red", "bold"), file=sys.stderr)
            files_failed += 1
            overall_exit_code = max(overall_exit_code, 2)
            continue
        except Exception as exc:  # pragma: no cover - defensive batch tolerance
            print(
                color(
                    f"error: {file_path}: parse/audit failed: {exc.__class__.__name__}: {exc}",
                    "red",
                    "bold",
                ),
                file=sys.stderr,
            )
            if args.verbose:
                traceback.print_exc()
            files_failed += 1
            overall_exit_code = max(overall_exit_code, 2)
            continue

        violations = outcome.violations
        files_audited += 1
        aggregate_violation_count += len(violations)
        aggregate_score_sum += float(outcome.score.get("score", 0.0))

        # Header line for each file in directory mode.
        if is_directory_run:
            print()
            print(color(f"== {rel_label} ==", "cyan", "bold"))

        if not args.quiet:
            _print_issue_table(violations, color)

        _print_summary_line(
            label="" if not is_directory_run else f"[{rel_label}]",
            score=outcome.score,
            violation_count=len(violations),
            source_format=outcome.result.format,
            color=color,
        )

        # Single-file: optional JSON / HTML / apply.
        if not is_directory_run:
            if args.json:
                json_path = Path(args.json)
                json_path.parent.mkdir(parents=True, exist_ok=True)
                json_path.write_text(
                    json.dumps(outcome.report_data, indent=2, default=str),
                    encoding="utf-8",
                )
                print(color(f"  Wrote JSON report: {json_path}", "green"))

            if args.html:
                html_path = Path(args.html)
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html = _build_html_report(
                    document_label=file_path.name,
                    source_format=outcome.result.format,
                    violations=outcome.report_data["violations"],
                    executions=outcome.report_data["executions"],
                    score=outcome.score,
                )
                html_path.write_text(html, encoding="utf-8")
                print(color(f"  Wrote HTML report: {html_path}", "green"))

            if args.apply:
                output_path = (
                    Path(args.output) if args.output else _default_remediated_path(file_path)
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    write_result = write_remediated(
                        file_path,
                        outcome.tree,
                        output_path,
                        source_format=outcome.result.format,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    print(
                        color(
                            f"  error: writer failed: {exc.__class__.__name__}: {exc}",
                            "red",
                            "bold",
                        ),
                        file=sys.stderr,
                    )
                    if args.verbose:
                        traceback.print_exc()
                    overall_exit_code = max(overall_exit_code, 2)
                else:
                    applied = len(write_result.get("applied", []) or [])
                    skipped = len(write_result.get("skipped", []) or [])
                    print(
                        color(
                            f"  Wrote remediated file: {output_path}  "
                            f"(applied: {applied}, skipped: {skipped})",
                            "green",
                        )
                    )

        # Track the gate exit code.
        if violations and overall_exit_code < 1:
            overall_exit_code = 1

    # Aggregate footer for directory runs.
    if is_directory_run:
        print()
        print(color("== Summary ==", "cyan", "bold"))
        print(
            f"  Files audited: {files_audited}"
            + (f"  -  failed: {files_failed}" if files_failed else "")
        )
        print(f"  Total issues: {aggregate_violation_count}")
        if files_audited:
            mean_score = aggregate_score_sum / files_audited
            print(
                f"  Mean score: {mean_score:.1f} (Grade: {_grade_letter(mean_score)})"
            )

    return overall_exit_code


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Headless 508 Agent: audit accessibility of PDF / DOCX / PPTX files "
            "from the command line."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    audit_p = sub.add_parser(
        "audit",
        help="Audit a file or every supported file in a folder.",
        description=(
            "Parse the document(s), run the analyzer suite, and print findings.  "
            "With --apply, also run the deterministic executors and write a "
            "remediated copy of the source file."
        ),
    )
    audit_p.add_argument("path", help="File or directory to audit.")
    audit_p.add_argument(
        "--json",
        metavar="PATH",
        help="Also write a JSON report to PATH (single-file only).",
    )
    audit_p.add_argument(
        "--html",
        metavar="PATH",
        help="Also write a printable HTML report to PATH (single-file only).",
    )
    audit_p.add_argument(
        "--apply",
        action="store_true",
        help="Run the executors and emit a remediated file (single-file only).",
    )
    audit_p.add_argument(
        "--approve-all-errors",
        action="store_true",
        help="With --apply, only auto-approve violations whose severity is 'error'.",
    )
    audit_p.add_argument(
        "--output",
        metavar="PATH",
        help="With --apply, write the remediated file to this path "
        "(default: <stem>-remediated<ext> alongside the source).",
    )
    audit_p.add_argument(
        "--policy",
        metavar="NAME",
        help="Policy name to apply.  Currently a placeholder; default policy is used.",
    )
    audit_p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the per-issue table; print only the summary line.",
    )
    audit_p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color even when stdout is a TTY.",
    )
    audit_p.add_argument(
        "--verbose",
        action="store_true",
        help="Print full Python tracebacks on errors.",
    )
    audit_p.set_defaults(func=_run_audit)
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point.  Returns an exit code (0/1/2)."""

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
