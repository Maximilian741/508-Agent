/**
 * Brand — logo mark + wordmark for 508 Agent.
 *
 * BrandMark is a small inline-SVG glyph: a rounded shield that hugs a stylised
 * "5/8" pair, with a checkmark cut out of the bottom right.  It scales
 * crisply (vector) and respects the active theme accent.
 *
 * BrandWordmark renders the mark next to the "508 Agent" text, sized for nav
 * bars and footers.
 *
 * favicon() returns a self-contained SVG string suitable for use as the page
 * favicon — useful from public/index.html if/when we choose to wire it in.
 */

import React from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

interface BrandMarkProps {
  size?: number;
  /** Override the accent — defaults to current theme.colors.accent. */
  color?: string;
  /** Override the secondary accent for the gradient. */
  colorSecondary?: string;
}

/**
 * Inline-SVG logo mark.  Uses RN-Web's SVG support via `dangerouslySetInnerHTML`
 * on web; on native we fall back to a rounded square with the "508" text so we
 * avoid pulling react-native-svg.
 */
export function BrandMark({ size = 28, color, colorSecondary }: BrandMarkProps) {
  const theme = useTheme();
  const fill = color ?? theme.colors.accent;
  const fill2 = colorSecondary ?? theme.colors.accentSecondary;
  const id = `bm-${size}`;

  if (Platform.OS === "web") {
    // Static (non-hover) gradient now uses a 35° tilt rather than the
    // previous flat 45° (x1=0,y1=0 -> x2=1,y2=1).  35° from horizontal:
    // dx = cos(35°) ≈ 0.819, dy = sin(35°) ≈ 0.574 — gives the mark a
    // slightly more dynamic, less "diagonal-square" feel.
    const svg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="${size}" height="${size}" aria-label="508 Agent logo">
  <defs>
    <linearGradient id="${id}" x1="0" y1="0" x2="0.819" y2="0.574">
      <stop offset="0%" stop-color="${fill}"/>
      <stop offset="100%" stop-color="${fill2}"/>
    </linearGradient>
  </defs>
  <path d="M16 1.5 L28.5 6 V16 C28.5 23.5 23 28.5 16 30.5 C9 28.5 3.5 23.5 3.5 16 V6 Z"
        fill="url(#${id})"/>
  <path d="M11 10 L11 13 L13.5 13 C15.5 13 16.5 14 16.5 15.5 C16.5 17 15.5 18 13.7 18 C12.6 18 11.7 17.6 11 16.8"
        stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" fill="none"/>
  <path d="M18.5 12.2 C19.8 11 21.6 11 22.7 12.1 C23.8 13.2 23.8 14.9 22.7 16 L20.5 18 L22.8 20.2"
        stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <path d="M11 22 L13.5 24.5 L20 18.5"
        stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.92"/>
</svg>`.trim();
    return (
      <View
        accessibilityLabel="508 Agent logo"
        style={{ width: size, height: size }}
        // @ts-ignore — RN-Web accepts dangerouslySetInnerHTML on View
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }

  // Native fallback — solid pill with the brand short-form.
  const radius = size * 0.28;
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        backgroundColor: fill,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Text style={{ color: "#FFFFFF", fontSize: size * 0.42, fontWeight: "800", letterSpacing: 0.4 }}>
        508
      </Text>
    </View>
  );
}

interface BrandWordmarkProps {
  /** Logo mark size; the wordmark text scales relative to it. */
  size?: number;
  color?: string;
}

export function BrandWordmark({ size = 28, color }: BrandWordmarkProps) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <BrandMark size={size} />
      <Text style={[styles.text, { color: color ?? theme.colors.text, fontSize: Math.round(size * 0.58) }]}>
        508 Agent
      </Text>
    </View>
  );
}

/**
 * Compact SVG suitable for use as a favicon.  Returns a self-contained string
 * (no external defs) — drop into public/index.html as a data URI:
 *   <link rel="icon" href="data:image/svg+xml;utf8,<encoded>" />
 */
export function favicon(accent = "#2D5BFF", accentSecondary = "#FF7A59"): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="${accent}"/><stop offset="100%" stop-color="${accentSecondary}"/></linearGradient></defs><path d="M16 1.5 L28.5 6 V16 C28.5 23.5 23 28.5 16 30.5 C9 28.5 3.5 23.5 3.5 16 V6 Z" fill="url(#g)"/><path d="M11 22 L13.5 24.5 L20 18.5" stroke="#FFFFFF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>`;
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  text: { fontWeight: "800", letterSpacing: -0.2 },
});
