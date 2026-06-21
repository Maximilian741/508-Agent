/**
 * Insights — a server-backed accessibility metrics dashboard.
 *
 * Unlike the on-device ActivityDashboard (which reads localStorage and is per
 * browser), this page reads the server's own persisted analysis results via
 * GET /metrics/overview. It is the source of truth: it survives a cache clear,
 * follows the user across devices, and aggregates the whole team's documents
 * when the user belongs to one.
 *
 * Auth-gated. Read-only — it never spends credits.
 */

import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import {
  Account,
  getMetricsOverview,
  loadAccount,
  MetricsOverview,
  refreshAccount,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

function _scoreTone(theme: ReturnType<typeof useTheme>, score: number): string {
  if (score >= 90) return theme.colors.success;
  if (score >= 70) return theme.colors.warning;
  return theme.colors.danger;
}

function _fmtDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function InsightsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [data, setData] = useState<MetricsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErrored(false);
    const acct = await refreshAccount();
    const resolved = acct ?? loadAccount();
    setAccount(resolved);
    if (resolved) {
      const overview = await getMetricsOverview();
      // The backend always returns a valid object (zeros when empty) for a
      // signed-in user, so a null result here means the request actually
      // failed — surface that instead of a misleading "no audits yet".
      if (overview) setData(overview);
      else setErrored(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // --- Auth gate -----------------------------------------------------------
  if (!account) {
    return (
      <Screen scroll title="Insights">
        <Hero
          eyebrow="INSIGHTS"
          title="Your accessibility metrics, in one place"
          subtitle="Sign in to see how many documents you've made accessible, how your scores trend over time, and what your whole team is shipping."
        />
        <Card>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            Use the Sign in button at the top right to view your insights.
          </Text>
        </Card>
      </Screen>
    );
  }

  if (loading) {
    return (
      <Screen scroll title="Insights">
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.accent} />
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
            Loading your metrics…
          </Text>
        </View>
      </Screen>
    );
  }

  if (errored) {
    return (
      <Screen scroll title="Insights">
        <Hero
          eyebrow="INSIGHTS"
          title="Couldn't load your metrics"
          subtitle="Something went wrong reaching the server. Your data is safe — this is just the dashboard view."
        />
        <Card>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            We couldn't load your metrics right now. Check your connection and try again.
          </Text>
          <View style={{ alignItems: "flex-start", marginTop: 12 }}>
            <Button title="Retry" onPress={() => void load()} />
          </View>
        </Card>
      </Screen>
    );
  }

  const totals = data?.totals;
  const isEmpty = !totals || totals.documentsAnalyzed === 0;

  if (isEmpty) {
    return (
      <Screen scroll title="Insights">
        <Hero eyebrow="INSIGHTS" title="No audits yet" subtitle="Run your first audit and your metrics will start filling in here." />
        <EmptyState
          title="Nothing to chart yet"
          message="Once you analyze a document, this dashboard tracks your scores, issues found and fixed, and how it all trends over time."
        />
        <View style={{ alignItems: "flex-start", marginTop: 12 }}>
          <Button title="Start an audit" onPress={() => router.push("/audit" as any)} />
        </View>
      </Screen>
    );
  }

  const scope = data!.scope;
  const scopeLabel =
    scope.kind === "team"
      ? `Team: ${scope.teamName || "your team"} · ${scope.memberCount} member${scope.memberCount === 1 ? "" : "s"}`
      : "Your documents";

  const maxWeekDocs = Math.max(1, ...data!.timeline.map((p) => p.documents));
  const maxFmtDocs = Math.max(1, ...data!.byFormat.map((f) => f.documents));
  const totalGradeDocs = Math.max(1, data!.byGrade.reduce((s, g) => s + g.documents, 0));

  return (
    <Screen scroll title="Insights">
      <Hero
        eyebrow="INSIGHTS"
        title="Accessibility metrics"
        subtitle="The server's own record of every document you've analyzed — persistent and, for teams, shared across everyone."
      />

      <View style={{ marginBottom: 12 }}>
        <Chip label={scopeLabel} tone={scope.kind === "team" ? "info" : "default"} />
      </View>

      {/* --- KPI strip ----------------------------------------------------- */}
      <View style={styles.kpiGrid}>
        <Stat label="Documents analyzed" value={String(totals!.documentsAnalyzed)} theme={theme} />
        <Stat
          label="Average score"
          value={String(totals!.avgScore)}
          valueColor={_scoreTone(theme, totals!.avgScore)}
          theme={theme}
        />
        <Stat label="Issues found" value={totals!.issuesFound.toLocaleString()} theme={theme} />
        <Stat
          label="Auto-fixed"
          value={`${totals!.autoFixablePct}%`}
          valueColor={theme.colors.success}
          sub={`${totals!.issuesAutoFixed.toLocaleString()} of ${totals!.issuesFound.toLocaleString()}`}
          theme={theme}
        />
        <Stat
          label="Pending manual"
          value={totals!.issuesPendingManual.toLocaleString()}
          theme={theme}
        />
      </View>

      {/* --- Timeline (weekly document volume) ----------------------------- */}
      <Card style={{ marginTop: 12 }}>
        <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Documents analyzed over time</Text>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 12 }]}>
          Last {data!.timeline.length} weeks. Bar height is the number of documents; the number above each bar is that week's average score.
        </Text>
        <View
          accessibilityRole={"img" as any}
          accessibilityLabel={`Weekly document volume for the last ${data!.timeline.length} weeks.`}
          style={styles.chartRow}
        >
          {data!.timeline.map((p) => {
            const h = 8 + Math.round((p.documents / maxWeekDocs) * 96);
            return (
              <View key={p.weekStart} style={styles.chartCol}>
                <Text style={[styles.chartValue, { color: theme.colors.textMuted }]}>
                  {p.documents > 0 ? p.avgScore : ""}
                </Text>
                <View
                  style={[
                    styles.bar,
                    {
                      height: h,
                      backgroundColor: p.documents > 0 ? theme.colors.accent : theme.colors.surface3,
                    },
                  ]}
                />
                <Text style={[styles.chartLabel, { color: theme.colors.textMuted }]} numberOfLines={1}>
                  {_fmtDate(p.weekStart)}
                </Text>
              </View>
            );
          })}
        </View>
      </Card>

      {/* --- By format ----------------------------------------------------- */}
      <Card style={{ marginTop: 12 }}>
        <Text style={[styles.cardTitle, { color: theme.colors.text }]}>By format</Text>
        {data!.byFormat.map((f) => (
          <View key={f.format} style={styles.breakRow}>
            <Text style={[styles.breakLabel, { color: theme.colors.text }]}>{f.format.toUpperCase()}</Text>
            <View style={[styles.breakBarTrack, { backgroundColor: theme.colors.surface3 }]}>
              <View
                style={[
                  styles.breakBarFill,
                  { width: `${Math.round((f.documents / maxFmtDocs) * 100)}%`, backgroundColor: theme.colors.accent },
                ]}
              />
            </View>
            <Text style={[styles.breakMeta, { color: theme.colors.textMuted }]}>
              {f.documents} doc{f.documents === 1 ? "" : "s"} · avg {f.avgScore}
            </Text>
          </View>
        ))}
      </Card>

      {/* --- By grade ------------------------------------------------------ */}
      <Card style={{ marginTop: 12 }}>
        <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Score distribution</Text>
        <View style={styles.gradeRow}>
          {data!.byGrade.map((g) => {
            const pct = Math.round((g.documents / totalGradeDocs) * 100);
            const tone = g.grade === "A" || g.grade === "B" ? "success" : g.grade === "C" ? "warning" : "danger";
            return (
              <View key={g.grade} style={styles.gradeItem}>
                <Chip label={`${g.grade}  ${g.documents}`} tone={tone as any} />
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 4 }]}>{pct}%</Text>
              </View>
            );
          })}
        </View>
      </Card>

      {/* --- Recent documents --------------------------------------------- */}
      <Card style={{ marginTop: 12 }}>
        <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Recent documents</Text>
        {data!.recentDocuments.map((d, i) => (
          <View
            key={d.documentId || `${d.filename}-${i}`}
            style={[
              styles.recentRow,
              { borderTopColor: theme.colors.border, borderTopWidth: i === 0 ? 0 : 1 },
            ]}
          >
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={[theme.typography.body, { color: theme.colors.text }]} numberOfLines={1}>
                {d.filename}
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                {d.format || "?"} · {d.issuesFound} issue{d.issuesFound === 1 ? "" : "s"} · {d.fixedAutomatically} auto-fixed
                {d.analyzedAt ? ` · ${_fmtDate(d.analyzedAt)}` : ""}
              </Text>
            </View>
            <Text style={[styles.recentScore, { color: _scoreTone(theme, d.score) }]}>
              {d.score}
              {d.grade ? ` ${d.grade}` : ""}
            </Text>
          </View>
        ))}
      </Card>

      <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 12 }]}>
        Read-only — these numbers are the server's own measurements. {scope.kind === "team" ? "Covers every member of your team." : "Covers documents you've analyzed."}
      </Text>
    </Screen>
  );
}

