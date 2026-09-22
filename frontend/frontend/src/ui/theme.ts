/**
 * Theme tokens — spacing, radius, type, shadows. Colours live in ./palettes
 * (pure data, so scripts/check-contrast.mjs can measure every pair).
 *
 * The look: a clean, dark-first web app. Deep near-black ink with a faint cool
 * bloom, one confident mint-aqua accent, a single modern sans (Inter, bundled
 * same-origin in public/fonts, with a system fallback), soft LAYERED shadows,
 * and rounded translucent "glass" surfaces. The only pixel art left anywhere
 * is the 508 logo mark (PixelLogo).
 */
import { Platform } from "react-native";

import { auroraColors, type ThemeColors } from "./palettes";

export type { ThemeColors } from "./palettes";
export { auroraColors, darkColors, lightColors } from "./palettes";
/** Back-compat alias: the "system" palette used to be called twilight. */
export const twilightColors = auroraColors;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
  huge: 48,
} as const;

// Rounded, consistent corners. `none` stays for genuinely square things
// (hairline rules, progress tracks); pill for dots, avatars and toggles.
export const radius = {
  none: 0,
  xs: 6,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 28,
  pill: 999,
} as const;

export const border = {
  thin: 1,
  medium: 2,
} as const;

// Single tokenised focus ring (WCAG-critical — keep 2px / 2px offset).
export const focus = {
  outlineWidth: 2,
  outlineOffset: 2,
} as const;

/**
 * Inter first (served from /fonts, see app/+html.tsx), then the platform UI
 * font. The fallback matters: until the woff2 arrives, and on any browser that
 * blocks web fonts, text renders in Segoe UI / SF / Roboto — never a serif.
 */
export const fontStacks = {
  sans: Platform.select({
    web: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    default: undefined,
  }) as string | undefined,
  mono: Platform.select({
    web: "ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace",
    ios: "Menlo",
    default: "monospace",
  }) as string,
} as const;

const sans = fontStacks.sans ? { fontFamily: fontStacks.sans } : {};

export const typography = {
  /** Page-level display headline (hero titles). */
  display: {
    ...sans,
    fontSize: 48,
    fontWeight: "700" as const,
    letterSpacing: -1.4,
    lineHeight: 54,
  },
  displaySmall: {
    ...sans,
    fontSize: 32,
    fontWeight: "700" as const,
    letterSpacing: -0.8,
    lineHeight: 38,
  },
  title: {
    ...sans,
    fontSize: 30,
    fontWeight: "700" as const,
    letterSpacing: -0.6,
  },
  h1: {
    ...sans,
    fontSize: 24,
    fontWeight: "700" as const,
    letterSpacing: -0.4,
    lineHeight: 30,
  },
  h2: {
    ...sans,
    fontSize: 18,
    fontWeight: "600" as const,
    letterSpacing: -0.2,
    lineHeight: 24,
  },
  body: {
    ...sans,
    fontSize: 15,
    fontWeight: "400" as const,
    lineHeight: 22,
  },
  caption: {
    ...sans,
    fontSize: 12,
    fontWeight: "600" as const,
    textTransform: "uppercase" as const,
    letterSpacing: 0.8,
  },
  mono: {
    fontFamily: fontStacks.mono,
    fontSize: 12,
  },
  // Historical key names (they were once an arcade pixel font). Kept so the
  // existing call sites compile; both are clean sans now.
  /** Small tracked uppercase label (eyebrows, tags). */
  pixel: {
    ...sans,
    fontSize: 12,
    fontWeight: "600" as const,
    letterSpacing: 1.2,
    textTransform: "uppercase" as const,
  },
  /** Big numeric readout (scores, prices, credit balances). */
  pixelLarge: {
    ...sans,
    fontSize: 30,
    fontWeight: "700" as const,
    letterSpacing: -0.8,
    fontVariant: ["tabular-nums"] as ("tabular-nums")[],
  },
} as const;

/**
 * Soft, layered shadows: a tight contact shadow plus a wide, low-opacity
 * ambient one, and (dark only) a faint lit top edge. Each tier carries native
 * RN props plus light/dark web boxShadow strings.
 */
export const shadows = {
  flat: {
    rn: {},
    web: "none",
    webDark: "none",
  },
  near: {
    rn: { shadowColor: "#020617", shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.14, shadowRadius: 12, elevation: 3 },
    web: "0 1px 2px rgba(15, 23, 42, 0.06), 0 6px 18px -6px rgba(15, 23, 42, 0.12)",
    webDark: "inset 0 1px 0 rgba(255, 255, 255, 0.04), 0 1px 2px rgba(0, 0, 0, 0.45), 0 10px 30px -10px rgba(0, 0, 0, 0.65)",
  },
  far: {
    rn: { shadowColor: "#020617", shadowOffset: { width: 0, height: 18 }, shadowOpacity: 0.28, shadowRadius: 36, elevation: 12 },
    web: "0 2px 6px rgba(15, 23, 42, 0.06), 0 28px 60px -18px rgba(15, 23, 42, 0.28)",
    webDark: "inset 0 1px 0 rgba(255, 255, 255, 0.06), 0 2px 8px rgba(0, 0, 0, 0.5), 0 36px 80px -20px rgba(0, 0, 0, 0.8)",
  },
} as const;

export type Theme = {
  colors: ThemeColors;
  /** True for the dark palettes (aurora, graphite). Use this, never a hex compare. */
  isDark: boolean;
  spacing: typeof spacing;
  radius: typeof radius;
  border: typeof border;
  focus: typeof focus;
  typography: typeof typography;
  shadows: typeof shadows;
};

/**
 * Web-only glass surface: translucent colour + backdrop blur. The colour is
 * opaque enough (>= 74%) that a browser without backdrop-filter still shows a
 * solid-looking panel, and the contrast script measures text on the colour
 * composited over the page, not on the blur.
 */
export function glassStyle(colors: ThemeColors, blur = 16): Record<string, unknown> {
  if (Platform.OS !== "web") return { backgroundColor: colors.surface };
  if (blur <= 0) return { backgroundColor: colors.glass };
  return {
    backgroundColor: colors.glass,
    backdropFilter: `blur(${blur}px) saturate(140%)`,
  };
}

/** "#5EEAD4" + alpha 0..1 -> "rgba(94, 234, 212, a)". Accepts 6-digit hex only. */
export function alpha(hex: string, a: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
