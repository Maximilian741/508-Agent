/**
 * Audit screen — primary remediator workflow.
 *
 * One page, four numbered steps, sequential issue review.  This rewrite adds
 * pro-user controls on top of the v1 layout:
 *
 *   • Keyboard shortcuts: j/k navigate, a approve, r reject, e edit,
 *     u/Ctrl+Z undo, ? help.
 *   • Undo stack with a tail-of-five Decision Log so the user can see what
 *     just happened.
 *   • Severity / decision filters that re-slice the queue without losing
 *     individual decisions.
 *   • Toast feedback on every decision (visible from any state).
 *   • Audit history persistence — every completed audit goes into
 *     localStorage so the home page can list recent work.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  PipelineExecutionResult,
  PipelineResponse,
  PipelineViolation,
  createApiClient,
} from "../src/api/client";
import { useLocalSearchParams } from "expo-router";

import { clearDraft, loadDraft, saveDraft } from "../src/domain/auditDraft";
import { appendHistory, findHistoryEntry } from "../src/domain/auditHistory";
import { loadWeights } from "../src/domain/scoreWeights";
import { lookupIssue } from "../src/domain/issueCatalog";
import { notify, playChime } from "../src/domain/notifications";
import { SAMPLE_DOCUMENTS, SampleDocument } from "../src/domain/sampleDocuments";
import { useFileDrop } from "../src/hooks/useFileDrop";
import { useKeyboardShortcuts } from "../src/hooks/useKeyboardShortcuts";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Dialog } from "../src/ui/components/Dialog";
import { DiffViewer } from "../src/ui/components/DiffViewer";
import { Divider } from "../src/ui/components/Divider";
import { EmptyState } from "../src/ui/components/EmptyState";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { ScoreBadge } from "../src/ui/components/ScoreBadge";
import { SeverityHeatmap } from "../src/ui/components/SeverityHeatmap";
import { IssueNavigator } from "../src/ui/components/IssueNavigator";
import { PdfPreview } from "../src/ui/components/PdfPreview";
import { Skeleton, SkeletonBlock } from "../src/ui/components/Skeleton";
import { UncertaintyChip } from "../src/ui/components/UncertaintyChip";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

const ACCEPTED_FILE_TYPES = [
  ".pdf",
  ".docx",
  ".pptx",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
].join(",");

type Decision = "pending" | "approved" | "rejected";
type SeverityFilter = "all" | "error" | "warning" | "info";
type DecisionFilter = "all" | "pending" | "approved" | "rejected";

interface IssueState {
  decision: Decision;
  customText?: string;
  /**
   * Optional reviewer note attached to this decision.  Persisted with the
   * draft and exported in JSON / HTML / CSV reports — useful for audit-log
   * narrative ("approved with caveat: pending designer's confirmation").
   */
  note?: string;
  decidedAt?: string;
}

interface DecisionLogItem {
  /** The violation id this entry refers to. */
  violationId: string;
  /** Plain-language title for the log row. */
  title: string;
  /** Decision applied. */
  decision: Decision;
  /** ISO timestamp. */
  at: string;
  /** Previous state for undo. */
  previous: IssueState;
}

