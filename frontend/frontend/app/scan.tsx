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
  const originalCanvasRefs = useRef<HTMLCanvasElement[]>([]);
  const fixedCanvasRefs = useRef<HTMLCanvasElement[]>([]);
  const pagesToRender = 3;

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
  const fixedUrl = fixedDocId ? `${apiBaseUrl}/documents/${fixedDocId}/pdf-fixed` : null;
  const openUrl = (url: string) => {
    if (Platform.OS === "web") {
      window.open(url, "_blank");
      return;
    }
    void Linking.openURL(url);
  };

  useEffect(() => {
    const loadDiff = async () => {
      if (!uploadedDocument || !fixedDocId) return;
      const result = await fetchDocumentDiff(uploadedDocument.docId);
      if (result.ok && result.data) {
        setDiffText(result.data.diffText || "");
        setDiffError(null);
      } else {
        setDiffError(result.error ?? "Unable to load diff.");
      }
    };
    void loadDiff();
  }, [uploadedDocument, fixedDocId, fetchDocumentDiff]);

  useEffect(() => {
    const renderVisualDiff = async () => {
      if (!uploadedDocument || !fixedDocId) return;
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
            const existing = document.getElementById("pdfjs-script");
            if (existing) {
              existing.addEventListener("load", () => {
                // @ts-ignore
                resolve(window.pdfjsLib);
              });
              existing.addEventListener("error", () => reject(new Error("Failed to load PDF.js")));
              return;
            }
            const script = document.createElement("script");
            script.id = "pdfjs-script";
            script.src = "https://unpkg.com/pdfjs-dist@4.2.67/legacy/build/pdf.min.js";
            script.async = true;
            script.onload = () => {
              // @ts-ignore
              resolve(window.pdfjsLib);
            };
            script.onerror = () => reject(new Error("Failed to load PDF.js"));
            document.body.appendChild(script);
          });

        const pdfjs = await loadPdfJs();
        if (!originalUrl || !fixedUrl) {
          setPdfError("Missing document URLs for diff rendering.");
          return;
        }
        const [originalResponse, fixedResponse] = await Promise.all([
          fetch(originalUrl, { cache: "no-store", mode: "cors" }),
          fetch(fixedUrl, { cache: "no-store", mode: "cors" }),
        ]);
        if (!originalResponse.ok) {
          throw new Error(`Original PDF fetch failed: ${originalResponse.status}`);
        }
        if (!fixedResponse.ok) {
          throw new Error(`Fixed PDF fetch failed: ${fixedResponse.status}`);
        }
        const [originalBuffer, fixedBuffer] = await Promise.all([
          originalResponse.arrayBuffer(),
          fixedResponse.arrayBuffer(),
        ]);
        const originalTask = pdfjs.getDocument({ data: originalBuffer, disableWorker: true });
        const fixedTask = pdfjs.getDocument({ data: fixedBuffer, disableWorker: true });
        const originalPdf = await originalTask.promise;
        const fixedPdf = await fixedTask.promise;
        const maxPages = Math.min(pagesToRender, originalPdf.numPages, fixedPdf.numPages);
        for (let i = 1; i <= maxPages; i += 1) {
          const originalPage = await originalPdf.getPage(i);
          const fixedPage = await fixedPdf.getPage(i);
          const scale = 0.6;
          const originalViewport = originalPage.getViewport({ scale });
          const fixedViewport = fixedPage.getViewport({ scale });
          const originalCanvas = originalCanvasRefs.current[i - 1];
          const fixedCanvas = fixedCanvasRefs.current[i - 1];
          if (originalCanvas) {
            const ctx = originalCanvas.getContext("2d");
            originalCanvas.height = originalViewport.height;
            originalCanvas.width = originalViewport.width;
            if (ctx) {
              await originalPage.render({ canvasContext: ctx, viewport: originalViewport }).promise;
            }
          }
          if (fixedCanvas) {
            const ctx = fixedCanvas.getContext("2d");
            fixedCanvas.height = fixedViewport.height;
            fixedCanvas.width = fixedViewport.width;
            if (ctx) {
              await fixedPage.render({ canvasContext: ctx, viewport: fixedViewport }).promise;
            }
          }
        }
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
  }, [uploadedDocument, fixedDocId, originalUrl, fixedUrl]);

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
          <View style={styles.summaryRow}>
            <Chip label={`Errors: ${docIssueCounts.error}`} tone="danger" />
            <Chip label={`Warnings: ${docIssueCounts.warning}`} tone="warning" />
            <Chip label={`Info: ${docIssueCounts.info}`} tone="info" />
          </View>
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
                <Chip
                  label={value === "all" ? "All" : value.toUpperCase()}
                  tone={value === "error" ? "danger" : value === "warning" ? "warning" : value === "info" ? "info" : "default"}
                />
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
        <InlineNotice
          title="Active document"
          message={`Showing results for ${uploadedDocument.filename} (${uploadedDocument.docId})`}
          tone="info"
        />
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
                  Example: missing_alt_text on image 1
                </Text>
              </View>
              <View style={styles.diffPanel}>
                <Text style={[styles.diffTitle, { color: theme.colors.text }]}>After</Text>
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  {documentIssues.length} issues remaining after fixes
                </Text>
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  Example: alt text added for image 1
                </Text>
              </View>
            </View>
          )}
          {fixedDocId && (
            <Card style={styles.diffTextCard}>
              <Text style={[styles.diffTitle, { color: theme.colors.text }]}>Text Diff</Text>
              {diffError ? (
                <Text style={[styles.nodeId, { color: theme.colors.danger }]}>{diffError}</Text>
              ) : (
                <Text style={[styles.diffText, { color: theme.colors.textMuted }]}>
                  {diffText || "No text differences detected."}
                </Text>
              )}
            </Card>
          )}
          {fixedDocId && (
            <Card style={styles.diffTextCard}>
              <Text style={[styles.diffTitle, { color: theme.colors.text }]}>Visual PDF Diff (first 3 pages)</Text>
              {Platform.OS !== "web" && (
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>
                  Visual diff is available on web only.
                </Text>
              )}
              {pdfLoading && (
                <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>Rendering pages...</Text>
              )}
              {pdfError && (
                <Text style={[styles.nodeId, { color: theme.colors.danger }]}>{pdfError}</Text>
              )}
              {Platform.OS === "web" && !pdfError && (
                <View style={styles.visualDiffGrid}>
                  {Array.from({ length: pagesToRender }).map((_, index) => (
                    <View key={`page-${index}`} style={styles.visualDiffRow}>
                      <View style={styles.visualPanel}>
                        <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>Before p.{index + 1}</Text>
                        <canvas
                          ref={(ref) => {
                            if (ref) originalCanvasRefs.current[index] = ref;
                          }}
                          style={styles.canvas}
                        />
                      </View>
                      <View style={styles.visualPanel}>
                        <Text style={[styles.nodeId, { color: theme.colors.textMuted }]}>After p.{index + 1}</Text>
                        <canvas
                          ref={(ref) => {
                            if (ref) fixedCanvasRefs.current[index] = ref;
                          }}
                          style={styles.canvas}
                        />
                      </View>
                    </View>
                  ))}
                </View>
              )}
            </Card>
          )}
        </Card>
      )}

      {isWide ? (
        <View style={styles.columns}>
          <View style={styles.leftColumn}>
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
          ) : (
            <IssuesList
              issues={documentIssues}
              onSelectIssue={(issue) => {
                const nodeIds = (issue.evidence?.nodeIds as string[]) ?? [];
                if (nodeIds.length > 0) {
                  setHighlightNodeId(nodeIds[0]);
                  setActiveTab("tree");
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

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  summaryRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  searchRow: { marginTop: 12, gap: 8 },
  searchInput: { borderWidth: 1, borderRadius: 12, padding: 10 },
  filterRow: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 12 },
  sortLink: { fontWeight: "600", marginTop: 4 },
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
  diffTitle: { fontWeight: "700", marginBottom: 8 },
  diffTextCard: { marginTop: 12 },
  diffText: { fontFamily: "Courier", fontSize: 12 },
  visualDiffGrid: { gap: 16 },
  visualDiffRow: { flexDirection: "row", gap: 16, flexWrap: "wrap" },
  visualPanel: { flex: 1, minWidth: 240, gap: 8 },
  canvas: { width: "100%", borderWidth: 1, borderColor: "#E2E8F0", borderRadius: 8 },
});





