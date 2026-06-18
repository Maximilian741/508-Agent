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

type RGB = [number, number, number];

function _blend(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

function _toHex([r, g, b]: RGB): string {
  return (
    "#" +
    [r, g, b]
      .map((x) => Math.max(0, Math.min(255, x)).toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()
  );
}

/**
 * Find the colour CLOSEST to ``color`` (smallest perceptual nudge) that, paired
 * with ``against``, reaches ``target`` contrast. Scans toward black and toward
 * white — a fine linear scan, not a binary search, because the ratio is U-shaped
 * (non-monotonic) when ``color`` and ``against`` straddle each other in
 * luminance. ``adjustingForeground`` controls which side of the ratio ``color``
 * sits on. Returns null when even pure black/white can't reach the target.
 */
function _nearestPassing(
  color: RGB,
  against: RGB,
  target: number,
  adjustingForeground: boolean,
): { hex: string; ratio: number } | null {
  const extremes: RGB[] = [
    [0, 0, 0],
    [255, 255, 255],
  ];
  let best: { hex: string; ratio: number; t: number } | null = null;
  for (const extreme of extremes) {
    for (let i = 1; i <= 100; i += 1) {
      const t = i / 100;
      const c = _blend(color, extreme, t);
      const ratio = adjustingForeground ? contrastRatio(c, against) : contrastRatio(against, c);
      if (ratio >= target) {
        if (!best || t < best.t) best = { hex: _toHex(c), ratio, t };
        break; // first passing step in this direction is the closest for it
      }
    }
  }
  return best ? { hex: best.hex, ratio: best.ratio } : null;
}

/**
 * Suggest the nearest passing colours for a failing pair: keep one channel and
 * nudge the other (and vice-versa) just enough to clear ``target`` (AA normal
 * = 4.5 by default). Either field is null if that side can't reach the target.
 */
export function suggestPassing(
  fgHex: string,
  bgHex: string,
  target = 4.5,
): {
  foreground: { hex: string; ratio: number } | null;
  background: { hex: string; ratio: number } | null;
} {
  const fg = parseHex(fgHex);
  const bg = parseHex(bgHex);
  if (!fg || !bg) return { foreground: null, background: null };
  return {
    foreground: _nearestPassing(fg, bg, target, true),
    background: _nearestPassing(bg, fg, target, false),
  };
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
