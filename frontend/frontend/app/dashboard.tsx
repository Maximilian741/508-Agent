/**
 * Dashboard - signed-in users only.
 *
 * Shows the user's audit queue + a small stat strip. Visually consistent
 * with the rest of the app (twilight Hero + Card + Chip primitives), not
 * the wireframe paper aesthetic.
 *
 * Auth gate: if no account is loaded, render a sign-in CTA instead of the
 * dashboard contents. The actual SignInModal is mounted in AppNav, so we
 * just nudge the user toward the top-right Sign in button.
 */

import { useEffect, useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { Account, loadAccount, refreshAccount } from "../src/domain/account";
import {
  AuditHistoryEntry,
  clearHistory,
  deleteHistoryEntry,
  loadHistory,
} from "../src/domain/auditHistory";
import { fuzzyFilter } from "../src/domain/fuzzy";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Dialog } from "../src/ui/components/Dialog";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { openHowItWorks } from "../src/ui/components/OnboardingTour";
import { openSystemCheck } from "../src/ui/components/SystemCheckWizard";
import { PixelIcon } from "../src/ui/components/PixelIcon";
import { Screen } from "../src/ui/components/Screen";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

const ONE_DAY_MS = 24 * 60 * 60 * 1000;
const ONE_WEEK_MS = 7 * ONE_DAY_MS;
const MINUTES_SAVED_PER_ISSUE = 4;

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "";
  const today = new Date();
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function formatTimeSaved(minutes: number): string {
  if (minutes <= 0) return "0 min";
  if (minutes < 60) return Math.round(minutes) + " min";
  const hours = minutes / 60;
  if (hours < 10) return hours.toFixed(1) + " hr";
  return Math.round(hours) + " hr";
}

interface DashboardStats {
  scansThisWeek: number;
  averageScore: number | null;
  timeSavedMinutes: number;
}

function computeStats(history: AuditHistoryEntry[]): DashboardStats {
  if (history.length === 0) {
    return { scansThisWeek: 0, averageScore: null, timeSavedMinutes: 0 };
  }
  const cutoff = Date.now() - ONE_WEEK_MS;
  let scansThisWeek = 0;
  let scoreSum = 0;
  let scoreCount = 0;
  let issuesHandled = 0;
  for (const entry of history) {
    const t = new Date(entry.ranAt).getTime();
    if (Number.isFinite(t) && t >= cutoff) scansThisWeek += 1;
    if (typeof entry.score === "number" && Number.isFinite(entry.score)) {
      scoreSum += entry.score;
      scoreCount += 1;
    }
    issuesHandled += (entry.approved ?? 0) + (entry.rejected ?? 0);
  }
  return {
    scansThisWeek,
    averageScore: scoreCount > 0 ? Math.round(scoreSum / scoreCount) : null,
    timeSavedMinutes: issuesHandled * MINUTES_SAVED_PER_ISSUE,
  };
}

function chipToneForScore(score: number): "success" | "info" | "warning" | "danger" {
  if (score >= 90) return "success";
  if (score >= 75) return "info";
  if (score >= 60) return "warning";
  return "danger";
}

