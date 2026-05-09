/**
 * SketchSeverityDot - tiny 8px round dot that shows severity at a glance.
 *
 * Five tones map to the highlighter palette so dots feel of-a-piece with
 * the rest of the sketch system. The dot has a 1px ink border so it stays
 * legible on the cream paper background.
 */
import { StyleSheet, View, ViewStyle, StyleProp } from "react-native";

import { sketchPalette } from "./fonts";

export type Severity = "critical" | "high" | "medium" | "low" | "info";

interface SketchSeverityDotProps {
  severity: Severity;
  size?: number;
  style?: StyleProp<ViewStyle>;
}

function colorFor(severity: Severity): string {
  switch (severity) {
    case "critical":
      return sketchPalette.hlCoral;
    case "high":
      return sketchPalette.hlPink;
    case "medium":
      return sketchPalette.hlYellow;
    case "low":
      return sketchPalette.hlGreen;
    case "info":
    default:
      return sketchPalette.hlBlue;
  }
}

export function SketchSeverityDot({
  severity,
  size = 8,
  style,
}: SketchSeverityDotProps) {
  return (
    <View
      style={[
        styles.dot,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: colorFor(severity),
        },
        style,
      ]}
      accessibilityLabel={severity + " severity"}
    />
  );
}

const styles = StyleSheet.create({
  dot: {
    borderWidth: 1,
    borderColor: sketchPalette.ink,
  },
});
