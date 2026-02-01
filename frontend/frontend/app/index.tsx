import { useEffect } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

const STEPS = ["Upload", "Scan", "Review Issues", "Apply Fixes", "Manual Review"];

export default function HomeScreen() {
  const router = useRouter();
  const theme = useTheme();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const backendUrlWarning = useAppStore((state) => state.backendUrlWarning);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const backendHealthMessage = useAppStore((state) => state.backendHealthMessage);
  const refreshBackendUrl = useAppStore((state) => state.refreshBackendUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const selectedDocument = useAppStore((state) => state.selectedDocument);

  useEffect(() => {
    if (!mockMode) {
      void refreshBackendUrl();
    }
  }, [mockMode, refreshBackendUrl]);

  const primaryLabel = selectedDocument ? "Start Scan" : "Upload Document";
  const primaryAction = () => router.push(selectedDocument ? "/documents" : "/documents");

  return (
    <Screen>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>508 Agent</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Guided remediation workflow</Text>
        </View>
        <Chip label={mockMode ? "Mock Mode" : "Live Mode"} tone={mockMode ? "warning" : "success"} />
      </View>

      <Card>
        <View style={styles.statusHeader}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Backend Status</Text>
          <Chip
            label={mockMode ? "Mock" : backendHealth === "ok" ? "Connected" : backendHealth === "error" ? "Disconnected" : "Checking"}
            tone={backendHealth === "ok" ? "success" : backendHealth === "error" ? "danger" : "default"}
          />
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Base URL: {apiBaseUrl}</Text>
        {!mockMode && backendHealth === "error" && (
          <InlineNotice
            title="Backend not reachable"
            message={backendHealthMessage ?? backendUrlWarning ?? "Run: python dev_run.py from backend/ to start on an available port automatically."}
            tone="danger"
          />
        )}
        {!mockMode && backendHealth === "ok" && (
          <InlineNotice
            title="Backend connected"
            message={`Connected to ${apiBaseUrl}`}
            tone="success"
          />
        )}
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Workflow</Text>
        <View style={styles.stepper}>
          {STEPS.map((step, index) => (
            <View key={step} style={styles.stepItem}>
              <View style={[styles.stepDot, { backgroundColor: theme.colors.accent }]} />
              <Text style={[theme.typography.body, { color: theme.colors.text }]}>{index + 1}. {step}</Text>
            </View>
          ))}
        </View>
      </Card>

      <View style={styles.ctaRow}>
        <Button title={primaryLabel} onPress={primaryAction} />
        <Button title="View Manual Review" onPress={() => router.push("/manual-review")} variant="secondary" />
        <Button title="Settings" onPress={() => router.push("/settings")} variant="ghost" />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  statusHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  stepper: { marginTop: 12, gap: 8 },
  stepItem: { flexDirection: "row", alignItems: "center", gap: 8 },
  stepDot: { width: 8, height: 8, borderRadius: 4 },
  ctaRow: { flexDirection: "row", gap: 12 },
});
