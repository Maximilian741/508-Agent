/**
 * ProgressBar — a slim rounded track with an accent fill.
 * (Replaces the old segmented pixel progress bar.)
 */
import React from "react";
import { Platform, View } from "react-native";

import { useTheme } from "../useTheme";

export interface ProgressBarProps {
  /** 0..1 */
  value: number;
  height?: number;
  color?: string;
  /** Accessible name; when set the bar is exposed as a progressbar. */
  accessibilityLabel?: string;
}

export function ProgressBar({ value, height = 8, color, accessibilityLabel }: ProgressBarProps) {
  const theme = useTheme();
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const a11y = accessibilityLabel
    ? ({
        accessibilityRole: "progressbar",
        accessibilityLabel,
        accessibilityValue: { min: 0, max: 100, now: pct },
      } as any)
    : ({ accessibilityElementsHidden: true, importantForAccessibility: "no-hide-descendants" } as any);
  return (
    <View
      {...a11y}
      style={{
        height,
        borderRadius: height / 2,
        backgroundColor: theme.colors.surface3,
        overflow: "hidden",
        width: "100%",
      }}
    >
      <View
        style={[
          { width: `${pct}%`, height: "100%", borderRadius: height / 2, backgroundColor: color ?? theme.colors.accent },
          Platform.OS === "web" ? ({ transition: "width 400ms ease" } as any) : null,
        ]}
      />
    </View>
  );
}

export default ProgressBar;
