/**
 * Batch audit screen — drop multiple documents, watch them analyze, walk
 * the results.
 *
 * The single-document audit screen at /audit is the deep workflow; this is
 * its peer for *folders* of documents.  We:
 *
 *   1. Accept N files via either a multi-select file input or a window-wide
 *      drag-and-drop (multi-file aware — we don't reuse useFileDrop because
 *      its callback signature is single-file).
 *   2. Run pipeline.analyze in parallel with a concurrency cap of 3 so the
 *      backend doesn't get hammered.  The cap is enforced with a simple
 *      in-flight counter inside a worker loop.
 *   3. Stream completed results into the queue card with status chips.
 *      Per-file failures are isolated — one bad file marks itself "error"
 *      and the rest keep going.
 *   4. Append every successful audit to the shared history store so the
 *      "Open audit" button can deep-link into /audit?historyId=<id> and
 *      restore the full snapshot.
 *   5. Persist the batch to localStorage under `508-batch-<id>` so a
 *      refresh keeps the queue.  We snapshot summaries only (no File
 *      objects, those don't survive a reload).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { PipelineResponse, createApiClient } from "../src/api/client";
import { loadAccount, loadToken, refreshAccount } from "../src/domain/account";
import { appendHistory, findHistoryEntry } from "../src/domain/auditHistory";
import { computeBatchConformance, conformanceTotals } from "../src/domain/wcagCriteria";
import {
  brandingAccentCss,
  brandingFooterHtml,
  brandingHeroHtml,
  loadBranding,
} from "../src/domain/reportBranding";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Dialog } from "../src/ui/components/Dialog";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { PixelSpinner } from "../src/ui/components/PixelSpinner";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

// Per-format remediation credit cost — mirrors the backend DOC_FORMAT_COSTS
// (pipeline.py). Used to show the batch total before charging.
const FORMAT_CREDIT_COST: Record<string, number> = { pdf: 5, docx: 3, pptx: 4 };
function _formatOf(filename: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(filename || "");
  return (m ? m[1] : "").toLowerCase();
}
function _costFor(filename: string): number {
  return FORMAT_CREDIT_COST[_formatOf(filename)] ?? 5;
}

const ACCEPTED_FILE_TYPES = [
  ".pdf",
  ".docx",
  ".pptx",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
].join(",");

const MAX_CONCURRENCY = 3;

type Status = "queued" | "analyzing" | "done" | "error";

interface QueueItem {
  /** Stable id — also used as the audit-history entry id so "Open audit" lines up. */
  id: string;
  filename: string;
  sizeBytes: number;
  status: Status;
  /** Populated when status === "done". */
  score?: number;
  grade?: string;
  totalIssues?: number;
  sourceFormat?: string;
  /** Compact findings (ruleId + severity) for the consolidated conformance report. */
  findings?: { ruleId: string; severity: string }[];
  /** Populated when status === "error". */
  errorMessage?: string;
  /** ISO timestamp of state transition — used only for the "ranAt" history field. */
  finishedAt?: string;
  /** Bulk-remediation state (the file must still be in memory to remediate). */
  remediateStatus?: "idle" | "remediating" | "remediated" | "error";
  remediateError?: string;
  appliedCount?: number;
  /** The produced remediated file, for ZIP bundling + individual download. */
  jobId?: string;
  remediatedFilename?: string;
  downloadUrl?: string;
}

interface PersistedBatch {
  batchId: string;
  createdAt: string;
  items: QueueItem[];
}

const BATCH_KEY_PREFIX = "508-batch-";
const ACTIVE_BATCH_POINTER = "508-batch-active";

function _newBatchId(): string {
  // Not a real uuid — just stable enough for a localStorage key.
  return (
    Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8)
  );
}

function _saveBlob(blob: Blob, name: string): void {
  if (typeof document === "undefined") return;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function _esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string),
  );
}

/**
 * Build a printable, branded multi-document Accessibility Assessment Report —
 * the consolidated deliverable an agency (e.g. Sunriver) hands a client after
 * running their document set. Self-contained HTML, no backend call.
 */
