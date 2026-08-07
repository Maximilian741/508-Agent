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

// Near-square radius vocabulary. Data/utility surfaces are right-angled (none);
// prose/marketing cards and dialogs get a single small 4px radius; pill is the
// named exception for genuine circles/pills (avatars, grade dots, toggle chips).
// Nothing should use a value between 5 and 998 — that uniform-soft rounding is
// the look we are deliberately leaving behind.
export const radius = {
  none: 0,
  xs: 2,
  sm: 3,
  md: 4,
  // Back-compat aliases (same near-square values) so existing call sites that
  // still reference lg/xl render square instead of failing to compile.
  lg: 4,
  xl: 4,
  pill: 999,
} as const;

// Border weights: a hairline carries structure everywhere; medium (2) is only
// for an active/selected surface and the focus ring. No 3px.
export const border = {
  thin: 1,
  medium: 2,
} as const;

// Single tokenised focus ring (WCAG-critical — keep 2px / 2px offset).
export const focus = {
  outlineWidth: 2,
  outlineOffset: 2,
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
    letterSpacing: -0.2,
    lineHeight: 48,
  },
  displaySmall: {
    fontFamily: serifStack,
    fontSize: 30,
    fontWeight: "700" as const,
    letterSpacing: -0.2,
    lineHeight: 36,
  },
  title: {
    fontSize: 30,
    fontWeight: "700" as const,
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
  // NOTE: these two keys used to be an arcade/pixel font (VT323/Press Start) —
  // a strong "look at this generated thing" tell. Re-pointed to clean type so
  // every existing consumer (wordmark labels, big numbers) reads as a typeset
  // dossier instead. Keys kept to avoid churning ~12 call sites.
  // `pixel` → a crisp tracked uppercase label (wordmark / eyebrow scale).
  pixel: {
    fontSize: 13,
    fontWeight: "700" as const,
    letterSpacing: 1.4,
    textTransform: "uppercase" as const,
  },
  // `pixelLarge` → a serif headline number (scores, prices) — editorial, not arcade.
  pixelLarge: {
    fontFamily: serifStack,
    fontSize: 30,
    fontWeight: "700" as const,
    letterSpacing: -0.2,
  },
} as const;

// One light source, straight down. No side-blur, no 0 0 Npx glow ring, no
// accent-tinted shadow — those omnidirectional halos are the AI-app tell. Each
// tier carries native RN props plus light/dark web boxShadow strings; the dark
// strings are heavier so they read on the near-black walnut background.
export const shadows = {
  // No shadow — the hairline border does the separating (used by data surfaces).
  flat: {
    rn: {},
    web: "none",
    webDark: "none",
  },
  // A close, low contact shadow for content cards / dropdowns.
  near: {
    rn: { shadowColor: "#1F140A", shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 4, elevation: 2 },
    web: "0 2px 4px rgba(31,20,10,0.10)",
    webDark: "0 2px 4px rgba(0,0,0,0.30)",
  },
  // A taller drop for modals/overlays, with a defined lit top edge (not a halo).
  far: {
    rn: { shadowColor: "#1F140A", shadowOffset: { width: 0, height: 8 }, shadowOpacity: 0.22, shadowRadius: 18, elevation: 8 },
    web: "inset 0 1px 0 rgba(245,239,227,0.06), 0 8px 18px rgba(31,20,10,0.22)",
    webDark: "inset 0 1px 0 rgba(245,239,227,0.08), 0 8px 18px rgba(0,0,0,0.45)",
  },
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
  /**
   * Text/icon colour to place ON TOP of `accent`. NOT always white: the accent
   * is light in the twilight and dark palettes, where white text measures
   * 1.76:1 and 2.13:1 against it — both far below the 4.5:1 that WCAG 1.4.3
   * Level AA requires, on this product's own primary buttons.
   */
  onAccent: string;
  /** Same idea for `danger`, which is a LIGHT red in the dark palettes. */
  onDanger: string;
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
  accent: "#A83A08",      // burnt orange (ember) — deepened from #C2410C,
                          // which measured 4.1-4.3:1 as text on this palette's
                          // own tinted surfaces, just under the 4.5:1 AA floor
  onAccent: "#FFFFFF",    // 5.2:1 on the burnt orange
  onDanger: "#FFFFFF",    // 6.5:1 on #B91C1C
  accentSecondary: "#9A1842", // deep magenta
  success: "#116634",     // deepened from #15803D (3.6:1 on the success chip)
  warning: "#8F5606",     // dark amber — deepened from #A16207, which
                          // measured 4.38:1 on this palette's own background
  danger: "#B91C1C",
  info: "#1E5C8E",        // teal-leaning blue
  successSoft: "rgba(17, 102, 52, 0.10)",
  warningSoft: "rgba(143, 86, 6, 0.12)",
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
  onAccent: "#2B1B0E",    // 9.4:1 — white would be 1.76:1 here
  onDanger: "#2B1B0E",    // 8.7:1 on #FCA5A5
  accentSecondary: "#F472B6", // rose
  success: "#86EFAC",
  warning: "#FCD34D",
  danger: "#FCA5A5",
  info: "#C7D2FE",        // periwinkle (lightened: #A5B4FC measured 3.9:1
                          // as chip text on this palette, under the 4.5:1 AA floor)
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
  onAccent: "#2B1B0E",    // 7.8:1 — white would be 2.13:1 here
  onDanger: "#2B1B0E",    // 6.0:1 on #F87171
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
  border: typeof border;
  focus: typeof focus;
  typography: typeof typography;
  shadows: typeof shadows;
};
