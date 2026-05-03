import { Platform } from "react-native";

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
} as const;

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
} as const;

export const typography = {
  title: {
    fontSize: 28,
    fontWeight: "700" as const,
    letterSpacing: -0.2,
  },
  h1: {
    fontSize: 22,
    fontWeight: "700" as const,
  },
  h2: {
    fontSize: 18,
    fontWeight: "600" as const,
  },
  body: {
    fontSize: 15,
    fontWeight: "400" as const,
  },
  caption: {
    fontSize: 12,
    fontWeight: "500" as const,
    textTransform: "uppercase" as const,
    letterSpacing: 0.6,
  },
  mono: {
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
    fontSize: 12,
  },
} as const;

const baseShadow = {
  shadowColor: "#000",
  shadowOffset: { width: 0, height: 8 },
  shadowOpacity: 0.08,
  shadowRadius: 18,
  elevation: 3,
};

export const shadows = {
  subtle: baseShadow,
} as const;

export type ThemeColors = {
  bg: string;
  surface: string;
  surface2: string;
  text: string;
  textMuted: string;
  border: string;
  shadow: string;
  accent: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
};

/**
 * Light palette — designed for long reading sessions on document audits.
 *
 * Backgrounds use Slate-25/Slate-50 instead of pure white to reduce eye
 * strain.  Surfaces have a hairline gray-100 border so cards feel like
 * defined objects rather than floating pieces.  Accent is the same
 * Indigo-blue we use in the brand mark — accessible against both white
 * surfaces and the gradient hero.
 */
export const lightColors: ThemeColors = {
  bg: "#F8FAFC",          // Slate-50
  surface: "#FFFFFF",
  surface2: "#F1F5F9",    // Slate-100
  text: "#0F172A",        // Slate-900
  textMuted: "#475569",   // Slate-600 — better contrast than the old #5B6475
  border: "#E2E8F0",      // Slate-200
  shadow: "rgba(15, 23, 42, 0.10)",
  accent: "#2D5BFF",
  success: "#15803D",     // Green-700 — meets AA on white
  warning: "#B45309",     // Amber-700 — meets AA on white
  danger: "#B91C1C",      // Red-700 — meets AA on white
  info: "#0369A1",        // Sky-700 — meets AA on white
} as const;

/**
 * Dark palette — true-black-friendly while preserving cards/accents.
 *
 * bg uses #07090F instead of pure #000 so white text doesn't fringe; surface
 * raises just enough to be discernible.  Accent is brightened slightly so it
 * still feels like the "primary action" colour against deep navy.
 */
export const darkColors: ThemeColors = {
  bg: "#07090F",
  surface: "#11151F",
  surface2: "#1A2031",
  text: "#F8FAFC",
  textMuted: "#94A3B8",   // Slate-400
  border: "#1F2A3D",
  shadow: "rgba(0, 0, 0, 0.65)",
  accent: "#6488FF",
  success: "#34D399",
  warning: "#FBBF24",
  danger: "#F87171",
  info: "#38BDF8",
} as const;

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  radius: typeof radius;
  typography: typeof typography;
  shadows: typeof shadows;
};
