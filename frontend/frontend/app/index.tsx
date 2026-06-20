/**
 * Home - workshop-feel dashboard.
 *
 * Three vertically-stacked sections, each with its own spatial rhythm:
 *   1. Welcome strip - serif greeting on the global maple shader, no card.
 *   2. Workshop bench - one tall, considered "Start an audit" panel.
 *   3. Recent work - rows separated by hairlines, not a grid of tiles.
 *
 * No stat tiles. No identical card stack. The page reads top-to-bottom like
 * a notebook page, not a dashboard.
 */

import { useEffect, useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { AuditHistoryEntry, clearHistory, loadHistory } from "../src/domain/auditHistory";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Chip } from "../src/ui/components/Chip";
import { ScoreBadge } from "../src/ui/components/ScoreBadge";
import { Screen } from "../src/ui/components/Screen";
import { Hero } from "../src/ui/components/Hero";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

function greetingFor(date: Date): string {
  const h = date.getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

const FIRST_RUN_STEPS = [
  {
    n: "1",
    title: "Drop a document",
    body: "Upload a PDF, Word, or PowerPoint file. We never train on your documents.",
  },
  {
    n: "2",
    title: "Review findings",
    body: "Walk every WCAG 2.1, Section 508, and PDF/UA finding one at a time, in plain English.",
  },
  {
    n: "3",
    title: "Approve & download",
    body: "Approve the fixes you trust, edit the rest, and download a remediated file — plus a conformance certificate.",
  },
];

function relativeTime(iso: string | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function HomeScreen() {
  const router = useRouter();
  const theme = useTheme();
  const toast = useToast();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const refreshBackendUrl = useAppStore((state) => state.refreshBackendUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const setMockMode = useAppStore((state) => state.setMockMode);

  const [history, setHistory] = useState<AuditHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [now] = useState(() => new Date());

  useEffect(() => {
    if (!mockMode) void refreshBackendUrl();
    setHistory(loadHistory());
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

  const ready = mockMode || backendHealth === "ok";

  // Resume copy - what is the user actually in the middle of?
  const resumeLine = useMemo(() => {
    const inProgress = history.find(
      (h) =>
        h.snapshot &&
        Object.values(h.snapshot.decisions).some((d) => d.decision === "pending"),
    );
    if (inProgress) {
      const pending = Object.values(inProgress.snapshot!.decisions).filter(
        (d) => d.decision === "pending",
      ).length;
      return `${pending} finding${pending === 1 ? "" : "s"} still waiting on ${inProgress.filename}.`;
    }
    if (history.length === 0)
      return "Find and fix accessibility issues in your PDFs, Word docs, and slides — checked against WCAG 2.1, Section 508, and PDF/UA.";
    return `${history.length} document${history.length === 1 ? "" : "s"} on the bench. Pick one up where you left it.`;
  }, [history]);

  const greeting = `${greetingFor(now)}.`;

  return (
    <Screen scroll title="Home">
      {/* === 1. Welcome hero ============================================== */}
      <Hero
        shader="nebula"
        eyebrow="HOME"
        title={greeting}
        subtitle={resumeLine}
      />
      {!ready ? (
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.warning },
          ]}
        >
          Analyzer offline. Demo mode will use sample data.
        </Text>
      ) : null}

      {/* === 2. Workshop bench - the primary action ====================== */}
      <Pressable
        onPress={() => {
          if (!ready) setMockMode(true);
          router.push("/audit");
        }}
        accessibilityRole="button"
        accessibilityLabel="Start an audit"
        style={({ hovered }: any) => [
          styles.bench,
          {
            borderColor: hovered ? theme.colors.accent : theme.colors.border,
            backgroundColor: theme.colors.surface,
          },
        ]}
      >
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.accent },
          ]}
        >
          BEGIN
        </Text>
        <Text
          style={[
            theme.typography.displaySmall as any,
            { color: theme.colors.text, marginTop: 8 },
          ]}
        >
          Open a document.
        </Text>
        <Text
          style={[
            theme.typography.body,
            {
              color: theme.colors.textMuted,
              marginTop: 6,
              maxWidth: 560,
              lineHeight: 22,
            },
          ]}
        >
          Drop a PDF, Word, or PowerPoint file on the audit screen and we will walk
          every accessibility finding with you, one at a time, in plain English.
        </Text>
        <View style={styles.benchFooter}>
          <Text
            style={[
              theme.typography.caption,
              { color: theme.colors.textMuted, letterSpacing: 0.8 },
            ]}
          >
            .PDF   .DOCX   .PPTX
          </Text>
          <Button
            title={ready ? "Start an audit" : "Start in demo mode"}
            onPress={() => {
              if (!ready) setMockMode(true);
              router.push("/audit");
            }}
          />
        </View>
      </Pressable>

      {/* Quiet row of side tools - secondary to the bench */}
      <View style={styles.toolRow}>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to batch"
          onPress={() => router.push("/batch")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Batch mode
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            A folder at a time
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to readability checker"
          onPress={() => router.push("/tools/readability")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Readability checker
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Plain-language score (3.1.5)
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to tools contrast"
          onPress={() => router.push("/tools/contrast")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Contrast checker
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            WCAG ratios, side-by-side
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to palette matrix"
          onPress={() => router.push("/tools/palette")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Palette matrix
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Which colour pairs pass AA
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to accessibility statement generator"
          onPress={() => router.push("/tools/accessibility-statement")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Statement generator
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Publish-ready, in seconds
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to AI alt-text generator"
          onPress={() => router.push("/tools/alt-text")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Alt-text generator
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Drop an image, get alt text
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to link text checker"
          onPress={() => router.push("/tools/link-text")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Link text checker
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Is “click here” hurting you?
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Go to help"
          onPress={() => router.push("/help")}
          style={({ hovered }: any) => [styles.toolLink, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            Help & glossary
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Every rule, plain-English
          </Text>
        </Pressable>
      </View>

      {/* === 3. Recent work - rows, not cards ============================= */}
      {history.length > 0 ? (
        <View style={styles.recent}>
          <View style={styles.recentHead}>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
              Recent work
            </Text>
            <Pressable accessibilityRole="button" accessibilityLabel="Clear history"
              onPress={() => {
                clearHistory();
                setHistory([]);
                toast.info("Audit history cleared");
              }}
            >
              <Text
                style={[
                  theme.typography.caption,
                  { color: theme.colors.textMuted, textDecorationLine: "underline" },
                ]}
              >
                Clear history
              </Text>
            </Pressable>
          </View>

          {history.length > 4 ? (
            <TextInput
              value={historyQuery}
              onChangeText={setHistoryQuery}
              placeholder="Filter by name, grade, format..."
              placeholderTextColor={theme.colors.textMuted}
              accessibilityLabel="Filter recent work"
              style={[
                styles.search,
                {
                  color: theme.colors.text,
                  borderColor: theme.colors.border,
                },
              ]}
            />
          ) : null}

          <View style={styles.rows}>
            {filteredHistory.map((entry, i) => (
              <RecentRow
                key={entry.id}
                entry={entry}
                first={i === 0}
                onPress={() => {
                  if (entry.snapshot) {
                    router.push(`/audit?historyId=${encodeURIComponent(entry.id)}` as any);
                  } else {
                    toast.warning(`Snapshot not available for ${entry.filename}`, {
                      description: "Drop the source file again - only the summary was kept.",
                    });
                    router.push("/audit");
                  }
                }}
              />
            ))}
          </View>
        </View>
      ) : (
        <FirstRun
          onSeePricing={() => router.push("/billing" as any)}
          onSeeTour={() => router.push("/landing" as any)}
        />
      )}

      {/* Footer - stays quiet at the bottom */}
      <View style={styles.footer}>
        <FooterLink label="About" path="/about" />
        <FooterDot />
        <FooterLink label="Security" path="/security" />
        <FooterDot />
        <FooterLink label="Settings" path="/settings" />
        <FooterDot />
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted },
          ]}
        >
          {ready ? `Connected to ${apiBaseUrl}` : "Demo mode"}
        </Text>
      </View>
    </Screen>
  );
}

function FirstRun({
  onSeePricing,
  onSeeTour,
}: {
  onSeePricing: () => void;
  onSeeTour: () => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.firstRun}>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>How it works</Text>
      <View style={styles.steps}>
        {FIRST_RUN_STEPS.map((s) => (
          <View
            key={s.n}
            style={[
              styles.step,
              { borderColor: theme.colors.border, backgroundColor: theme.colors.surface },
            ]}
          >
            <View style={[styles.stepDot, { backgroundColor: theme.colors.accent }]}>
              <Text style={styles.stepDotText}>{s.n}</Text>
            </View>
            <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
              {s.title}
            </Text>
            <Text
              style={[
                theme.typography.caption,
                {
                  color: theme.colors.textMuted,
                  marginTop: 4,
                  textTransform: "none",
                  letterSpacing: 0.2,
                  lineHeight: 18,
                },
              ]}
            >
              {s.body}
            </Text>
          </View>
        ))}
      </View>

      {/* Trust + transparency strip */}
      <View style={styles.trustRow}>
        <Chip label="WCAG 2.1" tone="info" />
        <Chip label="Section 508" tone="info" />
        <Chip label="PDF/UA" tone="info" />
      </View>
      <Text
        style={[
          theme.typography.caption,
          {
            color: theme.colors.textMuted,
            textTransform: "none",
            letterSpacing: 0.2,
            lineHeight: 19,
            marginTop: 10,
            maxWidth: 620,
          },
        ]}
      >
        Free to start — create an account in seconds and your first audits are on us (25 free
        credits). After that, credit packs start at $5, or subscribe for a monthly allowance
        with certificates included.
      </Text>
      <View style={styles.firstRunLinks}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="See pricing"
          onPress={onSeePricing}
          style={({ hovered }: any) => [hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.accent, fontWeight: "600" }]}>
            See pricing
          </Text>
        </Pressable>
        <Text style={{ color: theme.colors.textMuted, opacity: 0.5 }}>·</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="The full tour"
          onPress={onSeeTour}
          style={({ hovered }: any) => [hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.accent, fontWeight: "600" }]}>
            The full tour
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

function RecentRow({
  entry,
  first,
  onPress,
}: {
  entry: AuditHistoryEntry;
  first: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  const approved = entry.snapshot
    ? Object.values(entry.snapshot.decisions).filter((d) => d.decision === "approved").length
    : 0;
  const rejected = entry.snapshot
    ? Object.values(entry.snapshot.decisions).filter((d) => d.decision === "rejected").length
    : 0;

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`Open ${entry.filename}`}
      style={({ hovered }: any) => [
        styles.row,
        {
          borderTopColor: theme.colors.border,
          borderTopWidth: first ? 0 : StyleSheet.hairlineWidth,
          backgroundColor: hovered ? theme.colors.surface + "80" : "transparent",
        },
      ]}
    >
      <View style={{ flex: 1, minWidth: 0 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <Text
            style={[
              theme.typography.body,
              { color: theme.colors.text, fontSize: 16, fontWeight: "600", flexShrink: 1 },
            ]}
            numberOfLines={1}
          >
            {entry.filename}
          </Text>
          {entry.id.startsWith("demo:") ? <Chip label="Sample" tone="warning" /> : null}
        </View>
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted, marginTop: 4, textTransform: "none", letterSpacing: 0.2, fontSize: 12 },
          ]}
        >
          {entry.sourceFormat.toUpperCase()} . {entry.totalIssues} finding{entry.totalIssues === 1 ? "" : "s"}
          {approved + rejected > 0
            ? `   .   ${approved} approved, ${rejected} rejected`
            : ""}
          {"   .   "}{relativeTime(entry.ranAt)}
        </Text>
      </View>
      <ScoreBadge
        score={entry.score}
        grade={entry.grade}
        subLabel=""
      />
    </Pressable>
  );
}

function FooterLink({ label, path }: { label: string; path: string }) {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Pressable accessibilityRole="button"
      onPress={() => router.push(path as any)}
      accessibilityLabel={label}
      style={({ hovered, focused }: any) => [
        hovered ? { opacity: 0.7 } : null,
        focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
      ]}
    >
      <Text
        style={[
          theme.typography.caption,
          { color: theme.colors.textMuted },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function FooterDot() {
  const theme = useTheme();
  return (
    <Text style={{ color: theme.colors.textMuted, opacity: 0.5 }}>.</Text>
  );
}

const styles = StyleSheet.create({
  // 2. Bench - the workshop. Tall, considered, a single object.
  bench: {
    borderWidth: 1,
    borderRadius: 18,
    paddingHorizontal: 32,
    paddingVertical: 36,
    minHeight: 220,
    justifyContent: "flex-start",
    ...(Platform.OS === "web"
      ? ({ transition: "border-color 180ms ease" } as any)
      : {}),
  },
  benchFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 28,
    flexWrap: "wrap",
    gap: 16,
  },
  toolRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 32,
    marginTop: 32,
    paddingHorizontal: 4,
  },
  toolLink: {
    minWidth: 160,
    paddingVertical: 4,
  },
  // 3. Recent work
  recent: {
    marginTop: 48,
    paddingHorizontal: 4,
  },
  recentHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 16,
  },
  search: {
    borderBottomWidth: 1,
    paddingVertical: 8,
    paddingHorizontal: 0,
    marginBottom: 8,
    fontSize: 14,
  },
  rows: {
    marginTop: 4,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    paddingVertical: 14,
    paddingHorizontal: 4,
  },
  footer: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 10,
    paddingTop: 32,
    paddingBottom: 24,
  },
  // First-run onboarding (shown only when there is no audit history)
  firstRun: {
    marginTop: 48,
    paddingHorizontal: 4,
  },
  steps: {
    flexDirection: "row",
    gap: 12,
    flexWrap: "wrap",
    marginTop: 16,
  },
  step: {
    flex: 1,
    minWidth: 220,
    borderWidth: 1,
    borderRadius: 12,
    padding: 16,
  },
  stepDot: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 10,
  },
  stepDotText: { color: "#FFFFFF", fontWeight: "800", fontSize: 13 },
  trustRow: {
    flexDirection: "row",
    gap: 8,
    flexWrap: "wrap",
    marginTop: 24,
  },
  firstRunLinks: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginTop: 14,
  },
});
