/**
 * Admin - business metrics dashboard + audit-log viewer.
 *
 * The metrics section (revenue, usage, certificates, teams) is gated to admins
 * via GET /admin/metrics; non-admins get a clear access-denied state. The audit
 * log below pulls from the backend audit-log endpoint.
 */
import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import { AdminMetrics, AuditLogEntry, getAdminMetrics, getAuditLog } from "../src/domain/account";
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

type LogEntry = AuditLogEntry;

const PLAN_LABELS: Record<string, string> = {
  team: "Team (monthly)",
  team_annual: "Team (annual)",
  business: "Business (monthly)",
  business_annual: "Business (annual)",
};

function _money(n: number): string {
  return "$" + (n || 0).toLocaleString();
}

export default function AdminScreen() {
  const theme = useTheme();
  const toast = useToast();
  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const mockMode = useAppStore((s) => s.mockMode);

  // Metrics
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [denied, setDenied] = useState(false);

  // Audit log
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = async () => {
    setMetricsLoading(true);
    try {
      const m = await getAdminMetrics();
      setMetrics(m);
      setDenied(false);
    } catch (e: any) {
      if (e?.status === 403 || e?.status === 401) {
        setDenied(true);
      } else {
        toast.error("Could not load metrics", { description: e?.message || "Try again." });
      }
    } finally {
      setMetricsLoading(false);
    }
  };

  const fetchEntries = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAuditLog(200);
      setEntries(data);
    } catch (e: any) {
      if (e?.status === 403 || e?.status === 401) {
        setDenied(true);
      } else {
        setError(e?.message ?? "Could not load audit log.");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchMetrics();
  }, [apiBaseUrl, mockMode]);

  useEffect(() => {
    if (!denied) void fetchEntries();
  }, [apiBaseUrl, mockMode, denied]);

  return (
    <Screen scroll>
      <Hero
        eyebrow="ADMIN"
        title="Admin"
        subtitle="Revenue, usage, certificates, and a full audit trail of every action."
        rightSlot={<Chip label="Owner only" tone="info" />}
      />

      {denied ? (
        <Card style={{ borderColor: theme.colors.danger, borderWidth: 2 }}>
          <Text style={[theme.typography.h2, { color: theme.colors.danger }]}>Admins only</Text>
          <Text style={{ color: theme.colors.textMuted, marginTop: 8 }}>
            This dashboard is restricted to administrators. If you should have access, ask an owner to add your email to
            ADMIN_EMAILS or set your role to admin.
          </Text>
        </Card>
      ) : (
        <>
          {/* Metrics */}
          {metricsLoading ? (
            <Card>
              <View style={styles.loadingRow}>
                <PixelSpinner />
                <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Loading metrics…</Text>
              </View>
            </Card>
          ) : metrics ? (
            <View style={{ gap: 16 }}>
              <View style={styles.statGrid}>
                <Stat theme={theme} label="Est. MRR" value={_money(metrics.subscriptions.estimatedMrrUsd)} accent />
                <Stat theme={theme} label="Active subs" value={String(metrics.subscriptions.active)} />
                <Stat theme={theme} label="Users" value={String(metrics.users.total)} sub={`+${metrics.users.newLast30d} in 30d`} />
                <Stat theme={theme} label="Certificates" value={String(metrics.certificates.total)} sub={`+${metrics.certificates.last30d} in 30d`} />
              </View>

              <View style={styles.statGrid}>
                <Stat theme={theme} label="Credits sold" value={metrics.credits.granted.toLocaleString()} />
                <Stat theme={theme} label="Credits spent" value={metrics.credits.spent.toLocaleString()} />
                <Stat theme={theme} label="Outstanding" value={metrics.credits.outstanding.toLocaleString()} sub="unspent balance" />
                <Stat theme={theme} label="Overage rev." value={_money(metrics.overage.revenueUsd)} sub={`${metrics.overage.charges} charges`} />
              </View>

              <View style={styles.twoCol}>
                <Card style={styles.colCard}>
                  <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 8 }]}>Subscriptions by plan</Text>
                  {Object.keys(metrics.subscriptions.byPlan).length === 0 ? (
                    <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>No active subscriptions yet.</Text>
                  ) : (
                    Object.entries(metrics.subscriptions.byPlan).map(([plan, count]) => (
                      <View key={plan} style={styles.kvRow}>
                        <Text style={{ color: theme.colors.text, fontSize: 13 }}>{PLAN_LABELS[plan] || plan}</Text>
                        <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 13 }}>{count}</Text>
                      </View>
                    ))
                  )}
                  <View style={[styles.kvRow, { borderTopWidth: 1, borderTopColor: theme.colors.border, marginTop: 6, paddingTop: 8 }]}>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>Teams</Text>
                    <Text style={{ color: theme.colors.text, fontSize: 13 }}>
                      {metrics.teams.count} · {metrics.teams.seatsUsed}/{metrics.teams.seatsTotal} seats
                    </Text>
                  </View>
                </Card>

                <Card style={styles.colCard}>
                  <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 8 }]}>Recent certificates</Text>
                  {metrics.recentCertificates.length === 0 ? (
                    <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>None issued yet.</Text>
                  ) : (
                    metrics.recentCertificates.slice(0, 6).map((c) => (
                      <View key={c.id} style={styles.kvRow}>
                        <Text style={{ color: theme.colors.text, fontSize: 12, flex: 1 }} numberOfLines={1}>
                          {c.filename}
                        </Text>
                        <Chip label={c.paidWith === "subscription" ? "sub" : "credits"} tone={c.paidWith === "subscription" ? "success" : "default"} />
                      </View>
                    ))
                  )}
                </Card>
              </View>
            </View>
          ) : null}

          {/* Audit log */}
          <Card style={{ marginTop: 16 }}>
            <View style={styles.row}>
              <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Recent activity</Text>
              <Button title={loading ? "Loading" : "Refresh"} onPress={fetchEntries} disabled={loading} />
            </View>

            {loading ? (
              <View style={styles.loadingRow}>
                <PixelSpinner />
                <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Fetching log...</Text>
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
                  <View key={e.id} style={[styles.item, { borderRadius: theme.radius.none, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
                    <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>{e.event}</Text>
                    <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 12 }]}>
                      {new Date(e.at).toLocaleString()} - {e.actorEmail || "system"}
                      {e.docId ? ` - doc ${e.docId}` : ""}
                    </Text>
                    {e.details && Object.keys(e.details).length > 0 ? (
                      <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 12 }]}>
                        {JSON.stringify(e.details)}
                      </Text>
                    ) : null}
                  </View>
                ))}
              </View>
            )}
          </Card>
        </>
      )}
    </Screen>
  );
}

function Stat({
  theme,
  label,
  value,
  sub,
  accent,
}: {
  theme: ReturnType<typeof useTheme>;
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <View style={[styles.stat, { borderRadius: theme.radius.none, borderColor: accent ? theme.colors.accent : theme.colors.border, backgroundColor: theme.colors.surface }]}>
      <Text style={{ color: theme.colors.textMuted, fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</Text>
      <Text style={[theme.typography.pixelLarge, { color: accent ? theme.colors.accent : theme.colors.text, marginTop: 4 }]}>{value}</Text>
      {sub ? <Text style={{ color: theme.colors.textMuted, fontSize: 11, marginTop: 2 }}>{sub}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  loadingRow: { flexDirection: "row", gap: 8, padding: 12, alignItems: "center" },
  list: { gap: 8, marginTop: 12 },
  item: { borderWidth: 1, padding: 10, gap: 4 },
  statGrid: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  stat: { flex: 1, minWidth: 150, borderWidth: 1, padding: 14 },
  twoCol: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  colCard: { flex: 1, minWidth: 280 },
  kvRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8, paddingVertical: 4 },
});
