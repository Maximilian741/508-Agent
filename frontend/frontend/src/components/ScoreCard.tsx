import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { JobScorePass, PolicyDetail } from "../api/client";
import { Card } from "../ui/components/Card";
import { Chip } from "../ui/components/Chip";
import { useTheme } from "../ui/useTheme";

interface ScoreCardProps {
  scores: JobScorePass[];
  policyDetail?: PolicyDetail;
  emptyMessage?: string;
}

const PASS_ORDER = ["baseline", "post_fix", "post_manual"];

export function ScoreCard({ scores, policyDetail, emptyMessage = "Score will appear after scan completes." }: ScoreCardProps) {
  const theme = useTheme();
  const [showHow, setShowHow] = useState(false);
  const byPass = useMemo(() => {
    const out: Record<string, JobScorePass | undefined> = {};
    for (const passType of PASS_ORDER) {
      out[passType] = scores.find((entry) => (entry.passType ?? entry.pass_type) === passType);
    }
    return out;
  }, [scores]);

  const policyJson = policyDetail?.policy_json && typeof policyDetail.policy_json === "object" ? policyDetail.policy_json : undefined;
  const scoring = policyJson?.scoring && typeof policyJson.scoring === "object" ? (policyJson.scoring as Record<string, unknown>) : undefined;
  const thresholds = policyJson?.thresholds && typeof policyJson.thresholds === "object" ? (policyJson.thresholds as Record<string, unknown>) : undefined;

  return (
    <Card>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Score</Text>
      {scores.length === 0 ? (
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>{emptyMessage}</Text>
      ) : (
        <View style={styles.rows}>
          {PASS_ORDER.map((passType) => {
            const entry = byPass[passType];
            const score = entry ? (entry.scoreTotal ?? entry.score_total ?? 0) : undefined;
            const status = entry?.status;
            return (
              <View key={passType} style={styles.row}>
                <Text style={[theme.typography.body, { color: theme.colors.text }]}>{passType}</Text>
                {entry ? (
                  <View style={styles.rowRight}>
                    <Chip label={`${score}`} tone="info" />
                    <Chip
                      label={status ?? "unknown"}
                      tone={status === "pass" ? "success" : status === "needs_review" ? "warning" : status === "fail" ? "danger" : "default"}
                    />
                  </View>
                ) : (
                  <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>not available</Text>
                )}
              </View>
            );
          })}
        </View>
      )}
      <Pressable style={styles.howToggle} onPress={() => setShowHow((prev) => !prev)}>
        <Text style={[theme.typography.body, { color: theme.colors.accent }]}>{showHow ? "Hide how score works" : "How score works"}</Text>
      </Pressable>
      {showHow ? (
        <View style={[styles.howBody, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Severity weights: {scoring?.severityWeights ? JSON.stringify(scoring.severityWeights) : "policy snapshot not loaded yet"}
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Thresholds: {thresholds ? JSON.stringify(thresholds) : "policy snapshot not loaded yet"}
          </Text>
        </View>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  rows: { marginTop: 8, gap: 8 },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  rowRight: { flexDirection: "row", gap: 8, alignItems: "center" },
  howToggle: { marginTop: 10, alignItems: "flex-start" },
  howBody: { marginTop: 10, borderWidth: 1, borderRadius: 12, padding: 10, gap: 6 },
});