export default function AuditScreen() {
  const theme = useTheme();
  const toast = useToast();

  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const setMockMode = useAppStore((state) => state.setMockMode);

  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [restoredFromDraft, setRestoredFromDraft] = useState(false);
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const lastScoreRef = useRef<number>(0);
  const [downloadingFixed, setDownloadingFixed] = useState(false);
  const [fixedDownloadUrl, setFixedDownloadUrl] = useState<string | null>(null);
  const [lastRemediation, setLastRemediation] = useState<
    | {
        applied: Array<{ kind: string; target_id: string; summary: string }>;
        skipped: Array<{ target_id: string; reason: string }>;
      }
    | null
  >(null);
  const [report, setReport] = useState<PipelineResponse | null>(null);
  const [decisions, setDecisions] = useState<Record<string, IssueState>>({});
  const [decisionLog, setDecisionLog] = useState<DecisionLogItem[]>([]);
  const [reviewIndex, setReviewIndex] = useState(0);
  const [previousScore, setPreviousScore] = useState(0);
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [decisionFilter, setDecisionFilter] = useState<DecisionFilter>("all");
  const [showHelp, setShowHelp] = useState(false);
  const [editing, setEditing] = useState(false);

  const client = useMemo(
    () => createApiClient({ baseUrl: apiBaseUrl, mockMode }),
    [apiBaseUrl, mockMode],
  );

  /* ---- Filtered queue ---------------------------------------------------- */
  const filteredViolations = useMemo<PipelineViolation[]>(() => {
    if (!report) return [];
    return report.violations.filter((v) => {
      if (severityFilter !== "all" && v.severity !== severityFilter) return false;
      const decision = decisions[v.id]?.decision ?? "pending";
      if (decisionFilter !== "all" && decision !== decisionFilter) return false;
      return true;
    });
  }, [report, severityFilter, decisionFilter, decisions]);

  // When a filter change leaves the active issue out of the queue, clamp.
  useEffect(() => {
    if (reviewIndex >= filteredViolations.length) {
      setReviewIndex(Math.max(filteredViolations.length - 1, 0));
    }
  }, [filteredViolations.length, reviewIndex]);

  const params = useLocalSearchParams<{ historyId?: string | string[] }>();
  const historyId = Array.isArray(params.historyId) ? params.historyId[0] : params.historyId;

  // Restore draft on first mount, or restore from a specific history entry
  // when the user opened /audit?historyId=…
  useEffect(() => {
    if (historyId) {
      const entry = findHistoryEntry(historyId);
      if (entry?.snapshot) {
        setReport(entry.snapshot.report);
        setDecisions(entry.snapshot.decisions);
        setDecisionLog(entry.snapshot.decisionLog);
        setReviewIndex(0);
        setFilename(entry.filename);
        setRestoredFromDraft(true);
        toast.info(`Reopened ${entry.filename}`, {
          description: "Decisions and findings preserved from your last session.",
        });
        return;
      }
    }
    const draft = loadDraft();
    if (!draft) return;
    setReport(draft.report);
    setDecisions(draft.decisions);
    setDecisionLog(draft.decisionLog);
    setReviewIndex(draft.reviewIndex);
    setFilename(draft.filename);
    setRestoredFromDraft(true);
    toast.info("Restored your previous audit", {
      description: `${draft.filename} · ${
        Object.values(draft.decisions).filter((d) => d.decision !== "pending").length
      } prior decision(s) preserved.`,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyId]);

  // Track the previously-displayed score so the count-up animation has
  // a real "from" value when the user makes a decision.
  useEffect(() => {
    if (liveScore) {
      lastScoreRef.current = liveScore.score;
    }
  }, [liveScore?.score]);

  // Auto-save draft whenever the audit state changes meaningfully.
  useEffect(() => {
    if (!report || !filename) return;
    saveDraft({
      filename,
      report,
      decisions,
      decisionLog,
      reviewIndex,
      savedAt: new Date().toISOString(),
    });
  }, [report, filename, decisions, decisionLog, reviewIndex]);

  // Periodically refresh the history entry's decision counts + snapshot so a
  // user who closes the tab and re-opens the home page sees an accurate view.
  useEffect(() => {
    if (!report || !filename || !liveScore) return;
    const handle = window.setTimeout(() => {
      try {
        appendHistory({
          id: `${filename}-${reviewIndex < 0 ? "new" : "live"}`,
          filename,
          ranAt: new Date().toISOString(),
          score: liveScore.score,
          grade: liveScore.grade,
          totalIssues: report.violations.length,
          sourceFormat: report.summary.sourceFormat,
          approved: decisionCounts.approved,
          rejected: decisionCounts.rejected,
          pending: decisionCounts.pending,
          snapshot: { report, decisions, decisionLog: decisionLog.slice(0, 10) },
        });
      } catch {
        // ignore
      }
    }, 600);
    return () => window.clearTimeout(handle);
  }, [report, filename, liveScore, decisionCounts, decisions, decisionLog, reviewIndex]);

  const currentViolation: PipelineViolation | null = filteredViolations[reviewIndex] ?? null;
  const totalIssues = report?.violations.length ?? 0;
  const reviewedCount = useMemo(
    () =>
      Object.values(decisions).filter(
        (s) => s.decision === "approved" || s.decision === "rejected",
      ).length,
    [decisions],
  );

  const buckets = useMemo(() => {
    let errors = 0;
    let warnings = 0;
    let infos = 0;
    for (const v of report?.violations ?? []) {
      if (v.severity === "error") errors += 1;
      else if (v.severity === "warning") warnings += 1;
      else infos += 1;
    }
    return { errors, warnings, infos };
  }, [report]);

  const decisionCounts = useMemo(() => {
    let approved = 0;
    let rejected = 0;
    let pending = totalIssues;
    for (const s of Object.values(decisions)) {
      if (s.decision === "approved") {
        approved += 1;
        pending -= 1;
      } else if (s.decision === "rejected") {
        rejected += 1;
        pending -= 1;
      }
    }
    return { approved, rejected, pending: Math.max(pending, 0) };
  }, [decisions, totalIssues]);

  /**
   * Live score derived from the user's decisions.
   *
   * Each violation is worth points by severity (errors=2, warnings=1, info=0).
   * We start at 100 and subtract the unhandled-weight as a fraction of total
   * weight: approving a fix removes its weight (the fix will be applied).
   * Pending and rejected items keep their weight (the issue remains).
   *
   * This means the score moves as the user clicks Approve / Reject, instead
   * of being a frozen number from the analyzer's pre-execution view.
   */
  const liveScore = useMemo(() => {
    if (!report) return null;
    const weights = loadWeights();
    let totalWeight = 0;
    let outstanding = 0;
    let approvedWeight = 0;
    for (const v of report.violations) {
      const w =
        v.severity === "error"
          ? weights.error
          : v.severity === "warning"
          ? weights.warning
          : weights.info;
      totalWeight += w;
      const decision = decisions[v.id]?.decision ?? "pending";
      if (decision === "approved") {
        approvedWeight += w;
      } else {
        outstanding += w;
      }
    }
    if (totalWeight === 0) {
      return { score: 100, grade: _gradeFor(100), totalWeight: 0, outstanding: 0, approvedWeight: 0 };
    }
    const score = Math.max(0, Math.min(100, 100 * (1 - outstanding / totalWeight)));
    return {
      score: Math.round(score * 10) / 10,
      grade: _gradeFor(score),
      totalWeight,
      outstanding,
      approvedWeight,
    };
  }, [report, decisions]);

  /* ---- Pick file --------------------------------------------------------- */
  const handlePick = useCallback(() => {
    if (Platform.OS === "web" && inputRef.current) {
      inputRef.current.click();
    }
  }, []);

  const loadSample = useCallback(
    (sample: SampleDocument) => {
      setBusy(false);
      setError(null);
      setFilename(`${sample.title} (sample)`);
      setDecisions({});
      setDecisionLog([]);
      setReviewIndex(0);
      setEditing(false);
      const response = sample.buildResponse();
      setPreviousScore(report?.score.score ?? 0);
      setReport(response);
      toast.info(`Loaded sample: ${sample.title}`, {
        description: "Findings are pre-baked — no analyzer call. Try the keyboard shortcuts!",
      });
    },
    [report, toast],
  );

  const handleFile = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      setFilename(file.name);
      setSourceFile(file);
      setFixedDownloadUrl(null);
      setRestoredFromDraft(false);
      clearDraft();
      setDecisions({});
      setDecisionLog([]);
      setReviewIndex(0);
      setEditing(false);
      try {
        const response = await client.runPipeline(file, true);
        setPreviousScore(report?.score.score ?? 0);
        setReport(response);
        appendHistory({
          id: `${file.name}-${Date.now()}`,
          filename: file.name,
          ranAt: new Date().toISOString(),
          score: response.score.score,
          grade: response.score.grade,
          totalIssues: response.violations.length,
          sourceFormat: response.summary.sourceFormat,
          approved: 0,
          rejected: 0,
          pending: response.violations.length,
        });
        toast.success(`Audit complete — ${response.score.grade}`, {
          description: `${response.violations.length} finding${
            response.violations.length === 1 ? "" : "s"
          } across ${response.summary.pageCount} page(s).`,
        });
        notify(`Audit complete — ${response.score.grade}`, file.name);
        playChime();
      } catch (e) {
        const msg = (e as Error).message ?? "Couldn't analyze that file.";
        setError(msg);
        toast.error("Audit failed", { description: msg });
      } finally {
        setBusy(false);
      }
    },
    [client, report, toast],
  );

  /* ---- Decisions / undo --------------------------------------------------- */
  // Use functional setState so the callback captures only `toast` (stable
  // ref); `decisions` is read off the latest state inside the updater.
  const decide = useCallback(
    (violation: PipelineViolation, next: Decision, customText?: string) => {
      const at = new Date().toISOString();
      const catalog = lookupIssue(violation.ruleId);
      setDecisions((prev) => {
        const previous = prev[violation.id] ?? { decision: "pending" as Decision };
        // Stash the previous state on the log inside this updater so undo
        // works correctly even if the user mashes keys quickly.
        setDecisionLog((logPrev) =>
          [
            {
              violationId: violation.id,
              title: catalog.title,
              decision: next,
              at,
              previous,
            },
            ...logPrev,
          ].slice(0, 25),
        );
        return {
          ...prev,
          [violation.id]: { decision: next, customText, decidedAt: at },
        };
      });
      if (next === "approved") {
        toast.success(`Approved: ${catalog.title}`);
      } else if (next === "rejected") {
        toast.warning(`Rejected: ${catalog.title}`, { description: "Will queue for manual review." });
      }
    },
    [toast],
  );

  const bulkDecide = useCallback(
    (
      next: Decision,
      filterFn: (v: PipelineViolation) => boolean,
      label: string,
    ) => {
      if (!report) return;
      const at = new Date().toISOString();
      let count = 0;
      const newLog: DecisionLogItem[] = [];
      setDecisions((prev) => {
        const result = { ...prev };
        for (const v of report.violations) {
          if (!filterFn(v)) continue;
          const previous = prev[v.id] ?? { decision: "pending" as Decision };
          if (previous.decision === next) continue;
          result[v.id] = { decision: next, decidedAt: at };
          count += 1;
          const catalog = lookupIssue(v.ruleId);
          newLog.unshift({
            violationId: v.id,
            title: catalog.title,
            decision: next,
            at,
            previous,
          });
        }
        return result;
      });
      if (count === 0) {
        toast.info(`Nothing to ${next}`, { description: label });
        return;
      }
      setDecisionLog((prev) => [...newLog.reverse(), ...prev].slice(0, 25));
      toast.success(
        next === "approved"
          ? `Approved ${count} item${count === 1 ? "" : "s"}`
          : `Rejected ${count} item${count === 1 ? "" : "s"}`,
        { description: label + " · undo with Ctrl+Z (one step at a time)" },
      );
    },
    [report, toast],
  );

  const resetDecisions = useCallback(() => {
    if (!Object.keys(decisions).length) {
      toast.info("Nothing to reset");
      return;
    }
    setDecisions({});
    setDecisionLog([]);
    toast.info("All decisions reset");
  }, [decisions, toast]);

  const undoLast = useCallback(() => {
    setDecisionLog((prev) => {
      const [head, ...rest] = prev;
      if (!head) {
        toast.info("Nothing to undo");
        return prev;
      }
      setDecisions((d) => {
        const next = { ...d };
        if (head.previous.decision === "pending") {
          delete next[head.violationId];
        } else {
          next[head.violationId] = head.previous;
        }
        return next;
      });
      toast.info(`Undid: ${head.title}`);
      return rest;
    });
  }, [toast]);

  /* ---- Navigation -------------------------------------------------------- */
  const next = useCallback(() => {
    setEditing(false);
    setReviewIndex((i) => Math.min(i + 1, Math.max(filteredViolations.length - 1, 0)));
  }, [filteredViolations.length]);

  const prev = useCallback(() => {
    setEditing(false);
    setReviewIndex((i) => Math.max(i - 1, 0));
  }, []);

  const downloadRemediated = useCallback(async () => {
    if (mockMode) {
      toast.warning("Demo mode", {
        description: "Switch off Demo Mode in Settings, then re-run the audit on a real file to get a fixed download.",
      });
      return;
    }
    if (!sourceFile) {
      toast.warning("No source file", {
        description: "Pick a real document above before requesting a remediated file.",
      });
      return;
    }
    if (!report) {
      toast.warning("No audit yet", {
        description: "Run an audit first so we know which fixes to apply.",
      });
      return;
    }
    setDownloadingFixed(true);
    try {
      const approvedIds = Object.entries(decisions)
        .filter(([, s]) => s.decision === "approved")
        .map(([id]) => id);
      const rejectedIds = Object.entries(decisions)
        .filter(([, s]) => s.decision === "rejected")
        .map(([id]) => id);
      const result = await client.runPipelineRemediate(sourceFile, approvedIds, rejectedIds);
      const fullUrl = client.getPipelineFileUrl(result.jobId, result.filename);
      setFixedDownloadUrl(fullUrl);
      setLastRemediation(result.writer);
      toast.success("Remediated file ready", {
        description: `${result.writer.applied.length} change(s) baked in · ${result.writer.skipped.length} skipped.${
          result.manualReviewItemsCreated > 0
            ? ` ${result.manualReviewItemsCreated} item(s) queued for manual review.`
            : ""
        }`,
      });
      if (Platform.OS === "web") {
        window.open(fullUrl, "_blank");
      }
    } catch (e) {
      const msg = (e as Error).message ?? "Download failed";
      toast.error("Couldn't produce remediated file", { description: msg });
    } finally {
      setDownloadingFixed(false);
    }
  }, [client, decisions, mockMode, report, sourceFile, toast]);

  /* ---- Keyboard shortcuts ------------------------------------------------- */
  useKeyboardShortcuts(
    [
      { key: "j", label: "j", description: "Next issue", run: next },
      { key: "k", label: "k", description: "Previous issue", run: prev },
      {
        key: "a",
        label: "a",
        description: "Approve current issue",
        run: () => currentViolation && decide(currentViolation, "approved"),
      },
      {
        key: "r",
        label: "r",
        description: "Reject current issue",
        run: () => currentViolation && decide(currentViolation, "rejected"),
      },
      {
        key: "e",
        label: "e",
        description: "Edit current suggestion",
        run: () => setEditing(true),
      },
      { key: "u", label: "u", description: "Undo last decision", run: undoLast },
      {
        key: "z",
        ctrlOrCmd: true,
        label: "Ctrl+Z",
        description: "Undo last decision",
        run: undoLast,
      },
      {
        key: "?",
        label: "?",
        description: "Toggle keyboard help",
        run: () => setShowHelp((s) => !s),
      },
      {
        key: "/",
        label: "/",
        description: "Toggle keyboard help",
        run: () => setShowHelp((s) => !s),
      },
    ],
    [currentViolation, next, prev, decide, undoLast],
  );

  // Window-wide drag-and-drop file pickup.
  const { isDragging } = useFileDrop((file) => {
    const okExt = /\.(pdf|docx|pptx)$/i.test(file.name);
    if (!okExt) {
      toast.error("Unsupported file type", {
        description: "Drop a .pdf, .docx, or .pptx file.",
      });
      return;
    }
    void handleFile(file);
  });

  /* ---- Render ============================================================ */
  const noBackend = !mockMode && backendHealth === "error";

  return (
    <Screen scroll title="Audit">
      {isDragging ? (
        <View
          pointerEvents="none"
          style={[
            styles.dropOverlay,
            { backgroundColor: theme.colors.accent + "DD" },
          ]}
        >
          <Text style={styles.dropOverlayText}>Drop to audit</Text>
          <Text style={styles.dropOverlaySub}>PDF · DOCX · PPTX accepted</Text>
        </View>
      ) : null}
      {showHelp ? <KeyboardHelpOverlay onClose={() => setShowHelp(false)} /> : null}
      <Dialog
        open={resetDialogOpen}
        title="Reset all decisions?"
        message={`This clears every approve/reject for the current audit. The decision log will be cleared too. ${
          reviewedCount > 0 ? `You have ${reviewedCount} review${reviewedCount === 1 ? "" : "s"} that will be lost.` : ""
        }`}
        confirmLabel="Reset everything"
        cancelLabel="Keep decisions"
        destructive
        onConfirm={() => {
          resetDecisions();
          setResetDialogOpen(false);
        }}
        onCancel={() => setResetDialogOpen(false)}
      />
      {restoredFromDraft && report ? (
        <InlineNotice
          tone="info"
          title="Restored your previous audit"
          message="You picked up where you left off — decisions, log, and review position are preserved. Drop a new file (or click Choose) to start fresh."
        />
      ) : null}

      {/* === Header ============================================================ */}
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>
            508 Agent · Audit
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            Drop a document, walk through every finding, approve only the fixes you want.
          </Text>
        </View>
        <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
          {mockMode ? (
            <UncertaintyChip
              label="DEMO MODE"
              hint="You're seeing fake data. Turn off Demo Mode in Settings to analyze a real document."
            />
          ) : (
            <Chip
              label={
                backendHealth === "ok"
                  ? "Live"
                  : backendHealth === "error"
                  ? "No backend"
                  : "Checking…"
              }
              tone={
                backendHealth === "ok"
                  ? "success"
                  : backendHealth === "error"
                  ? "danger"
                  : "default"
              }
            />
          )}
          <Pressable
            onPress={() => setShowHelp(true)}
            accessibilityLabel="Show keyboard shortcuts"
            style={[
              styles.helpButton,
              { borderColor: theme.colors.border, backgroundColor: theme.colors.surface },
            ]}
          >
            <Text style={[styles.helpButtonText, { color: theme.colors.textMuted }]}>?</Text>
          </Pressable>
        </View>
      </View>

      {/* === Backend onboarding ================================================= */}
      {noBackend && !report ? (
        <Card>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
            The analyzer service isn't running yet
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
            Open a terminal in your project folder and run:
          </Text>
          <View
            style={[
              styles.codeBlock,
              { backgroundColor: theme.colors.surface2, borderColor: theme.colors.border },
            ]}
          >
            <Text style={[theme.typography.mono, { color: theme.colors.text }]}>
              cd backend{"\n"}python dev_run.py
            </Text>
          </View>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
            That starts the analyzer on a free port and writes the URL where this UI looks for it.
            When it says "Backend running at http://127.0.0.1:8000", come back here.
          </Text>
          <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
            <Button title="Try with sample data" onPress={() => setMockMode(true)} variant="secondary" />
          </View>
        </Card>
      ) : null}

      {/* === Step 1: Pick a file ============================================== */}
      <Card>
        <View style={styles.stepHeader}>
          <View style={[styles.stepNumber, { backgroundColor: theme.colors.accent }]}>
            <Text style={styles.stepNumberText}>1</Text>
          </View>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
            Pick the document you want to audit
          </Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Supported: PDF, Microsoft Word (.docx), Microsoft PowerPoint (.pptx). Files are processed
          locally.
        </Text>
        <View style={styles.row}>
          <Button
            title={busy ? "Analyzing…" : filename ? "Choose a different file" : "Choose file"}
            onPress={handlePick}
          />
          {filename ? <Chip label={filename} tone="default" /> : null}
        </View>
        {Platform.OS === "web" ? (
          // @ts-ignore — RN-Web supports a hidden file input
          <input
            ref={(el) => {
              inputRef.current = el;
            }}
            type="file"
            accept={ACCEPTED_FILE_TYPES}
            style={{ display: "none" }}
            onChange={(event: any) => {
              const f: File | undefined = event?.target?.files?.[0];
              if (f) {
                void handleFile(f);
                event.target.value = "";
              }
            }}
          />
        ) : (
          <InlineNotice
            title="Web only for now"
            message="Native mobile file pickers aren't wired up yet. Open this in a browser."
            tone="info"
          />
        )}
        {error ? (
          <InlineNotice title="Couldn't analyze that file" message={error} tone="danger" />
        ) : null}

        {!report && !busy ? (
          <View style={styles.sampleBlock}>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
              Or try a sample document
            </Text>
            <View style={styles.sampleRow}>
              {SAMPLE_DOCUMENTS.map((sample) => (
                <Pressable
                  key={sample.id}
                  onPress={() => loadSample(sample)}
                  accessibilityLabel={`Load sample document: ${sample.title}`}
                  style={[
                    styles.sampleCard,
                    { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
                  ]}
                >
                  <View
                    style={[
                      styles.sampleBadge,
                      {
                        backgroundColor:
                          sample.scenario === "clean"
                            ? theme.colors.success
                            : sample.scenario === "typical"
                            ? theme.colors.info
                            : theme.colors.danger,
                      },
                    ]}
                  >
                    <Text style={styles.sampleBadgeText}>
                      {sample.scenario === "clean" ? "EASY" : sample.scenario === "typical" ? "TYPICAL" : "HARD"}
                    </Text>
                  </View>
                  <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 14 }]}>
                    {sample.title}
                  </Text>
                  <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 12 }]}>
                    {sample.description}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : null}
      </Card>

      {/* === Step 2: Findings summary ========================================== */}
      {report ? (
        <Card>
          <View style={styles.stepHeader}>
            <View style={[styles.stepNumber, { backgroundColor: theme.colors.accent }]}>
              <Text style={styles.stepNumberText}>2</Text>
            </View>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
              We found {totalIssues} {totalIssues === 1 ? "issue" : "issues"}
            </Text>
          </View>

          <View style={styles.summaryRow}>
            <ScoreBadge
              score={liveScore?.score ?? report.score.score}
              grade={liveScore?.grade ?? report.score.grade}
              fromScore={lastScoreRef.current}
              subLabel={
                reviewedCount === 0
                  ? `${totalIssues} issue${totalIssues === 1 ? "" : "s"} found`
                  : `${reviewedCount} of ${totalIssues} reviewed`
              }
            />
            <View style={styles.summaryMeta}>
              <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
                {report.summary.title || "Untitled document"}
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
                {report.summary.sourceFormat.toUpperCase()} · {report.summary.pageCount} page(s)
                {" · "}
                {report.summary.imageCount} image(s) · {report.summary.tableCount} table(s)
              </Text>
              <View style={styles.scoreBreakdown}>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  How the score is computed
                </Text>
                <Text style={[theme.typography.body, { color: theme.colors.text }]}>
                  Live calculation: errors weight 2, warnings weight 1, info weight 0. Approving a
                  fix removes its weight (the issue will be resolved); pending and rejected items
                  keep their weight. Score moves as you decide.
                </Text>
                <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
                  <Chip label="A+ ≥ 95" tone="success" />
                  <Chip label="A ≥ 90" tone="success" />
                  <Chip label="B ≥ 80" tone="info" />
                  <Chip label="C ≥ 70" tone="warning" />
                  <Chip label="D ≥ 60" tone="warning" />
                  <Chip label="F < 60" tone="danger" />
                </View>
              </View>
              <UncertaintyChip
                label={`AI provider: ${report.aiProvider}`}
                hint={
                  report.aiProvider === "heuristic"
                    ? "No API key configured. Suggestions are heuristic, not vision-AI. Set ANTHROPIC_API_KEY or OPENAI_API_KEY before launching the backend for higher quality."
                    : "AI-generated suggestions are present. Always review before approving."
                }
              />
            </View>
          </View>

          <Divider />

          <SeverityHeatmap errors={buckets.errors} warnings={buckets.warnings} infos={buckets.infos} />
        </Card>
      ) : null}

      {/* === Step 3: Sequential review ========================================= */}
      {report && totalIssues > 0 ? (
        <Card>
          <View style={styles.stepHeader}>
            <View style={[styles.stepNumber, { backgroundColor: theme.colors.accent }]}>
              <Text style={styles.stepNumberText}>3</Text>
            </View>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
              {currentViolation
                ? `Review ${reviewIndex + 1} of ${filteredViolations.length}`
                : "Nothing matches your filters"}
            </Text>
          </View>

          {/* Filters */}
          <View style={styles.filterRow}>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Severity:</Text>
            {(["all", "error", "warning", "info"] as SeverityFilter[]).map((s) => (
              <Pressable key={s} onPress={() => setSeverityFilter(s)}>
                <Chip
                  label={
                    s === "all"
                      ? `All (${totalIssues})`
                      : s === "error"
                      ? `Errors (${buckets.errors})`
                      : s === "warning"
                      ? `Warnings (${buckets.warnings})`
                      : `Info (${buckets.infos})`
                  }
                  tone={
                    severityFilter === s
                      ? s === "error"
                        ? "danger"
                        : s === "warning"
                        ? "warning"
                        : s === "info"
                        ? "info"
                        : "success"
                      : "default"
                  }
                />
              </Pressable>
            ))}
          </View>
          <View style={styles.filterRow}>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Status:</Text>
            {(
              [
                { key: "all" as DecisionFilter, label: `All` },
                { key: "pending" as DecisionFilter, label: `Pending (${decisionCounts.pending})` },
                { key: "approved" as DecisionFilter, label: `Approved (${decisionCounts.approved})` },
                { key: "rejected" as DecisionFilter, label: `Rejected (${decisionCounts.rejected})` },
              ] as { key: DecisionFilter; label: string }[]
            ).map((opt) => (
              <Pressable key={opt.key} onPress={() => setDecisionFilter(opt.key)}>
                <Chip
                  label={opt.label}
                  tone={decisionFilter === opt.key ? "info" : "default"}
                />
              </Pressable>
            ))}
          </View>

          {totalIssues > 0 && reviewedCount === totalIssues ? (
            <View
              style={[
                styles.celebration,
                {
                  borderColor: theme.colors.success,
                  backgroundColor: theme.colors.success + "12",
                },
              ]}
            >
              <Text style={styles.celebrationEmoji}>✨</Text>
              <View style={{ flex: 1 }}>
                <Text style={[theme.typography.h2, { color: theme.colors.success }]}>
                  All {totalIssues} issue{totalIssues === 1 ? "" : "s"} reviewed
                </Text>
                <Text style={[theme.typography.body, { color: theme.colors.text, marginTop: 2 }]}>
                  {decisionCounts.approved} approved · {decisionCounts.rejected} rejected. Hit
                  "Apply approved fixes" below to wrap up, or download the audit report.
                </Text>
              </View>
            </View>
          ) : null}

          <View style={styles.bulkRow}>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Bulk:</Text>
            <Pressable
              onPress={() =>
                bulkDecide(
                  "approved",
                  (v) => v.severity === "error",
                  "Approved every error-level finding",
                )
              }
              accessibilityLabel="Approve all errors"
              style={[styles.bulkButton, { borderColor: theme.colors.border }]}
            >
              <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13 }]}>
                Approve all errors
              </Text>
            </Pressable>
            <Pressable
              onPress={() =>
                bulkDecide(
                  "approved",
                  (v) =>
                    v.recommendedActions.some(
                      (a) => a !== "FLAG_FOR_MANUAL_REVIEW" && a !== "GENERATE_ALT_TEXT" && a !== "IMPROVE_LINK_TEXT",
                    ),
                  "Approved every deterministic auto-fix",
                )
              }
              accessibilityLabel="Approve all deterministic fixes"
              style={[styles.bulkButton, { borderColor: theme.colors.border }]}
            >
              <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13 }]}>
                Approve safe auto-fixes
              </Text>
            </Pressable>
            <Pressable
              onPress={() =>
                bulkDecide(
                  "rejected",
                  (v) =>
                    v.recommendedActions.includes("GENERATE_ALT_TEXT") ||
                    v.recommendedActions.includes("IMPROVE_LINK_TEXT"),
                  "Rejected every AI/heuristic suggestion",
                )
              }
              accessibilityLabel="Reject all AI suggestions"
              style={[styles.bulkButton, { borderColor: theme.colors.border }]}
            >
              <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13 }]}>
                Reject heuristic suggestions
              </Text>
            </Pressable>
            <Pressable
              onPress={() => setResetDialogOpen(true)}
              accessibilityLabel="Reset all decisions"
              style={[styles.bulkButton, { borderColor: theme.colors.border }]}
            >
              <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13 }]}>
                Reset all
              </Text>
            </Pressable>
          </View>

          <ProgressBar
            current={reviewIndex}
            decisions={decisions}
            violations={filteredViolations}
            onJump={(i) => setReviewIndex(i)}
          />

          <View style={styles.workspaceRow}>
            {filteredViolations.length > 0 ? (
              <View style={styles.navColumn}>
                <IssueNavigator
                  violations={filteredViolations}
                  decisions={decisions}
                  currentId={currentViolation?.id ?? null}
                  onSelect={(i) => {
                    setReviewIndex(i);
                    setEditing(false);
                  }}
                />
              </View>
            ) : null}
            <View style={styles.detailColumn}>
              {currentViolation ? (
                <IssueCard
                  violation={currentViolation}
                  execution={report.executions.find(
                    (e) => e.targetNodeId === currentViolation.nodeId,
                  )}
                  decision={decisions[currentViolation.id]?.decision ?? "pending"}
                  customText={decisions[currentViolation.id]?.customText}
                  note={decisions[currentViolation.id]?.note}
                  isEditing={editing}
                  onSetEditing={setEditing}
                  onDecide={(d, t) => {
                    decide(currentViolation, d, t);
                    if (d !== "pending" && reviewIndex < filteredViolations.length - 1) {
                      setTimeout(() => next(), 120);
                    }
                  }}
                  onUpdateNote={(note) => {
                    setDecisions((prev) => {
                      const existing = prev[currentViolation.id] ?? { decision: "pending" as Decision };
                      return {
                        ...prev,
                        [currentViolation.id]: { ...existing, note: note || undefined },
                      };
                    });
                  }}
                />
              ) : (
                <EmptyState
                  title="No matches"
                  message="Loosen your filters to see more issues."
                />
              )}
            </View>
            {/*
              Preview column — only mounted when we have a PDF source AND the
              current violation is anchored to a page.  flexWrap on the parent
              row makes this drop below the IssueCard on narrow viewports
              instead of squeezing everything horizontally.
             */}
            {(() => {
              const isPdfSource =
                sourceFile?.type === "application/pdf" ||
                report.summary.sourceFormat?.toLowerCase() === "pdf";
              const previewPage = currentViolation?.page ?? null;
              if (!isPdfSource || !previewPage) return null;
              return (
                <View style={styles.previewColumn}>
                  <PdfPreview file={sourceFile} page={previewPage} />
                </View>
              );
            })()}
          </View>

          <View style={styles.navRow}>
            <Button title="← Previous (k)" onPress={prev} variant="ghost" />
            <Text
              style={[
                theme.typography.body,
                { color: theme.colors.textMuted, flex: 1, textAlign: "center" },
              ]}
            >
              {reviewedCount} of {totalIssues} reviewed
            </Text>
            <Button
              title={
                reviewIndex < filteredViolations.length - 1 ? "Next (j)" : "Done"
              }
              onPress={next}
              variant="ghost"
            />
          </View>

          <Divider />

          {/* Decision log */}
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Decision log {decisionLog.length > 0 ? `· press u or Ctrl+Z to undo` : ""}
          </Text>
          {decisionLog.length === 0 ? (
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              No decisions yet — approve, reject, or edit an issue to see entries here.
            </Text>
          ) : (
            <View style={styles.logList}>
              {decisionLog.slice(0, 5).map((entry) => (
                <View key={`${entry.violationId}-${entry.at}`} style={styles.logRow}>
                  <Chip
                    label={entry.decision}
                    tone={
                      entry.decision === "approved"
                        ? "success"
                        : entry.decision === "rejected"
                        ? "warning"
                        : "default"
                    }
                  />
                  <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1 }]}>
                    {entry.title}
                  </Text>
                  <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                    {_relativeTime(entry.at)}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </Card>
      ) : null}

      {/* === Step 4: Apply ===================================================== */}
      {report && totalIssues > 0 ? (
        <Card>
          <View style={styles.stepHeader}>
            <View style={[styles.stepNumber, { backgroundColor: theme.colors.accent }]}>
              <Text style={styles.stepNumberText}>4</Text>
            </View>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
              Apply approved fixes
            </Text>
          </View>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            We've already run the deterministic analyzer and proposed fixes. Hitting Apply commits
            the {decisionCounts.approved} fix(es) you approved into the document. Rejected items
            get queued for manual review instead.
          </Text>
          <View style={styles.row}>
            <Button
              title={
                downloadingFixed
                  ? "Applying & downloading…"
                  : `Apply ${decisionCounts.approved} approved · download remediated file`
              }
              onPress={downloadRemediated}
              loading={downloadingFixed}
              disabled={decisionCounts.approved === 0 && !mockMode}
              accessibilityHint="Sends only your approved fixes to the analyzer, bakes them into a remediated copy of the document, and opens that copy in a new tab."
            />
            <Button
              title={`Open audit report (${reviewedCount} reviewed)`}
              onPress={() =>
                _openReport(report, decisions, decisionLog, filename ?? "document")
              }
              variant="secondary"
            />
            <Button
              title="Export JSON"
              onPress={() =>
                _downloadJson(report, decisions, decisionLog, filename ?? "document")
              }
              variant="ghost"
              accessibilityHint="Download the raw audit data including decisions and log."
            />
            <Button
              title="Export CSV"
              onPress={() => _downloadCsv(report, decisions, filename ?? "document")}
              variant="ghost"
              accessibilityHint="Download the issue table as a spreadsheet-compatible CSV."
            />
          </View>
          {fixedDownloadUrl ? (
            <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
              Direct download URL:{" "}
              <Text style={[theme.typography.mono, { color: theme.colors.text }]}>
                {fixedDownloadUrl}
              </Text>
            </Text>
          ) : null}
          {lastRemediation ? (
            <View style={{ marginTop: 12, gap: 8 }}>
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>
                Changes baked into the remediated file
              </Text>
              <DiffViewer
                applied={lastRemediation.applied}
                skipped={lastRemediation.skipped}
              />
            </View>
          ) : null}
          {decisionCounts.approved === 0 ? (
            <InlineNotice
              tone="info"
              title="No fixes approved yet"
              message="Approve at least one fix above (or use the bulk Approve buttons) before downloading a remediated file. Without approvals, the remediated file would be identical to the original."
            />
          ) : (
            <InlineNotice
              tone="info"
              title="What gets applied"
              message={`Only the ${decisionCounts.approved} fix${
                decisionCounts.approved === 1 ? "" : "es"
              } you approved are baked into the remediated file. The ${
                decisionCounts.rejected
              } rejected and ${decisionCounts.pending} pending items are recorded in the audit report so a teammate can handle them.`}
            />
          )}
        </Card>
      ) : null}

      {/* === Loading skeleton =================================================== */}
      {busy ? (
        <Card>
          <View style={styles.stepHeader}>
            <Skeleton width={26} height={26} radius={13} />
            <Skeleton width={220} height={20} />
          </View>
          <View style={{ marginTop: 12 }}>
            <SkeletonBlock />
          </View>
          <View style={{ marginTop: 16, gap: 8 }}>
            <Skeleton width="100%" height={12} radius={6} />
            <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap" }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} width={64} height={22} radius={11} />
              ))}
            </View>
          </View>
          <Text
            style={[
              theme.typography.body,
              { color: theme.colors.textMuted, marginTop: 16, textAlign: "center" },
            ]}
          >
            Running parser → analyzers → planner → executors…
          </Text>
        </Card>
      ) : null}

      {/* === Empty state ======================================================= */}
      {!report && !busy ? (
        <EmptyState
          title="No audit yet"
          message="Pick a PDF, DOCX, or PPTX above — or click a sample card — and we'll walk you through every accessibility finding."
        />
      ) : null}
    </Screen>
  );
}

