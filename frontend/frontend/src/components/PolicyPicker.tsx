import { useMemo, useState } from "react";
import { Modal, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { PolicyDetail, PolicySummary } from "../api/client";
import { Button } from "../ui/components/Button";
import { Card } from "../ui/components/Card";
import { InlineNotice } from "../ui/components/InlineNotice";
import { useTheme } from "../ui/useTheme";

interface PolicyPickerProps {
  policies: PolicySummary[];
  selectedPolicyId: string | null;
  onSelectPolicy: (policyId: string) => void;
  onOpenDetails: (policyId: string) => void;
  detail?: PolicyDetail;
  loading?: boolean;
  error?: string;
}

export function PolicyPicker({
  policies,
  selectedPolicyId,
  onSelectPolicy,
  onOpenDetails,
  detail,
  loading = false,
  error,
}: PolicyPickerProps) {
  const theme = useTheme();
  const [showDetails, setShowDetails] = useState(false);
  const selected = useMemo(
    () => policies.find((item) => item.id === selectedPolicyId) ?? null,
    [policies, selectedPolicyId],
  );

  const detailBody = detail?.policy_json ? JSON.stringify(detail.policy_json, null, 2) : "Policy details unavailable from backend.";

  return (
    <Card>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Policy Pack</Text>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Choose the policy pack to snapshot on scan.</Text>
      {error ? <InlineNotice title="Policy load failed" message={error} tone="danger" /> : null}
      <View style={styles.options}>
        {policies.map((policy) => {
          const isSelected = policy.id === selectedPolicyId;
          return (
            <Pressable
              key={policy.id}
              onPress={() => onSelectPolicy(policy.id)}
              style={[
                styles.option,
                {
                  borderColor: isSelected ? theme.colors.accent : theme.colors.border,
                  backgroundColor: isSelected ? theme.colors.surface2 : theme.colors.surface,
                },
              ]}
            >
              <Text style={[theme.typography.body, { color: theme.colors.text }]}>{policy.name}</Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>v{policy.version}</Text>
            </Pressable>
          );
        })}
      </View>
      <View style={styles.actions}>
        <Button
          title="View policy details"
          onPress={() => {
            if (selectedPolicyId) {
              onOpenDetails(selectedPolicyId);
            }
            setShowDetails(true);
          }}
          variant="secondary"
          disabled={!selectedPolicyId || loading}
        />
      </View>
      <Modal visible={showDetails} transparent animationType="fade" onRequestClose={() => setShowDetails(false)}>
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalBody, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>{selected?.name ?? "Policy details"}</Text>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
              Targets: {(selected?.targets ?? []).join(", ") || "unknown"} | Version: {selected?.version ?? "unknown"}
            </Text>
            <View style={[styles.jsonWrap, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
              <Text selectable={Platform.OS === "web"} style={[styles.jsonText, { color: theme.colors.text }]}>
                {detailBody}
              </Text>
            </View>
            <Button title="Close" onPress={() => setShowDetails(false)} />
          </View>
        </View>
      </Modal>
    </Card>
  );
}

const styles = StyleSheet.create({
  options: { marginTop: 12, gap: 8 },
  option: { borderWidth: 1, borderRadius: 12, padding: 10, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  actions: { marginTop: 10, alignItems: "flex-start" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(15,23,42,0.45)", alignItems: "center", justifyContent: "center", padding: 16 },
  modalBody: { width: "100%", maxWidth: 760, maxHeight: "85%", borderWidth: 1, borderRadius: 14, padding: 14, gap: 10 },
  jsonWrap: { borderWidth: 1, borderRadius: 12, padding: 10, maxHeight: 360 },
  jsonText: { fontFamily: Platform.OS === "web" ? "monospace" : undefined, fontSize: 12 },
});
