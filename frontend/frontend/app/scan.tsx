import { useEffect, useMemo, useRef, useState } from "react";
import {
  FlatList,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
  Linking,
} from "react-native";
import { useRouter } from "expo-router";

import { DocumentIssue, TagTreeResponse, TagTreeNode } from "../src/api/client";
import { useAppStore } from "../src/store/useAppStore";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { EmptyState } from "../src/ui/components/EmptyState";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

const severityOrder: Record<string, number> = { error: 0, warning: 1, info: 2 };

export default function ScanScreen() {
  const router = useRouter();
  const theme = useTheme();
  const { width } = useWindowDimensions();
  const scanResults = useAppStore((state) => state.scanResults);
  const selectedDocument = useAppStore((state) => state.selectedDocument);
  const uploadedDocument = useAppStore((state) => state.uploadedDocument);
  const scanJob = useAppStore((state) => state.scanJob);
  const documentIssues = useAppStore((state) => state.documentIssues);
  const applyDocumentFixes = useAppStore((state) => state.applyDocumentFixes);
  const fixedDocId = useAppStore((state) => state.fixedDocId);
  const fetchDocumentDiff = useAppStore((state) => state.fetchDocumentDiff);
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const documentSummary = useAppStore((state) => state.documentSummary);
  const tagTree = useAppStore((state) => state.tagTree);
  const fixReport = useAppStore((state) => state.fixReport);
  const [afterVariant, setAfterVariant] = useState<"fixed" | "rebuilt">("fixed");
  const [sortDescending, setSortDescending] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [severityFilter, setSeverityFilter] = useState<"all" | "error" | "warning" | "info">("all");
  const [activeTab, setActiveTab] = useState<"issues" | "tree">("issues");
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
  const [treeLimit, setTreeLimit] = useState(500);
  const [diffText, setDiffText] = useState<string>("");
  const [diffError, setDiffError] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [showPdfHover, setShowPdfHover] = useState(false);
  const [focusedIssue, setFocusedIssue] = useState<DocumentIssue | null>(null);
  const [openSeverity, setOpenSeverity] = useState<"error" | "warning" | "info" | null>(null);
  const [noVisualDiff, setNoVisualDiff] = useState(false);
  const [showIssueHighlight, setShowIssueHighlight] = useState(true);
  const [fixReportFilter, setFixReportFilter] = useState<"all" | "fixed" | "remaining" | "manual">("all");
  const [canvasSize, setCanvasSize] = useState<{ width: number; height: number } | null>(null);
  const originalCanvasRefs = useRef<HTMLCanvasElement[]>([]);
  const fixedCanvasRefs = useRef<HTMLCanvasElement[]>([]);
  const renderTaskRef = useRef<{ original?: any; fixed?: any }>({});
  const renderSeqRef = useRef(0);
  const overlayCanvasRefs = useRef<HTMLCanvasElement[]>([]);
  const overlayFixedCanvasRefs = useRef<HTMLCanvasElement[]>([]);

  const isWide = Platform.OS === "web" && width >= 980;

  const issues = useMemo(() => {
    const list = documentIssues;
    const filtered = list.filter((issue) => {
      const matchesSeverity = severityFilter === "all" || issue.severity === severityFilter;
      const term = searchTerm.trim().toLowerCase();
      const matchesSearch =
        term.length === 0 ||
        issue.description.toLowerCase().includes(term) ||
        issue.ruleId.toLowerCase().includes(term) ||
        issue.nodeId.toLowerCase().includes(term);
      return matchesSeverity && matchesSearch;
    });
    const sorted = [...filtered].sort((a, b) => {
      const diff = severityOrder[a.severity] - severityOrder[b.severity];
      return sortDescending ? diff : -diff;
    });
    return sorted;
  }, [documentIssues, sortDescending, searchTerm, severityFilter]);

  const counts = useMemo(() => {
    const list = documentIssues;
    return {
      error: list.filter((issue) => issue.severity === "error").length,
      warning: list.filter((issue) => issue.severity === "warning").length,
      info: list.filter((issue) => issue.severity === "info").length,
      total: list.length,
    };
  }, [documentIssues]);

  const docIssueCounts = useMemo(() => {
    return {
      error: documentIssues.filter((issue) => issue.severity === "error").length,
      warning: documentIssues.filter((issue) => issue.severity === "warning").length,
      info: documentIssues.filter((issue) => issue.severity === "info").length,
      total: documentIssues.length,
    };
  }, [documentIssues]);

  const issueCategories = useMemo(() => {
    const grouped: Record<string, { issue: DocumentIssue; count: number }> = {};
    documentIssues.forEach((issue) => {
      if (!grouped[issue.ruleId]) {
        grouped[issue.ruleId] = { issue, count: 1 };
      } else {
        grouped[issue.ruleId].count += 1;
      }
    });
    return Object.values(grouped).sort((a, b) => {
      const severityDiff = severityOrder[a.issue.severity] - severityOrder[b.issue.severity];
      if (severityDiff !== 0) return severityDiff;
      return a.issue.ruleId.localeCompare(b.issue.ruleId);
    });
  }, [documentIssues]);

  const severityGroups = useMemo(() => {
    return {
      error: issueCategories.filter((item) => item.issue.severity === "error"),
      warning: issueCategories.filter((item) => item.issue.severity === "warning"),
      info: issueCategories.filter((item) => item.issue.severity === "info"),
    };
  }, [issueCategories]);

  const filteredFixReport = useMemo(() => {
    if (!fixReport) return { fixed: [], remaining: [], introduced: [], manual: [] };
    const fixed = fixReport.delta?.fixed ?? [];
    const remaining = fixReport.delta?.remaining ?? [];
    const introduced = fixReport.delta?.introduced ?? [];
    const manual = fixReport.manualReview ?? [];
    if (fixReportFilter === "fixed") {
      return { fixed, remaining: [], introduced: [], manual: [] };
    }
    if (fixReportFilter === "remaining") {
      return { fixed: [], remaining, introduced: [], manual: [] };
    }
    if (fixReportFilter === "manual") {
      return { fixed: [], remaining: [], introduced: [], manual };
    }
    return { fixed, remaining, introduced, manual };
  }, [fixReport, fixReportFilter]);

  const focusedIssueStatus = useMemo(() => {
    if (!fixReport || !focusedIssue) return "unknown";
    const key = makeIssueKey(focusedIssue);
    const inFixed = (fixReport.delta?.fixed ?? []).some((issue) => makeIssueKey(issue) === key);
    if (inFixed) return "fixed";
    const inRemaining = (fixReport.delta?.remaining ?? []).some((issue) => makeIssueKey(issue) === key);
    if (inRemaining) return "remaining";
    const inIntroduced = (fixReport.delta?.introduced ?? []).some((issue) => makeIssueKey(issue) === key);
    if (inIntroduced) return "introduced";
    return "unknown";
  }, [fixReport, focusedIssue]);

  if (!scanResults && documentIssues.length === 0 && !scanJob) {
    return (
      <Screen>
        <EmptyState
          title="No scan results"
          message="Run a scan from the Documents screen to see accessibility issues."
          icon="search"
        />
      </Screen>
    );
  }

  const displayDocumentId = uploadedDocument?.docId ?? scanResults?.documentId ?? "doc-1";
  const originalUrl = uploadedDocument ? `${apiBaseUrl}/documents/${uploadedDocument.docId}/pdf` : null;
  const fixedUrl = uploadedDocument ? `${apiBaseUrl}/documents/${uploadedDocument.docId}/pdf-fixed` : null;
  const rebuiltUrl = uploadedDocument ? `${apiBaseUrl}/documents/${uploadedDocument.docId}/pdf-rebuilt` : null;
  const fixedAvailable = fixReport?.fixedExists === true;
  const rebuiltAvailable = fixReport?.rebuiltExists === true;
  const afterUrl =
    afterVariant === "rebuilt" && rebuiltAvailable
      ? rebuiltUrl
      : fixedAvailable
      ? fixedUrl
      : null;
  const openUrl = (url: string) => {
    if (Platform.OS === "web") {
      window.open(url, "_blank");
      return;
    }
    void Linking.openURL(url);
  };

  const openManualReview = () => {
    router.push("/manual-review");
  };

  useEffect(() => {
    const loadDiff = async () => {
      if (!uploadedDocument || !afterUrl) return;
      const result = await fetchDocumentDiff(uploadedDocument.docId);
      if (result.ok && result.data) {
        setDiffText(result.data.diffText || "");
        setDiffError(null);
      } else {
        setDiffError(result.error ?? "Unable to load diff.");
      }
    };
    void loadDiff();
  }, [uploadedDocument, afterUrl, fetchDocumentDiff]);

  useEffect(() => {
    if (afterVariant === "rebuilt" && !rebuiltAvailable) {
      setAfterVariant("fixed");
    }
  }, [afterVariant, rebuiltAvailable]);

  useEffect(() => {
    if (!afterUrl && fixReport) {
      const message = fixReport.fixedExists
        ? "After artifact not available yet."
        : "Fixed artifact not available. Apply Fixes again to generate fixed PDF.";
      setPdfError(message);
    }
  }, [afterUrl, fixReport]);

  useEffect(() => {
    const renderVisualDiff = async () => {
      if (!uploadedDocument || !afterUrl) return;
      if (Platform.OS !== "web") return;
      setPdfLoading(true);
      setPdfError(null);
      try {
        const loadPdfJs = (): Promise<any> =>
          new Promise((resolve, reject) => {
            if (typeof window === "undefined") {
              reject(new Error("PDF.js is only available on web."));
              return;
            }
            // @ts-ignore
            if (window.pdfjsLib) {
              // @ts-ignore
              resolve(window.pdfjsLib);
              return;
            }
            const url = `${window.location.origin}/pdfjs/pdf.mjs`;
            const dynamicImport = (0, eval) as (code: string) => Promise<any>;
            dynamicImport(`import("${url}")`)
              .then((module) => {
                const resolved = module?.default ?? module;
                if (!resolved) {
                  reject(new Error("PDF.js module did not load."));
                  return;
                }
                // @ts-ignore
                window.pdfjsLib = resolved;
                resolve(resolved);
              })
              .catch(() => reject(new Error("Failed to load PDF.js")));
          });

        const pdfjs = await loadPdfJs();
        // @ts-ignore
        pdfjs.GlobalWorkerOptions.workerSrc = `${window.location.origin}/pdfjs/pdf.worker.mjs`;
        if (!originalUrl || !afterUrl) {
          setPdfError("Missing document URLs for diff rendering.");
          return;
        }
        const [originalResponse, fixedResponse] = await Promise.all([
          fetch(originalUrl, { cache: "no-store", mode: "cors" }),
          fetch(afterUrl, { cache: "no-store", mode: "cors" }),
        ]);
        if (!originalResponse.ok) {
          throw new Error(`Original PDF fetch failed: ${originalResponse.status}`);
        }
        if (!fixedResponse.ok) {
          const targetLabel = afterVariant === "rebuilt" ? "rebuilt" : "fixed";
          throw new Error(
            `After PDF fetch failed (${targetLabel}): ${fixedResponse.status} for ${afterUrl ?? "unknown URL"}`,
          );
        }
        const [originalBuffer, fixedBuffer] = await Promise.all([
          originalResponse.arrayBuffer(),
          fixedResponse.arrayBuffer(),
        ]);
        const originalTask = pdfjs.getDocument({ data: originalBuffer, disableWorker: true });
        const fixedTask = pdfjs.getDocument({ data: fixedBuffer, disableWorker: true });
        const originalPdf = await originalTask.promise;
        const fixedPdf = await fixedTask.promise;
        const maxPages = Math.min(originalPdf.numPages, fixedPdf.numPages);
        setPageCount(maxPages);
        const safePage = Math.min(Math.max(currentPage, 1), maxPages || 1);
        if (safePage !== currentPage) {
          setCurrentPage(safePage);
        }
        const originalPage = await originalPdf.getPage(safePage);
        const fixedPage = await fixedPdf.getPage(safePage);
        const scale = 0.45;
        const originalViewport = originalPage.getViewport({ scale });
        const fixedViewport = fixedPage.getViewport({ scale });
        setCanvasSize({
          width: Math.round(originalViewport.width),
          height: Math.round(originalViewport.height),
        });
        const originalCanvas = originalCanvasRefs.current[0];
        const fixedCanvas = fixedCanvasRefs.current[0];
        const renderId = renderSeqRef.current + 1;
        renderSeqRef.current = renderId;
        if (originalCanvas) {
          const ctx = originalCanvas.getContext("2d");
          originalCanvas.height = originalViewport.height;
          originalCanvas.width = originalViewport.width;
          if (ctx) {
            if (renderTaskRef.current.original?.cancel) {
              renderTaskRef.current.original.cancel();
            }
            const task = originalPage.render({ canvasContext: ctx, viewport: originalViewport });
            renderTaskRef.current.original = task;
            try {
              await task.promise;
            } catch (err) {
              if (renderSeqRef.current !== renderId) {
                return;
              }
            }
          }
        }
        if (fixedCanvas) {
          const ctx = fixedCanvas.getContext("2d");
          fixedCanvas.height = fixedViewport.height;
          fixedCanvas.width = fixedViewport.width;
          if (ctx) {
            if (renderTaskRef.current.fixed?.cancel) {
              renderTaskRef.current.fixed.cancel();
            }
            const task = fixedPage.render({ canvasContext: ctx, viewport: fixedViewport });
            renderTaskRef.current.fixed = task;
            try {
              await task.promise;
            } catch (err) {
              if (renderSeqRef.current !== renderId) {
                return;
              }
            }
          }
        }
        const overlayOriginal = overlayCanvasRefs.current[0];
        const overlayFixed = overlayFixedCanvasRefs.current[0];
        drawHighlightBoxes(overlayOriginal, [], "danger");
        drawHighlightBoxes(overlayFixed, [], "danger");
        if (overlayOriginal) {
          overlayOriginal.height = originalViewport.height;
          overlayOriginal.width = originalViewport.width;
        }
        if (overlayFixed) {
          overlayFixed.height = fixedViewport.height;
          overlayFixed.width = fixedViewport.width;
        }
        let issueBoxes: HighlightBox[] = [];
        if (focusedIssue && showIssueHighlight) {
          issueBoxes = await computeIssueBoxes(
            pdfjs,
            originalPage,
            originalViewport,
            focusedIssue,
            currentPage,
          );
        }
        setNoVisualDiff(issueBoxes.length === 0);
        const isFixed = focusedIssueStatus === "fixed";
        drawHighlightBoxes(overlayOriginal, issueBoxes, "danger");
        drawHighlightBoxes(overlayFixed, issueBoxes, isFixed ? "success" : "danger");
      } catch (error) {
        const message =
          error instanceof Error
            ? `Unable to render visual diff: ${error.message}`
            : "Unable to render visual diff. Check that the PDFs are reachable.";
        setPdfError(message);
      } finally {
        setPdfLoading(false);
      }
    };
    void renderVisualDiff();
  }, [uploadedDocument, afterUrl, originalUrl, currentPage, focusedIssue, afterVariant]);

  return (
    <Screen scroll>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>Scan Results</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            {counts.total} issues detected for {displayDocumentId}
          </Text>
        </View>
        <Chip label={`Sorted ${sortDescending ? "High to Low" : "Low to High"}`} tone="info" />
      </View>

      {documentIssues.length > 0 && (
        <Card>
          <View style={styles.searchRow}>
            <TextInput
              value={searchTerm}
              onChangeText={setSearchTerm}
              placeholder="Search by rule or text"
              placeholderTextColor={theme.colors.textMuted}
              style={[styles.searchInput, { borderColor: theme.colors.border, color: theme.colors.text }]}
            />
            <Pressable onPress={() => setSortDescending((prev) => !prev)}>
              <Text style={[styles.sortLink, { color: theme.colors.accent }]}>Toggle sort</Text>
            </Pressable>
          </View>
          <View style={styles.filterRow}>
            {(["all", "error", "warning", "info"] as const).map((value) => (
              <Pressable key={value} onPress={() => setSeverityFilter(value)}>
                {({ hovered, pressed }) => (
                  <Chip
                    label={value === "all" ? "All" : value === "error" ? "Issue" : value === "warning" ? "Warning" : "Info"}
                    tone={value === "error" ? "danger" : value === "warning" ? "warning" : value === "info" ? "info" : "default"}
                    style={[
                      severityFilter === value
                        ? {
                            backgroundColor:
                              value === "error"
                                ? theme.colors.danger
                                : value === "warning"
                                ? theme.colors.warning
                                : value === "info"
                                ? theme.colors.info
                                : theme.colors.accent,
                            borderColor:
                              value === "error"
                                ? theme.colors.danger
                                : value === "warning"
                                ? theme.colors.warning
                                : value === "info"
                                ? theme.colors.info
                                : theme.colors.accent,
                          }
                        : undefined,
                      hovered ? styles.filterHover : null,
                      pressed ? styles.filterPressed : null,
                    ]}
                    textStyle={severityFilter === value ? { color: theme.colors.surface } : undefined}
                  />
                )}
              </Pressable>
            ))}
          </View>
        </Card>
      )}

      {scanJob && scanJob.status !== "done" && (
        <InlineNotice
          title="Scanning in progress"
          message={`${scanJob.status} - ${scanJob.progress}%`}
          tone="info"
        />
      )}
      {scanJob && scanJob.status === "done" && documentIssues.length === 0 && (
        <InlineNotice
          title="Finalizing results"
          message="Scan completed. Loading issues..."
          tone="info"
        />
      )}

      {uploadedDocument && (
        <Card>
          <View style={styles.summaryRow}>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Active Document</Text>
            <Chip label={`Doc ${uploadedDocument.docId}`} tone="info" />
          </View>
          <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
            {uploadedDocument.filename}
          </Text>
          <View style={styles.summaryRow}>
            <Pressable onPress={openManualReview}>
              <Chip label="Manual Review Queue" tone="warning" />
            </Pressable>
          </View>
          <DocumentTree
            documentId={displayDocumentId}
            filename={uploadedDocument?.filename ?? null}
            issues={documentIssues}
            summary={documentSummary}
            tagTree={tagTree}
            highlightNodeId={highlightNodeId}
            treeLimit={treeLimit}
            onShowMore={() => setTreeLimit((prev) => prev + 500)}
          />
        </Card>
      )}

      {documentIssues.length > 0 && (
        <Card>
          <View style={styles.summaryRow}>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Document Issues</Text>
            {uploadedDocument && <Chip label={`Doc ${uploadedDocument.docId}`} tone="info" />}
          </View>
          <IssuesList
            issues={issues}
            onSelectIssue={(issue) => {
              const nodeIds = (issue.evidence?.nodeIds as string[]) ?? [];
              if (nodeIds.length > 0) {
                setHighlightNodeId(nodeIds[0]);
                setActiveTab("tree");
              }
              setFocusedIssue(issue);
              const page = getIssuePage(issue);
              if (page !== null) {
                const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                setCurrentPage(clamped);
              }
            }}
          />
          <Pressable
            onPress={() => uploadedDocument && applyDocumentFixes(uploadedDocument.docId)}
            style={styles.applyFixes}
          >
            <Chip label={fixedDocId ? "Fixes applied" : "Apply Fixes"} tone={fixedDocId ? "success" : "info"} />
          </Pressable>
          {originalUrl && (
            <View style={styles.downloadRow}>
              <Pressable onPress={() => openUrl(originalUrl)}>
                <Text style={[styles.link, { color: theme.colors.accent }]}>Download original</Text>
              </Pressable>
              {fixedUrl && (
                <Pressable onPress={() => openUrl(fixedUrl)}>
                  <Text style={[styles.link, { color: theme.colors.accent }]}>Download fixed</Text>
                </Pressable>
              )}
            </View>
          )}
          {fixedDocId && (
            <View style={styles.diffRow}>
              <View style={styles.diffPanel}>
                <Text style={[styles.diffTitle, { color: theme.colors.text }]}>Before</Text>
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  {documentIssues.length + 1} issues detected before fixes
                </Text>
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  {fixReport?.applied_fixes?.[0]
                    ? `Top fixed: ${fixReport.applied_fixes[0].ruleId}`
                    : "Top fixed: none"}
                </Text>
              </View>
              <View style={styles.diffPanel}>
                <Text style={[styles.diffTitle, { color: theme.colors.text }]}>After</Text>
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  {documentIssues.length} issues remaining after fixes
                </Text>
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  {fixReport?.remaining_issues?.[0]
                    ? `Top remaining: ${fixReport.remaining_issues[0].ruleId}`
                    : "Top remaining: none"}
                </Text>
              </View>
            </View>
          )}
        </Card>
      )}

          {fixedDocId && (
            <Card>
              <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Fix Report</Text>
              {fixReport ? (
                <View style={styles.fixReport}>
              <View style={styles.summaryRow}>
                <Chip label={`Before: ${fixReport.before.issueCount}`} tone="default" />
                <Chip label={`After: ${fixReport.after.issueCount}`} tone="default" />
              </View>
              <View style={styles.summaryRow}>
                <Pressable onPress={() => setFixReportFilter("all")}>
                  <Chip
                    label={`All (${(fixReport.delta?.fixed?.length ?? 0) + (fixReport.delta?.remaining?.length ?? 0) + (fixReport.delta?.introduced?.length ?? 0) + (fixReport.manualReview?.length ?? 0)})`}
                    tone="default"
                    style={fixReportFilter === "all" ? styles.filterActiveDefault : undefined}
                    textStyle={fixReportFilter === "all" ? styles.filterActiveText : undefined}
                  />
                </Pressable>
                <Pressable onPress={() => setFixReportFilter("fixed")}>
                  <Chip
                    label={`Fixed (${fixReport.delta?.fixed?.length ?? 0})`}
                    tone="success"
                    style={fixReportFilter === "fixed" ? styles.filterActiveSuccess : undefined}
                    textStyle={fixReportFilter === "fixed" ? styles.filterActiveText : undefined}
                  />
                </Pressable>
                <Pressable onPress={() => setFixReportFilter("remaining")}>
                  <Chip
                    label={`Remaining (${fixReport.delta?.remaining?.length ?? 0})`}
                    tone="warning"
                    style={fixReportFilter === "remaining" ? styles.filterActiveWarning : undefined}
                    textStyle={fixReportFilter === "remaining" ? styles.filterActiveText : undefined}
                  />
                </Pressable>
                <Pressable onPress={() => setFixReportFilter("manual")}>
                  <Chip
                    label={`Manual review (${fixReport.manualReview?.length ?? 0})`}
                    tone="info"
                    style={fixReportFilter === "manual" ? styles.filterActiveInfo : undefined}
                    textStyle={fixReportFilter === "manual" ? styles.filterActiveText : undefined}
                  />
                </Pressable>
              </View>
              <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                Deterministic: {fixReport.deterministic ? "yes" : "no"} • Mode: {fixReport.mode}
              </Text>
              <View style={styles.fixList}>
                {filteredFixReport.fixed.map((issue) => (
                  <Pressable
                    key={issue.id}
                    onPress={() => {
                      setFocusedIssue(issue);
                      const page = getIssuePage(issue);
                      if (page !== null) {
                        const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                        setCurrentPage(clamped);
                      }
                    }}
                  >
                    <View style={styles.fixRow}>
                      <Chip
                        label={issue.severity.toUpperCase()}
                        tone={issue.severity === "error" ? "danger" : issue.severity === "warning" ? "warning" : "info"}
                        icon={<Text style={{ color: theme.colors.success, fontWeight: "700" }}>✓</Text>}
                      />
                      <Chip label={issue.ruleId} />
                    </View>
                    <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>{issue.title}</Text>
                  </Pressable>
                ))}
              </View>
              {filteredFixReport.remaining.length > 0 && (
                <View style={styles.fixList}>
                  <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>Remaining issues</Text>
                  {filteredFixReport.remaining.map((item) => (
                    <Pressable
                      key={item.id}
                      onPress={() => {
                        const page = getIssuePage(item);
                        if (page !== null) {
                          const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                          setCurrentPage(clamped);
                        }
                      }}
                    >
                      <View style={styles.fixRow}>
                        <Chip label={item.severity.toUpperCase()} tone={item.severity === "error" ? "danger" : item.severity === "warning" ? "warning" : "info"} />
                        <Chip label={item.ruleId} />
                      </View>
                      <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>{item.title}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
              {filteredFixReport.introduced.length > 0 && (
                <View style={styles.fixList}>
                  <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>Introduced issues</Text>
                  {filteredFixReport.introduced.map((item) => (
                    <Pressable
                      key={item.id}
                      onPress={() => {
                        const page = getIssuePage(item);
                        if (page !== null) {
                          const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                          setCurrentPage(clamped);
                        }
                        setFocusedIssue(item);
                      }}
                    >
                      <View style={styles.fixRow}>
                        <Chip label={item.severity.toUpperCase()} tone={item.severity === "error" ? "danger" : item.severity === "warning" ? "warning" : "info"} />
                        <Chip label={item.ruleId} />
                      </View>
                      <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>{item.title}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
              {filteredFixReport.manual.length > 0 && (
                <View style={styles.fixList}>
                  <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>Manual review items</Text>
                  {filteredFixReport.manual.map((item) => (
                    <Pressable
                      key={item.id}
                      onPress={() => {
                        const page = item.pages?.[0];
                        if (typeof page === "number") {
                          const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                          setCurrentPage(clamped);
                        }
                      }}
                      >
                      <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                        {item.reason} • {item.suggestedFix ?? "Manual review required"}
                      </Text>
                    </Pressable>
                  ))}
                </View>
              )}
            </View>
          ) : (
            <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
              Fix report will appear after Apply Fixes.
            </Text>
          )}
          {rebuiltAvailable && (
            <View style={styles.summaryRow}>
              <Pressable onPress={() => setAfterVariant("fixed")}>
                <Chip
                  label="After: Fixed"
                  tone="default"
                  style={afterVariant === "fixed" ? styles.filterActiveDefault : undefined}
                  textStyle={afterVariant === "fixed" ? styles.filterActiveText : undefined}
                />
              </Pressable>
              <Pressable onPress={() => setAfterVariant("rebuilt")}>
                <Chip
                  label="After: Rebuilt"
                  tone="default"
                  style={afterVariant === "rebuilt" ? styles.filterActiveDefault : undefined}
                  textStyle={afterVariant === "rebuilt" ? styles.filterActiveText : undefined}
                />
              </Pressable>
            </View>
          )}
          <Card style={styles.diffTextCard}>
            <View style={styles.diffHeader}>
              <Text style={[styles.diffTitle, { color: theme.colors.text }]}>Visual PDF Diff</Text>
              {pdfLoading && (
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>Rendering page...</Text>
              )}
            </View>
            {Platform.OS !== "web" && (
              <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                Visual diff is available on web only.
              </Text>
            )}
            {pdfError && (
              <Text style={[styles.nodeId, { color: theme.colors.danger }]}>{pdfError}</Text>
            )}
            {!pdfError && noVisualDiff && (
              <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                No structural highlight available for this issue on this page.
              </Text>
            )}
            {Platform.OS === "web" && !pdfError && (
              <View style={styles.visualDiffGrid}>
                <View style={styles.summaryRow}>
                  <Pressable
                    onPress={() => {
                      setOpenSeverity((prev) => (prev === "error" ? null : "error"));
                    }}
                  >
                    <Chip label={`Errors: ${docIssueCounts.error}`} tone="danger" />
                  </Pressable>
                  <Pressable
                    onPress={() => {
                      setOpenSeverity((prev) => (prev === "warning" ? null : "warning"));
                    }}
                  >
                    <Chip label={`Warnings: ${docIssueCounts.warning}`} tone="warning" />
                  </Pressable>
                  <Pressable
                    onPress={() => {
                      setOpenSeverity((prev) => (prev === "info" ? null : "info"));
                    }}
                  >
                    <Chip label={`Info: ${docIssueCounts.info}`} tone="info" />
                  </Pressable>
                </View>
                <Pressable onPress={() => setShowIssueHighlight((prev) => !prev)}>
                  <Chip label={showIssueHighlight ? "Issue highlight: On" : "Issue highlight: Off"} tone="info" />
                </Pressable>
                {openSeverity && (
                  <View style={styles.dropdown}>
                      <Text style={[styles.dropdownTitle, { color: theme.colors.text }]}>
                        {openSeverity === "error" ? "Issue" : openSeverity === "warning" ? "Warning" : "Info"} issues
                      </Text>
                    <View style={styles.dropdownList}>
                      <FlatList
                        data={severityGroups[openSeverity]}
                        keyExtractor={(item) => item.issue.ruleId}
                        renderItem={({ item }) => (
                          <Pressable
                            onPress={() => {
                              setFocusedIssue(item.issue);
                              const page = getIssuePage(item.issue);
                              if (page !== null) {
                                const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                                setCurrentPage(clamped);
                              }
                            }}
                          >
                            <View style={styles.dropdownItem}>
                              <Chip label={getIssuePage(item.issue) !== null ? `${item.issue.ruleId} (p. ${getIssuePage(item.issue)})` : item.issue.ruleId} />
                              <Chip label={`${item.count}`} tone="info" />
                            </View>
                            <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                              {item.issue.title}
                            </Text>
                          </Pressable>
                        )}
                      />
                    </View>
                  </View>
                )}
                <View style={styles.pageControls}>
                  <Pressable
                    onPress={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                    disabled={currentPage <= 1}
                  >
                    <Chip label="Prev" tone={currentPage <= 1 ? "default" : "info"} />
                  </Pressable>
                  <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                    Page {currentPage} of {pageCount || 1}
                  </Text>
                  <Pressable
                    onPress={() => setCurrentPage((prev) => Math.min(pageCount || 1, prev + 1))}
                    disabled={pageCount > 0 && currentPage >= pageCount}
                  >
                    <Chip label="Next" tone={pageCount > 0 && currentPage >= pageCount ? "default" : "info"} />
                  </Pressable>
                </View>
                <View style={styles.visualDiffRow}>
                  <View
                    style={styles.visualPanel}
                    onMouseEnter={() => setShowPdfHover(true)}
                    onMouseLeave={() => setShowPdfHover(false)}
                  >
                    <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                      Before p.{currentPage}
                    </Text>
                    <View style={styles.canvasWrap}>
                      <canvas
                        ref={(ref) => {
                          if (ref) originalCanvasRefs.current[0] = ref;
                        }}
                        style={getCanvasStyle(styles.canvas, canvasSize)}
                      />
                      <canvas
                        ref={(ref) => {
                          if (ref) overlayCanvasRefs.current[0] = ref;
                        }}
                        style={getCanvasStyle(styles.overlayCanvas, canvasSize)}
                      />
                      {showPdfHover && focusedIssue && (
                        <View style={styles.overlayHint}>
                          <Text style={[styles.overlayText, { color: theme.colors.text }]}>
                            Issue: {focusedIssue.title}
                          </Text>
                          <Text style={[styles.overlayTextMuted, { color: theme.colors.textMuted }]}>
                            {focusedIssue.locationHint}
                          </Text>
                        </View>
                      )}
                    </View>
                  </View>
                  <View
                    style={styles.visualPanel}
                    onMouseEnter={() => setShowPdfHover(true)}
                    onMouseLeave={() => setShowPdfHover(false)}
                  >
                    <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                      After p.{currentPage}
                    </Text>
                    <View style={styles.canvasWrap}>
                      <canvas
                        ref={(ref) => {
                          if (ref) fixedCanvasRefs.current[0] = ref;
                        }}
                        style={getCanvasStyle(styles.canvas, canvasSize)}
                      />
                      <canvas
                        ref={(ref) => {
                          if (ref) overlayFixedCanvasRefs.current[0] = ref;
                        }}
                        style={getCanvasStyle(styles.overlayCanvas, canvasSize)}
                      />
                      {showPdfHover && focusedIssue && (
                        <View style={styles.overlayHint}>
                          <Text style={[styles.overlayText, { color: theme.colors.text }]}>
                            Issue: {focusedIssue.title}
                          </Text>
                          <Text style={[styles.overlayTextMuted, { color: theme.colors.textMuted }]}>
                            {focusedIssue.locationHint}
                          </Text>
                        </View>
                      )}
                    </View>
                  </View>
                </View>
              </View>
            )}
          </Card>
        </Card>
      )}

      {isWide ? (
        <View style={styles.columns}>
          <View style={styles.leftColumn}>
            <View />
          </View>
          <View style={styles.rightColumn}>
            {!documentIssues.length && (
              <EmptyState
                title="Issues will appear here"
                message="Scan is running or has not produced results yet."
                icon="hourglass-empty"
                tone="info"
              />
            )}
          </View>
        </View>
      ) : (
        <View style={styles.mobileTabs}>
          <View style={styles.tabRow}>
            <Pressable onPress={() => setActiveTab("issues")}>
              <Chip label="Issues" tone={activeTab === "issues" ? "info" : "default"} />
            </Pressable>
            <Pressable onPress={() => setActiveTab("tree")}>
              <Chip label="Document Tree" tone={activeTab === "tree" ? "info" : "default"} />
            </Pressable>
          </View>
          {activeTab === "tree" ? (
            <View />
          ) : (
            <IssuesList
              issues={documentIssues}
              onSelectIssue={(issue) => {
                const nodeIds = (issue.evidence?.nodeIds as string[]) ?? [];
                if (nodeIds.length > 0) {
                  setHighlightNodeId(nodeIds[0]);
                  setActiveTab("tree");
                }
                setFocusedIssue(issue);
                const page = getIssuePage(issue);
                if (page !== null) {
                  const clamped = pageCount ? Math.min(Math.max(1, page), pageCount) : Math.max(1, page);
                  setCurrentPage(clamped);
                }
              }}
            />
          )}
        </View>
      )}
    </Screen>
  );
}

function DocumentTree({
  documentId,
  filename,
  issues,
  summary,
  tagTree,
  highlightNodeId,
  treeLimit,
  onShowMore,
}: {
  documentId: string;
  filename: string | null;
  issues: DocumentIssue[];
  summary: {
    title: string;
    pages: number;
    images: number;
    tagged: boolean;
    tagCounts: Record<string, number>;
    figures: number;
    figuresMissingAlt: number;
    nodeCount: number;
    outlineCount: number;
    formFields: number;
    unlabeledFields: number;
  } | null;
  tagTree: TagTreeResponse | null;
  highlightNodeId: string | null;
  treeLimit: number;
  onShowMore: () => void;
}) {
  const theme = useTheme();
  const lines = useMemo(
    () => buildTreeLines(documentId, filename, issues, summary),
    [documentId, filename, issues, summary],
  );

  return (
    <Card style={styles.treeContainer}>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Document Tree</Text>
      <Text style={[styles.treeText, { color: theme.colors.textMuted }]}>{lines.join("\n")}</Text>
      {summary?.nodeCount !== undefined && (
        <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
          Tag tree nodes: {summary.nodeCount}
        </Text>
      )}
      {tagTree ? (
        tagTree.tree.nodes && Object.keys(tagTree.tree.nodes).length > 0 ? (
          <TagTreeViewer
            tagTree={tagTree}
            highlightNodeId={highlightNodeId}
            limit={treeLimit}
            onShowMore={onShowMore}
          />
        ) : (
          <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
            No tag tree available (PDF may be untagged or parsing failed).
          </Text>
        )
      ) : (
        <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
          Tag tree data not available yet.
        </Text>
      )}
    </Card>
  );
}

function buildTreeLines(
  documentId: string,
  filename: string | null,
  issues: DocumentIssue[],
  summary: {
    title: string;
    pages: number;
    images: number;
    tagged: boolean;
    tagCounts: Record<string, number>;
    figures: number;
    figuresMissingAlt: number;
    nodeCount: number;
    outlineCount: number;
    formFields: number;
    unlabeledFields: number;
  } | null,
): string[] {
  const lines: string[] = [];
  lines.push(`document (${documentId})`);
  if (filename) {
    lines.push(`  file ${JSON.stringify(filename)}`);
  }
  if (summary) {
    lines.push(`  pages ${summary.pages}`);
    lines.push(`  title ${summary.title ? JSON.stringify(summary.title) : "(missing)"}`);
    lines.push(`  images ${summary.images}`);
    lines.push(`  tagged ${summary.tagged ? "yes" : "no"}`);
    if (!summary.tagged) {
      lines.push("  structure tags (not tagged)");
    } else {
      const tagOrder = [
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
        "P",
        "L",
        "LI",
        "Table",
        "TR",
        "TH",
        "TD",
        "Figure",
      ];
      const tagLines = tagOrder
        .map((tag) => {
          const count = summary.tagCounts[tag];
          return count ? `    ${tag}: ${count}` : null;
        })
        .filter((value): value is string => Boolean(value));
      if (tagLines.length) {
        lines.push("  structure tags");
        lines.push(...tagLines);
      } else {
        lines.push("  structure tags (none detected)");
      }
      if (summary.figures) {
        lines.push(`  figures ${summary.figures} (missing alt: ${summary.figuresMissingAlt})`);
      }
    }
    lines.push(`  outline ${summary.outlineCount}`);
    lines.push(`  form fields ${summary.formFields}`);
    lines.push(`  unlabeled fields ${summary.unlabeledFields}`);
  }
  if (issues.length === 0) {
    lines.push("  issues (none detected yet)");
    return lines;
  }
  lines.push(`  issues (${issues.length})`);
  issues.forEach((issue, index) => {
    lines.push(`    ${index + 1}. ${issue.ruleId} (${issue.severity})`);
  });
  return lines;
}

function makeIssueKey(issue: DocumentIssue): string {
  const ruleId = (issue.ruleId ?? "").trim().toLowerCase();
  const severity = (issue.severity ?? "").trim().toLowerCase();
  const title = (issue.title ?? "").trim().toLowerCase();
  const location = (issue.locationHint ?? "").trim().toLowerCase();
  const evidence = issue.evidence ?? {};
  const nodeIds = Array.isArray(evidence.nodeIds)
    ? (evidence.nodeIds as string[]).map((value) => String(value)).slice(0, 10).sort()
    : [];
  const pages = Array.isArray(evidence.pages)
    ? (evidence.pages as number[]).filter((value) => typeof value === "number").map((value) => String(value)).sort()
    : [];
  return [
    `rule:${ruleId}`,
    `sev:${severity}`,
    `nodes:${nodeIds.join(",")}`,
    `pages:${pages.join(",")}`,
    `loc:${location}`,
    `title:${title}`,
  ].join("|");
}

function getIssuePage(issue: DocumentIssue): number | null {
  const evidencePages = issue.evidence?.pages;
  if (Array.isArray(evidencePages) && evidencePages.length > 0) {
    const pageValue = evidencePages.find((value) => typeof value === "number");
    if (typeof pageValue === "number") {
      return Math.max(1, Math.round(pageValue));
    }
  }
  const evidencePage = issue.evidence?.page;
  if (typeof evidencePage === "number" && Number.isFinite(evidencePage)) {
    return Math.max(1, Math.round(evidencePage));
  }
  const hint = issue.locationHint ?? "";
  const match = hint.match(/page\s+(\d+)/i);
  if (match) {
    return Math.max(1, Number.parseInt(match[1], 10));
  }
  return null;
}

function IssuesList({
  issues,
  onSelectIssue,
}: {
  issues: DocumentIssue[];
  onSelectIssue?: (issue: DocumentIssue) => void;
}) {
  if (issues.length === 0) {
    return (
      <EmptyState
        title="No matching issues"
        message="Try adjusting filters or run another scan."
        icon="check-circle"
        tone="info"
      />
    );
  }

  return (
    <FlatList
      data={issues}
      keyExtractor={(item) => item.id}
      renderItem={({ item }) => <DocumentIssueRow issue={item} onSelectIssue={onSelectIssue} />}
      ItemSeparatorComponent={() => <View style={styles.separator} />}
    />
  );
}

function DocumentIssueRow({
  issue,
  onSelectIssue,
}: {
  issue: DocumentIssue;
  onSelectIssue?: (issue: DocumentIssue) => void;
}) {
  const theme = useTheme();
  const severityTone = issue.severity === "error" ? "danger" : issue.severity === "warning" ? "warning" : "info";
  const fixableRules = new Set(["document_title_missing", "missing_heading_structure", "unlabeled_form_field"]);
  const fixLabel = fixableRules.has(issue.ruleId) ? "Auto-fix available" : "Not implemented yet";
  const fixTone = fixableRules.has(issue.ruleId) ? "success" : "warning";
  return (
    <Pressable onPress={() => onSelectIssue?.(issue)}>
      <Card style={styles.rowCard}>
        <View style={styles.rowHeader}>
          <Chip label={issue.severity.toUpperCase()} tone={severityTone} />
          <Chip label={issue.ruleId} />
          <Chip label={fixLabel} tone={fixTone} />
        </View>
        <Text style={[styles.description, { color: theme.colors.text }]}>{issue.title}</Text>
        <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>{issue.description}</Text>
        <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
          Location: {issue.locationHint}
        </Text>
        {Array.isArray(issue.evidence?.pages) && issue.evidence.pages.length > 0 && (
          <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
            Pages: {(issue.evidence.pages as number[]).slice(0, 6).join(", ")}
          </Text>
        )}
        {issue.evidence?.nodeIds && (
          <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
            Tag nodes: {(issue.evidence.nodeIds as string[]).slice(0, 3).join(", ")}
          </Text>
        )}
      </Card>
    </Pressable>
  );
}

function TagTreeViewer({
  tagTree,
  highlightNodeId,
  limit,
  onShowMore,
}: {
  tagTree: TagTreeResponse;
  highlightNodeId: string | null;
  limit: number;
  onShowMore: () => void;
}) {
  const theme = useTheme();
  const listRef = useRef<FlatList<{ id: string; depth: number; node: TagTreeNode }>>(null);

  const flattened = useMemo(() => {
    const nodes = tagTree.tree.nodes;
    const result: { id: string; depth: number; node: TagTreeNode }[] = [];
    const walk = (id: string, depth: number) => {
      const node = nodes[id];
      if (!node) return;
      result.push({ id, depth, node });
      node.kids.forEach((kid) => walk(kid, depth + 1));
    };
    walk(tagTree.tree.rootId, 0);
    return result;
  }, [tagTree]);

  useEffect(() => {
    if (!highlightNodeId) return;
    const idx = flattened.findIndex((entry) => entry.id === highlightNodeId);
    if (idx >= 0 && idx < limit) {
      listRef.current?.scrollToIndex({ index: idx, animated: true });
    }
  }, [highlightNodeId, flattened, limit]);

  const data = flattened.slice(0, limit);
  const canShowMore = flattened.length > limit;

  return (
    <View style={styles.tagTreeContainer}>
      <Text style={[styles.tagTreeTitle, { color: theme.colors.text }]}>Tag Tree</Text>
      {tagTree.warnings.length > 0 && (
        <Text style={[styles.nodeId, { color: theme.colors.warning }]}>
          {tagTree.warnings.join(" | ")}
        </Text>
      )}
      <FlatList
        ref={listRef}
        data={data}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => {
          const tagLabel = item.node.tag ?? item.node.role;
          const title = item.node.title ? ` \"${item.node.title}\"` : "";
          const missingAlt = item.node.tag === "Figure" && !item.node.alt;
          const isHighlighted = highlightNodeId === item.id;
          return (
            <View
              style={[
                styles.tagRow,
                { paddingLeft: 12 + item.depth * 12 },
                isHighlighted && { borderColor: theme.colors.accent, borderWidth: 1 },
              ]}
            >
              <Text style={[styles.tagRowText, { color: theme.colors.text }]}>
                {tagLabel}
                {title}
              </Text>
              {missingAlt && <Chip label="missing alt" tone="warning" />}
            </View>
          );
        }}
      />
      {canShowMore && (
        <Pressable onPress={onShowMore} style={styles.showMore}>
          <Text style={[styles.link, { color: theme.colors.accent }]}>Show more</Text>
        </Pressable>
      )}
    </View>
  );
}

type HighlightBox = { x: number; y: number; w: number; h: number };
type HighlightTone = "danger" | "success";

function getCanvasStyle(
  baseStyle: object,
  size: { width: number; height: number } | null,
): Record<string, unknown> {
  const flattened = StyleSheet.flatten(baseStyle) ?? {};
  if (!size) return flattened;
  return { ...flattened, width: size.width, height: size.height };
}

function drawHighlightBoxes(
  canvas: HTMLCanvasElement | null | undefined,
  boxes: HighlightBox[],
  tone: HighlightTone,
) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (boxes.length === 0) return;
  const color = tone === "success" ? "16, 185, 129" : "239, 68, 68";
  ctx.strokeStyle = `rgba(${color}, 0.9)`;
  ctx.fillStyle = `rgba(${color}, 0.15)`;
  ctx.lineWidth = 2;
  boxes.forEach((box) => {
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.strokeRect(box.x, box.y, box.w, box.h);
  });
}

function computeDiffBoxes(
  originalCanvas: HTMLCanvasElement | null,
  fixedCanvas: HTMLCanvasElement | null,
): HighlightBox[] {
  if (!originalCanvas || !fixedCanvas) return [];
  const ctxA = originalCanvas.getContext("2d");
  const ctxB = fixedCanvas.getContext("2d");
  if (!ctxA || !ctxB) return [];
  const width = Math.min(originalCanvas.width, fixedCanvas.width);
  const height = Math.min(originalCanvas.height, fixedCanvas.height);
  if (width === 0 || height === 0) return [];
  const dataA = ctxA.getImageData(0, 0, width, height).data;
  const dataB = ctxB.getImageData(0, 0, width, height).data;
  let minX = width;
  let minY = height;
  let maxX = 0;
  let maxY = 0;
  let changed = false;
  const threshold = 30;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const idx = (y * width + x) * 4;
      const diff =
        Math.abs(dataA[idx] - dataB[idx]) +
        Math.abs(dataA[idx + 1] - dataB[idx + 1]) +
        Math.abs(dataA[idx + 2] - dataB[idx + 2]) +
        Math.abs(dataA[idx + 3] - dataB[idx + 3]);
      if (diff > threshold) {
        changed = true;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (!changed) return [];
  const padding = 6;
  const x = Math.max(0, minX - padding);
  const y = Math.max(0, minY - padding);
  const w = Math.min(width - x, maxX - minX + padding * 2);
  const h = Math.min(height - y, maxY - minY + padding * 2);
  return [{ x, y, w, h }];
}

async function computeIssueBoxes(
  pdfjs: any,
  page: any,
  viewport: any,
  issue: DocumentIssue,
  currentPage: number,
): Promise<HighlightBox[]> {
  if (!issue) return [];
  const anchors = Array.isArray(issue.evidence?.anchors) ? (issue.evidence?.anchors as any[]) : [];
  const anchorsForPage = anchors.filter(
    (anchor) => typeof anchor?.page === "number" && anchor.page === currentPage && typeof anchor?.mcid === "number",
  );
  if (anchorsForPage.length > 0) {
    const boxes = await computeBoxesForAnchors(pdfjs, page, viewport, anchorsForPage);
    if (boxes.length > 0) return boxes;
  }
  if (issue.ruleId === "missing_alt_text") {
    return computeImageBoxes(pdfjs, page, viewport);
  }
  return computeTextBoxes(page, viewport);
}

async function computeTextBoxes(page: any, viewport: any): Promise<HighlightBox[]> {
  const content = await page.getTextContent();
  const boxes: HighlightBox[] = [];
  content.items.forEach((item: any) => {
    if (!item.transform) return;
    const [a, , , d, e, f] = item.transform;
    const x = e;
    const y = f;
    const width = item.width ?? Math.abs(a);
    const height = item.height ?? Math.abs(d);
    const p1 = viewport.convertToViewportPoint(x, y);
    const p2 = viewport.convertToViewportPoint(x + width, y + height);
    const minX = Math.min(p1[0], p2[0]);
    const minY = Math.min(p1[1], p2[1]);
    const maxX = Math.max(p1[0], p2[0]);
    const maxY = Math.max(p1[1], p2[1]);
    if (Number.isFinite(minX) && Number.isFinite(minY) && Number.isFinite(maxX) && Number.isFinite(maxY)) {
      boxes.push({ x: minX, y: minY, w: maxX - minX, h: maxY - minY });
    }
  });
  return boxes.slice(0, 6);
}

async function computeImageBoxes(pdfjs: any, page: any, viewport: any): Promise<HighlightBox[]> {
  const operatorList = await page.getOperatorList();
  const boxes: HighlightBox[] = [];
  const { OPS, Util } = pdfjs;
  let ctm = [1, 0, 0, 1, 0, 0];
  const stack: number[][] = [];
  const applyTransform = (point: number[], matrix: number[]) => Util.applyTransform(point, matrix);
  const toViewport = (point: number[]) => viewport.convertToViewportPoint(point[0], point[1]);

  operatorList.fnArray.forEach((fn: number, index: number) => {
    const args = operatorList.argsArray[index];
    if (fn === OPS.save) {
      stack.push(ctm.slice());
      return;
    }
    if (fn === OPS.restore) {
      const restored = stack.pop();
      if (restored) ctm = restored;
      return;
    }
    if (fn === OPS.transform) {
      ctm = Util.transform(ctm, args);
      return;
    }
    if (
      fn === OPS.paintImageXObject ||
      fn === OPS.paintInlineImageXObject ||
      fn === OPS.paintImageXObjectRepeat ||
      fn === OPS.paintJpegXObject
    ) {
      const p0 = applyTransform([0, 0], ctm);
      const p1 = applyTransform([1, 0], ctm);
      const p2 = applyTransform([1, 1], ctm);
      const p3 = applyTransform([0, 1], ctm);
      const vp0 = toViewport(p0);
      const vp1 = toViewport(p1);
      const vp2 = toViewport(p2);
      const vp3 = toViewport(p3);
      const xs = [vp0[0], vp1[0], vp2[0], vp3[0]];
      const ys = [vp0[1], vp1[1], vp2[1], vp3[1]];
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      if (Number.isFinite(minX) && Number.isFinite(minY) && Number.isFinite(maxX) && Number.isFinite(maxY)) {
        boxes.push({ x: minX, y: minY, w: maxX - minX, h: maxY - minY });
      }
    }
  });
  return boxes.slice(0, 6);
}

async function computeBoxesForAnchors(
  pdfjs: any,
  page: any,
  viewport: any,
  anchors: { page: number; mcid: number; kind?: string }[],
): Promise<HighlightBox[]> {
  const operatorList = await page.getOperatorList();
  const boxes: HighlightBox[] = [];
  const targetMcids = new Set(anchors.map((anchor) => anchor.mcid));
  const { OPS, Util } = pdfjs;
  let ctm = [1, 0, 0, 1, 0, 0];
  const stack: number[][] = [];
  const mcidStack: Array<number | null> = [];
  let textMatrix = [1, 0, 0, 1, 0, 0];
  let textLineMatrix = [1, 0, 0, 1, 0, 0];
  let fontSize = 12;
  let textHScale = 1;

  const applyTransform = (point: number[], matrix: number[]) => Util.applyTransform(point, matrix);
  const toViewport = (point: number[]) => viewport.convertToViewportPoint(point[0], point[1]);
  const readMcid = (props: any): number | null => {
    const candidates = [
      props?.MCID,
      props?.mcid,
      props?.get?.("MCID"),
      props?.get?.("/MCID"),
      props?.["MCID"],
      props?.["/MCID"],
    ];
    for (const value of candidates) {
      if (typeof value === "number" && Number.isFinite(value)) {
        return value;
      }
    }
    return null;
  };
  const currentMcid = () => (mcidStack.length ? mcidStack[mcidStack.length - 1] : null);

  operatorList.fnArray.forEach((fn: number, index: number) => {
    const args = operatorList.argsArray[index];
    if (fn === OPS.save) {
      stack.push(ctm.slice());
      return;
    }
    if (fn === OPS.restore) {
      const restored = stack.pop();
      if (restored) ctm = restored;
      return;
    }
    if (fn === OPS.transform) {
      ctm = Util.transform(ctm, args);
      return;
    }
    if (fn === OPS.beginText) {
      textMatrix = [1, 0, 0, 1, 0, 0];
      textLineMatrix = [1, 0, 0, 1, 0, 0];
      return;
    }
    if (fn === OPS.endText) {
      return;
    }
    if (fn === OPS.setTextMatrix && args && args.length >= 6) {
      textMatrix = [args[0], args[1], args[2], args[3], args[4], args[5]];
      textLineMatrix = textMatrix.slice() as number[];
      return;
    }
    if (fn === OPS.moveText && args && args.length >= 2) {
      textLineMatrix = Util.transform(textLineMatrix, [1, 0, 0, 1, args[0], args[1]]);
      textMatrix = textLineMatrix.slice() as number[];
      return;
    }
    if (fn === OPS.nextLine) {
      textLineMatrix = Util.transform(textLineMatrix, [1, 0, 0, 1, 0, -1]);
      textMatrix = textLineMatrix.slice() as number[];
      return;
    }
    if (fn === OPS.setFont && args && args.length >= 2) {
      if (typeof args[1] === "number") {
        fontSize = args[1];
      }
      return;
    }
    if (fn === OPS.setHScale && args && args.length >= 1) {
      if (typeof args[0] === "number") {
        textHScale = args[0] / 100;
      }
      return;
    }
    if (fn === OPS.beginMarkedContentProps && args && args.length >= 2) {
      const props = args[1];
      const mcid = readMcid(props);
      mcidStack.push(mcid ?? currentMcid());
      return;
    }
    if (fn === OPS.beginMarkedContent && args && args.length >= 1) {
      mcidStack.push(currentMcid());
      return;
    }
    if (fn === OPS.endMarkedContent) {
      mcidStack.pop();
      return;
    }
    const activeMcid = currentMcid();
    if (
      activeMcid !== null &&
      targetMcids.has(activeMcid) &&
      (fn === OPS.paintImageXObject ||
        fn === OPS.paintInlineImageXObject ||
        fn === OPS.paintImageXObjectRepeat ||
        fn === OPS.paintJpegXObject)
    ) {
      const p0 = applyTransform([0, 0], ctm);
      const p1 = applyTransform([1, 0], ctm);
      const p2 = applyTransform([1, 1], ctm);
      const p3 = applyTransform([0, 1], ctm);
      const vp0 = toViewport(p0);
      const vp1 = toViewport(p1);
      const vp2 = toViewport(p2);
      const vp3 = toViewport(p3);
      const xs = [vp0[0], vp1[0], vp2[0], vp3[0]];
      const ys = [vp0[1], vp1[1], vp2[1], vp3[1]];
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      if (Number.isFinite(minX) && Number.isFinite(minY) && Number.isFinite(maxX) && Number.isFinite(maxY)) {
        boxes.push({ x: minX, y: minY, w: maxX - minX, h: maxY - minY });
      }
      return;
    }
    if (
      activeMcid !== null &&
      targetMcids.has(activeMcid) &&
      (fn === OPS.showText ||
        fn === OPS.showSpacedText ||
        fn === OPS.nextLineShowText ||
        fn === OPS.nextLineSetSpacingShowText)
    ) {
      let textLength = 0;
      if (fn === OPS.showText && args && args.length >= 1) {
        const text = args[0];
        if (typeof text === "string") {
          textLength = text.length;
        } else if (Array.isArray(text)) {
          textLength = text.length;
        }
      }
      if (fn === OPS.showSpacedText && args && args.length >= 1 && Array.isArray(args[0])) {
        textLength = args[0].reduce((sum: number, part: any) => {
          if (typeof part === "string") return sum + part.length;
          return sum;
        }, 0);
      }
      const width = Math.max(1, textLength * fontSize * 0.5 * textHScale);
      const height = Math.max(1, fontSize);
      const textTransform = Util.transform(ctm, textMatrix);
      const p0 = applyTransform([0, 0], textTransform);
      const p1 = applyTransform([width, 0], textTransform);
      const p2 = applyTransform([width, height], textTransform);
      const p3 = applyTransform([0, height], textTransform);
      const vp0 = toViewport(p0);
      const vp1 = toViewport(p1);
      const vp2 = toViewport(p2);
      const vp3 = toViewport(p3);
      const xs = [vp0[0], vp1[0], vp2[0], vp3[0]];
      const ys = [vp0[1], vp1[1], vp2[1], vp3[1]];
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      if (Number.isFinite(minX) && Number.isFinite(minY) && Number.isFinite(maxX) && Number.isFinite(maxY)) {
        boxes.push({ x: minX, y: minY, w: maxX - minX, h: maxY - minY });
      }
    }
  });
  return boxes.slice(0, 6);
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  summaryRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  issueCategoryList: { marginTop: 12, gap: 8 },
  issueCategoryCard: { gap: 6 },
  searchRow: { marginTop: 12, gap: 8 },
  searchInput: { borderWidth: 1, borderRadius: 12, padding: 10 },
  filterRow: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 12 },
  sortLink: { fontWeight: "600", marginTop: 4 },
  filterHover: { opacity: 0.92 },
  filterPressed: { opacity: 0.85, transform: [{ scale: 0.98 }] },
  filterActiveText: { color: "#FFFFFF" },
  filterActiveDefault: { backgroundColor: "#0EA5E9", borderColor: "#0EA5E9" },
  filterActiveSuccess: { backgroundColor: "#22C55E", borderColor: "#22C55E" },
  filterActiveWarning: { backgroundColor: "#F59E0B", borderColor: "#F59E0B" },
  filterActiveInfo: { backgroundColor: "#0EA5E9", borderColor: "#0EA5E9" },
  columns: { flex: 1, flexDirection: "row", gap: 16 },
  leftColumn: { flex: 1 },
  rightColumn: { flex: 2 },
  mobileTabs: { gap: 12 },
  tabRow: { flexDirection: "row", gap: 8 },
  treeContainer: { gap: 8 },
  treeText: { fontFamily: "Courier", fontSize: 12 },
  tagTreeContainer: { marginTop: 12, gap: 6 },
  tagTreeTitle: { fontWeight: "700" },
  tagRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 4,
    borderRadius: 8,
  },
  tagRowText: { fontFamily: "Courier", fontSize: 12 },
  showMore: { marginTop: 8, alignItems: "flex-start" },
  row: { marginBottom: 12 },
  rowPressed: { opacity: 0.9 },
  rowCard: { gap: 8 },
  rowHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  nodeId: { marginTop: 2 },
  description: { marginTop: 4 },
  separator: { height: 12 },
  docIssueCard: { marginTop: 12, gap: 6 },
  applyFixes: { marginTop: 12, alignItems: "flex-start" },
  downloadRow: { marginTop: 12, flexDirection: "row", gap: 12 },
  link: { fontWeight: "600" },
  diffRow: { marginTop: 16, flexDirection: "row", gap: 12, flexWrap: "wrap" },
  diffPanel: { flex: 1, minWidth: 220, borderWidth: 1, borderColor: "#E2E8F0", borderRadius: 12, padding: 12 },
  diffHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  diffTitle: { fontWeight: "700", marginBottom: 8 },
  diffTextCard: { marginTop: 12 },
  diffText: { fontFamily: "Courier", fontSize: 12 },
  visualDiffGrid: { gap: 16 },
  visualDiffRow: { flexDirection: "row", gap: 16, flexWrap: "wrap" },
  visualPanel: { flex: 1, minWidth: 240, gap: 8 },
  pageControls: { flexDirection: "row", alignItems: "center", gap: 12 },
  canvasWrap: { position: "relative" },
  canvas: { width: "100%", borderWidth: 1, borderColor: "#E2E8F0", borderRadius: 8 },
  overlayCanvas: {
    position: "absolute",
    left: 0,
    top: 0,
    right: 0,
    bottom: 0,
    pointerEvents: "none",
  },
  dropdown: { marginTop: 12, gap: 8 },
  dropdownTitle: { fontWeight: "600" },
  dropdownList: { maxHeight: 220 },
  dropdownItem: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 8 },
  overlayHint: {
    position: "absolute",
    left: 12,
    top: 12,
    right: 12,
    backgroundColor: "rgba(15, 23, 42, 0.7)",
    borderRadius: 10,
    padding: 10,
  },
  overlayText: { fontWeight: "700", fontSize: 12 },
  overlayTextMuted: { fontSize: 11, marginTop: 4 },
});