/* -------------------------------------------------------------------------- */
/* Sub-components                                                              */
/* -------------------------------------------------------------------------- */

function ProgressBar(props: {
  current: number;
  decisions: Record<string, IssueState>;
  violations: PipelineViolation[];
  onJump: (index: number) => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.progressRow}>
      {props.violations.map((v, i) => {
        const decision = props.decisions[v.id]?.decision ?? "pending";
        let bg = theme.colors.surface2;
        if (decision === "approved") bg = theme.colors.success;
        else if (decision === "rejected") bg = theme.colors.danger;
        else if (i === props.current) bg = theme.colors.accent;
        return (
          <Pressable
            key={v.id}
            onPress={() => props.onJump(i)}
            accessibilityLabel={`Jump to issue ${i + 1}`}
            style={[
              styles.progressCell,
              {
                backgroundColor: bg,
                borderColor:
                  i === props.current ? theme.colors.text : "transparent",
              },
            ]}
          />
        );
      })}
    </View>
  );
}

function IssueCard(props: {
  violation: PipelineViolation;
  execution?: PipelineExecutionResult;
  decision: Decision;
  customText?: string;
  note?: string;
  isEditing: boolean;
  onSetEditing: (b: boolean) => void;
  onDecide: (decision: Decision, customText?: string) => void;
  onUpdateNote: (note: string) => void;
}) {
  const theme = useTheme();
  const v = props.violation;
  const catalog = lookupIssue(v.ruleId);
  const tone =
    v.severity === "error"
      ? theme.colors.danger
      : v.severity === "warning"
      ? theme.colors.warning
      : theme.colors.info;
  const [draft, setDraft] = useState(props.customText ?? "");

  // Reset the editor when we move to a new issue.
  useEffect(() => {
    setDraft(props.customText ?? "");
  }, [props.violation.id, props.customText]);

  return (
    <View style={[styles.issueCard, { borderColor: theme.colors.border }]}>
      <View style={styles.issueTop}>
        <View style={[styles.severityDot, { backgroundColor: tone }]} />
        <View style={{ flex: 1 }}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>{catalog.title}</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            {catalog.summary}
          </Text>
        </View>
        <Chip
          label={v.severity}
          tone={
            v.severity === "error"
              ? "danger"
              : v.severity === "warning"
              ? "warning"
              : "info"
          }
        />
      </View>

      <Section title="Why this matters" body={catalog.why} />
      <Section
        title="Where in the document"
        body={
          v.page
            ? `Page ${v.page}, node ${v.nodeId}`
            : `Node ${v.nodeId} (no page available — likely document-level metadata)`
        }
      />
      <Section title="What the auto-fix will do" body={catalog.autoFix} />
      {catalog.manualJudgment ? (
        <Section title="What needs your judgment" body={catalog.manualJudgment} tone="warning" />
      ) : null}

      {props.execution ? (
        <>
          <Section
            title="What we did this run"
            body={`${labelStatus(props.execution.status)} — ${props.execution.notes}`}
            tone={props.execution.status === "success" ? "success" : "info"}
          />
          {(() => {
            const provenance = _extractProvenance(props.execution.notes);
            if (!provenance) return null;
            return (
              <View style={styles.standards}>
                <UncertaintyChip
                  label={`${provenance.provider}`}
                  confidence={provenance.confidence}
                  hint={
                    provenance.provider === "heuristic"
                      ? "Suggestion came from a local rule, not an AI model. Lower confidence than vision-AI."
                      : "Suggestion came from an AI provider. Always verify before approving."
                  }
                />
              </View>
            );
          })()}
        </>
      ) : null}

      <View style={styles.standards}>
        {catalog.standards.wcag.map((id) => (
          <Chip key={`wcag-${id}`} label={`WCAG ${id}`} tone="default" />
        ))}
        {catalog.standards.section508.map((id) => (
          <Chip key={`508-${id}`} label={`§508 ${id}`} tone="default" />
        ))}
        {catalog.standards.pdfUa.map((id) => (
          <Chip key={`pdfua-${id}`} label={`PDF/UA ${id}`} tone="default" />
        ))}
        {catalog.learnMoreUrl ? (
          <Pressable onPress={() => Linking.openURL(catalog.learnMoreUrl)}>
            <Chip label="Learn more ↗" tone="info" />
          </Pressable>
        ) : null}
      </View>

      <ReviewerNote value={props.note ?? ""} onChange={props.onUpdateNote} />

      {props.isEditing ? (
        <View style={[styles.editor, { borderColor: theme.colors.border }]}>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Custom replacement text (replaces the auto-fix suggestion)
          </Text>
          <TextInput
            value={draft}
            onChangeText={setDraft}
            placeholder="Type the value you want applied"
            placeholderTextColor={theme.colors.textMuted}
            multiline
            style={[
              styles.editorInput,
              { color: theme.colors.text, borderColor: theme.colors.border },
            ]}
          />
          <View style={styles.row}>
            <Button
              title="Save edit & approve"
              onPress={() => {
                props.onDecide("approved", draft.trim() || undefined);
                props.onSetEditing(false);
              }}
            />
            <Button title="Cancel" variant="ghost" onPress={() => props.onSetEditing(false)} />
          </View>
        </View>
      ) : (
        <View style={styles.decisionRow}>
          <Button
            title={props.decision === "approved" ? "✓ Approved (a)" : "Approve (a)"}
            onPress={() => props.onDecide("approved", props.customText)}
            variant={props.decision === "approved" ? "primary" : "secondary"}
          />
          <Button title="Edit & approve (e)" onPress={() => props.onSetEditing(true)} variant="ghost" />
          <Button
            title={props.decision === "rejected" ? "✗ Rejected (r)" : "Reject (r)"}
            onPress={() => props.onDecide("rejected")}
            variant={props.decision === "rejected" ? "primary" : "ghost"}
          />
        </View>
      )}
    </View>
  );
}