export default function DashboardScreen() {
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const [history, setHistory] = useState<AuditHistoryEntry[]>([]);
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [authChecked, setAuthChecked] = useState(false);
  const [query, setQuery] = useState("");
  // Pending delete target (single row) or the "clear all" sentinel.
  const [pendingDelete, setPendingDelete] = useState<AuditHistoryEntry | null>(null);
  const [confirmClearAll, setConfirmClearAll] = useState(false);

  // On mount: refresh the account from the backend (in case the token
  // is stale or the user just signed in on another tab) and load the
  // history. We always load history so the empty state still shows
  // properly when an account exists but has no audits yet.
  useEffect(() => {
    let cancelled = false;
    setHistory(loadHistory());
    refreshAccount()
      .then((fresh) => {
        if (cancelled) return;
        if (fresh) setAccount(fresh);
        setAuthChecked(true);
      })
      .catch(() => {
        if (cancelled) return;
        setAuthChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo(() => computeStats(history), [history]);
  // Fuzzy-filter by filename (and format) when searching; otherwise show the
  // 12 most recent. When a query is present, search the FULL history so an
  // older audit isn't hidden by the 12-row cap.
  const filtered = useMemo(() => {
    if (query.trim()) {
      return fuzzyFilter(
        history,
        query,
        (e) => `${e.filename || ""} ${(e.sourceFormat || "")}`,
      );
    }
    return history.slice(0, 12);
  }, [history, query]);

  const handleDelete = (entry: AuditHistoryEntry) => {
    const next = deleteHistoryEntry(entry.id);
    setHistory(next);
    setPendingDelete(null);
    toast.success("Audit removed", {
      description: `"${entry.filename || "untitled"}" was removed from your history.`,
    });
  };

  const handleClearAll = () => {
    clearHistory();
    setHistory([]);
    setConfirmClearAll(false);
    toast.success("History cleared", { description: "All saved audits were removed." });
  };

  // Auth gate. While we're checking, render a quiet placeholder so the
  // unauthenticated CTA does not flash for users who actually are signed in.
  if (!account) {
    return (
      <Screen scroll title="Dashboard">
        <Hero
          eyebrow="DASHBOARD"
          title="Your audit queue"
          subtitle={
            authChecked
              ? "Sign in to see your saved audits, your time saved, and your remaining credits."
              : "Loading your dashboard..."
          }
        />
        {authChecked ? (
          <Card>
            <View style={styles.signInBlock}>
              <PixelIcon name="key" size={4} color={theme.colors.accent} />
              <View style={{ flex: 1, gap: 6 }}>
                <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
                  Sign in to see your dashboard
                </Text>
                <Text
                  style={[
                    theme.typography.body,
                    { color: theme.colors.textMuted },
                  ]}
                >
                  Your dashboard is private. Once you sign in, this page shows
                  every audit you have run, your weekly volume, the time you
                  have saved, and your remaining credits. Use the
                  {" "}
                  <Text style={{ color: theme.colors.accent, fontWeight: "700" }}>
                    Sign in
                  </Text>
                  {" "}
                  button in the top right.
                </Text>
                <View style={styles.signInActions}>
                  <Button
                    title="Run a free audit instead"
                    variant="ghost"
                    onPress={() => router.push("/audit" as any)}
                  />
                </View>
              </View>
            </View>
          </Card>
        ) : null}
      </Screen>
    );
  }

  // Signed-in state.
  const greetingName = account.displayName || account.email.split("@")[0] || "there";

  return (
    <Screen scroll title="Dashboard">
      <Hero
        eyebrow="DASHBOARD"
        title={"Welcome back, " + greetingName}
        subtitle={
          history.length === 0
            ? "Your dashboard is ready. Run your first audit to see stats and history here."
            : "Your audit queue, at a glance. Recent work is at the top."
        }
        rightSlot={
          <Chip
            label={account.credits + " credits"}
            tone={account.credits > 0 ? "success" : "warning"}
          />
        }
      />

      <View style={styles.statStrip}>
        <StatTile label="Scans this week" value={String(stats.scansThisWeek)} />
        <StatTile
          label="Average score"
          value={stats.averageScore == null ? "--" : String(stats.averageScore)}
          suffix={stats.averageScore == null ? "" : " / 100"}
        />
        <StatTile
          label="Time saved"
          value={formatTimeSaved(stats.timeSavedMinutes)}
        />
      </View>

      {account.credits === 0 ? (
        <InlineNotice
          tone="warning"
          title="No credits left"
          message="You can still run audits, but applying fixes and downloading remediated files needs credits. Top up to keep going."
          actionLabel="Buy credits"
          onAction={() => router.push("/billing" as any)}
        />
      ) : null}

      <Card>
        <View style={styles.cardHeaderRow}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
            Recent audits
          </Text>
          <View style={styles.headerActions}>
            <Button title="System check" variant="ghost" onPress={openSystemCheck} />
            <Button title="How it works" variant="ghost" onPress={openHowItWorks} />
            {history.length > 0 ? (
              <Button
                title="Clear all"
                variant="ghost"
                onPress={() => setConfirmClearAll(true)}
              />
            ) : null}
            <Button
              title="Run a new audit"
              onPress={() => router.push("/audit" as any)}
            />
          </View>
        </View>

        {history.length > 0 ? (
          <View style={[styles.searchRow, { borderRadius: theme.radius.xs, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
            <TextInput
              value={query}
              onChangeText={setQuery}
              placeholder="Search by file name…"
              placeholderTextColor={theme.colors.textMuted}
              accessibilityLabel="Search audits by file name"
              style={[styles.searchInput, { color: theme.colors.text }]}
              autoCapitalize="none"
              autoCorrect={false}
            />
            {query ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Clear search"
                onPress={() => setQuery("")}
                style={({ hovered }: any) => [{ padding: 4 }, hovered ? { opacity: 0.6 } : null]}
              >
                <Text style={{ color: theme.colors.textMuted, fontSize: 16 }}>✕</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {history.length === 0 ? (
          <EmptyState
            icon="doc"
            title="No audits yet"
            body="Drop a document on the audit screen to run your first audit. Past scans show up here so you can pick one back up."
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon="doc"
            title="No matches"
            body={`No saved audit matches "${query}". Try fewer letters, or clear the search.`}
          />
        ) : (
          <View style={styles.list}>
            <View style={[styles.listHeaderRow, { borderColor: theme.colors.border }]}>
              <Text style={[styles.colHeader, styles.colFile, { color: theme.colors.textMuted }]}>
                File
              </Text>
              <Text style={[styles.colHeader, styles.colScore, { color: theme.colors.textMuted }]}>
                Score
              </Text>
              <Text style={[styles.colHeader, styles.colDate, { color: theme.colors.textMuted }]}>
                When
              </Text>
              <Text style={[styles.colHeader, styles.colStatus, { color: theme.colors.textMuted }]}>
                Status
              </Text>
              <Text style={[styles.colHeader, styles.colAction, { color: theme.colors.textMuted }]}>
                Actions
              </Text>
            </View>
            {filtered.map((entry, idx) => (
              <AuditRow
                key={entry.id}
                entry={entry}
                isLast={idx === filtered.length - 1}
                onOpen={() =>
                  router.push(("/audit?historyId=" + encodeURIComponent(entry.id)) as any)
                }
                onDelete={() => setPendingDelete(entry)}
              />
            ))}
          </View>
        )}
      </Card>

      <Dialog
        open={!!pendingDelete}
        title="Remove this audit?"
        message={
          pendingDelete
            ? `"${pendingDelete.filename || "untitled"}" will be removed from your saved history on this device. This does not affect any file you already downloaded.`
            : ""
        }
        confirmLabel="Remove"
        cancelLabel="Keep"
        destructive
        onConfirm={() => pendingDelete && handleDelete(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />

      <Dialog
        open={confirmClearAll}
        title="Clear all saved audits?"
        message={`This removes all ${history.length} saved ${history.length === 1 ? "audit" : "audits"} from your history on this device. Downloaded files are not affected.`}
        confirmLabel="Clear all"
        cancelLabel="Cancel"
        destructive
        onConfirm={handleClearAll}
        onCancel={() => setConfirmClearAll(false)}
      />
    </Screen>
  );
}

interface StatTileProps {
  label: string;
  value: string;
  suffix?: string;
}

function StatTile({ label, value, suffix }: StatTileProps) {
  const theme = useTheme();
  return (
    <Card style={styles.statTile}>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
        {label}
      </Text>
      <View style={styles.statValueRow}>
        <Text style={[theme.typography.title, { color: theme.colors.text }]}>
          {value}
        </Text>
        {suffix ? (
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            {suffix}
          </Text>
        ) : null}
      </View>
    </Card>
  );
}

interface AuditRowProps {
  entry: AuditHistoryEntry;
  isLast: boolean;
  onOpen: () => void;
  onDelete: () => void;
}

function AuditRow({ entry, isLast, onOpen, onDelete }: AuditRowProps) {
  const theme = useTheme();
  const pending = entry.pending ?? 0;
  const status = pending > 0 ? "In review" : "Done";
  const statusTone: "warning" | "success" = pending > 0 ? "warning" : "success";

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={"Open audit for " + (entry.filename || "untitled")}
      onPress={onOpen}
      style={({ hovered, pressed }: any) => [
        styles.row,
        { borderRadius: theme.radius.none },
        !isLast
          ? { borderBottomWidth: 1, borderBottomColor: theme.colors.border }
          : null,
        hovered ? { backgroundColor: theme.colors.surface2 } : null,
        pressed ? { opacity: 0.85 } : null,
      ]}
    >
      <View style={[styles.cell, styles.colFile]}>
        <Text
          style={[
            theme.typography.body,
            { color: theme.colors.text, fontWeight: "700" },
          ]}
          numberOfLines={1}
        >
          {entry.filename || "(untitled)"}
        </Text>
        <Text
          style={[
            theme.typography.body,
            { color: theme.colors.textMuted, fontSize: 12, marginTop: 2 },
          ]}
          numberOfLines={1}
        >
          {(entry.sourceFormat || "doc").toUpperCase()}
          {"  "}
          {entry.totalIssues}
          {" issues"}
        </Text>
      </View>

      <View style={[styles.cell, styles.colScore, styles.scoreCell]}>
        <Chip
          label={entry.score + " " + (entry.grade || "")}
          tone={chipToneForScore(entry.score)}
        />
      </View>

      <View style={[styles.cell, styles.colDate]}>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 13 }]}>
          {formatDate(entry.ranAt)}
        </Text>
      </View>

      <View style={[styles.cell, styles.colStatus]}>
        <Chip label={status} tone={statusTone} />
      </View>

      <View style={[styles.cell, styles.colAction, styles.actionCell]}>
        <Button title="Open" variant="ghost" onPress={onOpen} />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={"Remove audit for " + (entry.filename || "untitled")}
          onPress={(e: any) => {
            if (e?.stopPropagation) e.stopPropagation();
            onDelete();
          }}
          style={({ hovered, pressed }: any) => [
            styles.deleteBtn,
            { borderRadius: theme.radius.sm, borderColor: theme.colors.border },
            hovered ? { borderColor: theme.colors.danger, opacity: 1 } : null,
            pressed ? { opacity: 0.7 } : null,
          ]}
        >
          <Text style={{ color: theme.colors.danger, fontSize: 13, fontWeight: "700" }}>✕</Text>
        </Pressable>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  signInBlock: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 16,
    paddingVertical: 8,
  },
  signInActions: {
    flexDirection: "row",
    gap: 8,
    flexWrap: "wrap",
    marginTop: 12,
  },
  statStrip: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
  },
  statTile: {
    flexBasis: 220,
    flexGrow: 1,
    minWidth: 200,
  },
  statValueRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 6,
    marginTop: 6,
  },
  cardHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    flexWrap: "wrap",
  },
  headerActions: { flexDirection: "row", alignItems: "center", gap: 8 },
  searchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 12,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    // RN-Web: kill the default focus outline; the border conveys focus.
    ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as any) : null),
  },
  list: { marginTop: 12 },
  listHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingBottom: 6,
    marginBottom: 4,
    borderBottomWidth: 1,
  },
  colHeader: {
    fontSize: 11,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  colFile: { flexBasis: 240, flexGrow: 3 },
  colScore: { flexBasis: 110, flexGrow: 1 },
  colDate: { flexBasis: 90, flexGrow: 1 },
  colStatus: { flexBasis: 110, flexGrow: 1 },
  colAction: { flexBasis: 130, flexGrow: 0, alignItems: "flex-end" },
  actionCell: { flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 6 },
  deleteBtn: {
    width: 32,
    height: 32,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    opacity: 0.75,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    paddingHorizontal: 4,
    gap: 8,
  },
  cell: { paddingRight: 8 },
  scoreCell: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
});
