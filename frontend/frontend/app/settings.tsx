import { useState } from "react";
import { StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function SettingsScreen() {
  const theme = useTheme();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const backendUrlWarning = useAppStore((state) => state.backendUrlWarning);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const backendHealthMessage = useAppStore((state) => state.backendHealthMessage);
  const setApiBaseUrl = useAppStore((state) => state.setApiBaseUrl);
  const saveApiBaseUrl = useAppStore((state) => state.saveApiBaseUrl);
  const setMockMode = useAppStore((state) => state.setMockMode);
  const [draftUrl, setDraftUrl] = useState(apiBaseUrl);

  return (
    <Screen scroll>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>Settings</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>API and environment</Text>
        </View>
        <Chip label={mockMode ? "Mock Mode" : "Live Mode"} tone={mockMode ? "warning" : "success"} />
      </View>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>API Base URL</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Current: {apiBaseUrl}</Text>
        <TextInput
          value={draftUrl}
          onChangeText={setDraftUrl}
          style={[styles.input, { borderColor: theme.colors.border, color: theme.colors.text }]}
          placeholder="http://localhost:8000"
          placeholderTextColor={theme.colors.textMuted}
        />
        <View style={styles.buttonRow}>
          <Button title="Save & Check" onPress={() => saveApiBaseUrl(draftUrl)} />
          <Button title="Save Only" onPress={() => setApiBaseUrl(draftUrl)} variant="ghost" />
        </View>
        {!mockMode && backendHealth === "error" && (
          <InlineNotice
            title="Backend not reachable"
            message={backendHealthMessage ?? backendUrlWarning ?? "Backend is unreachable."}
            tone="danger"
          />
        )}
      </Card>

      <Card>
        <View style={styles.toggleRow}>
          <View>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Mock Mode</Text>
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              Toggle between mocked data and live backend
            </Text>
          </View>
          <Switch value={mockMode} onValueChange={setMockMode} />
        </View>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  input: { borderWidth: 1, borderRadius: 10, padding: 10, marginTop: 12 },
  buttonRow: { marginTop: 12, alignItems: "flex-start" },
  toggleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 16 },
});
