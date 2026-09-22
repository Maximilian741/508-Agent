/**
 * Home — the one-step fixer.
 *
 * A person drops ANY document and gets an accessible file back, with as
 * little thinking as possible, and sees where every remaining problem is.
 * The whole flow lives in src/ui/flow/OneStepFixer; this page only supplies
 * the shader hero it runs inside (the shader belongs to the front doors, see
 * scripts/check-design.mjs) and the quiet extras below the fold.
 *
 * First-time visitors see: the drop zone, one sentence, one link. Returning
 * visitors also get their recent fixed files and the advanced tools, BELOW
 * the drop zone and hidden while a file is in progress.
 *
 * The step-by-step review with every rule, score and decision is still
 * there, unchanged, as the "Advanced audit" (/audit).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { AuditHistoryEntry, clearHistory, loadHistory } from "../src/domain/auditHistory";
import { loadAccount, onAccountChanged } from "../src/domain/account";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { RecentRemediations } from "../src/ui/components/RecentRemediations";
import { ScoreBadge } from "../src/ui/components/ScoreBadge";
import { Screen } from "../src/ui/components/Screen";
import { Seo } from "../src/ui/components/Seo";
import { linkProps } from "../src/ui/components/linkProps";
import { OneStepFixer } from "../src/ui/flow/OneStepFixer";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

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

const TOOLS: Array<{ label: string; sub: string; path: string }> = [
  { label: "Many files at once", sub: "Fix a whole folder", path: "/batch" },
  { label: "Check a web page", sub: "Paste a link", path: "/scan-url" },
  { label: "Colour contrast", sub: "Is this text easy to read?", path: "/tools/contrast" },
  { label: "Picture descriptions", sub: "Write a good one", path: "/tools/alt-text" },
  { label: "Link words", sub: "Is “click here” a problem?", path: "/tools/link-text" },
  { label: "Headings", sub: "Check the outline", path: "/tools/headings" },
  { label: "Plain language", sub: "How easy is it to read?", path: "/tools/readability" },
  { label: "Help", sub: "Every check, explained", path: "/help" },
];

export default function HomeScreen() {
  const router = useRouter();
  const theme = useTheme();
  const toast = useToast();
  const params = useLocalSearchParams<{ resume?: string; paid?: string }>();
  const backendHealth = useAppStore((state) => state.backendHealth);
  const refreshBackendUrl = useAppStore((state) => state.refreshBackendUrl);

  // Back from the checkout page (or a reload mid-flow): pick the file up again.
  const [resume] = useState(() => (params.resume === "fix" ? { paid: params.paid === "1" } : null));
  const onResumeRead = useCallback(() => {
    // Clear the query so a reload doesn't re-run it.
    try {
      router.replace("/");
    } catch {
      /* ignore */
    }
  }, [router]);

  const [history, setHistory] = useState<AuditHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [active, setActive] = useState(false);
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    void refreshBackendUrl();
    setHistory(loadHistory());
    setSignedIn(!!loadAccount());
    return onAccountChanged(() => setSignedIn(!!loadAccount()));
  }, [refreshBackendUrl]);

  const filteredHistory = useMemo(() => {
    const q = historyQuery.trim().toLowerCase();
    if (!q) return history;
    return history.filter((e) => e.filename.toLowerCase().includes(q) || e.sourceFormat.toLowerCase().includes(q));
  }, [history, historyQuery]);

  const returning = signedIn || history.length > 0;

  return (
    <Screen scroll title="Fix a document">
      <Seo
        title="508 Agent — Make any document accessible: PDF, Word, PowerPoint, Excel"
        description="Drop a PDF, Word, PowerPoint, Excel file or web page. We check it for accessibility free, fix what we can automatically, and show you exactly where anything left is."
      />

      <OneStepFixer
        resume={resume}
        onResumeRead={onResumeRead}
        onBusyChange={setActive}
        renderHero={({ intensity, children }) => (
          <Hero
            shader
            shaderIntensity={intensity}
            title="Make your document accessible."
            subtitle="Drop a PDF, Word, PowerPoint, Excel file, web page or picture. We check it for free and fix what we can."
          >
            <View style={{ marginTop: 10 }}>{children}</View>
          </Hero>
        )}
      />

      {backendHealth === "error" ? (
        <InlineNotice tone="warning" title="We can't reach the service right now" message="Try again in a moment." />
      ) : null}

      <View style={styles.fine}>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          Your file stays private. We delete it after about a day and never use it to train AI.{" "}
        </Text>
        <Pressable
          {...linkProps("/privacy")}
          style={({ focused }: any) => [focused ? focusRing(theme) : null]}
        >
          <Text style={[theme.typography.caption, { color: theme.colors.text, textDecorationLine: "underline" }]}>
            Privacy
          </Text>
        </Pressable>
      </View>

      <View style={styles.advanced}>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Want to review every change yourself?
        </Text>
        <Button title="Advanced audit" variant="ghost" href="/audit" />
      </View>

      {!active && signedIn ? (
        <RecentRemediations title="Your fixed files" subtitle="Download any of them again." />
      ) : null}

      {!active && history.length > 0 ? (
        <View style={styles.recent}>
          <View style={styles.recentHead}>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Your advanced audits</Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                clearHistory();
                setHistory([]);
                toast.info("History cleared");
              }}
              style={({ focused }: any) => [focused ? focusRing(theme) : null]}
            >
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted, textDecorationLine: "underline" }]}>
                Clear history
              </Text>
            </Pressable>
          </View>
          {history.length > 4 ? (
            <TextInput
              value={historyQuery}
              onChangeText={setHistoryQuery}
              placeholder="Filter by name or type…"
              placeholderTextColor={theme.colors.textMuted}
              accessibilityLabel="Filter your advanced audits"
              style={[styles.search, { color: theme.colors.text, borderColor: theme.colors.border }]}
            />
          ) : null}
          <View>
            {filteredHistory.map((entry, i) => (
              <RecentRow
                key={entry.id}
                entry={entry}
                first={i === 0}
                onPress={() => {
                  if (entry.snapshot) {
                    router.push(`/audit?historyId=${encodeURIComponent(entry.id)}` as any);
                  } else {
                    toast.warning(`We only kept the summary of ${entry.filename}`, {
                      description: "Drop the file again to see everything.",
                    });
                    router.push("/audit");
                  }
                }}
              />
            ))}
          </View>
        </View>
      ) : null}

      {!active && returning ? (
        <View style={styles.tools}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>More tools</Text>
          <View style={styles.toolRow}>
            {TOOLS.map((t) => (
              <Pressable
                key={t.path}
                {...linkProps(t.path)}
                style={({ hovered, focused }: any) => [
                  styles.toolLink,
                  hovered ? { opacity: 0.75 } : null,
                  focused ? focusRing(theme) : null,
                ]}
              >
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>{t.label}</Text>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{t.sub}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      ) : null}

      <View style={styles.footer}>
        <FooterLink label="How it works" path="/landing" />
        <FooterDot />
        <FooterLink label="Pricing" path="/billing" />
        <FooterDot />
        <FooterLink label="Fix guides" path="/fix" />
        <FooterDot />
        <FooterLink label="About" path="/about" />
        <FooterDot />
        <FooterLink label="Security" path="/security" />
        <FooterDot />
        <FooterLink label="Settings" path="/settings" />
      </View>
    </Screen>
  );
}

function focusRing(theme: ReturnType<typeof useTheme>) {
  return {
    outlineColor: theme.colors.accent,
    outlineWidth: 2,
    outlineStyle: "solid",
    outlineOffset: 2,
  } as any;
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
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`Open ${entry.filename}`}
      style={({ hovered, focused }: any) => [
        styles.row,
        {
          borderTopColor: theme.colors.border,
          borderTopWidth: first ? 0 : StyleSheet.hairlineWidth,
          backgroundColor: hovered ? theme.colors.surface + "80" : "transparent",
        },
        focused ? focusRing(theme) : null,
      ]}
    >
      <View style={{ flex: 1, minWidth: 0 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <Text
            style={[theme.typography.body, { color: theme.colors.text, fontSize: 16, fontWeight: "600", flexShrink: 1 }]}
            numberOfLines={1}
          >
            {entry.filename}
          </Text>
          {entry.id.startsWith("demo:") ? <Chip label="Sample" tone="warning" /> : null}
        </View>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 4 }]}>
          {entry.sourceFormat.toUpperCase()} · {entry.totalIssues} {entry.totalIssues === 1 ? "thing" : "things"} found · {relativeTime(entry.ranAt)}
        </Text>
      </View>
      <ScoreBadge score={entry.score} grade={entry.grade} subLabel="" />
    </Pressable>
  );
}

function FooterLink({ label, path }: { label: string; path: string }) {
  const theme = useTheme();
  return (
    <Pressable
      {...linkProps(path)}
      style={({ hovered, focused }: any) => [hovered ? { opacity: 0.7 } : null, focused ? focusRing(theme) : null]}
    >
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{label}</Text>
    </Pressable>
  );
}

function FooterDot() {
  const theme = useTheme();
  return <Text style={{ color: theme.colors.textMuted }}>·</Text>;
}

const styles = StyleSheet.create({
  fine: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    paddingHorizontal: 4,
  },
  advanced: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 4,
  },
  tools: {
    marginTop: 16,
    paddingHorizontal: 4,
    gap: 12,
  },
  toolRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 24,
  },
  toolLink: {
    minWidth: 150,
    paddingVertical: 4,
  },
  recent: {
    marginTop: 16,
    paddingHorizontal: 4,
  },
  recentHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 12,
  },
  search: {
    borderBottomWidth: 1,
    paddingVertical: 8,
    paddingHorizontal: 0,
    marginBottom: 8,
    fontSize: 14,
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
});
