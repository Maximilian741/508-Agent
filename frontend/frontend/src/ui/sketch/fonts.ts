/**
 * Sketch fonts.
 *
 * Hand-drawn / notebook aesthetic. We pull Kalam (body), Caveat (display),
 * and Architects Daughter (labels) from Google Fonts on web, falling back
 * to system fonts elsewhere.
 *
 * The Google Fonts stylesheet is injected once at module load on web.
 * On native we just resolve to platform serif/sans/mono fallbacks; the
 * sketch components still render, just without the handwriting feel.
 */
import { Platform } from "react-native";

const GFONTS_HREF =
  "https://fonts.googleapis.com/css2?family=Architects+Daughter&family=Caveat:wght@400;600;700&family=Kalam:wght@400;700&display=swap";

const GFONTS_LINK_ID = "508-sketch-fonts";

let injected = false;

export function ensureSketchFontsInjected(): void {
  if (injected) return;
  if (Platform.OS !== "web") {
    injected = true;
    return;
  }
  if (typeof document === "undefined") return;
  if (document.getElementById(GFONTS_LINK_ID)) {
    injected = true;
    return;
  }
  try {
    const link = document.createElement("link");
    link.id = GFONTS_LINK_ID;
    link.rel = "stylesheet";
    link.href = GFONTS_HREF;
    document.head.appendChild(link);
    injected = true;
  } catch {
    // ignore - non-fatal, falls back to system fonts
  }
}

export const sketchFontFamily = {
  body: Platform.select({
    ios: "Marker Felt, Bradley Hand, Georgia, serif",
    android: "casual, serif",
    default: "'Kalam', 'Bradley Hand', 'Marker Felt', Georgia, serif",
  }) as string,
  display: Platform.select({
    ios: "Marker Felt, Bradley Hand, Georgia, serif",
    android: "casual, serif",
    default: "'Caveat', 'Marker Felt', 'Bradley Hand', cursive",
  }) as string,
  label: Platform.select({
    ios: "Menlo, Courier, monospace",
    android: "monospace",
    default: "'Architects Daughter', 'Courier New', monospace",
  }) as string,
};

export const sketchPalette = {
  paper: "#fafaf6",
  paper2: "#f3f1ea",
  ink: "#1a1a1a",
  ink2: "#2a2a28",
  pencil: "#4a4a45",
  line: "#d8d6cf",
  lineSoft: "#e8e6df",
  lineHard: "#c0bdb3",
  hlYellow: "#fef3a8",
  hlPink: "#fbcfe8",
  hlGreen: "#c7f0d2",
  hlBlue: "#c8e0f8",
  hlCoral: "#fdb6a8",
  accent: "#c96442",
} as const;

export type HighlighterTone = "yellow" | "pink" | "green" | "blue" | "coral";

export function highlighterColor(tone: HighlighterTone): string {
  switch (tone) {
    case "pink":
      return sketchPalette.hlPink;
    case "green":
      return sketchPalette.hlGreen;
    case "blue":
      return sketchPalette.hlBlue;
    case "coral":
      return sketchPalette.hlCoral;
    case "yellow":
    default:
      return sketchPalette.hlYellow;
  }
}
