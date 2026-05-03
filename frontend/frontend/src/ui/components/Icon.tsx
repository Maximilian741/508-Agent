/**
 * Tiny hand-rolled SVG icon set.
 *
 * No external dep, no font file, no MaterialIcons. We pull paths from a
 * handful of the most-used icons in the app. Each icon is centered in a
 * 24x24 viewBox so callers can scale uniformly.
 *
 * Web only renders inline <svg>. Native renders a Text fallback so layout
 * doesn't break (no glyphs).
 */

import React from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export type IconName =
  | "check"
  | "x"
  | "info"
  | "warning"
  | "error"
  | "search"
  | "settings"
  | "copy"
  | "download"
  | "undo"
  | "keyboard"
  | "eye"
  | "upload"
  | "play"
  | "history"
  | "sparkle"
  | "filter"
  | "chart"
  | "help"
  | "shield"
  | "arrow-right";

const PATHS: Record<IconName, string> = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12 M18 6L6 18",
  info: "M12 9v6 M12 7.5h.01 M12 3a9 9 0 100 18 9 9 0 000-18z",
  warning: "M12 3 L22 21 L2 21 Z M12 10v5 M12 17.5h.01",
  error: "M12 3a9 9 0 100 18 9 9 0 000-18z M9 9l6 6 M15 9l-6 6",
  search: "M11 3a8 8 0 105.293 14.293L21 21 M11 3a8 8 0 010 16",
  settings:
    "M12 9.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z M19 12.4a7 7 0 00-.06-1.16l2-1.55-1.91-3.31-2.36.95a7 7 0 00-2-1.16l-.36-2.5h-3.82l-.36 2.5a7 7 0 00-2 1.16l-2.36-.95L3.86 9.69l2 1.55a7 7 0 000 2.32l-2 1.55 1.91 3.31 2.36-.95a7 7 0 002 1.16l.36 2.5h3.82l.36-2.5a7 7 0 002-1.16l2.36.95 1.91-3.31-2-1.55c.04-.38.06-.77.06-1.16z",
  copy:
    "M9 9h11v11H9z M5 5h11v3 M5 5v11h3",
  download: "M12 4v12 M7 11l5 5 5-5 M5 21h14",
  undo: "M9 14l-4-4 4-4 M5 10h10a4 4 0 010 8h-3",
  keyboard:
    "M3 7h18v10H3z M7 10h.01 M11 10h.01 M15 10h.01 M19 10h.01 M7 13h.01 M11 13h.01 M15 13h.01 M19 13h.01 M8 16h8",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 9a3 3 0 100 6 3 3 0 000-6z",
  upload: "M12 16V4 M7 9l5-5 5 5 M5 21h14",
  play: "M7 5l11 7-11 7V5z",
  history:
    "M12 7v5l3 2 M3.5 12a8.5 8.5 0 1014.7-5.85L21 4 M21 4v5h-5",
  sparkle: "M12 3l1.8 5.4L19 10l-5.2 1.6L12 17l-1.8-5.4L5 10l5.2-1.6z",
  filter: "M3 5h18 M6 12h12 M10 19h4",
  chart: "M4 20V8 M10 20V4 M16 20v-8 M22 20H2",
  help: "M12 3a9 9 0 100 18 9 9 0 000-18z M10 9.5a2 2 0 113.5 1.4L12 12.5V14 M12 17h.01",
  shield: "M12 3l8 3v5c0 4.5-3.5 8.5-8 10-4.5-1.5-8-5.5-8-10V6l8-3z M9 12l2 2 4-4",
  "arrow-right": "M5 12h14 M13 6l6 6-6 6",
};

export interface IconProps {
  name: IconName;
  size?: number;
  color?: string;
  /** Add 1px under-glyph stroke shadow for legibility on busy backgrounds. */
  glow?: boolean;
  /** Decorative icons should be aria-hidden. Default: true. Set false for icon-only buttons and provide an accessibilityLabel on the parent. */
  decorative?: boolean;
}

export function Icon({ name, size = 18, color, glow = false, decorative = true }: IconProps) {
  const theme = useTheme();
  const fillColor = color ?? theme.colors.text;

  if (Platform.OS !== "web") {
    return (
      <Text
        accessibilityElementsHidden={decorative}
        importantForAccessibility={decorative ? "no-hide-descendants" : "auto"}
        style={{ fontSize: size, color: fillColor, fontWeight: "700" }}
      >
        {EMOJI_FALLBACK[name] ?? "•"}
      </Text>
    );
  }

  // Web SVG path — RN-Web allows raw <svg>.
  return (
    <View style={[styles.box, { width: size, height: size }]}>
      {/* @ts-ignore — JSX intrinsic for web */}
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        aria-hidden={decorative ? "true" : undefined}
        focusable="false"
        style={{ display: "block", overflow: "visible" }}
      >
        {/* @ts-ignore */}
        <path
          d={PATHS[name]}
          fill="none"
          stroke={fillColor}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={glow ? { filter: "drop-shadow(0 1px 0 rgba(0,0,0,0.15))" } : undefined}
        />
      </svg>
    </View>
  );
}

const EMOJI_FALLBACK: Record<IconName, string> = {
  check: "✓",
  x: "✗",
  info: "i",
  warning: "!",
  error: "!",
  search: "🔍",
  settings: "⚙",
  copy: "⎘",
  download: "↓",
  undo: "↶",
  keyboard: "⌨",
  eye: "👁",
  upload: "↑",
  play: "▶",
  history: "↻",
  sparkle: "✨",
  filter: "≣",
  chart: "📊",
  help: "?",
  shield: "🛡",
  "arrow-right": "→",
};

const styles = StyleSheet.create({
  box: { alignItems: "center", justifyContent: "center" },
});
