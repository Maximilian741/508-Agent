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

export const lightColors = {
  bg: "#F6F7FB",
  surface: "#FFFFFF",
  surface2: "#F1F3F8",
  text: "#0F172A",
  textMuted: "#5B6475",
  border: "#E2E8F0",
  shadow: "rgba(15, 23, 42, 0.12)",
  accent: "#2D5BFF",
  success: "#16A34A",
  warning: "#F59E0B",
  danger: "#DC2626",
  info: "#0EA5E9",
} as const;

export const darkColors = {
  bg: "#0B0F1A",
  surface: "#121826",
  surface2: "#1A2233",
  text: "#F8FAFC",
  textMuted: "#A0AEC0",
  border: "#243048",
  shadow: "rgba(2, 6, 23, 0.6)",
  accent: "#5B8CFF",
  success: "#22C55E",
  warning: "#FBBF24",
  danger: "#F87171",
  info: "#38BDF8",
} as const;

export type ThemeColors = typeof lightColors;

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  radius: typeof radius;
  typography: typeof typography;
  shadows: typeof shadows;
};
