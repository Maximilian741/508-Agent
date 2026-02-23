import { useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { RemediationAction } from "../../src/api/client";
import { useAppStore } from "../../src/store/useAppStore";
import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Chip } from "../../src/ui/components/Chip";
import { EmptyState } from "../../src/ui/components/EmptyState";
import { InlineNotice } from "../../src/ui/components/InlineNotice";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

type Notice = { tone: "success" | "warning" | "danger" | "info"; title: string; message: string };

export default function IssueDetailScreen() {
  const router = useRouter();
  const theme = useTheme();
  const params = useLocalSearchParams<{ id?: string }>();
  const issueId = typeof params.id === "string" ? params.id : "";

  const scanResults = useAppStore((state) => state.scanResults);
  const runRemediate = useAppStore((state) => state.runRemediate);
  const addManualReviewItem = useAppStore((state) => state.addManualReviewItem);
  const fetchManualReview = useAppStore((state) => state.fetchManualReview);
  const mockMode = useAppStore((state) => state.mockMode);

  const issue = useMemo(() => {
    return scanResults?.issues.find((item) => item.id === issueId) ?? null;
  }, [scanResults, issueId]);

  const [selectedAction, setSelectedAction] = useState<RemediationAction | null>(
    issue?.recommendedActions?.[0] ?? null,
  );
  const [notice, setNotice] = useState<Notice | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  useEffect(() => {
    setSelectedAction(issue?.recommendedActions?.[0] ?? null);
  }, [issue]);

  const handleRunFix = async () => {
    if (!issue || !selectedAction) {
      setNotice({ tone: "warning", title: "Select an action", message: "Choose a recommended fix to run." });
      return;
    }
    setIsRunning(true);
    setNotice(null);
    const response = await runRemediate({
      issueId: issue.id,
      targetNodeId: issue.nodeId,
      actionCode: selectedAction.actionCode,
    });
    setIsRunning(false);
    if (!response.ok || !response.results) {
      setNotice({
        tone: "danger",
        title: "Remediation failed",
        message: response.error ?? "Unable to execute remediation.",
      });
      return;
    }
    const result = response.results[0];
    if (result.status !== "success") {
      if (mockMode) {
        addManualReviewItem({
          id: `${issue.id}-${selectedAction.actionCode}-${Date.now()}`,
          issueId: issue.id,
          targetNodeId: issue.nodeId,
          reason: "Execution blocked by policy.",
          notes: result.notes,
        });
      } else {
        await fetchManualReview();
      }
      setNotice({
        tone: "warning",
        title: "Manual review required",
        message: "The action was blocked and queued for review.",
      });
      router.push("/manual-review");
      return;
    }
    setNotice({
      tone: "success",
      title: "Remediation complete",
      message: result.notes,
    });
  };

  if (!issue) {
    return (
      <Screen>
        <EmptyState
          title="Issue not found"
          message="Run a scan and select an issue to view details."
          icon="search"
        />
      </Screen>
    );
  }

  return (
    <Screen scroll>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>Issue Detail</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Node {issue.nodeId}</Text>
        </View>
        <Chip label={issue.ruleId} />
      </View>

      {notice && <InlineNotice title={notice.title} message={notice.message} tone={notice.tone} />}

      <Card style={styles.summaryCard}>
        <View style={styles.summaryRow}>
          <Chip
            label={issue.severity.toUpperCase()}
            tone={issue.severity === "error" ? "danger" : issue.severity === "warning" ? "warning" : "info"}
          />
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>{issue.description}</Text>
        </View>
        <View style={styles.contextGrid}>
          <Text style={[styles.meta, { color: theme.colors.textMuted }]}>Rule: {issue.ruleId}</Text>
          <Text style={[styles.meta, { color: theme.colors.textMuted }]}>Node ID: {issue.nodeId}</Text>
          {issue.nodePath && (
            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
              Path: {issue.nodePath.join(" > ")}
            </Text>
          )}
          {issue.evidence && (
            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
              Evidence: {JSON.stringify(issue.evidence)}
            </Text>
          )}
        </View>
      </Card>

      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Recommended Actions</Text>
      {issue.recommendedActions.length === 0 && (
        <EmptyState title="No actions available" message="This issue requires manual review." icon="info" />
      )}
      {issue.recommendedActions.map((action) => (
        <Pressable
          key={action.actionCode}
          onPress={() => setSelectedAction(action)}
          style={({ pressed }) => [styles.actionPress, pressed && styles.actionPressed]}
        >
          <Card
            style={[
              styles.actionCard,
              selectedAction?.actionCode === action.actionCode ? styles.actionSelected : undefined,
            ]}
          >
            <Text style={[styles.actionTitle, { color: theme.colors.text }]}>{action.actionCode}</Text>
            <Text style={{ color: theme.colors.textMuted }}>{action.description}</Text>
            <View style={styles.actionTags}>
              {action.requiresAi && <Chip label="AI" tone="info" />}
              {action.requiresHumanReview && <Chip label="Human Review" tone="warning" />}
              {action.isAutoApplicable && <Chip label="Auto" tone="success" />}
            </View>
          </Card>
        </Pressable>
      ))}

      <Button
        title={isRunning ? "Running..." : "Run Fix"}
        onPress={handleRunFix}
        variant="primary"
        disabled={!selectedAction || isRunning}
        loading={isRunning}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  summaryCard: { gap: 12 },
  summaryRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  contextGrid: { gap: 4 },
  meta: { marginTop: 2 },
  actionPress: { marginBottom: 8 },
  actionPressed: { opacity: 0.9 },
  actionCard: { gap: 8 },
  actionSelected: { borderColor: "#5B8CFF", borderWidth: 2 },
  actionTitle: { fontWeight: "700" },
  actionTags: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 8 },
});
