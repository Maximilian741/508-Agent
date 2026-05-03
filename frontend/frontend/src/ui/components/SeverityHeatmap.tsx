import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export interface SeverityHeatmapProps {
  errors: number;
  warnings: number;
  infos: number;
}

/**
 * SeverityHeatmap — three colored bars whose widths scale with the count of
 * each severity bucket.  Designed to be small and "glanceable" so users can
 * see at a glance whether a document is in good shape.
 */
export function SeverityHeatmap({ errors, warnings, infos }: SeverityHeatmapProps) {
  const theme = useTheme();
  const total = Math.max(errors + warnings + infos, 1);
  const seg = (count: number, color: string) => (
    <View
      key={color}
      style={[
        styles.seg,
        {
          flex: count / total || 0.0001,
          backgroundColor: color,
          opacity: count === 0 ? 0.15 : 1,
        },
      ]}
    >
      {count > 0 ? <Text style={styles.count}>{count}</Text> : null}
    </View>
  );

  return (
    <View style={styles.wrap}>
      <View style={[styles.bar, { backgroundColor: theme.colors.surface2 }]}>
        {seg(errors, theme.colors.danger)}
        {seg(warnings, theme.colors.warning)}
        {seg(infos, theme.colors.info)}
      </View>
      <View style={styles.legend}>
        <_LegendDot color={theme.colors.danger} label={`Errors ${errors}`} />
        <_LegendDot color={theme.colors.warning} label={`Warnings ${warnings}`} />
        <_LegendDot color={theme.colors.info} label={`Info ${infos}`} />
      </View>
    </View>
  );
}

function _LegendDot({ color, label }: { color: string; label: string }) {
  const theme = useTheme();
  return (
    <View style={styles.legendItem}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Text style={[styles.legendLabel, { color: theme.colors.textMuted }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  bar: { flexDirection: "row", height: 12, borderRadius: 6, overflow: "hidden" },
  seg: { alignItems: "center", justifyContent: "center" },
  count: { color: "#FFFFFF", fontSize: 9, fontWeight: "800", letterSpacing: 0.4 },
  legend: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  legendLabel: { fontSize: 11, fontWeight: "600" },
});
