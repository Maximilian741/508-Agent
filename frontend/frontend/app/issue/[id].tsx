import { useLocalSearchParams } from "expo-router";
import { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";

import { useAppStore } from "../../src/store/useAppStore";
import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Chip } from "../../src/ui/components/Chip";
import { EmptyState } from "../../src/ui/components/EmptyState";
import { InlineNotice } from "../../src/ui/components/InlineNotice";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

/**
 * Issue detail for a finding from the legacy JSON /scan.
 *
 * It used to offer "Run Fix", which called the legacy POST /remediate: a free,
 * unthrottled route to the paid AI provider, now retired (410). Fixes are
 * applied only on Audit, so this page describes the issue and sends the user
 * there instead of showing a button that can only fail.
 */
export default function IssueDetailScreen() {
  const theme = useTheme();
  const params = useLocalSearchParams<{ id?: string }>();
  const issueId = typeof params.id === "string" ? params.id : "";

  const scanResults = useAppStore((state) => state.scanResults);

  const issue = useMemo(() => {
    return scanResults?.issues.find((item) => item.id === issueId) ?? null;
  }, [scanResults, issueId]);

  if (!issue) {
    return (
      <Screen>
        <EmptyState
          title="Issue not found"
          message="This issue is no longer loaded. Upload the document on the Audit screen to see its issues."
          materialIcon="search"
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

      <InlineNotice
        title="Fixes are applied in Audit"
        message="Upload the document on the Audit screen, approve the fixes you want, and confirm the credit cost before anything changes."
        tone="info"
      />

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
        <EmptyState title="No actions available" message="This issue requires manual review." materialIcon="info" />
      )}
      {issue.recommendedActions.map((action) => (
        <Card key={action.actionCode} style={styles.actionCard}>
          <Text style={[styles.actionTitle, { color: theme.colors.text }]}>{action.actionCode}</Text>
          <Text style={{ color: theme.colors.textMuted }}>{action.description}</Text>
          <View style={styles.actionTags}>
            {action.requiresAi && <Chip label="AI" tone="info" />}
            {action.requiresHumanReview && <Chip label="Human Review" tone="warning" />}
            {action.isAutoApplicable && <Chip label="Auto" tone="success" />}
          </View>
        </Card>
      ))}

      <Button title="Fix this in Audit" href="/audit" variant="primary" />
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  summaryCard: { gap: 12 },
  summaryRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  contextGrid: { gap: 4 },
  meta: { marginTop: 2 },
  actionCard: { gap: 8, marginBottom: 8 },
  actionTitle: { fontWeight: "700" },
  actionTags: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 8 },
});
