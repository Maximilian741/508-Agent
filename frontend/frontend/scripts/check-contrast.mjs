#!/usr/bin/env node
/**
 * Measure WCAG 2.1 contrast for every text/background token pair in every
 * palette in src/ui/palettes.ts, and fail (exit 1) on any pair under AA.
 *
 *   node scripts/check-contrast.mjs            # summary + failures
 *   node scripts/check-contrast.mjs --verbose  # every measured pair
 *   node scripts/check-contrast.mjs --palettes <file>  # check another file
 *
 * Thresholds: 4.5:1 for text (WCAG 1.4.3), 3:1 for UI component boundaries
 * such as the focus ring and the primary button edge (WCAG 1.4.11).
 *
 * What counts as a "background" here is deliberately pessimistic:
 *  - the solid surfaces (bg, surface, surface2, surface3);
 *  - the page gradient at its brightest (and, for light palettes, darkest)
 *    point: every rgba layer composited over every solid stop, including all
 *    layers stacked on top of each other;
 *  - glass cards (translucent `glass`) composited over each of those;
 *  - the hero shader under its scrim, over EVERY pixel it can emit. The
 *    fragment shader only ever mixes (convexly) between the four shader
 *    colours, plus at most 1/255 of dither, so each background is handled as
 *    a luminance RANGE with provable bounds (see "luminance ranges" below);
 *  - tinted fills: each status *Soft token, `accentSoft`, and the `+"22"` /
 *    `+"1A"` hex-alpha tints call sites build (ScoreBadge, nav pills).
 *
 * Pure node, no dependencies, and no TypeScript loader: it reads the object
 * literals out of palettes.ts as text (that file is kept as plain data).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const argIdx = process.argv.indexOf("--palettes");
const palettePath = argIdx > 0 ? process.argv[argIdx + 1] : join(here, "..", "src", "ui", "palettes.ts");
const source = readFileSync(palettePath, "utf8");
const verbose = process.argv.includes("--verbose");

// ---- read palettes ---------------------------------------------------------
const palettes = {};
const re = /export const (\w+)\s*:\s*ThemeColors\s*=\s*(\{[\s\S]*?\n\});/g;
let m;
while ((m = re.exec(source))) {
  // eslint-disable-next-line no-new-func
  palettes[m[1]] = Function(`"use strict"; return (${m[2]});`)();
}
if (Object.keys(palettes).length === 0) {
  console.error("[contrast] no palettes found in src/ui/palettes.ts");
  process.exit(1);
}

// ---- colour maths ----------------------------------------------------------
function parse(c) {
  c = c.trim();
  let mm = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(c);
  if (mm) {
    const n = parseInt(mm[1], 16);
    const a = mm[2] ? parseInt(mm[2], 16) / 255 : 1;
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a };
  }
  mm = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(c);
  if (mm) return { r: +mm[1], g: +mm[2], b: +mm[3], a: mm[4] === undefined ? 1 : +mm[4] };
  throw new Error(`unparseable colour: ${c}`);
}
/** Source-over composite in sRGB space (what browsers do for CSS colours). */
function over(fg, bg) {
  const a = fg.a;
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1,
  };
}
function lum({ r, g, b }) {
  const f = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
const hex = ({ r, g, b }) =>
  "#" + [r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
const withAlpha = (h, suffix) => parse(h + suffix);
const clampBox = (c, d) => ({ r: Math.max(0, Math.min(255, c.r + d)), g: Math.max(0, Math.min(255, c.g + d)), b: Math.max(0, Math.min(255, c.b + d)), a: 1 });

// ---- backgrounds as luminance ranges -------------------------------------
// A background is { label, lo, hi, loC, hiC }: every pixel it can show has
// relative luminance in [lo, hi]; loC / hiC are colours channel-wise <= / >=
// every pixel. A solid colour is the degenerate range lo == hi.
//
// Two facts keep the bounds rigorous:
//  - L is monotonic in each sRGB channel, so compositing the channel-wise
//    extremes bounds the result (the "box" bound);
//  - the sRGB -> linear curve is convex, so for any convex mix of colours
//    (every mix() in the shader, every CSS gradient blend, every alpha
//    composite) L(mix) <= the same mix of the L values (Jensen). That is a
//    much tighter UPPER bound for a shader whose colours differ in hue.
const solidRange = (label, c) => ({ label, lo: lum(c), hi: lum(c), loC: c, hiC: c });
function composite(label, top, base) {
  const loC = over(top, base.loC);
  const hiC = over(top, base.hiC);
  const jensen = top.a * lum(top) + (1 - top.a) * base.hi;
  return { label, lo: lum(loC), hi: Math.min(lum(hiC), jensen), loC, hiC };
}
/** Worst-case contrast of a solid foreground against any pixel of a range. */
function worst(fg, range) {
  const L = lum(fg);
  if (L >= range.hi) return (L + 0.05) / (range.hi + 0.05);
  if (L <= range.lo) return (range.lo + 0.05) / (L + 0.05);
  return 1; // some pixel could match the text's luminance
}

// ---- pair generation -------------------------------------------------------
let failures = 0;
let checked = 0;
const lines = [];

for (const [name, p] of Object.entries(palettes)) {
  const solid = (k) => parse(p[k]);
  const bg = solid("bg");

  // Page gradient extremes.
  const gradColors = (p.bgGradient.match(/rgba?\([^)]*\)|#[0-9a-f]{6}\b/gi) || []).map(parse);
  const layers = gradColors.filter((c) => c.a < 1);
  const stops = [bg, ...gradColors.filter((c) => c.a >= 1)];
  const page = [];
  for (const s of stops) {
    const base = solidRange(`page ${hex(s)}`, s);
    page.push(base);
    for (const l of layers) page.push(composite(`page ${hex(s)}+${hex(l)}@${l.a}`, l, base));
    if (layers.length > 1) {
      page.push(layers.reduce((acc, l) => composite(`page ${hex(s)}+all-layers`, l, acc), base));
    }
  }

  const glass = parse(p.glass);
  const glassBgs = page.map((pg) => composite(`glass/${pg.label}`, glass, pg));

  // The hero shader under its scrim. Pixels are convex mixes of the four
  // shader colours, plus at most half a code value of dither (bounded by 1).
  const sh = ["shaderBase", "shaderDeep", "shaderGlow", "shaderGlow2"].map(solid);
  const hiC = clampBox({ r: Math.max(...sh.map((c) => c.r)), g: Math.max(...sh.map((c) => c.g)), b: Math.max(...sh.map((c) => c.b)), a: 1 }, 1);
  const loC = clampBox({ r: Math.min(...sh.map((c) => c.r)), g: Math.min(...sh.map((c) => c.g)), b: Math.min(...sh.map((c) => c.b)), a: 1 }, -1);
  const field = {
    label: "shader field",
    lo: lum(loC),
    hi: Math.min(lum(hiC), Math.max(...sh.map((c) => lum(clampBox(c, 1))))),
    loC,
    hiC,
  };
  const hero = [composite("hero shader + scrim (any pixel)", parse(p.scrim), field)];

  const neutral = [
    solidRange("bg", bg),
    solidRange("surface", solid("surface")),
    solidRange("surface2", solid("surface2")),
    solidRange("surface3", solid("surface3")),
    ...page,
    ...glassBgs,
    ...hero,
  ];

  const check = (fgLabel, fg, range, min) => {
    const r = worst(fg, range);
    checked++;
    const ok = r >= min;
    if (!ok) failures++;
    if (!ok || verbose) {
      lines.push(`${ok ? "  ok  " : "  FAIL"} ${name.padEnd(13)} ${fgLabel.padEnd(18)} on ${range.label.padEnd(46)} ${r.toFixed(2).padStart(5)}:1 (need ${min})`);
    }
  };

  // 1. Text tokens on every neutral background.
  const textTokens = ["text", "textMuted", "accent", "success", "warning", "danger", "info"];
  for (const t of textTokens) for (const b of neutral) check(t, solid(t), b, 4.5);

  // 2. Status + accent text on their own tints, over the surfaces they sit on.
  const tintBases = [
    solidRange("surface", solid("surface")),
    solidRange("surface2", solid("surface2")),
    solidRange("bg", bg),
    ...glassBgs,
  ];
  const tints = [
    ["success", "successSoft"],
    ["warning", "warningSoft"],
    ["danger", "dangerSoft"],
    ["info", "infoSoft"],
    ["accent", "accentSoft"],
  ];
  for (const [fgk, softk] of tints) {
    for (const base of tintBases) {
      check(fgk, solid(fgk), composite(`${softk} over ${base.label}`, parse(p[softk]), base), 4.5);
      check(fgk, solid(fgk), composite(`${fgk}+"22" over ${base.label}`, withAlpha(p[fgk], "22"), base), 4.5);
      check(fgk, solid(fgk), composite(`${fgk}+"1A" over ${base.label}`, withAlpha(p[fgk], "1A"), base), 4.5);
    }
  }
  // The hero eyebrow pill: accent text on accentSoft over the shader. (The
  // shader hero hosts text, the eyebrow and buttons only — Hero.tsx says so;
  // status chips belong on surfaces, which are checked above.)
  for (const h of hero) check("accent", solid("accent"), composite(`accentSoft over ${h.label}`, parse(p.accentSoft), h), 4.5);

  // Default-tone text inside tinted chips.
  for (const [, softk] of tints) {
    check("text", solid("text"), composite(`${softk} over surface`, parse(p[softk]), solidRange("surface", solid("surface"))), 4.5);
  }

  // 3. Foregrounds placed on filled colours.
  check("onAccent", solid("onAccent"), solidRange("accent", solid("accent")), 4.5);
  check("onAccent", solid("onAccent"), solidRange("accentSecondary", solid("accentSecondary")), 4.5);
  check("onDanger", solid("onDanger"), solidRange("danger", solid("danger")), 4.5);
  for (const s of ["success", "warning", "danger", "info", "accent"]) check("bg", bg, solidRange(`${s} (filled dot)`, solid(s)), 4.5);

  // 4. UI boundaries (focus ring = accent, primary button edge) — 3:1.
  for (const b of neutral) check("accent (UI/focus)", solid("accent"), b, 3);
}

console.log(lines.join("\n"));
console.log(
  `[contrast] ${Object.keys(palettes).length} palettes, ${checked} pairs measured, ${failures} below AA` +
    (failures ? "" : " — all pass"),
);
process.exit(failures ? 1 : 0);
