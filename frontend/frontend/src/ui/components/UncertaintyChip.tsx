import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export interface UncertaintyChipProps {
  /** A short label such as "Heuristic", "Beta", "Low confidence". */
  label?: string;
  /** Optional confidence in [0, 1] — rendered as a percentage in the tooltip. */
  confidence?: number;
  /** Tooltip / hover text explaining why the chip is shown. */
  hint?: string;
}

/**
 * UncertaintyChip — a yellow info pill that calls attention to features whose
 * output is heuristic, AI-generated, or otherwise uncertain.  Use it next to
 * any value (alt text, language, title suggestion) the agent produced
 * non-deterministically so the user knows to double-check.
 */
export function UncertaintyChip({ label = "Heuristic", confidence, hint }: UncertaintyChipProps) {
  const theme = useTheme();
  const formattedConfidence = typeof confidence === "number" ? `${Math.round(confidence * 100)}%` : null;
  const tooltip = [hint, formattedConfidence ? `Confidence ${formattedConfidence}` : null]
    .filter(Boolean)
    .join(" — ");
  return (
    <View
      // @ts-ignore — RN-Web honors `title`
      title={tooltip}
      accessibilityLabel={tooltip || label}
      style={[
        styles.wrap,
        {
          borderColor: theme.colors.warning,
          backgroundColor: theme.colors.warning + "22",
        },
      ]}
    >
      <View style={[styles.dot, { backgroundColor: theme.colors.warning }]} />
      <Text style={[styles.label, { color: theme.colors.warning }]}>
        {label}
        {formattedConfidence ? ` · ${formattedConfidence}` : ""}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: "flex-start",
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  label: { fontSize: 11, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
});
