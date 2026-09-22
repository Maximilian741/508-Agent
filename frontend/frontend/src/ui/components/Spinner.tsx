/**
 * Spinner — a clean circular loading indicator in the theme accent.
 * (Replaces the old pixel "chasing squares" spinner.)
 */
import React from "react";
import { ActivityIndicator } from "react-native";

import { useTheme } from "../useTheme";

export interface SpinnerProps {
  /** Diameter in px (web honours any number; native maps to small/large). */
  size?: number;
  color?: string;
  /** Announced to screen readers; omit when a visible label says the same. */
  accessibilityLabel?: string;
}

export function Spinner({ size = 20, color, accessibilityLabel }: SpinnerProps) {
  const theme = useTheme();
  return (
    <ActivityIndicator
      size={size as any}
      color={color ?? theme.colors.accent}
      accessibilityLabel={accessibilityLabel}
    />
  );
}

export default Spinner;
