/**
 * ActivityDashboard — replacement for the bare "Your work so far" card on
 * the home screen.
 *
 * Composed of three pieces:
 *   1. Top metric strip — docs audited, issues found, avg score, longest
 *      streak, achievements unlocked.
 *   2. Weekly heatmap (5 weeks × 7 days, GitHub-style) of audit activity.
 *      Rendered as inline SVG with a green ramp.
 *   3. Most-flagged-rules mini bar chart from the last 20 audits.
 *
 * Plus a "Today" callout: if there's an audit dated today we celebrate it,
 * otherwise we surface the most recent draft and a Resume button.
 *
 * All inputs come from the existing localStorage stores so this component
 * doesn't take props beyond a few callbacks.
 */

import React, { useMemo } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { unlockedCount, totalAchievements } from "../../domain/achievements";
import { AuditHistoryEntry } from "../../domain/auditHistory";
import { useTheme } from "../useTheme";
import { Card } from "./Card";
import { Chip } from "./Chip";

export interface ActivityDashboardProps {
  history: AuditHistoryEntry[];
  /** Called when the user taps the "Resume" button on the Today callout. */
  onResume?: (entry: AuditHistoryEntry) => void;
  /** Called when the user taps the "Start new audit" CTA. */
  onStartAudit?: () => void;
}