function ReviewerNote({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const theme = useTheme();
  const [draft, setDraft] = useState(value);
  // Keep the draft in sync when the active issue changes (parent passes a new value).
  useEffect(() => {
    setDraft(value);
  }, [value]);
  const dirty = draft !== value;
  return (
    <View style={[styles.reviewerNote, { borderColor: theme.colors.border }]}>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          Reviewer note (optional, exported with the audit report)
        </Text>
        {dirty ? (
          <Pressable
            onPress={() => onChange(draft.trim())}
            accessibilityLabel="Save reviewer note"
          >
            <Text style={[theme.typography.caption, { color: theme.colors.accent, fontWeight: "700" }]}>
              Save
            </Text>
          </Pressable>
        ) : null}
      </View>
      <TextInput
        value={draft}
        onChangeText={setDraft}
        onBlur={() => {
          if (dirty) onChange(draft.trim());
        }}
        placeholder="Why did you approve / reject this?  Free text."
        placeholderTextColor={theme.colors.textMuted}
        multiline
        accessibilityLabel="Reviewer note for this issue"
        style={[
          styles.reviewerNoteInput,
          { color: theme.colors.text, borderColor: theme.colors.border },
        ]}
      />
    </View>
  );
}

function Section(props: {
  title: string;
  body: string;
  tone?: "warning" | "success" | "info";
}) {
  const theme = useTheme();
  const accent =
    props.tone === "warning"
      ? theme.colors.warning
      : props.tone === "success"
      ? theme.colors.success
      : props.tone === "info"
      ? theme.colors.info
      : theme.colors.textMuted;
  return (
    <View style={styles.section}>
      <View style={[styles.sectionRule, { backgroundColor: accent }]} />
      <View style={{ flex: 1 }}>
        <Text style={[theme.typography.caption, { color: accent }]}>{props.title}</Text>
        <Text style={[theme.typography.body, { color: theme.colors.text, marginTop: 2 }]}>
          {props.body}
        </Text>
      </View>
    </View>
  );
}

function KeyboardHelpOverlay({ onClose }: { onClose: () => void }) {
  const theme = useTheme();
  return (
    <Pressable
      onPress={onClose}
      accessibilityLabel="Close keyboard shortcuts overlay"
      style={[styles.overlay, { backgroundColor: theme.colors.shadow }]}
    >
      <View
        style={[
          styles.overlayCard,
          { backgroundColor: theme.colors.surface, borderColor: theme.colors.border },
        ]}
      >
        <Text style={[theme.typography.h1, { color: theme.colors.text }]}>Keyboard shortcuts</Text>
        <View style={styles.overlayList}>
          {[
            ["j", "Next issue"],
            ["k", "Previous issue"],
            ["a", "Approve"],
            ["r", "Reject"],
            ["e", "Edit suggestion"],
            ["u or Ctrl+Z", "Undo last decision"],
            ["?", "Show this help"],
          ].map(([key, desc]) => (
            <View key={key} style={styles.overlayRow}>
              <View
                style={[
                  styles.kbd,
                  { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
                ]}
              >
                <Text style={[theme.typography.mono, { color: theme.colors.text }]}>{key}</Text>
              </View>
              <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1 }]}>
                {desc}
              </Text>
            </View>
          ))}
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 12 }]}>
          Click anywhere outside this card to dismiss.
        </Text>
      </View>
    </Pressable>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                     */
