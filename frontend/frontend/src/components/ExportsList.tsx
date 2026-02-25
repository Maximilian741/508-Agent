import { Platform, Pressable, StyleSheet, Text, View, Linking } from "react-native";

import { EvidenceBundleSummary, createApiClient } from "../api/client";
import { useAppStore } from "../store/useAppStore";
import { Button } from "../ui/components/Button";
import { Card } from "../ui/components/Card";
import { InlineNotice } from "../ui/components/InlineNotice";
import { useTheme } from "../ui/useTheme";

interface ExportsListProps {
  title?: string;
  docId?: string | null;
  bundles: EvidenceBundleSummary[];
  loading?: boolean;
  error?: string;
  onRefresh?: () => void;
}

export function ExportsList({ title = "Evidence Bundles", docId, bundles, loading = false, error, onRefresh }: ExportsListProps) {
  const theme = useTheme();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);

  const openDownload = (bundleId: string) => {
    const client = createApiClient({ baseUrl: apiBaseUrl, mockMode: false });
    const url = client.getEvidenceBundleDownloadUrl(bundleId);
    if (Platform.OS === "web") {
      window.open(url, "_blank");
      return;
    }
    void Linking.openURL(url);
  };

  return (
    <Card>
      <View style={styles.header}>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>{title}</Text>
        <Button title="Refresh" onPress={() => onRefresh?.()} variant="ghost" disabled={!docId || loading} />
      </View>
      {!docId ? (
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Exports available after upload.</Text>
      ) : null}
      {error ? <InlineNotice title="Export list error" message={error} tone="danger" /> : null}
      {docId && bundles.length === 0 && !loading ? (
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>No exports yet.</Text>
      ) : null}
      <View style={styles.rows}>
        {bundles.map((bundle) => (
          <View key={bundle.bundleId} style={[styles.row, { borderColor: theme.colors.border }]}>
            <View style={styles.meta}>
              <Text style={[theme.typography.body, { color: theme.colors.text }]}>{bundle.createdAt}</Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                hash {bundle.bundleHash.slice(0, 8)} | options {JSON.stringify(bundle.options ?? {})}
              </Text>
            </View>
            <Pressable onPress={() => openDownload(bundle.bundleId)}>
              <Text style={[theme.typography.body, { color: theme.colors.accent }]}>Download</Text>
            </Pressable>
          </View>
        ))}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  rows: { marginTop: 10, gap: 8 },
  row: { borderWidth: 1, borderRadius: 12, padding: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  meta: { flex: 1, minWidth: 0 },
});
