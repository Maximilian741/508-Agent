/**
 * Colour palettes — pure data, no imports.
 *
 * Kept free of react-native so `scripts/check-contrast.mjs` can read these
 * objects straight out of this file and MEASURE every text/background pair
 * against WCAG 2.1 AA (4.5:1 text, 3:1 large text and UI). Keep each palette a
 * plain object literal of string values: the script evaluates the literal
 * between `= {` and the closing `};`, so no spreads, calls or references.
 *
 * Every base colour that call sites alpha-suffix (`accent + "1A"`,
 * `success + "22"`, `surface + "80"`) MUST stay a 6-digit hex.
 *
 * Direction: dark-first, a deep near-black ink with a faint cool bloom, ONE
 * confident accent (a clear mint-aqua that reads as "verified"), soft layered
 * shadows and translucent glass surfaces. The light palette is the same
 * system on paper-white.
 */

export type ThemeColors = {
  bg: string;
  surface: string;
  surface2: string;
  surface3: string;
  text: string;
  textMuted: string;
  border: string;
  /** Modal / overlay backdrop colour (translucent). */
  shadow: string;
  accent: string;
  /**
   * Text/icon colour to place ON TOP of `accent`. NOT always white: the accent
   * is a light mint in the dark palettes, where white measures under 2:1.
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
  /** Tinted fill behind accent-coloured text (active nav pill, selected tab). */
  accentSoft: string;
  /** Web page backdrop: a CSS gradient layered over `bg`. */
  bgGradient: string;
  /** Translucent card/nav surface; blurred with backdrop-filter on web. */
  glass: string;
  /** Hairline for glass surfaces. */
  glassBorder: string;
  /** Wash laid over the shader so text on top keeps its contrast. */
  scrim: string;
  /** Shader field colours. Every shader pixel is a convex mix of these four. */
  shaderBase: string;
  shaderDeep: string;
  shaderGlow: string;
  shaderGlow2: string;
};

/**
 * Aurora — the default ("system") look. Deep ink with a cool blue bloom.
 */
export const auroraColors: ThemeColors = {
  bg: "#060910",
  surface: "#0C111B",
  surface2: "#121A28",
  surface3: "#182234",
  text: "#EAF0F8",
  textMuted: "#9AA8BE",
  border: "#1F2A3D",
  shadow: "rgba(2, 5, 12, 0.72)",
  accent: "#5EEAD4",
  onAccent: "#04241F",
  onDanger: "#2A0808",
  accentSecondary: "#93B4FF",
  success: "#4ADE80",
  warning: "#FACC15",
  danger: "#FC8181",
  info: "#7DD3FC",
  successSoft: "rgba(74, 222, 128, 0.12)",
  warningSoft: "rgba(250, 204, 21, 0.12)",
  dangerSoft: "rgba(252, 129, 129, 0.12)",
  infoSoft: "rgba(125, 211, 252, 0.12)",
  accentSoft: "rgba(94, 234, 212, 0.12)",
  bgGradient:
    "radial-gradient(1100px 560px at 8% -12%, rgba(64, 110, 255, 0.13), transparent 62%), radial-gradient(900px 520px at 100% -6%, rgba(94, 234, 212, 0.08), transparent 58%), linear-gradient(180deg, #080C16 0%, #060910 42%)",
  glass: "rgba(13, 19, 30, 0.74)",
  glassBorder: "rgba(148, 170, 205, 0.14)",
  scrim: "rgba(6, 9, 16, 0.40)",
  shaderBase: "#060910",
  shaderDeep: "#0B1A40",
  shaderGlow: "#0A5550",
  shaderGlow2: "#2A36B8",
};

/**
 * Light — the same system on paper-white, with a deep teal accent.
 */
export const lightColors: ThemeColors = {
  bg: "#F4F6FA",
  surface: "#FFFFFF",
  surface2: "#EEF2F7",
  surface3: "#E3E9F1",
  text: "#0B1220",
  textMuted: "#4A5566",
  border: "#D6DDE7",
  shadow: "rgba(15, 23, 42, 0.42)",
  accent: "#0B6B62",
  onAccent: "#FFFFFF",
  onDanger: "#FFFFFF",
  accentSecondary: "#3B55C9",
  success: "#11692F",
  warning: "#8A5300",
  danger: "#B42318",
  info: "#1D58A6",
  successSoft: "rgba(17, 105, 47, 0.09)",
  warningSoft: "rgba(138, 83, 0, 0.10)",
  dangerSoft: "rgba(180, 35, 24, 0.09)",
  infoSoft: "rgba(29, 88, 166, 0.09)",
  accentSoft: "rgba(11, 107, 98, 0.10)",
  bgGradient:
    "radial-gradient(1100px 560px at 8% -12%, rgba(59, 85, 201, 0.08), transparent 62%), radial-gradient(900px 520px at 100% -6%, rgba(11, 107, 98, 0.07), transparent 58%), linear-gradient(180deg, #F8FAFD 0%, #F4F6FA 42%)",
  glass: "rgba(255, 255, 255, 0.80)",
  glassBorder: "rgba(15, 23, 42, 0.09)",
  scrim: "rgba(244, 246, 250, 0.55)",
  shaderBase: "#F4F6FA",
  shaderDeep: "#E7EDF8",
  shaderGlow: "#C6F0EA",
  shaderGlow2: "#D2DBFF",
};

/**
 * Graphite — the "dark" choice: neutral, hue-free near-black for maximum
 * contrast, same accent.
 */
export const darkColors: ThemeColors = {
  bg: "#09090B",
  surface: "#111113",
  surface2: "#18181B",
  surface3: "#202024",
  text: "#F4F4F5",
  textMuted: "#A1A1AA",
  border: "#2A2A30",
  shadow: "rgba(0, 0, 0, 0.72)",
  accent: "#5EEAD4",
  onAccent: "#04241F",
  onDanger: "#2A0808",
  accentSecondary: "#A5B4FC",
  success: "#4ADE80",
  warning: "#FACC15",
  danger: "#FC8181",
  info: "#7DD3FC",
  successSoft: "rgba(74, 222, 128, 0.12)",
  warningSoft: "rgba(250, 204, 21, 0.12)",
  dangerSoft: "rgba(252, 129, 129, 0.12)",
  infoSoft: "rgba(125, 211, 252, 0.12)",
  accentSoft: "rgba(94, 234, 212, 0.12)",
  bgGradient:
    "radial-gradient(1000px 520px at 50% -14%, rgba(255, 255, 255, 0.06), transparent 62%), linear-gradient(180deg, #0C0C0F 0%, #09090B 42%)",
  glass: "rgba(18, 18, 21, 0.76)",
  glassBorder: "rgba(255, 255, 255, 0.08)",
  scrim: "rgba(9, 9, 11, 0.42)",
  shaderBase: "#09090B",
  shaderDeep: "#141419",
  shaderGlow: "#0B4F4A",
  shaderGlow2: "#1D2238",
};
