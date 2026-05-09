/**
 * Dashboard - hand-drawn audit queue.
 *
 * Notebook aesthetic: paper background, Caveat heading, three "stat strip"
 * cards with wobble borders, then a list of recent audits styled as table
 * rows. Each row links into the existing audit screen via the router.
 *
 * Read history from the existing localStorage-backed loadHistory() so the
 * dashboard always reflects the same audits the home screen sees.
 */

import { useEffect, useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { AuditHistoryEntry, loadHistory } from "../src/domain/auditHistory";
import { Screen } from "../src/ui/components/Screen";
import {
  SketchCard,
  SketchHeading,
  SketchChip,
  SketchButton,
  SketchSeverityDot,
  SketchHighlight,
  SketchHandNote,
  Severity,
  ensureSketchFontsInjected,
  sketchPalette,
  sketchFontFamily,
} from "../src/ui/sketch";

const ONE_DAY_MS = 24 * 60 * 60 * 1000;
const ONE_WEEK_MS = 7 * ONE_DAY_MS;
// Rough estimate: each issue not having to be hand-fixed saves about 4 minutes
// of reviewer time.
const MINUTES_SAVED_PER_ISSUE = 4;

function severityForScore(score: number): Severity {
  if (score >= 90) return "low";
  if (score >= 75) return "medium";
  if (score >= 60) return "high";
  return "critical";
}

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
    if (Number.isFinite(t) && t >= cutoff) {
      scansThisWeek += 1;
    }
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

export default function DashboardScreen() {
  const router = useRouter();
  const [history, setHistory] = useState<AuditHistoryEntry[]>([]);

  useEffect(() => {
    ensureSketchFontsInjected();
    setHistory(loadHistory());
  }, []);

  const stats = useMemo(() => computeStats(history), [history]);
  const recent = useMemo(() => history.slice(0, 12), [history]);

  return (
    <Screen scroll title="Dashboard" contentStyle={dashStyles.screen}>
      <View style={dashStyles.headerRow}>
        <SketchHeading size="display" eyebrow="DASHBOARD">
          Your audit queue
        </SketchHeading>
        <SketchHandNote arrow="left" tilt={-0.4}>
          recent work, top of pile
        </SketchHandNote>
      </View>

      <View style={dashStyles.statStrip}>
        <StatTile
          label="Scans this week"
          value={String(stats.scansThisWeek)}
          highlighter="yellow"
          tilt={-0.4}
        />
        <StatTile
          label="Score average"
          value={stats.averageScore == null ? "--" : String(stats.averageScore)}
          highlighter="green"
          suffix={stats.averageScore == null ? "" : " / 100"}
          tilt={0}
        />
        <StatTile
          label="Time saved (estimate)"
          value={formatTimeSaved(stats.timeSavedMinutes)}
          highlighter="blue"
          tilt={0.4}
        />
      </View>

      <SketchCard density="cozy" tilt={0}>
        <View style={dashStyles.tableHeaderRow}>
          <Text style={[dashStyles.colHeader, dashStyles.colFile]}>File</Text>
          <Text style={[dashStyles.colHeader, dashStyles.colScore]}>Score</Text>
          <Text style={[dashStyles.colHeader, dashStyles.colDate]}>When</Text>
          <Text style={[dashStyles.colHeader, dashStyles.colStatus]}>Status</Text>
          <Text style={[dashStyles.colHeader, dashStyles.colAction]}>Open</Text>
        </View>

        {recent.length === 0 ? (
          <EmptyQueue onStart={() => router.push("/" as any)} />
        ) : (
          recent.map((entry, idx) => (
            <AuditRow
              key={entry.id}
              entry={entry}
              isLast={idx === recent.length - 1}
              onOpen={() => router.push("/audit" as any)}
            />
          ))
        )}
      </SketchCard>
    </Screen>
  );
}

interface StatTileProps {
  label: string;
  value: string;
  suffix?: string;
  highlighter: "yellow" | "pink" | "green" | "blue" | "coral";
  tilt: -0.4 | 0 | 0.4;
}

function StatTile({ label, value, suffix, highlighter, tilt }: StatTileProps) {
  return (
    <SketchCard density="cozy" tilt={tilt} style={dashStyles.statTile}>
      <Text style={dashStyles.statLabel}>{label}</Text>
      <View style={dashStyles.statValueRow}>
        <SketchHighlight tone={highlighter}>
          <Text style={dashStyles.statValueInner}>{value}</Text>
        </SketchHighlight>
        {suffix ? <Text style={dashStyles.statSuffix}>{suffix}</Text> : null}
      </View>
    </SketchCard>
  );
}

interface AuditRowProps {
  entry: AuditHistoryEntry;
  isLast: boolean;
  onOpen: () => void;
}

function AuditRow({ entry, isLast, onOpen }: AuditRowProps) {
  const sev = severityForScore(entry.score);
  const pending = entry.pending ?? 0;
  const status = pending > 0 ? "In review" : "Done";
  return (
    <View
      style={[
        dashStyles.row,
        !isLast ? dashStyles.rowDivider : null,
      ]}
    >
      <View style={[dashStyles.cell, dashStyles.colFile]}>
        <Text style={dashStyles.fileName} numberOfLines={1}>
          {entry.filename || "(untitled)"}
        </Text>
        <Text style={dashStyles.fileMeta} numberOfLines={1}>
          {(entry.sourceFormat || "doc").toUpperCase()}
          {"  -  "}
          {entry.totalIssues} issues
        </Text>
      </View>

      <View style={[dashStyles.cell, dashStyles.colScore, dashStyles.scoreCell]}>
        <SketchSeverityDot severity={sev} />
        <Text style={dashStyles.scoreText}>{entry.score}</Text>
        <Text style={dashStyles.gradeText}>{entry.grade}</Text>
      </View>

      <View style={[dashStyles.cell, dashStyles.colDate]}>
        <Text style={dashStyles.dateText}>{formatDate(entry.ranAt)}</Text>
      </View>

      <View style={[dashStyles.cell, dashStyles.colStatus]}>
        <SketchChip
          label={status}
          highlighter={pending > 0 ? "yellow" : "green"}
        />
      </View>

      <View style={[dashStyles.cell, dashStyles.colAction]}>
        <SketchButton
          title="Open"
          variant="primary"
          onPress={onOpen}
          accessibilityLabel={"Open audit for " + (entry.filename || "untitled")}
        />
      </View>
    </View>
  );
}

function EmptyQueue({ onStart }: { onStart: () => void }) {
  return (
    <View style={dashStyles.empty}>
      <SketchHeading size="subtitle">No audits yet</SketchHeading>
      <Text style={dashStyles.emptyBody}>
        Drop a document on the home screen to run your first audit. Past
        scans show up here so you can pick one back up.
      </Text>
      <View style={{ marginTop: 12, alignSelf: "flex-start" }}>
        <SketchButton title="Start an audit" onPress={onStart} />
      </View>
      <SketchHandNote arrow="left" tilt={-0.4}>
        first one is the hardest
      </SketchHandNote>
    </View>
  );
}

const dashStyles = StyleSheet.create({
  screen: {
    backgroundColor: sketchPalette.paper,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: 12,
    marginBottom: 4,
  },
  statStrip: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 16,
    marginBottom: 8,
  },
  statTile: {
    flexBasis: 220,
    flexGrow: 1,
    minWidth: 200,
  },
  statLabel: {
    fontFamily: sketchFontFamily.label,
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: 0.04 * 11,
    color: sketchPalette.pencil,
    marginBottom: 8,
  },
  statValueRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 6,
  },
  statValueInner: {
    fontFamily: sketchFontFamily.display,
    fontSize: 30,
    fontWeight: "600",
    color: sketchPalette.ink,
    lineHeight: 32,
  },
  statSuffix: {
    fontFamily: sketchFontFamily.body,
    fontSize: 14,
    color: sketchPalette.pencil,
  },
  tableHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingBottom: 6,
    marginBottom: 4,
    borderBottomWidth: 1.5,
    borderBottomColor: sketchPalette.ink,
  },
  colHeader: {
    fontFamily: sketchFontFamily.label,
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: 0.04 * 11,
    color: sketchPalette.pencil,
  },
  colFile: { flexBasis: 240, flexGrow: 3 },
  colScore: { flexBasis: 120, flexGrow: 1 },
  colDate: { flexBasis: 90, flexGrow: 1 },
  colStatus: { flexBasis: 110, flexGrow: 1 },
  colAction: { flexBasis: 90, flexGrow: 0, alignItems: "flex-end" as const },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    gap: 8,
  },
  rowDivider: {
    borderBottomWidth: 1,
    borderBottomColor: sketchPalette.lineSoft,
    borderStyle: "dashed",
  },
  cell: {
    paddingRight: 8,
  },
  fileName: {
    fontFamily: sketchFontFamily.body,
    fontSize: 14,
    fontWeight: "700",
    color: sketchPalette.ink,
  },
  fileMeta: {
    fontFamily: sketchFontFamily.body,
    fontSize: 12,
    color: sketchPalette.pencil,
    marginTop: 2,
  },
  scoreCell: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  scoreText: {
    fontFamily: sketchFontFamily.display,
    fontSize: 20,
    fontWeight: "700",
    color: sketchPalette.ink,
  },
  gradeText: {
    fontFamily: sketchFontFamily.label,
    fontSize: 11,
    color: sketchPalette.pencil,
    textTransform: "uppercase",
    letterSpacing: 0.04 * 11,
  },
  dateText: {
    fontFamily: sketchFontFamily.body,
    fontSize: 13,
    color: sketchPalette.ink2,
  },
  empty: {
    alignItems: "flex-start",
    paddingVertical: 16,
    gap: 6,
  },
  emptyBody: {
    fontFamily: sketchFontFamily.body,
    fontSize: 14,
    color: sketchPalette.pencil,
    lineHeight: 19,
    maxWidth: 480,
  },
});
