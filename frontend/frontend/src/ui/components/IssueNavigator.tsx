/**
 * Issue navigator — sidebar list of every finding in the audit, with severity
 * color, decision badge, and click-to-jump.
 *
 * When the queue contains repeats of the same rule (common — many docs flag
 * MISSING_ALT_TEXT 5+ times) we collapse them under a single expandable
 * group so the sidebar stays scannable.  Click the group header to expand
 * and see each individual issue.
 */

import React, { useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { PipelineViolation } from "../../api/client";
import { lookupIssue } from "../../domain/issueCatalog";
import { useTheme } from "../useTheme";

export type IssueDecision = "pending" | "approved" | "rejected";

export interface IssueNavigatorProps {
  violations: PipelineViolation[];
  decisions: Record<string, { decision: IssueDecision }>;
  currentId: string | null;
  onSelect: (index: number) => void;
}

interface Group {
  ruleId: string;
  title: string;
  severity: PipelineViolation["severity"];
  members: { violation: PipelineViolation; index: number }[];
}

export function IssueNavigator({
  violations,
  decisions,
  currentId,
  onSelect,
}: IssueNavigatorProps) {
  const theme = useTheme();
  const groups = useMemo<Group[]>(() => {
    const map = new Map<string, Group>();
    violations.forEach((v, index) => {
      const existing = map.get(v.ruleId);
      if (existing) {
        existing.members.push({ violation: v, index });
      } else {
        const catalog = lookupIssue(v.ruleId);
        map.set(v.ruleId, {
          ruleId: v.ruleId,
          title: catalog.title,
          severity: v.severity,
          members: [{ violation: v, index }],
        });
      }
    });
    return Array.from(map.values());
  }, [violations]);

  // Default-expanded groups: the one containing the current issue, plus any
  // group that has only one member (always show those flat).
  const [expandedRules, setExpandedRules] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const g of groups) {
      if (g.members.length === 1) initial.add(g.ruleId);
    }
    return initial;
  });
  const currentGroup = useMemo(
    () => groups.find((g) => g.members.some((m) => m.violation.id === currentId)),
    [groups, currentId],
  );

  return (
    <ScrollView
      style={[
        styles.outer,
        { backgroundColor: theme.colors.surface, borderColor: theme.colors.border },
      ]}
      contentContainerStyle={styles.content}
    >
      <Text style={[styles.title, { color: theme.colors.textMuted }]}>
        Issues ({violations.length})
      </Text>
      {violations.length === 0 ? (
        <Text style={[styles.empty, { color: theme.colors.textMuted }]}>
          No issues match your filters.
        </Text>
      ) : (
        groups.map((group) => {
          const expanded = expandedRules.has(group.ruleId) || currentGroup?.ruleId === group.ruleId;
          const sevColor =
            group.severity === "error"
              ? theme.colors.danger
              : group.severity === "warning"
              ? theme.colors.warning
              : theme.colors.info;
          const stats = _groupStats(group, decisions);
          return (
            <View key={group.ruleId} style={{ marginBottom: 4 }}>
              <Pressable accessibilityRole="button"
                onPress={() =>
                  setExpandedRules((prev) => {
                    const next = new Set(prev);
                    if (next.has(group.ruleId)) {
                      next.delete(group.ruleId);
                    } else {
                      next.add(group.ruleId);
                    }
                    return next;
                  })
                }
                accessibilityLabel={`${group.title}: ${group.members.length} issues. ${
                  expanded ? "collapse" : "expand"
                }`}
                accessibilityState={{ expanded }}
                style={[
                  styles.groupHeader,
                  { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
                ]}
              >
                <View style={[styles.sevBar, { backgroundColor: sevColor }]} />
                <View style={{ flex: 1 }}>
                  <Text
                    style={[styles.groupTitle, { color: theme.colors.text }]}
                    numberOfLines={1}
                  >
                    {group.title}
                  </Text>
                  <Text style={[styles.groupMeta, { color: theme.colors.textMuted }]}>
                    {group.members.length} issue{group.members.length === 1 ? "" : "s"}
                    {stats.approved ? ` · ${stats.approved} ✓` : ""}
                    {stats.rejected ? ` · ${stats.rejected} ✗` : ""}
                  </Text>
                </View>
                <Text style={[styles.chev, { color: theme.colors.textMuted }]}>
                  {expanded ? "▾" : "▸"}
                </Text>
              </Pressable>

              {expanded
                ? group.members.map(({ violation, index }) => {
                    const decision = decisions[violation.id]?.decision ?? "pending";
                    const isActive = violation.id === currentId;
                    return (
                      <Pressable accessibilityRole="button"
                        key={violation.id}
                        onPress={() => onSelect(index)}
                        accessibilityLabel={`Open issue ${index + 1}`}
                        accessibilityState={{ selected: isActive }}
                        style={[
                          styles.row,
                          {
                            borderColor: isActive ? theme.colors.accent : "transparent",
                            backgroundColor: isActive
                              ? theme.colors.accent + "15"
                              : "transparent",
                          },
                        ]}
                      >
                        <View
                          style={[
                            styles.indent,
                            { backgroundColor: sevColor + "55" },
                          ]}
                        />
                        <Text
                          style={[styles.rowText, { color: theme.colors.text }]}
                          numberOfLines={1}
                        >
                          #{index + 1}
                          {violation.page ? ` · p.${violation.page}` : ""}
                        </Text>
                        <View
                          style={[
                            styles.decisionBadge,
                            {
                              backgroundColor:
                                decision === "approved"
                                  ? theme.colors.success
                                  : decision === "rejected"
                                  ? theme.colors.danger
                                  : "transparent",
                              borderColor:
                                decision === "pending" ? theme.colors.border : "transparent",
                            },
                          ]}
                        >
                          <Text
                            style={[
                              styles.decisionBadgeText,
                              {
                                color:
                                  decision === "pending"
                                    ? theme.colors.textMuted
                                    : "#FFFFFF",
                              },
                            ]}
                          >
                            {decision === "approved" ? "✓" : decision === "rejected" ? "✗" : "○"}
                          </Text>
                        </View>
                      </Pressable>
                    );
                  })
                : null}
            </View>
          );
        })
      )}
    </ScrollView>
  );
}

function _groupStats(
  group: Group,
  decisions: Record<string, { decision: IssueDecision }>,
): { approved: number; rejected: number; pending: number } {
  let approved = 0;
  let rejected = 0;
  let pending = 0;
  for (const m of group.members) {
    const d = decisions[m.violation.id]?.decision ?? "pending";
    if (d === "approved") approved += 1;
    else if (d === "rejected") rejected += 1;
    else pending += 1;
  }
  return { approved, rejected, pending };
}

const styles = StyleSheet.create({
  outer: {
    borderWidth: 1,
    borderRadius: 12,
    maxHeight: 480,
  },
  content: { padding: 10, gap: 4 },
  title: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    marginBottom: 8,
  },
  empty: { fontSize: 13 },
  groupHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 8,
  },
  sevBar: { width: 4, alignSelf: "stretch", borderRadius: 2 },
  groupTitle: { fontSize: 13, fontWeight: "700" },
  groupMeta: { fontSize: 11, marginTop: 1 },
  chev: { fontSize: 13, marginLeft: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 6,
    paddingVertical: 5,
    paddingHorizontal: 6,
    marginTop: 2,
    marginLeft: 14,
  },
  indent: { width: 3, alignSelf: "stretch", borderRadius: 2 },
  rowText: { flex: 1, fontSize: 12 },
  decisionBadge: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  decisionBadgeText: { fontSize: 10, fontWeight: "800" },
});