function _buildBatchReportHtml(
  items: Array<{
    filename: string;
    status: string;
    score?: number;
    grade?: string;
    totalIssues?: number;
    sourceFormat?: string;
    remediateStatus?: string;
    appliedCount?: number;
  }>,
  stats: { done: number; total: number; avgScore: number; totalIssues: number },
  totalApplied: number,
): string {
  const branding = loadBranding(); // white-label: agency logo/name/colour
  const date = new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  const done = items.filter((i) => i.status === "done");
  const rows = done
    .map((i) => {
      const score = i.score ?? 0;
      const tone = score >= 90 ? "#15803D" : score >= 70 ? "#B45309" : "#B91C1C";
      const rem =
        i.remediateStatus === "remediated"
          ? `<span style="color:#15803D">Remediated · ${i.appliedCount ?? 0} fix(es)</span>`
          : i.remediateStatus === "error"
            ? `<span style="color:#B91C1C">Not remediated</span>`
            : `<span style="color:#6B7280">Analyzed only</span>`;
      return `<tr>
        <td>${_esc(i.filename)}</td>
        <td style="text-transform:uppercase">${_esc(i.sourceFormat || _formatOf(i.filename))}</td>
        <td style="text-align:center;font-weight:700;color:${tone}">${score} ${_esc(i.grade || "")}</td>
        <td style="text-align:center">${i.totalIssues ?? 0}</td>
        <td>${rem}</td>
      </tr>`;
    })
    .join("");
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Accessibility Assessment Report</title>
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1A1A1A;max-width:920px;margin:40px auto;padding:0 24px;line-height:1.5}
  h1{font-size:28px;margin:0 0 4px} .sub{color:#6B7280;margin:0 0 24px}
  .grid{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}
  .tile{flex:1;min-width:160px;border:1px solid #E5E7EB;border-radius:12px;padding:16px}
  .tile .n{font-size:30px;font-weight:800;color:#C2410C} .tile .l{color:#6B7280;font-size:13px}
  table{width:100%;border-collapse:collapse;margin-top:16px;font-size:14px}
  th,td{text-align:left;padding:10px 8px;border-bottom:1px solid #E5E7EB}
  th{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#6B7280}
  .foot{margin-top:32px;color:#6B7280;font-size:12px;border-top:1px solid #E5E7EB;padding-top:16px}
  @media print{body{margin:0}}
</style>${brandingAccentCss(branding)}</head><body>
  ${brandingHeroHtml(branding)}
  <h1>Accessibility Assessment Report</h1>
  <p class="sub">${done.length} document${done.length === 1 ? "" : "s"} assessed · ${_esc(date)}</p>
  <div class="grid">
    <div class="tile"><div class="n">${done.length}</div><div class="l">Documents assessed</div></div>
    <div class="tile"><div class="n">${stats.totalIssues}</div><div class="l">Accessibility issues found</div></div>
    <div class="tile"><div class="n">${stats.done > 0 ? stats.avgScore.toFixed(1) : "—"}</div><div class="l">Average score / 100</div></div>
    <div class="tile"><div class="n">${totalApplied}</div><div class="l">Fixes applied</div></div>
  </div>
  <table>
    <thead><tr><th>File</th><th>Type</th><th>Score</th><th>Issues</th><th>Remediation</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p class="foot">${brandingFooterHtml(branding)} This is an automated remediation summary checked against WCAG 2.1, Section 508, and PDF/UA structural criteria — not a formal conformance determination. Items requiring human judgment (colour contrast in complex layouts, reading order, image meaning) are listed for manual review where applicable.</p>
</body></html>`;
}

function _buildBatchConformanceReportHtml(
  docs: { filename: string; findings: { ruleId: string; severity: string }[]; score?: number; sourceFormat?: string }[],
): string {
  const branding = loadBranding(); // white-label: agency logo/name/colour
  const verdicts = computeBatchConformance(docs.map((d) => ({ filename: d.filename, findings: d.findings })));
  const totals = conformanceTotals(verdicts);
  const totalCriteria = totals.evaluatedTotal + totals.notEvaluated;
  const date = new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  const statusClass = (s: string) =>
    s === "Supports" ? "ok" : s === "Partially Supports" ? "warn" : s === "Does Not Support" ? "err" : "na";
  const rows = verdicts
    .map((v) => {
      const label = v.status === "Supports" && v.partialCoverage ? "Supports&#8224;" : _esc(v.status);
      const affected =
        v.evaluated && v.docsAffected > 0 ? ` <em>(${v.docsAffected}/${v.totalDocs} docs)</em>` : "";
      return `<tr><td class="num">${_esc(v.num)}</td><td>${_esc(v.title)}</td><td class="lvl">${_esc(v.level)}</td>
        <td><span class="pill pill-${statusClass(v.status)}">${label}</span></td>
        <td class="remark">${_esc(v.remark)}${affected}</td></tr>`;
    })
    .join("");
  const docRows = docs
    .map(
      (d) =>
        `<tr><td>${_esc(d.filename)}</td><td style="text-transform:uppercase">${_esc(d.sourceFormat || _formatOf(d.filename))}</td>
         <td style="text-align:center">${d.findings.length}</td></tr>`,
    )
    .join("");
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Batch Accessibility Conformance Report</title>
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0F172A;max-width:980px;margin:40px auto;padding:0 24px;line-height:1.5}
  h1{font-size:28px;margin:0 0 4px} h2{font-size:19px;border-bottom:2px solid #E2E8F0;padding-bottom:6px;margin-top:32px}
  .sub{color:#5B6475;margin:0 0 16px}
  .headline{font-size:15px;background:#EFF6FF;border-left:4px solid #2D5BFF;padding:12px 16px;border-radius:6px}
  .tally{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}
  .cell{border:1px solid #E2E8F0;border-radius:10px;padding:14px;text-align:center}
  .cell .n{font-size:26px;font-weight:800}.cell .k{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
  .ok{color:#16A34A}.warn{color:#B45309}.err{color:#DC2626}.na{color:#64748B}
  table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
  th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #E2E8F0;vertical-align:top}
  th{background:#F6F7FB;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#5B6475}
  td.num{font-weight:700;white-space:nowrap}td.lvl{color:#5B6475;font-weight:700}td.remark{color:#5B6475}
  .pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}
  .pill-ok{background:#DCFCE7;color:#16A34A}.pill-warn{background:#FEF3C7;color:#B45309}.pill-err{background:#FEE2E2;color:#DC2626}.pill-na{background:#F1F5F9;color:#64748B}
  .callout{background:#FFFBEB;border-left:4px solid #F59E0B;padding:12px 16px;border-radius:6px;margin-top:12px;font-size:13px}
  .print-btn{position:fixed;top:16px;right:16px;padding:10px 16px;border-radius:999px;border:none;background:#2D5BFF;color:#fff;font-weight:700;cursor:pointer}
  @media print{.print-btn{display:none}body{margin:0}}
</style>${brandingAccentCss(branding)}</head><body>
  <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
  ${brandingHeroHtml(branding)}
  <h1>Batch Accessibility Conformance Report</h1>
  <p class="sub">${docs.length} document${docs.length === 1 ? "" : "s"} · WCAG 2.1 AA · ${_esc(date)}</p>
  <p class="headline">Across ${docs.length} document${docs.length === 1 ? "" : "s"}, automated testing evaluated ${totals.evaluatedTotal} of ${totalCriteria} WCAG 2.1 A/AA criteria: ${totals.supports} Supports, ${totals.partial} Partially Supports, ${totals.unsupported} Does Not Support. The remaining ${totals.notEvaluated} were not tested and require manual review. A criterion is rated by the worst result across the batch.</p>
  <div class="tally">
    <div class="cell"><div class="n ok">${totals.supports}</div><div class="k ok">Supports</div></div>
    <div class="cell"><div class="n warn">${totals.partial}</div><div class="k warn">Partially</div></div>
    <div class="cell"><div class="n err">${totals.unsupported}</div><div class="k err">Does Not Support</div></div>
    <div class="cell"><div class="n na">${totals.notEvaluated}</div><div class="k na">Not Evaluated</div></div>
  </div>
  <h2>Documents assessed</h2>
  <table><thead><tr><th>File</th><th>Type</th><th>Findings</th></tr></thead><tbody>${docRows}</tbody></table>
  <h2>WCAG 2.1 Level A &amp; AA — consolidated</h2>
  <div class="callout"><strong>How to read this table.</strong> Each criterion is rated by the WORST result across all ${docs.length} documents. “Supports” means no document had an automated finding for it — not a guarantee of full conformance. “Not Evaluated” criteria were not tested by this tool. A dagger (&#8224;) marks criteria where automated testing covers only part of the requirement. Only ${totals.evaluatedTotal} of ${totalCriteria} criteria are evaluated automatically; the rest require manual review by a qualified assessor.</div>
  <table><thead><tr><th>Criterion</th><th>Name</th><th>Level</th><th>Conformance</th><th>Remarks</th></tr></thead><tbody>${rows}</tbody></table>
  <p class="callout" style="background:#F6F7FB;border-left-color:#64748B;margin-top:24px">${brandingFooterHtml(branding)} · Automated accessibility assessment, not a substitute for a formal manual WCAG 2.1 / Section 508 audit · ${_esc(date)}</p>
</body></html>`;
}

function _loadBatch(batchId: string): PersistedBatch | null {
  if (Platform.OS !== "web") return null;
  try {
    const raw = window.localStorage.getItem(BATCH_KEY_PREFIX + batchId);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedBatch;
    if (!parsed || !Array.isArray(parsed.items)) return null;
    // If the page was killed mid-flight, stuck-in-progress items can't be
    // resumed (we lost the File handle), so reset them to "error" with a
    // hint rather than leaving them spinning forever.
    const items = parsed.items.map((item) =>
      item.status === "analyzing"
        ? {
            ...item,
            status: "error" as const,
            errorMessage:
              "Interrupted by page reload — re-add the file to retry.",
          }
        : item,
    );
    return { ...parsed, items };
  } catch {
    return null;
  }
}

function _saveBatch(batch: PersistedBatch): void {
  if (Platform.OS !== "web") return;
  try {
    window.localStorage.setItem(
      BATCH_KEY_PREFIX + batch.batchId,
      JSON.stringify(batch),
    );
    window.localStorage.setItem(ACTIVE_BATCH_POINTER, batch.batchId);
  } catch {
    // Quota exceeded or storage disabled — silently ignore; in-memory state
    // remains authoritative for the session.
  }
}

function _clearStoredBatch(batchId: string): void {
  if (Platform.OS !== "web") return;
  try {
    window.localStorage.removeItem(BATCH_KEY_PREFIX + batchId);
    window.localStorage.removeItem(ACTIVE_BATCH_POINTER);
  } catch {
    // ignore
  }
}

function _activeBatchId(): string | null {
  if (Platform.OS !== "web") return null;
  try {
    return window.localStorage.getItem(ACTIVE_BATCH_POINTER);
  } catch {
    return null;
  }
}

export default function BatchScreen() {
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const mockMode = useAppStore((s) => s.mockMode);

  const client = useMemo(
    () => createApiClient({ baseUrl: apiBaseUrl, mockMode }),
    [apiBaseUrl, mockMode],
  );

  const inputRef = useRef<HTMLInputElement | null>(null);

  // Lazily initialize the batch id from localStorage so a refresh keeps
  // the same key.  If the active pointer is missing or stale, mint a new id.
  const [batchId, setBatchId] = useState<string>(() => {
    const existing = _activeBatchId();
    if (existing) {
      const loaded = _loadBatch(existing);
      if (loaded) return existing;
    }
    return _newBatchId();
  });

  const [items, setItems] = useState<QueueItem[]>(() => {
    const existing = _activeBatchId();
    if (existing) {
      const loaded = _loadBatch(existing);
      if (loaded) return loaded.items;
    }
    return [];
  });

  const [isDragging, setIsDragging] = useState(false);

  // Pending files keyed by item id — File objects are not serializable so
  // they live in a ref rather than state.  The processor pulls from here.
  const pendingFilesRef = useRef<Map<string, File>>(new Map());
  // RETAINED files (not deleted after analyze) so the bulk-remediate step can
  // re-submit the originals. In-memory only — they don't survive a reload.
  const keepFilesRef = useRef<Map<string, File>>(new Map());

  // Track in-flight analyses so we can throttle to MAX_CONCURRENCY.
  const inFlightRef = useRef(0);
  const processingRef = useRef(false);

  // Bulk-remediation state.
  const [confirmRemediateOpen, setConfirmRemediateOpen] = useState(false);
  const [remediatingAll, setRemediatingAll] = useState(false);
  const [downloadingZip, setDownloadingZip] = useState(false);

  /* ---- Persistence ------------------------------------------------------- */
  useEffect(() => {
    _saveBatch({
      batchId,
      createdAt: new Date().toISOString(),
      items,
    });
  }, [batchId, items]);

  /* ---- Aggregate stats --------------------------------------------------- */
  const stats = useMemo(() => {
    const done = items.filter((i) => i.status === "done");
    const errored = items.filter((i) => i.status === "error");
    const totalIssues = done.reduce(
      (sum, i) => sum + (i.totalIssues ?? 0),
      0,
    );
    const avgScore =
      done.length > 0
        ? Math.round(
            (done.reduce((sum, i) => sum + (i.score ?? 0), 0) / done.length) *
              10,
          ) / 10
        : 0;
    return {
      done: done.length,
      errored: errored.length,
      total: items.length,
      avgScore,
      totalIssues,
    };
  }, [items]);

  /* ---- Bulk remediation derived values ---------------------------------- */
  const bulk = useMemo(() => {
    // A done item is remediatable only if we still hold its File in memory.
    const remediatable = items.filter(
      (i) => i.status === "done" && keepFilesRef.current.has(i.id),
    );
    const pendingRemediate = remediatable.filter(
      (i) => i.remediateStatus === "idle" || i.remediateStatus === "error",
    );
    const remediated = items.filter((i) => i.remediateStatus === "remediated");
    const totalCost = pendingRemediate.reduce((s, i) => s + _costFor(i.filename), 0);
    const totalApplied = remediated.reduce((s, i) => s + (i.appliedCount ?? 0), 0);
    return { remediatable, pendingRemediate, remediated, totalCost, totalApplied };
  }, [items]);

  /* ---- Bulk actions ------------------------------------------------------ */
  const remediateAll = useCallback(async () => {
    const token = loadToken();
    if (!token) {
      toast.warning("Sign in to remediate", {
        description: "Bulk remediation spends credits. Use the Sign in button, top right.",
        dedupeKey: "batch-remediate-auth",
      });
      return;
    }
    setConfirmRemediateOpen(false);
    setRemediatingAll(true);
    const queue = items.filter(
      (i) => i.status === "done" && keepFilesRef.current.has(i.id) &&
        (i.remediateStatus === "idle" || i.remediateStatus === "error"),
    );
    setItems((prev) =>
      prev.map((it) =>
        queue.find((q) => q.id === it.id)
          ? { ...it, remediateStatus: "remediating" as const, remediateError: undefined }
          : it,
      ),
    );

    const worker = async (item: QueueItem) => {
      const file = keepFilesRef.current.get(item.id);
      if (!file) {
        setItems((prev) => prev.map((it) => it.id === item.id ? { ...it, remediateStatus: "error" as const, remediateError: "File no longer in memory — re-add it." } : it));
        return;
      }
      try {
        const snap = findHistoryEntry(item.id);
        const approvedIds: string[] = snap?.snapshot?.report?.violations?.map((v: any) => v.id) ?? [];
        const result = await client.runPipelineRemediate(file, approvedIds, [], token);
        setItems((prev) => prev.map((it) => it.id === item.id ? {
          ...it,
          remediateStatus: "remediated" as const,
          appliedCount: result.writer?.applied?.length ?? 0,
          jobId: result.jobId,
          remediatedFilename: result.filename,
          downloadUrl: result.downloadUrl,
        } : it));
      } catch (e) {
        const err = e as Error & { status?: number };
        const msg = err.status === 402 ? "Out of credits" : (err.message || "Remediation failed");
        setItems((prev) => prev.map((it) => it.id === item.id ? { ...it, remediateStatus: "error" as const, remediateError: msg } : it));
      }
    };

    // Concurrency-capped runner.
    let idx = 0;
    const runNext = async () => {
      while (idx < queue.length) {
        const i = idx++;
        await worker(queue[i]);
      }
    };
    await Promise.all(Array.from({ length: Math.min(MAX_CONCURRENCY, queue.length) }, runNext));

    setRemediatingAll(false);
    if (!mockMode) void refreshAccount();
    toast.success("Batch remediation complete", {
      description: "Download all as a ZIP, or grab individual files below.",
    });
  }, [items, client, mockMode, toast]);

  const downloadAllZip = useCallback(async () => {
    const jobs = items
      .filter((i) => i.remediateStatus === "remediated" && i.jobId && i.remediatedFilename)
      .map((i) => ({ jobId: i.jobId as string, filename: i.remediatedFilename as string }));
    if (!jobs.length) {
      toast.info("Nothing to download yet", { description: "Remediate the batch first." });
      return;
    }
    setDownloadingZip(true);
    try {
      const blob = await client.batchZip(jobs, loadToken() ?? undefined);
      _saveBlob(blob, "508-remediated-batch.zip");
      toast.success(`Downloaded ${jobs.length} remediated file${jobs.length === 1 ? "" : "s"}`);
    } catch (e) {
      toast.error("Couldn't build the ZIP", { description: (e as Error).message });
    } finally {
      setDownloadingZip(false);
    }
  }, [items, client, toast]);

  const openConsolidatedReport = useCallback(() => {
    const html = _buildBatchReportHtml(items, stats, bulk.totalApplied);
    if (Platform.OS === "web" && typeof window !== "undefined") {
      const w = window.open("", "_blank");
      if (w) {
        w.document.write(html);
        w.document.close();
      } else {
        // Popup blocked — fall back to a downloaded HTML file.
        _saveBlob(new Blob([html], { type: "text/html" }), "508-assessment-report.html");
      }
    }
  }, [items, stats, bulk.totalApplied]);

  /** Download a consolidated VPAT-style conformance report across the batch. */
  const downloadBatchConformanceReport = useCallback(() => {
    if (Platform.OS !== "web") return;
    const docs = items
      .filter((i) => i.status === "done" && i.findings)
      .map((i) => ({
        filename: i.filename,
        findings: i.findings ?? [],
        score: i.score,
        sourceFormat: i.sourceFormat,
      }));
    if (docs.length === 0) {
      toast.warning("Nothing to report yet", {
        description: "Analyze at least one document first.",
        dedupeKey: "no-batch-conformance",
      });
      return;
    }
    const html = _buildBatchConformanceReportHtml(docs);
    _saveBlob(new Blob([html], { type: "text/html" }), "508-batch-conformance-report.html");
    toast.success("Conformance report downloaded", {
      description: `Consolidated WCAG 2.1 AA report across ${docs.length} document${docs.length === 1 ? "" : "s"}.`,
    });
  }, [items, toast]);

  /* ---- Concurrency-bounded processor ------------------------------------- */
  // Walks the queue, kicks off up to MAX_CONCURRENCY analyses at a time, and
  // marks each file done/error as it resolves.  We drive this off state
  // rather than a generator so adding new files mid-batch is trivial — the
  // useEffect below just calls processQueue() again.
  const processQueue = useCallback(() => {
    if (processingRef.current) return;
    processingRef.current = true;

    const tick = () => {
      // Snapshot items via setItems(prev => …) so we always see latest.
      let kicked = 0;
      setItems((prev) => {
        let next = prev;
        let changed = false;
        for (const item of prev) {
          if (inFlightRef.current >= MAX_CONCURRENCY) break;
          if (item.status !== "queued") continue;
          const file = pendingFilesRef.current.get(item.id);
          if (!file) continue;

          inFlightRef.current += 1;
          kicked += 1;
          changed = true;
          next = next.map((it) =>
            it.id === item.id ? { ...it, status: "analyzing" as const } : it,
          );

          // Fire and forget — each completion calls back into setItems.
          void runOne(item.id, file);
        }
        return changed ? next : prev;
      });

      // If we kicked something off, schedule another tick after a microtask
      // to fill remaining slots; otherwise stop.
      if (kicked > 0) {
        Promise.resolve().then(tick);
      } else {
        processingRef.current = false;
      }
    };

    const runOne = async (id: string, file: File) => {
      try {
        // execute=false: we just want the analysis report for the queue
        // overview.  The real audit screen will re-run with execute=true if
        // the user opens the entry.
        const response: PipelineResponse = await client.runPipeline(
          file,
          false,
        );
        const finishedAt = new Date().toISOString();
        // Push to the shared audit history so /audit?historyId=<id> works.
        try {
          appendHistory({
            id,
            filename: file.name,
            ranAt: finishedAt,
            score: response.score.score,
            grade: response.score.grade,
            totalIssues: response.violations.length,
            sourceFormat: response.summary.sourceFormat,
            approved: 0,
            rejected: 0,
            pending: response.violations.length,
            snapshot: { report: response, decisions: {}, decisionLog: [] },
          });
        } catch {
          // History write is best-effort — don't fail the queue item over it.
        }
        setItems((prev) =>
          prev.map((it) =>
            it.id === id
              ? {
                  ...it,
                  status: "done",
                  score: response.score.score,
                  grade: response.score.grade,
                  totalIssues: response.violations.length,
                  sourceFormat: response.summary.sourceFormat,
                  finishedAt,
                  // Compact per-criterion inputs for the consolidated VPAT report.
                  findings: response.violations.map((v) => ({ ruleId: v.ruleId, severity: v.severity })),
                }
              : it,
          ),
        );
      } catch (e) {
        const err = e as Error & { status?: number };
        let msg = err.message ?? "Analyzer failed";
        // Anonymous batch runs hit the API's auth requirement — show a human
        // sentence instead of raw 401 JSON, and prompt sign-in once.
        if (err.status === 401 || msg.includes("authentication_required")) {
          msg = "Sign in to run batch audits — use the Sign in button in the top right.";
          toast.info("Create a free account to run batches", {
            description: "Your first audits are on us (25 free credits).",
            dedupeKey: "batch-auth-required",
          });
        }
        setItems((prev) =>
          prev.map((it) =>
            it.id === id
              ? {
                  ...it,
                  status: "error",
                  errorMessage: msg,
                  finishedAt: new Date().toISOString(),
                }
              : it,
          ),
        );
      } finally {
        pendingFilesRef.current.delete(id);
        inFlightRef.current = Math.max(0, inFlightRef.current - 1);
        // Try to kick off the next queued file.
        processingRef.current = false;
        processQueue();
      }
    };

    tick();
  }, [client]);

  // Whenever we add files, prod the processor.
  useEffect(() => {
    if (items.some((i) => i.status === "queued")) {
      processQueue();
    }
  }, [items, processQueue]);

  /* ---- File intake ------------------------------------------------------- */
  const addFiles = useCallback(
    (files: File[]) => {
      if (!files.length) return;
      const accepted: QueueItem[] = [];
      let rejected = 0;
      for (const file of files) {
        if (!/\.(pdf|docx|pptx)$/i.test(file.name)) {
          rejected += 1;
          continue;
        }
        const id = `${batchId}-${Date.now()}-${Math.random()
          .toString(36)
          .slice(2, 8)}-${file.name}`;
        pendingFilesRef.current.set(id, file);
        keepFilesRef.current.set(id, file); // retained for bulk remediate
        accepted.push({
          id,
          filename: file.name,
          sizeBytes: file.size,
          status: "queued",
          remediateStatus: "idle",
        });
      }
      if (rejected > 0) {
        toast.warning(
          `Skipped ${rejected} unsupported file${rejected === 1 ? "" : "s"}`,
          { description: "Only PDF, DOCX, and PPTX are accepted." },
        );
      }
      if (!accepted.length) return;
      setItems((prev) => [...prev, ...accepted]);
      toast.info(
        `Queued ${accepted.length} file${accepted.length === 1 ? "" : "s"}`,
        { description: `Analyzing up to ${MAX_CONCURRENCY} at a time.` },
      );
    },
    [batchId, toast],
  );

  const handlePick = useCallback(() => {
    if (Platform.OS === "web" && inputRef.current) {
      inputRef.current.click();
    }
  }, []);

  /* ---- Multi-file drag-and-drop ----------------------------------------- */
  // useFileDrop only takes a single file; we want all of them, so the
  // multi-file logic is inlined here.
  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (typeof window === "undefined") return;

    let depth = 0;
    const hasFiles = (e: DragEvent) => {
      const types = e.dataTransfer?.types;
      if (!types) return false;
      for (let i = 0; i < types.length; i += 1) {
        if (types[i] === "Files") return true;
      }
      return false;
    };

    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      if (depth === 1) setIsDragging(true);
    };
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = Math.max(0, depth - 1);
      if (depth === 0) setIsDragging(false);
    };
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setIsDragging(false);
      const dropped = Array.from(e.dataTransfer?.files ?? []);
      if (dropped.length) addFiles(dropped);
    };

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [addFiles]);

  /* ---- Reset ------------------------------------------------------------- */
  const clearBatch = useCallback(() => {
    pendingFilesRef.current.clear();
    inFlightRef.current = 0;
    processingRef.current = false;
    _clearStoredBatch(batchId);
    const fresh = _newBatchId();
    setBatchId(fresh);
    setItems([]);
    toast.info("Batch cleared");
  }, [batchId, toast]);

  /* ---- Render ============================================================ */
  const styles = createStyles(theme);
  const inFlight = items.filter((i) => i.status === "analyzing").length;
  const queued = items.filter((i) => i.status === "queued").length;

  return (
    <Screen scroll title="Batch audit">
      {isDragging ? (
        <View
          pointerEvents="none"
          style={[
            styles.dropOverlay,
            { backgroundColor: theme.colors.accent + "DD" },
          ]}
        >
          <Text style={styles.dropOverlayText}>Drop to queue</Text>
          <Text style={styles.dropOverlaySub}>
            Multiple files OK · PDF · DOCX · PPTX
          </Text>
        </View>
      ) : null}

      <Hero
        shader="pumpkin"
        eyebrow="BATCH"
        title="Batch audit"
        subtitle={`Drop multiple documents and we'll analyze them in parallel - up to ${MAX_CONCURRENCY} at once. Each finished audit lands in your history.`}
      />

      {/* === File picker / drop zone ====================================== */}
      <Card>
        <View style={styles.row}>
          <Button
            title={items.length ? "Add more files" : "Choose files"}
            onPress={handlePick}
          />
          {items.length > 0 ? (
            <Button
              title="Clear batch"
              variant="ghost"
              onPress={clearBatch}
            />
          ) : null}
          <Chip
            label={`Concurrency: ${MAX_CONCURRENCY}`}
            tone="info"
          />
          <Chip label={`Batch id: ${batchId.slice(0, 8)}…`} tone="default" />
        </View>
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted, marginTop: theme.spacing.xs },
          ]}
        >
          Drop a folder of documents anywhere on this page, or click Choose
          files to multi-select. PDF, DOCX, PPTX accepted.
        </Text>
        {Platform.OS === "web" ? (
          // @ts-ignore — RN-Web supports a hidden file input
          <input
            ref={(el) => {
              inputRef.current = el;
            }}
            type="file"
            multiple
            accept={ACCEPTED_FILE_TYPES}
            style={{ display: "none" }}
            onChange={(event: any) => {
              const fl: FileList | undefined = event?.target?.files;
              if (fl && fl.length) {
                addFiles(Array.from(fl));
                event.target.value = "";
              }
            }}
          />
        ) : (
          <InlineNotice
            tone="info"
            title="Web only for now"
            message="Native multi-file pickers aren't wired up yet. Open this in a browser."
          />
        )}
      </Card>

      {/* === Queue ======================================================== */}
      {items.length === 0 ? (
        <EmptyState
          icon="doc"
          title="No documents queued yet"
          message="Drop a folder of documents anywhere on this page, or click the button above to multi-select files."
          actionLabel="Choose files"
          onAction={handlePick}
        />
      ) : (
        <Card>
          {/* --- Aggregate stats bar --- */}
          <View style={styles.statsBar}>
            <Text
              style={[theme.typography.h2, { color: theme.colors.text }]}
            >
              {stats.done} of {stats.total} done
            </Text>
            <View style={styles.statChips}>
              <Chip
                label={`avg score: ${
                  stats.done > 0 ? stats.avgScore.toFixed(1) : "—"
                }`}
                tone={
                  stats.done === 0
                    ? "default"
                    : stats.avgScore >= 90
                    ? "success"
                    : stats.avgScore >= 70
                    ? "warning"
                    : "danger"
                }
              />
              <Chip
                label={`total issues: ${stats.totalIssues}`}
                tone="default"
              />
              {inFlight > 0 ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <PixelSpinner size={3} />
                  <Chip label={`analyzing: ${inFlight}`} tone="info" />
                </View>
              ) : null}
              {queued > 0 ? (
                <Chip label={`queued: ${queued}`} tone="default" />
              ) : null}
              {stats.errored > 0 ? (
                <Chip
                  label={`errored: ${stats.errored}`}
                  tone="danger"
                />
              ) : null}
            </View>
          </View>

          <View
            style={[styles.divider, { backgroundColor: theme.colors.border }]}
          />

          {/* --- Bulk actions (remediate / download / report) --- */}
          {stats.done > 0 ? (
            <View style={[styles.bulkBar, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
              <View style={{ flex: 1, minWidth: 200 }}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                  Bulk remediation
                </Text>
                <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }]}>
                  {bulk.remediated.length > 0
                    ? `${bulk.remediated.length} remediated · ${bulk.totalApplied} fix(es) applied${bulk.pendingRemediate.length > 0 ? ` · ${bulk.pendingRemediate.length} left` : ""}`
                    : keepFilesRef.current.size === 0
                      ? "Re-add the files (reload cleared them) to remediate."
                      : `Apply fixes to all ${bulk.pendingRemediate.length} document(s) · ${bulk.totalCost} credits`}
                </Text>
              </View>
              <View style={styles.bulkBtns}>
                <Button
                  title={remediatingAll ? "Remediating…" : `Remediate all (${bulk.totalCost} cr)`}
                  onPress={() => setConfirmRemediateOpen(true)}
                  loading={remediatingAll}
                  disabled={remediatingAll || bulk.pendingRemediate.length === 0 || mockMode}
                />
                <Button
                  title={downloadingZip ? "Zipping…" : "Download all (ZIP)"}
                  onPress={downloadAllZip}
                  loading={downloadingZip}
                  disabled={bulk.remediated.length === 0}
                  variant="secondary"
                />
                <Button
                  title="Assessment report"
                  onPress={openConsolidatedReport}
                  variant="ghost"
                />
                <Button
                  title="Conformance report (VPAT)"
                  onPress={downloadBatchConformanceReport}
                  variant="ghost"
                  accessibilityHint="Downloads one consolidated WCAG 2.1 AA conformance report covering every analyzed document in this batch."
                />
              </View>
            </View>
          ) : null}

          {/* --- Rows --- */}
          <View style={styles.queueList}>
            {items.map((item) => (
              <BatchRow
                key={item.id}
                item={item}
                onOpen={() =>
                  router.push(
                    `/audit?historyId=${encodeURIComponent(item.id)}` as any,
                  )
                }
              />
            ))}
          </View>
        </Card>
      )}

      <Dialog
        open={confirmRemediateOpen}
        title="Remediate the whole batch?"
        message={
          `This applies the available auto-fixes to ${bulk.pendingRemediate.length} document(s) and produces a remediated copy of each. It spends ${bulk.totalCost} credits (PDF 5, Word 3, PowerPoint 4 per file). Findings that need human judgment are queued for manual review, not silently claimed.`
        }
        confirmLabel={`Charge ${bulk.totalCost} & remediate`}
        cancelLabel="Cancel"
        onConfirm={remediateAll}
        onCancel={() => setConfirmRemediateOpen(false)}
      />
    </Screen>
  );
}

