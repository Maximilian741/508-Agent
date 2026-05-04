/**
 * PixelProgress - segmented chunky progress bar.
 *
 * A row of N square cells (default 16). Cells fill left-to-right based on
 * `value` 0..1. Filled cells use the accent color, unfilled use a faint
 * grid color. Two-pixel gap between cells creates the "Pac-Man dot" feel.
 */
import React from "react";
import { View } from "react-native";

import { useTheme } from "../useTheme";

export interface PixelProgressProps {
  value: number; // 0..1
  cells?: number;
  cellSize?: number; // px
  color?: string;
  dimColor?: string;
  gap?: number;
}

export function PixelProgress({
  value,
  cells = 16,
  cellSize = 10,
  color,
  dimColor,
  gap = 3,
}: PixelProgressProps) {
  const theme = useTheme();
  const c = color ?? theme.colors.accent;
  const d = dimColor ?? theme.colors.border;
  const clamped = Math.max(0, Math.min(1, value));
  const filled = Math.round(clamped * cells);

  return (
    <View style={{ flexDirection: "row", gap }}>
      {Array.from({ length: cells }).map((_, i) => (
        <View
          key={i}
          style={{
            width: cellSize,
            height: cellSize,
            backgroundColor: i < filled ? c : d,
          }}
        />
      ))}
    </View>
  );
}

export default PixelProgress;
