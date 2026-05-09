/**
 * Admin - lightweight audit log viewer.
 *
 * Restored as a stub after a tooling truncation. Pulls from the backend
 * audit-log endpoint (or a soft-fail empty state when offline).
 */
import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import { createApiClient } from "../src/api/client";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { PixelSpinner } from "../src/ui/components/PixelSpinner";
import { EmptyState } from "../src/ui/components/EmptyState";
import { useToast } from "../src/ui/toast";
import { useAppStore } from "../src/store/useAppStore";
import { useTheme } from "../src/ui/useTheme";

interface LogEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  documentId?: string | null;
  details?: string | null;
}

export default function AdminScreen() {
  const theme = useTheme();
  const toast = useToast();
  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const mockMode = useAppStore((s) => s.mockMode);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchEntries = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = createApiClient({ baseUrl: apiBaseUrl, mockMode });
      const data = await (client as any).getAuditLog?.();
      setEntries(Array.isArray(data) ? data : []);
    } catch (e: any) {
      const msg = e?.message ?? "Could not load audit log.";
      setError(msg);
      toast.error("Could not load audit log", { description: msg });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEntries();
  }, [apiBaseUrl, mockMode]);

  return (
    <Screen scroll>
      <Hero
        shader="nebula"
        eyebrow="ADMIN"
        title="Admin"
        subtitle="Audit-log entries from every analyze, remediate, share, and download."
        rightSlot={<Chip label="Audit log" tone="info" />}
      />

      <Card>
        <View style={styles.row}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Recent activity</Text>
          <Button
            title={loading ? "Loading" : "Refresh"}
            onPress={fetchEntries}
            disabled={loading}
          />
        </View>

        {loading ? (
          <View style={styles.loadingRow}>
            <PixelSpinner />
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              Fetching log...
            </Text>
          </View>
        ) : error ? (
          <InlineNotice title="Could not load audit log" message={error} tone="danger" />
        ) : entries.length === 0 ? (
          <EmptyState
            icon="doc"
            title="No log entries yet"
            body="Run an audit, share a report, or download a remediated file - every action will surface here for review."
          />
        ) : (
          <View style={styles.list}>
            {entries.map((e) => (
              <View
                key={e.id}
                style={[
                  styles.item,
                  {
                    borderColor: theme.colors.border,
                    backgroundColor: theme.colors.surface2,
                  },
                ]}
              >
                <Text
                  style={[
                    theme.typography.body,
                    { color: theme.colors.text, fontWeight: "700" },
                  ]}
                >
                  {e.action}
                </Text>
                <Text
                  style={[
                    theme.typography.body,
                    { color: theme.colors.textMuted, fontSize: 12 },
                  ]}
                >
                  {new Date(e.at).toLocaleString()} - {e.actor}
                </Text>
                {e.details ? (
                  <Text
                    style={[
                      theme.typography.body,
                      { color: theme.colors.textMuted, fontSize: 12 },
                    ]}
                  >
                    {e.details}
                  </Text>
                ) : null}
              </View>
            ))}
          </View>
        )}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginBottom: 8,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  loadingRow: { flexDirection: "row", gap: 8, padding: 12, alignItems: "center" },
  list: { gap: 8, marginTop: 12 },
  item: { borderWidth: 1, borderRadius: 10, padding: 10, gap: 4 },
});