export function ActivityDashboard({
  history,
  onResume,
  onStartAudit,
}: ActivityDashboardProps) {
  const theme = useTheme();

  /* ---- Derived stats ----------------------------------------------------- */
  const stats = useMemo(() => _computeStats(history), [history]);
  const heatmap = useMemo(() => _buildHeatmap(history, 5), [history]);
  const topRules = useMemo(() => _topRules(history, 20, 5), [history]);

  // "Today" check — has the user run any audit dated today (local time)?
  const today = new Date();
  const todayKey = _ymd(today);
  const auditedToday = history.some((h) => {
    try {
      return _ymd(new Date(h.ranAt)) === todayKey;
    } catch {
      return false;
    }
  });
  const mostRecent = history[0];

  return (
    <Card>
      <View style={styles.headerRow}>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
          Your work so far
        </Text>
        {history.length > 0 ? (
          <Chip
            label={auditedToday ? "Active today" : "Pick up where you left off"}
            tone={auditedToday ? "success" : "info"}
          />
        ) : null}
      </View>

      {/* ---- Top metric strip ---- */}
      <View style={styles.metricStrip}>
        <Metric value={`${stats.docs}`} label="docs audited" />
        <Metric value={`${stats.issues}`} label="issues found" />
        <Metric
          value={stats.docs > 0 ? stats.avgScore.toFixed(1) : "—"}
          label="avg score"
        />
        <Metric value={`${stats.longestStreak}`} label="day streak" />
        <Metric
          value={`${stats.achievements}/${stats.achievementTotal}`}
          label="badges"
        />
      </View>

      {/* ---- Today / Resume callout ---- */}
      <View
        style={[
          styles.callout,
          {
            backgroundColor: auditedToday
              ? theme.colors.successSoft
              : theme.colors.surface2,
            borderColor: auditedToday ? theme.colors.success : theme.colors.border,
          },
        ]}
      >
        <View style={{ flex: 1 }}>
          <Text
            style={[
              styles.calloutHead,
              {
                color: auditedToday ? theme.colors.success : theme.colors.text,
              },
            ]}
          >
            {auditedToday ? "Nice — you audited today!" : "Pick up where you left off"}
          </Text>
          <Text
            style={[styles.calloutBody, { color: theme.colors.textMuted }]}
            numberOfLines={2}
          >
            {auditedToday
              ? `${stats.todayCount} audit${stats.todayCount === 1 ? "" : "s"} today across ${stats.todayIssues} issue${
                  stats.todayIssues === 1 ? "" : "s"
                }. Keep the streak alive.`
              : mostRecent
              ? `Last document: ${mostRecent.filename}`
              : "Run your first audit to start tracking activity here."}
          </Text>
        </View>
        {mostRecent && onResume ? (
          <Pressable accessibilityRole="button"
            onPress={() => onResume(mostRecent)}
            accessibilityLabel={`Resume audit ${mostRecent.filename}`}
            style={[
              styles.resumeBtn,
              { backgroundColor: theme.colors.accent },
            ]}
          >
            <Text style={styles.resumeBtnText}>
              {auditedToday ? "Open latest" : "Resume"}
            </Text>
          </Pressable>
        ) : onStartAudit ? (
          <Pressable accessibilityRole="button"
            onPress={onStartAudit}
            accessibilityLabel="Start an audit"
            style={[
              styles.resumeBtn,
              { backgroundColor: theme.colors.accent },
            ]}
          >
            <Text style={styles.resumeBtnText}>Start an audit</Text>
          </Pressable>
        ) : null}
      </View>

      {/* ---- Heatmap ---- */}
      <Text
        style={[
          theme.typography.caption,
          { color: theme.colors.textMuted, marginTop: 16, marginBottom: 6 },
        ]}
      >
        Activity (last 5 weeks)
      </Text>
      <Heatmap weeks={heatmap} themeAccent={theme.colors.success} />

      {/* ---- Most-flagged rules ---- */}
      {topRules.length > 0 ? (
        <View style={{ marginTop: 16 }}>
          <Text
            style={[
              theme.typography.caption,
              { color: theme.colors.textMuted, marginBottom: 6 },
            ]}
          >
            Most-flagged rules (last 20 audits)
          </Text>
          <View style={{ gap: 6 }}>
            {topRules.map((row) => {
              const pct = (row.count / topRules[0].count) * 100;
              return (
                <View key={row.code} style={styles.barRow}>
                  <Text
                    style={[
                      styles.barLabel,
                      { color: theme.colors.text },
                    ]}
                    numberOfLines={1}
                  >
                    {row.code}
                  </Text>
                  <View
                    style={[
                      styles.barTrack,
                      { backgroundColor: theme.colors.surface2 },
                    ]}
                  >
                    <View
                      style={[
                        styles.barFill,
                        {
                          width: `${Math.max(8, pct)}%` as any,
                          backgroundColor: theme.colors.accentSecondary,
                        },
                      ]}
                    />
                  </View>
                  <Text
                    style={[
                      styles.barCount,
                      { color: theme.colors.textMuted },
                    ]}
                  >
                    {row.count}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>
      ) : null}
    </Card>
  );
}

/* ------------------------------------------------------------------ *
 * Sub-components                                                      *
 * ------------------------------------------------------------------ */

function Metric({ value, label }: { value: string; label: string }) {
  const theme = useTheme();
  return (
    <View style={styles.metric}>
      <Text style={[styles.metricValue, { color: theme.colors.text }]}>
        {value}
      </Text>
      <Text style={[styles.metricLabel, { color: theme.colors.textMuted }]}>
        {label}
      </Text>
    </View>
  );
}

interface HeatmapDay {
  date: string;
  count: number;
}

function Heatmap({
  weeks,
  themeAccent,
}: {
  weeks: HeatmapDay[][];
  themeAccent: string;
}) {
  const theme = useTheme();
  const cell = 14;
  const gap = 3;
  const labelW = 22;
  const labelH = 12;
  const cols = weeks.length;
  const rows = 7;
  const width = labelW + cols * (cell + gap);
  const height = labelH + rows * (cell + gap);
  const max = Math.max(1, ...weeks.flat().map((d) => d.count));

  const colorFor = (n: number): string => {
    if (n <= 0) return theme.colors.surface2;
    const intensity = Math.min(1, n / max);
    // Map 0..1 onto 4 discrete shades for crisp legibility.
    if (intensity > 0.75) return _shade(themeAccent, 1.0);
    if (intensity > 0.5) return _shade(themeAccent, 0.75);
    if (intensity > 0.25) return _shade(themeAccent, 0.5);
    return _shade(themeAccent, 0.3);
  };

  const dayLabels = ["M", "W", "F"];
  const dayLabelRowIdx = [0, 2, 4];

  // RN-Web does not render arbitrary <svg> children from RN's <View>; we
  // use a raw DOM <svg> on web and fall back to a bunch of <View>s on native.
  if (Platform.OS === "web") {
    return (
      // @ts-ignore — raw DOM svg
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Audit activity over the last 5 weeks"
      >
        {dayLabels.map((d, i) => (
          // @ts-ignore — text in SVG
          <text
            key={d}
            x={0}
            y={labelH + dayLabelRowIdx[i] * (cell + gap) + cell - 3}
            fontSize={9}
            fill={theme.colors.textMuted}
          >
            {d}
          </text>
        ))}
        {weeks.map((week, x) =>
          week.map((day, y) => (
            // @ts-ignore — rect in SVG
            <rect
              key={`${x}-${y}`}
              x={labelW + x * (cell + gap)}
              y={labelH + y * (cell + gap)}
              width={cell}
              height={cell}
              rx={3}
              ry={3}
              fill={colorFor(day.count)}
            >
              {/* @ts-ignore — title for accessible tooltip */}
              <title>
                {day.date}: {day.count} audit{day.count === 1 ? "" : "s"}
              </title>
            </rect>
          )),
        )}
      </svg>
    );
  }

  // Native fallback: stacked rows.
  return (
    <View style={{ gap: 3 }}>
      {Array.from({ length: rows }).map((_, y) => (
        <View key={y} style={{ flexDirection: "row", gap: 3 }}>
          {weeks.map((week, x) => (
            <View
              key={`${x}-${y}`}
              style={{
                width: cell,
                height: cell,
                borderRadius: 3,
                backgroundColor: colorFor(week[y]?.count ?? 0),
              }}
            />
          ))}
        </View>
      ))}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Pure helpers                                                        *
 * ------------------------------------------------------------------ */

function _ymd(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function _computeStats(history: AuditHistoryEntry[]) {
  const docs = history.length;
  const issues = history.reduce((acc, e) => acc + (e.totalIssues || 0), 0);
  const avgScore = docs > 0 ? history.reduce((acc, e) => acc + (e.score || 0), 0) / docs : 0;

  // Day-set for streaks.
  const days = new Set<string>();
  for (const e of history) {
    try {
      days.add(_ymd(new Date(e.ranAt)));
    } catch {
      /* ignore bad dates */
    }
  }
  const sorted = Array.from(days).sort();
  let longest = sorted.length > 0 ? 1 : 0;
  let cur = 1;
  for (let i = 1; i < sorted.length; i++) {
    const a = new Date(sorted[i - 1] + "T00:00:00").getTime();
    const b = new Date(sorted[i] + "T00:00:00").getTime();
    const diff = Math.round((b - a) / 86_400_000);
    if (diff === 1) {
      cur++;
      longest = Math.max(longest, cur);
    } else if (diff > 1) {
      cur = 1;
    }
  }

  // Today rollup.
  const todayKey = _ymd(new Date());
  const todays = history.filter((e) => {
    try {
      return _ymd(new Date(e.ranAt)) === todayKey;
    } catch {
      return false;
    }
  });

  let achievements = 0;
  let achievementTotal = 0;
  try {
    achievements = unlockedCount();
    achievementTotal = totalAchievements();
  } catch {
    // ignore
  }

  return {
    docs,
    issues,
    avgScore,
    longestStreak: longest,
    todayCount: todays.length,
    todayIssues: todays.reduce((acc, e) => acc + (e.totalIssues || 0), 0),
    achievements,
    achievementTotal,
  };
}

/**
 * Build the heatmap grid: `weeks` columns × 7 rows (Mon..Sun).  Most-recent
 * week is the rightmost column.
 */
function _buildHeatmap(history: AuditHistoryEntry[], weeks: number): HeatmapDay[][] {
  const counts: Record<string, number> = {};
  for (const e of history) {
    try {
      const k = _ymd(new Date(e.ranAt));
      counts[k] = (counts[k] ?? 0) + 1;
    } catch {
      /* ignore */
    }
  }
  // Anchor on the Monday of the current week, then walk back `weeks - 1`
  // weeks to find the start.
  const today = new Date();
  const dow = (today.getDay() + 6) % 7; // Mon = 0
  const startOfThisWeek = new Date(today);
  startOfThisWeek.setHours(0, 0, 0, 0);
  startOfThisWeek.setDate(today.getDate() - dow);

  const grid: HeatmapDay[][] = [];
  for (let w = 0; w < weeks; w++) {
    const col: HeatmapDay[] = [];
    for (let d = 0; d < 7; d++) {
      const day = new Date(startOfThisWeek);
      day.setDate(startOfThisWeek.getDate() - (weeks - 1 - w) * 7 + d);
      const key = _ymd(day);
      col.push({ date: key, count: counts[key] ?? 0 });
    }
    grid.push(col);
  }
  return grid;
}

/**
 * Top-N rule codes across the most recent `windowSize` audits.  We cheat a
 * little: AuditHistoryEntry only carries summary counts, but the snapshot
 * (when present) holds the full report and therefore the rule ids.  When a
 * snapshot is absent we fall back to category buckets keyed on the source
 * format so the chart still has *something* to show.
 */
function _topRules(
  history: AuditHistoryEntry[],
  windowSize: number,
  top: number,
): { code: string; count: number }[] {
  const counts: Record<string, number> = {};
  for (const e of history.slice(0, windowSize)) {
    const snap: any = (e as any).snapshot;
    const violations: any[] | undefined = snap?.report?.violations;
    if (Array.isArray(violations) && violations.length > 0) {
      for (const v of violations) {
        const code = (v?.ruleId || v?.rule_id || "UNKNOWN") as string;
        counts[code] = (counts[code] ?? 0) + 1;
      }
    } else {
      // Fallback bucket: format-level "issues found" so the chart isn't empty
      // for users whose snapshots got dropped on quota.
      const code = `${(e.sourceFormat || "doc").toUpperCase()} (summary)`;
      counts[code] = (counts[code] ?? 0) + (e.totalIssues || 0);
    }
  }
  const rows = Object.entries(counts)
    .map(([code, count]) => ({ code, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, top);
  return rows;
}

/**
 * Lighten / darken a hex color for the heatmap ramp.  Falls back to the
 * input if parsing fails.
 */
function _shade(hexOrRgb: string, weight: number): string {
  try {
    if (hexOrRgb.startsWith("rgba(") || hexOrRgb.startsWith("rgb(")) {
      const m = hexOrRgb.match(/\d+(\.\d+)?/g);
      if (!m) return hexOrRgb;
      const [r, g, b] = m.slice(0, 3).map((s) => parseInt(s, 10));
      return `rgba(${r}, ${g}, ${b}, ${Math.min(1, weight)})`;
    }
    const hex = hexOrRgb.replace("#", "");
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${Math.min(1, Math.max(0.15, weight))})`;
  } catch {
    return hexOrRgb;
  }
}

/* ------------------------------------------------------------------ *
 * Styles                                                              *
 * ------------------------------------------------------------------ */

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 10,
    flexWrap: "wrap",
    gap: 8,
  },
  metricStrip: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    marginBottom: 12,
  },
  metric: {
    flex: 1,
    minWidth: 90,
    alignItems: "flex-start",
  },
  metricValue: {
    fontSize: 24,
    fontWeight: "800",
    letterSpacing: -0.4,
  },
  metricLabel: {
    fontSize: 11,
    fontWeight: "600",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    marginTop: 2,
  },
  callout: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    marginTop: 4,
  },
  calloutHead: {
    fontSize: 14,
    fontWeight: "700",
    marginBottom: 2,
  },
  calloutBody: {
    fontSize: 13,
    lineHeight: 18,
  },
  resumeBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 8,
  },
  resumeBtnText: {
    color: "#FFFFFF",
    fontWeight: "700",
    fontSize: 13,
  },
  barRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  barLabel: {
    width: 200,
    fontSize: 12,
    fontWeight: "600",
  },
  barTrack: {
    flex: 1,
    height: 8,
    borderRadius: 999,
    overflow: "hidden",
  },
  barFill: {
    height: "100%",
    borderRadius: 999,
  },
  barCount: {
    fontSize: 12,
    fontWeight: "700",
    minWidth: 26,
    textAlign: "right",
  },
});