/* -------------------------------------------------------------------------- */

function _extractProvenance(notes: string): { provider: string; confidence?: number } | null {
  // Executor notes look like:
  //   "Generated alt text via heuristic (confidence 0.40). Pending human review. Text='...'"
  //   "Set document title from None to 'Annual Report 2025' via heuristic (0.35)."
  //   "Rewrote link text 'click here' → 'Read the example.com report' via heuristic (0.45)."
  //   "Set language to 'en' via heuristic (0.55)."
  // Try to pull provider name and confidence number if present.
  const providerMatch = /\bvia\s+([a-zA-Z][a-zA-Z0-9-]*)\b/.exec(notes);
  if (!providerMatch) return null;
  const provider = providerMatch[1].toLowerCase();
  // Match either "confidence 0.42" or a parenthesized decimal "(0.42)" /
  // "(1.00)".  Allow >=1 fractional digit and >=1 leading digit so we don't
  // silently drop high-confidence values.
  const confidenceMatch =
    /confidence\s+([0-9]+(?:\.[0-9]+)?)/i.exec(notes) ||
    /\(([0-9]+(?:\.[0-9]+)?)\)/.exec(notes);
  const confidence = confidenceMatch ? parseFloat(confidenceMatch[1]) : undefined;
  return {
    provider,
    confidence:
      typeof confidence === "number" && Number.isFinite(confidence)
        ? confidence
        : undefined,
  };
}

