import { FlatList, Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useEffect, useMemo, useState } from "react";

import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { EmptyState } from "../src/ui/components/EmptyState";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function ManualReviewScreen() {
  const manualReviewQueue = useAppStore((state) => state.manualReviewQueue);
  const clearManualReviewQueue = useAppStore((state) => state.clearManualReviewQueue);
  const fetchManualReview = useAppStore((state) => state.fetchManualReview);
  const clearManualReview = useAppStore((state) => state.clearManualReview);
  const updateManualReview = useAppStore((state) => state.updateManualReview);
  const lastFetched = useAppStore((state) => state.manualReviewLastFetched);
  const uploadedDocument = useAppStore((state) => state.uploadedDocument);
  const fixedDocId = useAppStore((state) => state.fixedDocId);
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const theme = useTheme();
  // Selected filter chip: the one accent, with its paired on-accent text.
  const activeChip = { backgroundColor: theme.colors.accent, borderColor: theme.colors.accent };
  const activeChipText = { color: theme.colors.onAccent };
  const [editedText, setEditedText] = useState<Record<string, string>>({});
  const [reviewFilter, setReviewFilter] = useState<"all" | "pending" | "resolved">("pending");
  const currentDocId = uploadedDocument?.docId ?? fixedDocId ?? null;
  const [scope, setScope] = useState<"current" | "all">(currentDocId ? "current" : "all");

  const docName = (uploadedDocument?.filename ?? "").toLowerCase();
  const inferredDocType =
    uploadedDocument?.docType ?? (docName.endsWith(".pdf") ? "pdf" : docName.endsWith(".docx") ? "docx" : docName.endsWith(".pptx") ? "pptx" : null);
  const isPdfDoc = inferredDocType === "pdf";
  const originalUrl = currentDocId
    ? isPdfDoc
      ? `${apiBaseUrl}/documents/${currentDocId}/pdf`
      : `${apiBaseUrl}/documents/${currentDocId}/download`
    : null;
  // Deliberately no "fixed document" link and no Finalize: the legacy fixed-file
  // download and finalize endpoints are retired (they handed out remediated files
  // without charging). Remediated files come only from Audit, which prices each
  // fix before anything is charged.
  const scopedDocId = scope === "current" ? currentDocId ?? undefined : undefined;

  useEffect(() => {
    if (!currentDocId) {
      setScope("all");
    }
  }, [currentDocId]);

  const handleManualDecision = async (
    itemId: string,
    status: "pending" | "approved" | "rejected",
    approvedText?: string,
  ) => {
    await updateManualReview(itemId, { status, approvedText }, scopedDocId);
  };

  useEffect(() => {
    const load = async () => {
      if (mockMode) return;
      const result = await fetchManualReview(scopedDocId);
      if (
        scope === "current" &&
        currentDocId &&
        result.ok &&
        Array.isArray(result.data) &&
        result.data.length === 0
      ) {
        setScope("all");
        await fetchManualReview();
      }
    };
    void load();
  }, [mockMode, fetchManualReview, scopedDocId, scope, currentDocId]);

  const subtitle = useMemo(() => {
    if (!lastFetched) return "Not refreshed yet";
    return `Last refreshed: ${new Date(lastFetched).toLocaleString()}`;
  }, [lastFetched]);

  const pendingCount = useMemo(
    () => manualReviewQueue.filter((item) => !item.status || item.status === "pending").length,
    [manualReviewQueue],
  );
  const resolvedCount = useMemo(
    () => manualReviewQueue.filter((item) => item.status === "approved" || item.status === "rejected").length,
    [manualReviewQueue],
  );

  const stageMessage = useMemo(() => {
    if (pendingCount > 0) {
      return {
        title: "Next step",
        message: `Resolve ${pendingCount} pending item(s).`,
        tone: "warning" as const,
      };
    }
    return {
      title: "Queue status",
      message: "No pending items right now.",
      tone: "success" as const,
    };
  }, [pendingCount]);

  if (manualReviewQueue.length === 0) {
    return (
      <Screen>
        <EmptyState
          title={scope === "current" ? "No manual review items for this document" : "Manual review queue empty"}
          message={
            scope === "current"
              ? "Findings you reject during an audit appear here. Switch to All unresolved to see earlier items."
              : "No findings are waiting for review. Findings you reject during an audit appear here."
          }
          icon="spark"
          actionLabel={mockMode ? undefined : "Refresh"}
          onAction={mockMode ? undefined : () => fetchManualReview(scopedDocId)}
        />
        {currentDocId && (
          <View style={styles.scopeRow}>
            <Pressable accessibilityRole="button" accessibilityLabel="Set scope to current" onPress={() => setScope("current")}>
              <Chip
                label="Current Document"
                tone="default"
                style={scope === "current" ? activeChip : undefined}
                textStyle={scope === "current" ? activeChipText : undefined}
              />
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Set scope to all" onPress={() => setScope("all")}>
              <Chip
                label="All Unresolved"
                tone="default"
                style={scope === "all" ? activeChip : undefined}
                textStyle={scope === "all" ? activeChipText : undefined}
              />
            </Pressable>
          </View>
        )}
      </Screen>
    );
  }

  const sortedQueue = [...manualReviewQueue].sort((a, b) => {
    const aPending = !a.status || a.status === "pending";
    const bPending = !b.status || b.status === "pending";
    if (aPending !== bPending) return aPending ? -1 : 1;
    const aTime = a.createdAt ? new Date(a.createdAt).getTime() : 0;
    const bTime = b.createdAt ? new Date(b.createdAt).getTime() : 0;
    return bTime - aTime;
  });
  const filteredQueue = sortedQueue.filter((item) => {
    const isPending = !item.status || item.status === "pending";
    if (reviewFilter === "pending") return isPending;
    if (reviewFilter === "resolved") return !isPending;
    return true;
  });

  return (
    <Screen>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>Manual Review Workspace</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            {pendingCount} pending of {manualReviewQueue.length} loaded
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>{subtitle}</Text>
        </View>
        <View style={styles.buttonRow}>
          <Button title="Back to Audit" href="/audit" variant="ghost" />
          <Button title="Refresh" onPress={() => fetchManualReview(scopedDocId)} variant="secondary" />
          <Button
            title="Clear Queue"
            onPress={() => (mockMode ? clearManualReviewQueue() : clearManualReview())}
            variant="danger"
          />
        </View>
      </View>

      {currentDocId && (
        <View style={styles.scopeRow}>
          <Pressable accessibilityRole="button" accessibilityLabel="Set scope to current" onPress={() => setScope("current")}>
            <Chip
              label={`Current Document (${currentDocId})`}
              tone="default"
              style={scope === "current" ? activeChip : undefined}
              textStyle={scope === "current" ? activeChipText : undefined}
            />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Set scope to all" onPress={() => setScope("all")}>
            <Chip
              label="All Unresolved"
              tone="default"
              style={scope === "all" ? activeChip : undefined}
              textStyle={scope === "all" ? activeChipText : undefined}
            />
          </Pressable>
        </View>
      )}

      <InlineNotice
        title={scope === "current" ? "Reviewing current document items" : "Reviewing all unresolved queue items"}
        message="Approve only human-verified content. A decision is recorded for your team and does not change any file. To fix a document, run it through Audit."
        tone="info"
      />
      <InlineNotice title={stageMessage.title} message={stageMessage.message} tone={stageMessage.tone} />
      <Card style={styles.card}>
        <Text style={[styles.title, { color: theme.colors.text }]}>Queue Summary</Text>
        <View style={styles.buttonRow}>
          <Chip label={`Pending ${pendingCount}`} tone="warning" />
          <Chip label={`Resolved ${resolvedCount}`} tone="default" />
        </View>
        <Text style={{ color: theme.colors.textMuted }}>Resolving an item records the decision. It does not change a file.</Text>
        <View style={styles.scopeRow}>
          <Pressable accessibilityRole="button" accessibilityLabel="Set review filter to pending" onPress={() => setReviewFilter("pending")}>
            <Chip
              label={`Pending (${pendingCount})`}
              tone="warning"
              style={reviewFilter === "pending" ? activeChip : undefined}
              textStyle={reviewFilter === "pending" ? activeChipText : undefined}
            />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Set review filter to resolved" onPress={() => setReviewFilter("resolved")}>
            <Chip
              label={`Resolved (${resolvedCount})`}
              tone="default"
              style={reviewFilter === "resolved" ? activeChip : undefined}
              textStyle={reviewFilter === "resolved" ? activeChipText : undefined}
            />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Set review filter to all" onPress={() => setReviewFilter("all")}>
            <Chip
              label={`All (${manualReviewQueue.length})`}
              tone="info"
              style={reviewFilter === "all" ? activeChip : undefined}
              textStyle={reviewFilter === "all" ? activeChipText : undefined}
            />
          </Pressable>
        </View>
      </Card>

      {originalUrl && (
        <Card style={styles.card}>
          <Text style={[styles.title, { color: theme.colors.text }]}>Document Preview</Text>
          <InlineNotice
            title="Preview not available in this build"
            message="Use the link below to open the document safely."
            tone="warning"
          />
          <View style={styles.linkRow}>
            {/* No accessibilityLabel: the visible text already says which
                document the link opens, and "Open external link" both
                erased that distinction (WCAG 2.4.4) and replaced the words
                the user can see and say (WCAG 2.5.3). */}
            <Pressable accessibilityRole="button" onPress={() => originalUrl && Linking.openURL(originalUrl)}>
              <Text style={[styles.link, { color: theme.colors.accent }]}>Open original document</Text>
            </Pressable>
          </View>
        </Card>
      )}

      <FlatList
        data={filteredQueue}
        keyExtractor={(item) => item.id}
        ListEmptyComponent={
          <Card style={styles.card}>
            <Text style={{ color: theme.colors.textMuted }}>
              No items match the selected filter.
            </Text>
          </Card>
        }
        renderItem={({ item }) => {
          const isPending = !item.status || item.status === "pending";
          return (
          <Card style={styles.card}>
            <View style={styles.rowHeader}>
              <View style={styles.statusTitleRow}>
                <Text style={styles.statusIcon}>
                  {!isPending ? "✓" : "⏳"}
                </Text>
                <Text style={[styles.title, { color: theme.colors.text }]}>{item.reason || item.issueId}</Text>
              </View>
              <Chip
                label={item.status === "approved" ? "APPROVED" : item.status === "rejected" ? "REJECTED" : "PENDING"}
                tone={item.status === "approved" ? "success" : item.status === "rejected" ? "danger" : "warning"}
              />
            </View>
            <Text style={{ color: theme.colors.textMuted }}>Issue: {item.issueId}</Text>
            <Text style={{ color: theme.colors.textMuted }}>Target: {item.targetNodeId}</Text>
            {item.notes && <Text style={{ color: theme.colors.textMuted }}>Notes: {item.notes}</Text>}
            {item.suggestedFix && <Text style={{ color: theme.colors.textMuted }}>Suggested fix: {item.suggestedFix}</Text>}
            {isPending && item.suggestedText && (
              <View style={styles.suggestionBox}>
                <Text style={{ color: theme.colors.textMuted }}>Suggested alt text</Text>
                <TextInput
                  value={editedText[item.id] ?? item.approvedText ?? item.suggestedText}
                  onChangeText={(value) => setEditedText((prev) => ({ ...prev, [item.id]: value }))}
                  style={[styles.input, { borderColor: theme.colors.border, color: theme.colors.text, borderRadius: theme.radius.xs }]}
                  placeholder="Edit suggested text"
                  placeholderTextColor={theme.colors.textMuted}
                />
                <View style={styles.buttonRow}>
                  <Button
                    title="Approve"
                    onPress={() => void handleManualDecision(
                      item.id,
                      "approved",
                      editedText[item.id] ?? item.approvedText ?? item.suggestedText,
                    )}
                    variant="secondary"
                  />
                  <Button
                    title="Reject"
                    onPress={() => void handleManualDecision(item.id, "rejected")}
                    variant="danger"
                  />
                </View>
              </View>
            )}
            {isPending && !item.suggestedText && (
              <View style={styles.buttonRow}>
                <Button
                  title="Mark Approved"
                  onPress={() => void handleManualDecision(item.id, "approved")}
                  variant="secondary"
                />
                <Button
                  title="Reject"
                  onPress={() => void handleManualDecision(item.id, "rejected")}
                  variant="danger"
                />
              </View>
            )}
            {!isPending && (
              <View style={styles.buttonRow}>
                <Text style={{ color: theme.colors.textMuted }}>
                  Decision recorded.
                </Text>
                <Button
                  title="Set Pending"
                  onPress={() => void handleManualDecision(item.id, "pending")}
                  variant="ghost"
                />
              </View>
            )}
            {item.createdAt && (
              <Text style={{ color: theme.colors.textMuted }}>
                Created: {new Date(item.createdAt).toLocaleString()}
              </Text>
            )}
          </Card>
          );
        }}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 },
  buttonRow: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  scopeRow: { marginTop: 8, marginBottom: 8, flexDirection: "row", gap: 8, flexWrap: "wrap" },
  card: { marginBottom: 12, gap: 6 },
  rowHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" },
  statusTitleRow: { flexDirection: "row", alignItems: "center", gap: 8, flex: 1, minWidth: 0 },
  statusIcon: { fontWeight: "800", fontSize: 14 },
  title: { fontWeight: "700" },
  linkRow: { gap: 8 },
  link: { fontWeight: "600" },
  suggestionBox: { gap: 8, marginTop: 6 },
  input: { borderWidth: 1, padding: 8 },
});
