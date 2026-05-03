import React, { useEffect, useRef, useState } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export interface ScoreBadgeProps {
  score: number;
  grade: string;
  /** Initial value rendered before the count-up animation starts. */
  fromScore?: number;
  /** Optional sub-label (e.g. "after fixes"). */
  subLabel?: string;
}

/**
 * ScoreBadge — gamified accessibility score readout with an animated count-up
 * and color that tracks the grade.  Used on the dashboard and at the top of
 * every fix-report view to give users a quick "how am I doing" hit.
 */
export function ScoreBadge({ score, grade, fromScore = 0, subLabel }: ScoreBadgeProps) {
  const theme = useTheme();
  const animated = useRef(new Animated.Value(fromScore)).current;
  const [display, setDisplay] = useState(fromScore);

  useEffect(() => {
    Animated.timing(animated, {
      toValue: score,
      duration: 900,
      useNativeDriver: false,
    }).start();
    const id = animated.addListener(({ value }) => setDisplay(value));
    return () => animated.removeListener(id);
  }, [animated, score]);

  const tone = _gradeTone(grade, theme);

  return (
    <View style={[styles.wrap, { backgroundColor: tone.bg, borderColor: tone.border }]}>
      <View style={styles.row}>
        <Text style={[styles.score, { color: tone.text }]}>{display.toFixed(1)}</Text>
        <View style={[styles.gradePill, { backgroundColor: tone.text }]}>
          <Text style={[styles.gradeText, { color: tone.bg }]}>{grade}</Text>
        </View>
      </View>
      <Text style={[styles.label, { color: tone.text }]}>Accessibility Score</Text>
      {subLabel ? <Text style={[styles.sub, { color: tone.text }]}>{subLabel}</Text> : null}
    </View>
  );
}

function _gradeTone(grade: string, theme: ReturnType<typeof useTheme>) {
  const c = theme.colors;
  if (grade === "A+" || grade === "A") {
    return { bg: c.success + "22", border: c.success, text: c.success };
  }
  if (grade === "B") {
    return { bg: c.info + "22", border: c.info, text: c.info };
  }
  if (grade === "C") {
    return { bg: c.warning + "22", border: c.warning, text: c.warning };
  }
  return { bg: c.danger + "22", border: c.danger, text: c.danger };
}

const styles = StyleSheet.create({
  wrap: {
    borderWidth: 1.5,
    borderRadius: 16,
    padding: 16,
    alignItems: "center",
    minWidth: 180,
  },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  score: { fontSize: 42, fontWeight: "800", letterSpacing: -1 },
  gradePill: { borderRadius: 999, paddingHorizontal: 12, paddingVertical: 4 },
  gradeText: { fontSize: 16, fontWeight: "800", letterSpacing: 0.4 },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase", marginTop: 6 },
  sub: { fontSize: 12, fontWeight: "500", marginTop: 2, opacity: 0.85 },
});
