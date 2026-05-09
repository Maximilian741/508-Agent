/**
 * PixelSpinner - 8-bit chasing-square loading indicator.
 *
 * Four colored pixel squares orbit a center every 800ms. Looks like a
 * mid-90s arcade "LOADING" indicator. Replaces ActivityIndicator wherever
 * the retro feel matters more than instant native parity.
 */
import React, { useEffect, useState } from "react";
import { Platform, View } from "react-native";

import { useTheme } from "../useTheme";

export interface PixelSpinnerProps {
  size?: number; // pixel size of each block
  color?: string;
  speedMs?: number;
}

export function PixelSpinner({ size = 4, color, speedMs = 700 }: PixelSpinnerProps) {
  const theme = useTheme();
  const c = color ?? theme.colors.accent;
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    if (Platform.OS !== "web" && Platform.OS !== "ios" && Platform.OS !== "android") return;
    const t = setInterval(() => setPhase((p) => (p + 1) % 4), Math.max(100, speedMs / 4));
    return () => clearInterval(t);
  }, [speedMs]);

  // Positions for the 4 corner-orbiting squares around a 5x5 grid.
  // phase 0: TL, phase 1: TR, phase 2: BR, phase 3: BL all rotate one step.
  const positions = [
    [0, 0],
    [4, 0],
    [4, 4],
    [0, 4],
  ];
  const lit = positions.map((_, i) => (i + phase) % 4);

  const total = 5 * size;

  return (
    <View style={{ width: total, height: total }}>
      {positions.map(([x, y], i) => {
        const opacity = 0.25 + (lit[i] / 3) * 0.75;
        return (
          <View
            key={i}
            style={{
              position: "absolute",
              left: x * size,
              top: y * size,
              width: size,
              height: size,
              backgroundColor: c,
              opacity,
            }}
          />
        );
      })}
    </View>
  );
}

export default PixelSpinner;
