/**
 * Home — landing page with one obvious entry point + recent audits.
 */

import { useEffect, useMemo, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { AuditHistoryEntry, clearHistory, loadHistory } from "../src/domain/auditHistory";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { HistoryList } from "../src/ui/components/HistoryList";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { TrendChart } from "../src/ui/components/TrendChart";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

export default function HomeScreen() {
  const router = useRouter();
  const theme = useTheme();
  const toast = useToast();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const backendHealthMessage = useAppStore((state) => state.backendHealthMessage);
  const refreshBackendUrl = useAppStore((state) => state.refreshBackendUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const setMockMode = useAppStore((state) => state.setMockMode);

  const [history, setHistory] = useState<AuditHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");

  useEffect(() => {
    if (!mockMode) {
      void refreshBackendUrl();
    }
    setHistory(loadHistory());
    // history & toast aren't dependencies — we want this to run on mount and
    // whenever mockMode flips, full stop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mockMode, refreshBackendUrl]);

  const filteredHistory = useMemo(() => {
    const q = historyQuery.trim().toLowerCase();
    if (!q) return history;
    return history.filter(
      (e) =>
        e.filename.toLowerCase().includes(q) ||
        e.grade.toLowerCase().includes(q) ||
        e.sourceFormat.toLowerCase().includes(q),
    );
  }, [history, historyQuery]);

  const trendPoints = useMemo(
    () =>
      [...history]
        .reverse()
        .slice(-12)
        .map((e) => ({ label: e.filename, score: e.score })),
    [history],
  );

  const ready = mockMode || backendHealth === "ok";
  const statusLabel = mockMode
    ? "Demo mode"
    : backendHealth === "ok"
    ? "Connected"
    : backendHealth === "error"
    ? "Backend offline"
    : "Checking…";

  const summary = useMemo(() => {
    if (!history.length) return null;
    const totalIssues = history.reduce((acc, e) => acc + e.totalIssues, 0);
    const avgScore = history.reduce((acc, e) => acc + e.score, 0) / history.length;
    return { docs: history.length, totalIssues, avgScore };
  }, [history]);

  return (
    <Screen scroll title="Home">
      <Hero
        eyebrow="Section 508 · WCAG 2.1 · PDF/UA"
        title="508 Agent"
        subtitle="Open a Word, PowerPoint, or PDF document and walk through every accessibility issue — one at a time, in plain English, with a clear fix proposal you can approve or reject."
        rightSlot={
          <Button
            title={ready ? "Start an audit →" : "Start an audit (Demo)"}
            onPress={() => {
              if (!ready) setMockMode(true);
              router.push("/audit");
            }}
            variant="secondary"
          />
        }
      />

      <Card>
        <View style={styles.statusHead}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>System status</Text>
          <Chip
            label={statusLabel}
            tone={
              mockMode
                ? "warning"
                : backendHealth === "ok"
                ? "success"
                : backendHealth === "error"
                ? "danger"
                : "default"
            }
          />
        </View>
        {mockMode ? (
          <InlineNotice
            tone="warning"
            title="You're in Demo Mode"
            message="The numbers and findings you see are fake — useful for exploring the UI but not for real audits. Switch off Demo Mode in Settings (or click below) to analyze a real document."
          />
        ) : backendHealth === "error" ? (
          <InlineNotice
            tone="danger"
            title="Analyzer service isn't running"
            message={
              backendHealthMessage ??
              "Open a terminal in the project folder and run:  cd backend  then  python dev_run.py — leave it running, then come back."
            }
          />
        ) : (
          <InlineNotice
            tone="success"
            title="Ready"
            message={`Connected to ${apiBaseUrl}. Pick a file on the next screen to start an audit.`}
          />
        )}
      </Card>

      {summary ? (
        <Card>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Your work so far</Text>
          <View style={styles.metricRow}>
            <Metric value={`${summary.docs}`} label="documents audited" />
            <Metric value={`${summary.totalIssues}`} label="issues found" />
            <Metric value={summary.avgScore.toFixed(1)} label="avg score" />
          </View>
          <View style={{ marginTop: 16 }}>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 4 }]}>
              Score trend (last {trendPoints.length} runs)
            </Text>
            <TrendChart points={trendPoints} />
          </View>
        </Card>
      ) : null}

      <Card>
        <View style={styles.statusHead}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Recent audits</Text>
          {history.length > 0 ? <Chip label={`${history.length}`} tone="info" /> : null}
        </View>
        {history.length > 4 ? (
          <TextInput
            value={historyQuery}
            onChangeText={setHistoryQuery}
            placeholder="Search by filename, grade, or format…"
            placeholderTextColor={theme.colors.textMuted}
            accessibilityLabel="Search audit history"
            style={[
              styles.search,
              {
                color: theme.colors.text,
                borderColor: theme.colors.border,
                backgroundColor: theme.colors.surface,
              },
            ]}
          />
        ) : null}
        <HistoryList
          entries={filteredHistory}
          onSelect={(entry) => {
            if (entry.snapshot) {
              toast.info(`Reopening ${entry.filename}`, {
                description: "Restoring your decisions and findings from the saved snapshot.",
              });
              router.push(`/audit?historyId=${encodeURIComponent(entry.id)}` as any);
            } else {
              toast.warning(`Snapshot not available for ${entry.filename}`, {
                description: "Drop the source file again on the audit screen — only the summary was kept.",
              });
              router.push("/audit");
            }
          }}
          onClear={
            history.length
              ? () => {
                  clearHistory();
                  setHistory([]);
                  toast.info("Audit history cleared");
                }
              : undefined
          }
        />
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>What happens in an audit</Text>
        <View style={styles.steps}>
          {STEPS.map((step, i) => (
            <View key={step.title} style={styles.step}>
              <View style={[styles.stepDot, { backgroundColor: theme.colors.accent }]}>
                <Text style={styles.stepDotText}>{i + 1}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>
                  {step.title}
                </Text>
                <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
                  {step.body}
                </Text>
              </View>
            </View>
          ))}
        </View>
      </Card>

      <View style={styles.cta}>
        <Button
          title={ready ? "Start an audit →" : "Start an audit (Demo)"}
          onPress={() => {
            if (!ready) setMockMode(true);
            router.push("/audit");
          }}
        />
        <View style={styles.ctaSecondary}>
          <Button
            title="Contrast checker"
            onPress={() => router.push("/tools/contrast")}
            variant="ghost"
          />
          <Button
            title="Help & glossary"
            onPress={() => router.push("/help")}
            variant="ghost"
          />
          <Button title="Settings" onPress={() => router.push("/settings")} variant="ghost" />
        </View>
      </View>
    </Screen>
  );
}

function Metric({ value, label }: { value: string; label: string }) {
  const theme = useTheme();
  return (
    <View style={styles.metric}>
      <Text style={[theme.typography.title, { color: theme.colors.text, fontSize: 28 }]}>
        {value}
      </Text>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{label}</Text>
    </View>
  );
}

const STEPS = [
  {
    title: "You drop a document",
    body: "PDF, Word (.docx) or PowerPoint (.pptx). Files are processed locally — nothing leaves the machine unless you've explicitly configured an AI key.",
  },
  {
    title: "We list every accessibility issue",
    body: "Each finding gets a plain-English explanation: what's wrong, why it matters for users with disabilities, and which WCAG/§508 rule it cites.",
  },
  {
    title: "You approve fixes one at a time",
    body: "Some are deterministic and safe (remove decorative alt text, set scope on header cells). Others are AI-suggested and need your judgment (alt text, link rewrites). You're in control of every change.",
  },
  {
    title: "We apply only what you approved",
    body: "Rejected items get queued for manual handling. The remediated file is yours to download — and you can export an audit report for compliance records.",
  },
];

const styles = StyleSheet.create({
  hero: { gap: 6, marginBottom: 8 },
  statusHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  metricRow: { flexDirection: "row", justifyContent: "space-around", gap: 16, marginTop: 8 },
  metric: { alignItems: "center", flex: 1 },
  steps: { gap: 14, marginTop: 10 },
  step: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  stepDot: { width: 26, height: 26, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  stepDotText: { color: "#FFFFFF", fontWeight: "800", fontSize: 13 },
  cta: { gap: 10, marginTop: 4 },
  ctaSecondary: { flexDirection: "row", gap: 8 },
  search: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
    marginTop: 8,
    marginBottom: 8,
  },
});