/* ====================================================================== *
 * BatchRow — one document in the queue.                                  *
 * ====================================================================== */

function BatchRow({
  item,
  onOpen,
}: {
  item: QueueItem;
  onOpen: () => void;
}) {
  const theme = useTheme();
  const styles = createStyles(theme);

  return (
    <View
      style={[styles.row_item, { borderColor: theme.colors.border }]}
    >
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text
          numberOfLines={1}
          style={[styles.filename, { color: theme.colors.text }]}
        >
          {item.filename}
        </Text>
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted },
          ]}
        >
          {_formatBytes(item.sizeBytes)}
          {item.sourceFormat ? ` · ${item.sourceFormat}` : ""}
          {item.status === "error" && item.errorMessage
            ? ` · ${item.errorMessage}`
            : ""}
        </Text>
      </View>

      <View style={styles.row_chips}>
        {item.status === "queued" ? (
          <Chip label="queued" tone="default" />
        ) : null}
        {item.status === "analyzing" ? <PulsingChip /> : null}
        {item.status === "error" ? <Chip label="error" tone="danger" /> : null}
        {item.status === "done" && item.score !== undefined ? (
          <>
            <Chip
              label={`score ${item.score.toFixed(1)}`}
              tone={
                item.score >= 90
                  ? "success"
                  : item.score >= 70
                  ? "warning"
                  : "danger"
              }
            />
            <Chip
              label={item.grade ?? ""}
              tone={
                item.score >= 90
                  ? "success"
                  : item.score >= 70
                  ? "warning"
                  : "danger"
              }
            />
            <Chip
              label={`${item.totalIssues ?? 0} issue${
                (item.totalIssues ?? 0) === 1 ? "" : "s"
              }`}
              tone="default"
            />
            {item.remediateStatus === "remediating" ? (
              <Chip label="remediating…" tone="info" />
            ) : item.remediateStatus === "remediated" ? (
              <Chip label={`fixed · ${item.appliedCount ?? 0}`} tone="success" />
            ) : item.remediateStatus === "error" ? (
              <Chip label={item.remediateError || "fix failed"} tone="danger" />
            ) : null}
            {item.remediateStatus === "remediated" && item.downloadUrl ? (
              <Pressable
                onPress={() => {
                  if (Platform.OS === "web" && item.downloadUrl) {
                    const a = document.createElement("a");
                    a.href = item.downloadUrl;
                    if (item.remediatedFilename) a.download = item.remediatedFilename;
                    a.rel = "noopener";
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                  }
                }}
                accessibilityRole="button"
                accessibilityLabel={`Download remediated ${item.filename}`}
                style={({ hovered }: any) => [
                  styles.openButton,
                  { backgroundColor: "transparent", borderWidth: 1, borderColor: theme.colors.border },
                  hovered ? { opacity: 0.8 } : null,
                ]}
              >
                <Text style={[styles.openButtonText, { color: theme.colors.text }]}>Download</Text>
              </Pressable>
            ) : null}
            <Pressable
              onPress={onOpen}
              accessibilityRole="button"
              accessibilityLabel={`Open audit for ${item.filename}`}
              style={[
                styles.openButton,
                {
                  backgroundColor: theme.colors.accent,
                },
              ]}
            >
              <Text style={styles.openButtonText}>Open audit</Text>
            </Pressable>
          </>
        ) : null}
      </View>
    </View>
  );
}