function _gradeFor(score: number): string {
  if (score >= 95) return "A+";
  if (score >= 90) return "A";
  if (score >= 80) return "B";
  if (score >= 70) return "C";
  if (score >= 60) return "D";
  return "F";
}

function labelStatus(status: string): string {
  if (status === "success") return "Successfully applied";
  if (status === "skipped") return "Skipped";
  if (status === "ready") return "Ready to apply";
  return "Not implemented";
}

function _relativeTime(iso: string): string {
  const delta = Date.now() - new Date(iso).getTime();
  if (delta < 5_000) return "just now";
  if (delta < 60_000) return `${Math.round(delta / 1000)}s ago`;
  if (delta < 60 * 60_000) return `${Math.round(delta / 60_000)}m ago`;
  return new Date(iso).toLocaleTimeString();
}

function _openReport(
  report: PipelineResponse,
  decisions: Record<string, IssueState>,
  log: DecisionLogItem[],
  filename: string,
) {
  if (Platform.OS !== "web") return;
  const html = _buildReportHtml(report, decisions, log, filename);
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
}

function _downloadJson(
  report: PipelineResponse,
  decisions: Record<string, IssueState>,
  log: DecisionLogItem[],
  filename: string,
) {
  if (Platform.OS !== "web") return;
  const payload = {
    schema: "508-agent-audit-v1",
    exportedAt: new Date().toISOString(),
    filename,
    pipeline: report,
    decisions,
    decisionLog: log,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  _saveBlob(blob, _safeFilename(filename) + "-audit.json");
}

function _downloadCsv(
  report: PipelineResponse,
  decisions: Record<string, IssueState>,
  filename: string,
) {
  if (Platform.OS !== "web") return;
  const rows = [
    [
      "rule_id",
      "title",
      "severity",
      "page",
      "node_id",
      "decision",
      "custom_text",
      "reviewer_note",
      "wcag",
      "section_508",
      "pdf_ua",
    ],
    ...report.violations.map((v) => {
      const catalog = lookupIssue(v.ruleId);
      const state = decisions[v.id];
      return [
        v.ruleId,
        catalog.title,
        v.severity,
        v.page ?? "",
        v.nodeId,
        state?.decision ?? "pending",
        state?.customText ?? "",
        state?.note ?? "",
        catalog.standards.wcag.join("; "),
        catalog.standards.section508.join("; "),
        catalog.standards.pdfUa.join("; "),
      ];
    }),
  ];
  const csv = rows
    .map((row) =>
      row
        .map((cell) => {
          const s = String(cell ?? "");
          if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
          return s;
        })
        .join(","),
    )
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  _saveBlob(blob, _safeFilename(filename) + "-issues.csv");
}

function _saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function _safeFilename(s: string): string {
  return (s || "audit").replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 80);
}

