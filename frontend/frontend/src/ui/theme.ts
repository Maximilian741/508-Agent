/**
 * Theme - warm autumnal palette designed to coexist with the smoke shaders.
 *
 * Deliberately moves away from the "pure-black + navy" look common to
 * AI-generated apps. Backgrounds carry warmth (deep umbers in dark mode,
 * cream parchment in light mode) so the maple-mist global shader reads
 * naturally and the app feels hand-crafted instead of templated.
 *
 * Accents pull from the same palette as the shaders: amber, ember orange,
 * and a deep magenta secondary for hover/CTA highlights.
 */
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
  lg: 14,
  xl: 20,
} as const;

const serifStack = Platform.select({
  ios: "Iowan Old Style, Charter, Georgia, serif",
  android: "serif",
  default: "'Iowan Old Style', 'Charter', 'Georgia', serif",
});

export const typography = {
  /**
   * display - serif headline used on hero titles and reference page titles.
   * Pairs with sans-serif body for hand-designed paper-and-ink feel.
   */
  display: {
    fontFamily: serifStack,
    fontSize: 42,
    fontWeight: "700" as const,
    letterSpacing: -0.8,
    lineHeight: 48,
  },
  displaySmall: {
    fontFamily: serifStack,
    fontSize: 30,
    fontWeight: "700" as const,
    letterSpacing: -0.5,
    lineHeight: 36,
  },
  title: {
    fontSize: 30,
    fontWeight: "800" as const,
    letterSpacing: -0.4,
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
  // Retro pixel-art look. Monospace stack with chunky letter spacing so
  // titles/numbers read like an arcade HUD or 8-bit boot screen.
  pixel: {
    fontFamily: Platform.select({
      ios: "Menlo",
      android: "monospace",
      default: "ui-monospace, 'VT323', 'Press Start 2P', 'Silkscreen', monospace",
    }),
    fontSize: 13,
    fontWeight: "700" as const,
    letterSpacing: 1.2,
  },
  pixelLarge: {
    fontFamily: Platform.select({
      ios: "Menlo",
      android: "monospace",
      default: "ui-monospace, 'VT323', 'Press Start 2P', 'Silkscreen', monospace",
    }),
    fontSize: 28,
    fontWeight: "800" as const,
    letterSpacing: 2.5,
  },
} as const;

const baseShadow = {
  shadowColor: "#1F140A",
  shadowOffset: { width: 0, height: 12 },
  shadowOpacity: 0.18,
  shadowRadius: 24,
  elevation: 4,
};

export const shadows = {
  subtle: baseShadow,
} as const;

export type ThemeColors = {
  bg: string;
  surface: string;
  surface2: string;
  surface3: string;
  text: string;
  textMuted: string;
  border: string;
  shadow: string;
  accent: string;
  accentSecondary: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
  successSoft: string;
  warningSoft: string;
  dangerSoft: string;
  infoSoft: string;
};

/**
 * Light palette - cream parchment with soft tan surfaces.
 * The maple-mist shader at low opacity gives this a paper-warm feel.
 */
export const lightColors: ThemeColors = {
  bg: "#F8F1E4",          // warm cream
  surface: "#FFFAF0",     // ivory
  surface2: "#F2E8D5",    // light tan
  surface3: "#EADBC0",    // mid tan
  text: "#2B1B0E",        // deep walnut
  textMuted: "#6B5840",   // warm taupe
  border: "#E0CFB0",      // soft sand
  shadow: "rgba(60, 30, 10, 0.16)",
  accent: "#C2410C",      // burnt orange (ember)
  accentSecondary: "#9A1842", // deep magenta
  success: "#15803D",
  warning: "#A16207",     // dark amber
  danger: "#B91C1C",
  info: "#1E5C8E",        // teal-leaning blue
  successSoft: "rgba(21, 128, 61, 0.10)",
  warningSoft: "rgba(161, 98, 7, 0.12)",
  dangerSoft: "rgba(185, 28, 28, 0.10)",
  infoSoft: "rgba(30, 92, 142, 0.10)",
} as const;

/**
 * Twilight palette - dusky midpoint between cream parchment and warm umber.
 * Used for the "Auto" / system theme so every theme option looks visibly
 * distinct: this one reads like late dusk with a violet-rose undertone,
 * mid-luminosity backgrounds, and accents that lean plum instead of ember.
 */
export const twilightColors: ThemeColors = {
  bg: "#3A2A3F",          // dusky aubergine
  surface: "#4A3550",     // raised dusk
  surface2: "#574060",    // panel
  surface3: "#664B70",    // nested card
  text: "#FBEFE0",        // cream
  textMuted: "#D4B8C8",   // dusty mauve
  border: "#7A5C84",      // amethyst border
  shadow: "rgba(20, 8, 24, 0.42)",
  accent: "#FFB36B",      // warm peach (pops against violet bg)
  accentSecondary: "#F472B6", // rose
  success: "#86EFAC",
  warning: "#FCD34D",
  danger: "#FCA5A5",
  info: "#A5B4FC",        // periwinkle
  successSoft: "rgba(134, 239, 172, 0.16)",
  warningSoft: "rgba(252, 211, 77, 0.16)",
  dangerSoft: "rgba(252, 165, 165, 0.16)",
  infoSoft: "rgba(165, 180, 252, 0.18)",
} as const;

/**
 * Dark palette - deep warm umber, NOT pure-black + navy.
 * Backgrounds carry a russet/walnut undertone so the maple-mist shader
 * reads as "warm fog drifting through a dim room" instead of "blue tint
 * on top of black."
 */
export const darkColors: ThemeColors = {
  bg: "#150E08",          // walnut-charcoal (NOT pure black)
  surface: "#221610",     // warm dark surface
  surface2: "#2D1F17",    // raised panel
  surface3: "#3A2A1F",    // pull-quote / nested card
  text: "#F5EFE3",        // cream
  textMuted: "#B8A48A",   // warm taupe
  border: "#3D2D1E",      // warm umber border (replaces navy)
  shadow: "rgba(0, 0, 0, 0.55)",
  accent: "#F59E4A",      // ember orange (warm primary)
  accentSecondary: "#E04D7A", // hot magenta secondary
  success: "#5BD394",
  warning: "#FBBF24",
  danger: "#F87171",
  info: "#7AB8E0",
  successSoft: "rgba(91, 211, 148, 0.14)",
  warningSoft: "rgba(251, 191, 36, 0.14)",
  dangerSoft: "rgba(248, 113, 113, 0.14)",
  infoSoft: "rgba(122, 184, 224, 0.14)",
} as const;

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  radius: typeof radius;
  typography: typeof typography;
  shadows: typeof shadows;
};