/* PulsingChip — analyzing-state indicator with a slow pulse on the dot. */
function PulsingChip() {
  const theme = useTheme();
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    if (Platform.OS !== "web") return;
    const id = window.setInterval(() => setPhase((p) => (p + 1) % 3), 450);
    return () => window.clearInterval(id);
  }, []);

  const dots = ".".repeat(phase + 1);
  return (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: theme.spacing.sm,
        paddingVertical: theme.spacing.xs,
        borderRadius: theme.radius.sm,
        borderWidth: 1,
        borderColor: theme.colors.info,
        backgroundColor: "rgba(14, 165, 233, 0.16)",
      }}
    >
      <View
        style={{
          width: 8,
          height: 8,
          borderRadius: 4,
          backgroundColor: theme.colors.info,
          opacity: 0.5 + phase * 0.2,
        }}
      />
      <Text
        style={{
          fontSize: 12,
          fontWeight: "600",
          color: theme.colors.info,
        }}
      >
        analyzing{dots}
      </Text>
    </View>
  );
}

/* ---- helpers ----------------------------------------------------------- */

function _formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    row: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.sm,
      flexWrap: "wrap",
    },
    statsBar: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.md,
      flexWrap: "wrap",
    },
    statChips: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.xs,
      flexWrap: "wrap",
      marginLeft: "auto",
    },
    divider: {
      height: 1,
      width: "100%",
      marginVertical: theme.spacing.md,
    },
    bulkBar: {
      flexDirection: "row",
      flexWrap: "wrap",
      alignItems: "center",
      gap: 12,
      borderWidth: 1,
      borderRadius: 12,
      padding: 14,
      marginBottom: theme.spacing.md,
    },
    bulkBtns: {
      flexDirection: "row",
      flexWrap: "wrap",
      gap: 8,
      alignItems: "center",
    },
    queueList: {
      gap: theme.spacing.sm,
    },
    row_item: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.md,
      paddingVertical: theme.spacing.sm,
      paddingHorizontal: theme.spacing.md,
      borderWidth: 1,
      borderRadius: theme.radius.md,
      flexWrap: "wrap",
    },
    row_chips: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.xs,
      marginLeft: "auto",
      flexWrap: "wrap",
    },
    filename: {
      ...theme.typography.body,
      fontWeight: "600",
    },
    openButton: {
      paddingHorizontal: theme.spacing.sm,
      paddingVertical: theme.spacing.xs,
      borderRadius: theme.radius.sm,
    },
    openButtonText: {
      color: "#FFFFFF",
      fontSize: 12,
      fontWeight: "700",
    },
    dropOverlay: {
      position: "absolute" as any,
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      alignItems: "center",
      justifyContent: "center",
      zIndex: 999,
    },
    dropOverlayText: {
      color: "#FFFFFF",
      fontSize: 32,
      fontWeight: "800",
    },
    dropOverlaySub: {
      color: "#FFFFFF",
      fontSize: 14,
      opacity: 0.85,
      marginTop: 6,
    },
  });
