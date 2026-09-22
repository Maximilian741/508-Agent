#!/usr/bin/env node
/**
 * Design invariants the owner asked for, enforced at build time:
 *
 *  1. No pixel art outside the logo. The old pixel components (PixelIcon,
 *     PixelGlyph, PixelSpinner, PixelProgress, PixelFrame) must not come back,
 *     and PixelLogo — the one sanctioned pixel mark — is only used by AppNav.
 *  2. No pixel / arcade / handwriting fonts and no third-party font hosts
 *     (the CSP only allows font-src 'self'; Inter ships from /fonts).
 *  3. One brand lockup: the "508 · AGENT" double wordmark is gone.
 *  4. The first-visit tour does not auto-open (it is reachable from Help).
 *
 *   node scripts/check-design.mjs      # exit 1 on any violation
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const scanDirs = ["app", "src", "public"].map((d) => join(root, d));
const exts = /\.(tsx?|jsx?|mjs|css|html)$/;

function walk(dir, out = []) {
  let entries = [];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) {
      if (name === "node_modules" || name === "pdfjs") continue;
      walk(p, out);
    } else if (exts.test(name)) out.push(p);
  }
  return out;
}

const RULES = [
  { re: /\bPixel(Icon|Glyph|Spinner|Progress|Frame)\b/, why: "pixel-art component outside the logo (use Icon / Spinner / ProgressBar)" },
  { re: /fonts\.(googleapis|gstatic)\.com|use\.typekit\.net|fonts\.bunny\.net/, why: "third-party font host (CSP allows font-src 'self' only)" },
  { re: /VT323|Press Start 2P|PressStart2P|Silkscreen|Pixelify|Architects Daughter|\bKalam\b/, why: "pixel/arcade/handwriting font" },
  { re: /508 · AGENT|508 · AGENT/, why: "the old double brand wordmark" },
];

const violations = [];
for (const file of scanDirs.flatMap((d) => walk(d))) {
  const rel = relative(root, file).split(sep).join("/");
  const text = readFileSync(file, "utf8");
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    for (const rule of RULES) {
      if (rule.re.test(line)) violations.push(`${rel}:${i + 1}: ${rule.why}\n    ${line.trim().slice(0, 140)}`);
    }
  });
  if (/\bPixelLogo\b/.test(text) && !/src\/ui\/components\/(PixelLogo|AppNav)\.tsx$/.test(rel) && !rel.endsWith("src/ui/theme.ts")) {
    violations.push(`${rel}: PixelLogo used outside the nav lockup (the pixel mark is the logo only)`);
  }
}

// The tour must not open itself.
const tour = readFileSync(join(root, "src", "ui", "components", "OnboardingTour.tsx"), "utf8");
if (/if\s*\(\s*!\s*isCompleted\(\)\s*\)\s*setOpen\(true\)/.test(tour)) {
  violations.push("src/ui/components/OnboardingTour.tsx: the tour auto-opens on first visit again");
}

if (violations.length) {
  console.error(`[design] ${violations.length} violation(s):\n` + violations.join("\n"));
  process.exit(1);
}
console.log("[design] no pixel art outside the logo, no external/pixel fonts, one brand lockup, no auto-opening tour — ok");
