/**
 * Recent audits list — used on the home screen.
 */

import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { AuditHistoryEntry } from "../../domain/auditHistory";
import { useTheme } from "../useTheme";
import { Chip } from "./Chip";

export interface HistoryListProps {
  entries: AuditHistoryEntry[];
  onSelect?: (entry: AuditHistoryEntry) => void;
  onClear?: () => void;
  emptyHint?: string;
}

export function HistoryList({ entries, onSelect, onClear, emptyHint }: HistoryListProps) {
  const theme = useTheme();
  if (!entries.length) {
    return (
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
        {emptyHint ?? "No recent audits yet. Your history will appear here once you've run one."}
      </Text>
    );
  }
  return (
    <View>
      {entries.slice(0, 8).map((entry) => (
        <Pressable accessibilityRole="button"
          key={entry.id}
          onPress={() => onSelect?.(entry)}
          style={[styles.row, { borderColor: theme.colors.border }]}
          accessibilityLabel={`Open audit for ${entry.filename}`}
        >
          <View style={[styles.gradeDot, { backgroundColor: _gradeColor(entry.grade, theme) }]}>
            <Text style={styles.gradeDotText}>{entry.grade}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 15 }]} numberOfLines={1}>
              {entry.filename}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              {entry.sourceFormat.toUpperCase()} · {entry.totalIssues} issue
              {entry.totalIssues === 1 ? "" : "s"} · {_relative(entry.ranAt)}
            </Text>
          </View>
          <Chip
            label={`${entry.score.toFixed(0)}`}
            tone={
              entry.score >= 90
                ? "success"
                : entry.score >= 70
                ? "info"
                : entry.score >= 60
                ? "warning"
                : "danger"
            }
          />
        </Pressable>
      ))}
      {onClear ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Clear history" onPress={onClear} style={styles.clear}>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Clear history
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function _gradeColor(grade: string, theme: ReturnType<typeof useTheme>): string {
  if (grade === "A+" || grade === "A") return theme.colors.success;
  if (grade === "B") return theme.colors.info;
  if (grade === "C") return theme.colors.warning;
  if (grade === "D") return theme.colors.warning;
  return theme.colors.danger;
}

function _relative(iso: string): string {
  const delta = Date.now() - new Date(iso).getTime();
  if (delta < 60_000) return "just now";
  if (delta < 60 * 60_000) return `${Math.round(delta / 60_000)}m ago`;
  if (delta < 24 * 60 * 60_000) return `${Math.round(delta / (60 * 60_000))}h ago`;
  return new Date(iso).toLocaleDateString();
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 10,
    paddingHorizontal: 4,
    borderTopWidth: 1,
  },
  gradeDot: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  gradeDotText: { color: "#FFFFFF", fontWeight: "800", fontSize: 12 },
  clear: { paddingVertical: 10, alignItems: "center" },
});
