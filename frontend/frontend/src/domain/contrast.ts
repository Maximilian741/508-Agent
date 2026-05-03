/**
 * WCAG color-contrast math.
 *
 * Implements the algorithm from
 * https://www.w3.org/TR/WCAG21/#dfn-relative-luminance and the contrast ratio
 * formula in §1.4.3.
 */

export interface ContrastResult {
  ratio: number;
  /** AA pass for normal text (≥ 4.5:1). */
  aaNormal: boolean;
  /** AA pass for large text (≥ 3:1). */
  aaLarge: boolean;
  /** AAA pass for normal text (≥ 7:1). */
  aaaNormal: boolean;
  /** AAA pass for large text (≥ 4.5:1). */
  aaaLarge: boolean;
  /** AA pass for non-text UI components (≥ 3:1). */
  aaUiComponents: boolean;
}

/**
 * Parse a #RRGGBB or #RGB hex color into [r, g, b] integers in 0-255.
 * Returns null on invalid input.
 */
export function parseHex(input: string): [number, number, number] | null {
  if (!input) return null;
  const trimmed = input.trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]+$/.test(trimmed)) return null;
  let hex = trimmed;
  if (hex.length === 3) {
    hex = hex
      .split("")
      .map((c) => c + c)
      .join("");
  }
  if (hex.length !== 6) return null;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return [r, g, b];
}

function _channelLum(c: number): number {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

export function relativeLuminance([r, g, b]: [number, number, number]): number {
  return 0.2126 * _channelLum(r) + 0.7152 * _channelLum(g) + 0.0722 * _channelLum(b);
}

export function contrastRatio(
  fg: [number, number, number],
  bg: [number, number, number],
): number {
  const lum1 = relativeLuminance(fg);
  const lum2 = relativeLuminance(bg);
  const [light, dark] = lum1 > lum2 ? [lum1, lum2] : [lum2, lum1];
  return (light + 0.05) / (dark + 0.05);
}

export function evaluate(
  fgHex: string,
  bgHex: string,
): { ok: true; result: ContrastResult } | { ok: false; reason: string } {
  const fg = parseHex(fgHex);
  if (!fg) return { ok: false, reason: `Foreground "${fgHex}" is not a valid hex color.` };
  const bg = parseHex(bgHex);
  if (!bg) return { ok: false, reason: `Background "${bgHex}" is not a valid hex color.` };
  const ratio = contrastRatio(fg, bg);
  return {
    ok: true,
    result: {
      ratio,
      aaNormal: ratio >= 4.5,
      aaLarge: ratio >= 3,
      aaaNormal: ratio >= 7,
      aaaLarge: ratio >= 4.5,
      aaUiComponents: ratio >= 3,
    },
  };
}