function Stat({
  label,
  value,
  sub,
  valueColor,
  theme,
}: {
  label: string;
  value: string;
  sub?: string;
  valueColor?: string;
  theme: ReturnType<typeof useTheme>;
}) {
  return (
    <View style={[styles.stat, { backgroundColor: theme.colors.surface2, borderColor: theme.colors.border }]}>
      <Text style={[styles.statValue, { color: valueColor || theme.colors.text }]}>{value}</Text>
      <Text style={[styles.statLabel, { color: theme.colors.textMuted }]}>{label}</Text>
      {sub ? (
        <Text style={[styles.statSub, { color: theme.colors.textMuted }]} numberOfLines={2}>
          {sub}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  center: { alignItems: "center", justifyContent: "center", paddingVertical: 48 },
  kpiGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  stat: { flexGrow: 1, flexBasis: 150, minWidth: 140, borderWidth: 1, borderRadius: 6, padding: 14 },
  statValue: { fontSize: 28, fontWeight: "800", letterSpacing: -0.5 },
  statLabel: { fontSize: 12, fontWeight: "600", marginTop: 2 },
  statSub: { fontSize: 11, marginTop: 2 },
  cardTitle: { fontSize: 16, fontWeight: "700", marginBottom: 4 },
  chartRow: { flexDirection: "row", alignItems: "flex-end", gap: 4, height: 140 },
  chartCol: { flex: 1, alignItems: "center", justifyContent: "flex-end" },
  chartValue: { fontSize: 9, fontWeight: "700", marginBottom: 2 },
  bar: { width: "78%", borderTopLeftRadius: 3, borderTopRightRadius: 3, minHeight: 8 },
  chartLabel: { fontSize: 9, marginTop: 4 },
  breakRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  breakLabel: { width: 56, fontSize: 12, fontWeight: "700" },
  breakBarTrack: { flex: 1, height: 10, borderRadius: 5, overflow: "hidden" },
  breakBarFill: { height: 10, borderRadius: 5 },
  breakMeta: { width: 130, fontSize: 12, textAlign: "right" },
  gradeRow: { flexDirection: "row", flexWrap: "wrap", gap: 16, marginTop: 8 },
  gradeItem: { alignItems: "center" },
  recentRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10 },
  recentScore: { fontSize: 18, fontWeight: "800" },
});
