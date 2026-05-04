/**
 * Read-only public view of a shared audit.
 *
 * The share endpoint isn't wired in the current backend snapshot, so this
 * screen is a friendly placeholder. When share APIs ship it can be expanded
 * to fetch and render the audit.
 */
import { useLocalSearchParams, useRouter } from "expo-router";
import { Text, View } from "react-native";

import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

export default function SharedAuditScreen() {
  const theme = useTheme();
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string | string[] }>();
  const id = Array.isArray(params.id) ? params.id[0] : params.id ?? "";

  return (
    <Screen scroll>
      <View style={{ paddingVertical: 8 }}>
        <Text
          accessibilityRole="header"
          style={[theme.typography.title, { color: theme.colors.text }]}
        >
          Shared audit
        </Text>
      </View>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
          Sharing isn't enabled in this build
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          The link you opened {id ? "(id: " + id + ")" : ""} points at the
          audit-share endpoint, which isn't running in this environment yet.
          Audits remain local-only until sharing is enabled.
        </Text>
        <View style={{ marginTop: 12, flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
          <Button title="Back to dashboard" onPress={() => router.push("/" as any)} />
          <Button
            title="Run a new audit"
            variant="ghost"
            onPress={() => router.push("/audit" as any)}
          />
        </View>
      </Card>
    </Screen>
  );
}
