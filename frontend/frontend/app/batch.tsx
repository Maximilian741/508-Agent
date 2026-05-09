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
import { appendHistory } from "../src/domain/auditHistory";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { PixelSpinner } from "../src/ui/components/PixelSpinner";
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
  /** Populated when status === "error". */
  errorMessage?: string;
  /** ISO timestamp of state transition — used only for the "ranAt" history field. */
  finishedAt?: string;
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

  // Track in-flight analyses so we can throttle to MAX_CONCURRENCY.
  const inFlightRef = useRef(0);
  const processingRef = useRef(false);

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
                }
              : it,
          ),
        );
      } catch (e) {
        const msg = (e as Error).message ?? "Analyzer failed";
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
        accepted.push({
          id,
          filename: file.name,
          sizeBytes: file.size,
          status: "queued",
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
