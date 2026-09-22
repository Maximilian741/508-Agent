/**
 * Icon — the app's one icon component: clean 24px line icons (Feather, via
 * @expo/vector-icons) on web and native alike.
 *
 * Pass any Feather glyph name, or one of the semantic aliases below (so call
 * sites say "doc" or "credits" and the glyph can change in one place).
 *
 * Icons are decorative by default: hidden from assistive technology, because
 * the text next to them already carries the meaning. For an icon that stands
 * alone, pass `decorative={false}` plus an `accessibilityLabel`.
 */
import React from "react";
import { Platform, StyleProp, View, ViewStyle } from "react-native";
import Feather from "@expo/vector-icons/Feather";
import * as Font from "expo-font";

import { useTheme } from "../useTheme";

export type FeatherName = keyof typeof Feather.glyphMap;

/** Semantic names -> Feather glyphs. Includes the old pixel-glyph names. */
const ALIASES = {
  // status
  warning: "alert-triangle",
  error: "x-circle",
  success: "check-circle",
  // things
  doc: "file-text",
  document: "file-text",
  credits: "credit-card",
  coin: "credit-card",
  bolt: "zap",
  spark: "zap",
  sparkle: "star",
  gear: "settings",
  trophy: "award",
  history: "clock",
  undo: "rotate-ccw",
  keyboard: "command",
  chart: "bar-chart-2",
  help: "help-circle",
  "arrow-right": "arrow-right",
} as const satisfies Record<string, FeatherName>;

export type IconName = FeatherName | keyof typeof ALIASES;

export function resolveIcon(name: IconName): FeatherName {
  return (ALIASES as Record<string, FeatherName>)[name] ?? (name as FeatherName);
}

export interface IconProps {
  name: IconName;
  size?: number;
  color?: string;
  /** Default true: hidden from screen readers (the adjacent text is the label). */
  decorative?: boolean;
  /** Required when decorative={false}. */
  accessibilityLabel?: string;
  style?: StyleProp<ViewStyle>;
  /** Accepted for back-compat; ignored. */
  glow?: boolean;
}

export function Icon({ name, size = 18, color, decorative = true, accessibilityLabel, style }: IconProps) {
  const theme = useTheme();
  const glyph = resolveIcon(name);
  const a11y = decorative
    ? ({
        accessibilityElementsHidden: true,
        importantForAccessibility: "no-hide-descendants",
        ...(Platform.OS === "web" ? { "aria-hidden": true } : {}),
      } as any)
    : ({ accessible: true, accessibilityRole: "image", accessibilityLabel } as any);
  return (
    <View {...a11y} style={[{ width: size, height: size, alignItems: "center", justifyContent: "center" }, style]}>
      <Feather name={glyph} size={size} color={color ?? theme.colors.text} />
    </View>
  );
}

/**
 * Static web export: register the icon font during the server render so the
 * exported HTML carries its @font-face + <link rel="preload">, and the glyphs
 * are in the page before hydration (vector-icons renders nothing until it
 * believes the font is loaded). No-op in the browser and on native, where the
 * icon component loads the font itself.
 */
export function registerIconFontForStaticRender(): void {
  if (Platform.OS !== "web" || typeof window !== "undefined") return;
  try {
    void Font.loadAsync(Feather.font);
  } catch {
    /* the client-side load in the icon component still covers it */
  }
}

export default Icon;
