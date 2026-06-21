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
  const formatOptions = (options?: Record<string, unknown>) => {
    if (!options || typeof options !== "object") return "default options";
    const parts: string[] = [];
    if (options.includeOriginal === true) parts.push("original");
    if (options.includeFixedIfAvailable === true) parts.push("fixed");
    if (options.includeRebuiltIfAvailable === true) parts.push("rebuilt");
    if (parts.length === 0) return "default options";
    return parts.join(", ");
  };
  const formatDate = (value: string) => {
    const ts = Date.parse(value);
    if (Number.isNaN(ts)) return value;
    return new Date(ts).toLocaleString();
  };
  const sortedBundles = [...bundles].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));

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
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          No exports yet. Use Export Evidence Bundle to create one.
        </Text>
      ) : null}
      <View style={styles.rows}>
        {sortedBundles.map((bundle, index) => (
          <View key={bundle.bundleId} style={[styles.row, { borderColor: theme.colors.border }]}>
            <View style={styles.meta}>
              <View style={styles.metaHeader}>
                <Text style={[theme.typography.body, { color: theme.colors.text }]}>{formatDate(bundle.createdAt)}</Text>
                {index === 0 ? <Text style={[theme.typography.caption, { color: theme.colors.info }]}>Latest</Text> : null}
              </View>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                hash {bundle.bundleHash.slice(0, 8)} | {formatOptions(bundle.options)}
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
  row: { borderWidth: 1, borderRadius: 0, padding: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  meta: { flex: 1, minWidth: 0 },
  metaHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
});