function _buildReportHtml(
  report: PipelineResponse,
  decisions: Record<string, IssueState>,
  log: DecisionLogItem[],
  filename: string,
): string {
  const approved = report.violations.filter(
    (v) => decisions[v.id]?.decision === "approved",
  );
  const rejected = report.violations.filter(
    (v) => decisions[v.id]?.decision === "rejected",
  );
  const pending = report.violations.filter(
    (v) => !decisions[v.id]?.decision || decisions[v.id]?.decision === "pending",
  );

  const conformanceClaim =
    report.score.score >= 95
      ? `Conforms to WCAG 2.1 Level AA after applying ${approved.length} approved fix${approved.length === 1 ? "" : "es"}.`
      : report.score.score >= 80
      ? `Substantial conformance to WCAG 2.1 Level AA. ${pending.length + rejected.length} item(s) require additional remediation.`
      : `Partial conformance to WCAG 2.1 Level AA. Significant remediation work remaining.`;

  const findingsSection = _findingsTable(report.violations, decisions);
  const decisionRows = log
    .map(
      (l) =>
        `<tr><td>${new Date(l.at).toLocaleString()}</td><td>${l.decision}</td><td>${_escape(l.title)}</td></tr>`,
    )
    .join("");

  const today = new Date();
  const formattedDate = today.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Accessibility Conformance Report — ${_escape(filename)}</title>
<style>
  :root { color-scheme: light; --accent:#2D5BFF; --ok:#16A34A; --warn:#F59E0B; --err:#DC2626; --bg:#F6F7FB; --fg:#0F172A; --muted:#5B6475; --border:#E2E8F0; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background: var(--bg); color: var(--fg); margin: 0; padding: 32px 16px; }
  .page { max-width: 960px; margin: 0 auto; background: white; border-radius: 16px; box-shadow: 0 8px 32px rgba(15, 23, 42, 0.08); overflow: hidden; }
  .hero { background: linear-gradient(135deg, var(--accent) 0%, #1E3A8A 100%); color: white; padding: 32px 40px; }
  .hero .eyebrow { font-size: 11px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; opacity: 0.85; }
  .hero h1 { font-size: 32px; margin: 6px 0 4px; letter-spacing: -0.5px; }
  .hero .filename { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; opacity: 0.85; }
  .hero-grid { display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: center; margin-top: 24px; }
  .score-block { display: flex; align-items: baseline; gap: 12px; }
  .score-num { font-size: 64px; font-weight: 800; letter-spacing: -2px; }
  .score-grade { display: inline-block; background: rgba(255,255,255,0.2); border: 1.5px solid rgba(255,255,255,0.4); padding: 6px 14px; border-radius: 999px; font-weight: 800; font-size: 18px; }
  .conformance { font-size: 14px; line-height: 1.5; max-width: 360px; opacity: 0.9; }
  .body { padding: 32px 40px; }
  .body h2 { font-size: 20px; border-bottom: 2px solid var(--border); padding-bottom: 6px; margin-top: 32px; margin-bottom: 12px; letter-spacing: -0.2px; }
  .body h2:first-child { margin-top: 0; }
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 12px; }
  .summary-tile { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .summary-tile .label { font-size: 11px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; color: var(--muted); }
  .summary-tile .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
  .conformance-callout { background: #EFF6FF; border-left: 4px solid var(--accent); padding: 12px 16px; border-radius: 6px; margin-top: 12px; line-height: 1.5; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { background: var(--bg); font-weight: 700; font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase; color: var(--muted); }
  td.sev-error { color: var(--err); font-weight: 700; }
  td.sev-warning { color: var(--warn); font-weight: 700; }
  td.sev-info { color: #0369A1; }
  td.decision-approved { color: var(--ok); font-weight: 700; }
  td.decision-rejected { color: var(--err); font-weight: 700; }
  td.decision-pending { color: var(--muted); }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: 0.4px; }
  .pill-ok { background: #DCFCE7; color: var(--ok); }
  .pill-warn { background: #FEF3C7; color: var(--warn); }
  .pill-err { background: #FEE2E2; color: var(--err); }
  .signatures { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; margin-top: 56px; padding-top: 24px; border-top: 1px dashed #CBD5E1; }
  .sig-block .label { font-size: 11px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; color: var(--muted); }
  .sig-block .line { border-bottom: 1px solid var(--fg); height: 32px; margin-top: 6px; }
  .sig-block .meta { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .footer { background: var(--bg); padding: 16px 40px; font-size: 11px; color: var(--muted); text-align: center; }
  .print-btn { position: fixed; top: 16px; right: 16px; padding: 10px 16px; border-radius: 999px; border: none; background: var(--accent); color: white; cursor: pointer; font-weight: 700; box-shadow: 0 4px 12px rgba(45, 91, 255, 0.4); }
  @media print {
    body { background: white; padding: 0; }
    .page { box-shadow: none; border-radius: 0; max-width: none; }
    .print-btn { display: none; }
    h2 { page-break-after: avoid; }
    table { page-break-inside: auto; }
    tr { page-break-inside: avoid; }
  }
</style>
</head>
<body>
  <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>

  <div class="page">
    <header class="hero">
      <div class="eyebrow">Accessibility Conformance Report</div>
      <h1>${_escape(report.summary.title || filename)}</h1>
      <div class="filename">${_escape(filename)}</div>
      <div class="hero-grid">
        <div class="score-block">
          <div class="score-num">${report.score.score.toFixed(0)}</div>
          <div>
            <div class="score-grade">Grade ${report.score.grade}</div>
            <div style="margin-top: 6px; font-size: 13px; opacity: 0.85;">
              ${report.violations.length} finding(s) · ${approved.length} approved · ${rejected.length} rejected · ${pending.length} pending
            </div>
          </div>
        </div>
        <div class="conformance">${_escape(conformanceClaim)}</div>
      </div>
    </header>

    <div class="body">
      <h2>Document overview</h2>
      <div class="summary-grid">
        <div class="summary-tile"><div class="label">Format</div><div class="value">${_escape(report.summary.sourceFormat.toUpperCase())}</div></div>
        <div class="summary-tile"><div class="label">Pages / slides</div><div class="value">${report.summary.pageCount}</div></div>
        <div class="summary-tile"><div class="label">Images</div><div class="value">${report.summary.imageCount}</div></div>
        <div class="summary-tile"><div class="label">Tables</div><div class="value">${report.summary.tableCount}</div></div>
        <div class="summary-tile"><div class="label">Language</div><div class="value">${_escape(report.summary.language || "—")}</div></div>
        <div class="summary-tile"><div class="label">AI provider</div><div class="value">${_escape(report.aiProvider)}</div></div>
      </div>

      <div class="conformance-callout">
        <strong>Audit date:</strong> ${formattedDate}<br>
        <strong>Standards evaluated:</strong> WCAG 2.1 (Levels A & AA), Section 508, PDF/UA<br>
        <strong>Methodology:</strong> Deterministic structural analyzers + heuristic / vision-AI suggestions for human review.
      </div>

      <h2>Findings</h2>
      ${findingsSection}

      ${
        approved.length > 0
          ? `<h2>Fixes applied (${approved.length})</h2>
             ${_decisionTable(approved, decisions, report.executions, "approved")}`
          : ""
      }

      ${
        rejected.length > 0
          ? `<h2>Rejected — queued for manual review (${rejected.length})</h2>
             ${_decisionTable(rejected, decisions, report.executions, "rejected")}`
          : ""
      }

      ${
        pending.length > 0
          ? `<h2>Pending — not yet reviewed (${pending.length})</h2>
             ${_decisionTable(pending, decisions, report.executions, "pending")}`
          : ""
      }

      <h2>Decision log</h2>
      <table>
        <thead><tr><th>Timestamp</th><th>Decision</th><th>Issue</th></tr></thead>
        <tbody>${decisionRows || `<tr><td colspan="3" style="color:var(--muted);">No decisions recorded.</td></tr>`}</tbody>
      </table>

      <div class="signatures">
        <div class="sig-block">
          <div class="label">Reviewer</div>
          <div class="line"></div>
          <div class="meta">Name and title</div>
        </div>
        <div class="sig-block">
          <div class="label">Date approved</div>
          <div class="line"></div>
          <div class="meta">YYYY-MM-DD</div>
        </div>
      </div>
    </div>

    <div class="footer">
      Generated by 508 Agent · This report records the state of the audit at ${today.toLocaleString()}.
    </div>
  </div>
</body></html>`;
}

function _findingsTable(
  violations: PipelineResponse["violations"],
  decisions: Record<string, IssueState>,
): string {
  if (!violations.length) {
    return `<p style="color: var(--muted);">No accessibility issues detected.</p>`;
  }
  const rows = violations
    .map((v) => {
      const state = decisions[v.id];
      const catalog = lookupIssue(v.ruleId);
      const decision = state?.decision ?? "pending";
      const standards = [
        ...catalog.standards.wcag.map((s) => `WCAG ${s}`),
        ...catalog.standards.section508.map((s) => `§508 ${s}`),
      ]
        .slice(0, 3)
        .join(" · ");
      return `<tr>
        <td>
          <div style="font-weight:600;">${_escape(catalog.title)}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px;">${_escape(standards || catalog.ruleId)}</div>
        </td>
        <td class="sev-${v.severity}">${v.severity.toUpperCase()}</td>
        <td>${v.page ?? "—"}</td>
        <td class="decision-${decision}">${decision}</td>
      </tr>`;
    })
    .join("");
  return `<table>
    <thead><tr><th>Issue</th><th>Severity</th><th>Page</th><th>Decision</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function _decisionTable(
  violations: PipelineResponse["violations"],
  decisions: Record<string, IssueState>,
  executions: PipelineResponse["executions"],
  _kind: "approved" | "rejected" | "pending",
): string {
  if (!violations.length) return "";
  const rows = violations
    .map((v) => {
      const catalog = lookupIssue(v.ruleId);
      const exec = executions.find((e) => e.targetNodeId === v.nodeId);
      const customText = decisions[v.id]?.customText;
      return `<tr>
        <td>
          <div style="font-weight:600;">${_escape(catalog.title)}</div>
          ${customText ? `<div style="font-size:12px;margin-top:2px;color:var(--accent);">Custom text: <em>${_escape(customText)}</em></div>` : ""}
          ${exec ? `<div style="font-size:11px;margin-top:2px;color:var(--muted);">${_escape(exec.notes)}</div>` : ""}
        </td>
        <td>${v.page ?? "—"}</td>
      </tr>`;
    })
    .join("");
  return `<table>
    <thead><tr><th>Issue</th><th>Page</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function _escape(s: string): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* -------------------------------------------------------------------------- */
/* Styles                                                                      */
/* -------------------------------------------------------------------------- */

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 12,
  },
  helpButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  helpButtonText: { fontSize: 16, fontWeight: "800" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 12, flexWrap: "wrap" },
  stepHeader: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 8 },
  stepNumber: { width: 26, height: 26, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  stepNumberText: { color: "#FFFFFF", fontWeight: "800", fontSize: 13 },
  codeBlock: { marginTop: 8, borderWidth: 1, borderRadius: 10, padding: 12 },
  summaryRow: { flexDirection: "row", gap: 16, flexWrap: "wrap", alignItems: "center", marginTop: 4 },
  summaryMeta: { flex: 1, minWidth: 220, gap: 6 },
  scoreBreakdown: { gap: 4, marginTop: 4 },
  filterRow: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 8 },
  progressRow: { flexDirection: "row", gap: 4, marginVertical: 12, flexWrap: "wrap" },
  progressCell: { width: 14, height: 14, borderRadius: 3, borderWidth: 2 },
  issueCard: { borderWidth: 1, borderRadius: 12, padding: 14, gap: 12, marginTop: 8 },
  issueTop: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  severityDot: { width: 10, height: 10, borderRadius: 5, marginTop: 6 },
  section: { flexDirection: "row", gap: 10 },
  sectionRule: { width: 3, borderRadius: 2, alignSelf: "stretch" },
  standards: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  decisionRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  navRow: { flexDirection: "row", alignItems: "center", marginTop: 12, gap: 8 },
  editor: { borderWidth: 1, borderRadius: 10, padding: 10, gap: 8 },
  editorInput: { borderWidth: 1, borderRadius: 8, padding: 10, minHeight: 64 },
  logList: { gap: 6, marginTop: 6 },
  logRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  overlay: {
    position: Platform.OS === "web" ? ("fixed" as any) : "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    zIndex: 10000,
  },
  overlayCard: {
    borderWidth: 1,
    borderRadius: 16,
    padding: 24,
    width: "100%",
    maxWidth: 460,
    gap: 12,
  },
  overlayList: { gap: 8, marginTop: 6 },
  overlayRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  kbd: {
    minWidth: 80,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    alignItems: "center",
  },
  sampleBlock: { gap: 8, marginTop: 16 },
  sampleRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  sampleCard: {
    flex: 1,
    minWidth: 220,
    maxWidth: 320,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    gap: 6,
  },
  sampleBadge: {
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 999,
  },
  sampleBadgeText: { color: "#FFFFFF", fontWeight: "800", fontSize: 10, letterSpacing: 0.5 },
  bulkRow: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 8,
  },
  bulkButton: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
  },
  workspaceRow: {
    flexDirection: "row",
    gap: 12,
    flexWrap: "wrap",
    alignItems: "flex-start",
    marginTop: 12,
  },
  navColumn: { width: 280, flexShrink: 0, flexGrow: 0 },
  detailColumn: { flex: 1, minWidth: 320 },
  // Preview column wraps below on narrow viewports thanks to workspaceRow.flexWrap.
  // Width matches the PdfPreview internal max so it doesn't stretch awkwardly.
  previewColumn: { width: 360, flexShrink: 0, flexGrow: 0, minWidth: 320 },
  dropOverlay: {
    position: Platform.OS === "web" ? ("fixed" as any) : "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
    zIndex: 9999,
    gap: 8,
  },
  dropOverlayText: {
    color: "#FFFFFF",
    fontSize: 36,
    fontWeight: "800",
    letterSpacing: -0.5,
  },
  dropOverlaySub: { color: "#FFFFFF", opacity: 0.85, fontSize: 14, fontWeight: "600" },
  celebration: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    borderWidth: 1.5,
    borderRadius: 12,
    padding: 14,
    marginTop: 8,
  },
  celebrationEmoji: { fontSize: 28 },
  skippedBlock: {
    borderWidth: 1.5,
    borderRadius: 12,
    padding: 12,
    marginTop: 12,
  },
  reviewerNote: {
    borderWidth: 1,
    borderStyle: Platform.OS === "web" ? ("dashed" as any) : "solid",
    borderRadius: 10,
    padding: 10,
    gap: 6,
  },
  reviewerNoteInput: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 8,
    minHeight: 48,
    fontSize: 13,
  },
});
