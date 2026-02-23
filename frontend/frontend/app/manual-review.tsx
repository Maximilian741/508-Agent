import { FlatList, Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useEffect, useMemo, useState } from "react";

import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
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
    const [editedText, setEditedText] = useState<Record<string, string>>({});

    const docName = (uploadedDocument?.filename ?? "").toLowerCase();
    const isPdfDoc = docName.endsWith(".pdf");
    const originalUrl = uploadedDocument
      ? isPdfDoc
        ? `${apiBaseUrl}/documents/${uploadedDocument.docId}/pdf`
        : `${apiBaseUrl}/documents/${uploadedDocument.docId}/download`
      : null;
    const fixedUrl = fixedDocId ? `${apiBaseUrl}/documents/${fixedDocId}/file-fixed` : null;

  useEffect(() => {
    if (!mockMode) {
      void fetchManualReview();
    }
  }, [mockMode, fetchManualReview]);

  const subtitle = useMemo(() => {
    if (!lastFetched) return "Not refreshed yet";
    return `Last refreshed: ${new Date(lastFetched).toLocaleString()}`;
  }, [lastFetched]);

  if (manualReviewQueue.length === 0) {
    return (
      <Screen>
        <EmptyState
          title="Manual review queue empty"
          message="No blocked remediation items are waiting for review."
          icon="assignment-turned-in"
          actionLabel={mockMode ? undefined : "Refresh"}
          onAction={mockMode ? undefined : () => fetchManualReview()}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>Manual Review</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            {manualReviewQueue.length} items waiting
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>{subtitle}</Text>
        </View>
        <View style={styles.buttonRow}>
          <Button title="Refresh" onPress={() => fetchManualReview()} variant="secondary" />
          <Button
            title="Clear Queue"
            onPress={() => (mockMode ? clearManualReviewQueue() : clearManualReview())}
            variant="danger"
          />
        </View>
      </View>

      <InlineNotice
        title="Why items appear here"
        message="These fixes require human validation or were blocked by policy."
        tone="info"
      />

      {originalUrl && (
        <Card style={styles.card}>
          <Text style={[styles.title, { color: theme.colors.text }]}>Document Preview</Text>
          {Platform.OS === "web" && isPdfDoc ? (
            // @ts-ignore - iframe is valid on web
            <iframe src={fixedUrl ?? originalUrl} style={{ width: "100%", height: 360, border: "none" }} />
          ) : (
            <View style={styles.linkRow}>
              <Pressable onPress={() => originalUrl && Linking.openURL(originalUrl)}>
                <Text style={[styles.link, { color: theme.colors.accent }]}>Open original document</Text>
              </Pressable>
              {fixedUrl && (
                <Pressable onPress={() => Linking.openURL(fixedUrl)}>
                  <Text style={[styles.link, { color: theme.colors.accent }]}>Open fixed document</Text>
                </Pressable>
              )}
            </View>
          )}
        </Card>
      )}

      <FlatList
        data={manualReviewQueue}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <Card style={styles.card}>
            <Text style={[styles.title, { color: theme.colors.text }]}>Issue {item.issueId}</Text>
            <Text style={{ color: theme.colors.textMuted }}>Node: {item.targetNodeId}</Text>
            <Text style={{ color: theme.colors.textMuted }}>Reason: {item.reason}</Text>
            {item.notes && <Text style={{ color: theme.colors.textMuted }}>Notes: {item.notes}</Text>}
            {item.suggestedText && (
              <View style={styles.suggestionBox}>
                <Text style={{ color: theme.colors.textMuted }}>Suggested alt text</Text>
                <TextInput
                  value={editedText[item.id] ?? item.approvedText ?? item.suggestedText}
                  onChangeText={(value) => setEditedText((prev) => ({ ...prev, [item.id]: value }))}
                  style={[styles.input, { borderColor: theme.colors.border, color: theme.colors.text }]}
                  placeholder="Edit suggested text"
                  placeholderTextColor={theme.colors.textMuted}
                />
                <View style={styles.buttonRow}>
                  <Button
                    title="Approve"
                    onPress={() =>
                      updateManualReview(item.id, {
                        status: "approved",
                        approvedText: editedText[item.id] ?? item.approvedText ?? item.suggestedText,
                      })
                    }
                    variant="secondary"
                  />
                  <Button title="Reject" onPress={() => updateManualReview(item.id, { status: "rejected" })} variant="danger" />
                </View>
              </View>
            )}
            {item.createdAt && (
              <Text style={{ color: theme.colors.textMuted }}>
                Created: {new Date(item.createdAt).toLocaleString()}
              </Text>
            )}
          </Card>
        )}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
    header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 },
    buttonRow: { flexDirection: "row", gap: 12 },
    card: { marginBottom: 12, gap: 6 },
    title: { fontWeight: "700" },
    linkRow: { gap: 8 },
    link: { fontWeight: "600" },
    suggestionBox: { gap: 8, marginTop: 6 },
    input: { borderWidth: 1, borderRadius: 8, padding: 8 },
});
