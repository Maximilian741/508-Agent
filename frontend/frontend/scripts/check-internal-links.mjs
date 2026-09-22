// Fail the build if the static export's internal links are broken or gone.
//
// Two regressions this guards against, both invisible in the running app:
//
//  1. Navigation silently reverting to onPress-only Pressables. Those render as
//     <div role="button">, which search engines do not follow — the export once
//     shipped with ZERO internal <a href> links, so every page (including the
//     /fix guides) was an orphan reachable only via the sitemap.
//  2. A link to a route that no longer exports (renamed screen, a fix slug
//     that dropped out of the catalog). Google reports those as soft-404s and
//     a user gets the not-found page.
//
// Usage: node scripts/check-internal-links.mjs [distDir]   (default: dist)

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const dist = process.argv[2] || "dist";
if (!existsSync(dist)) {
  console.error(`[links] dist dir not found: ${dist}`);
  process.exit(1);
}

// Pages that must link onward. If one of these has no internal anchors, the
// crawlable-navigation work has regressed.
const MUST_LINK = ["index.html", "landing.html", "fix/index.html", "help.html", "pricing.html"];

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith(".html") && !name.startsWith("[") && !name.startsWith("+")) out.push(p);
  }
  return out;
}

function targetExists(path) {
  const clean = path.split(/[?#]/)[0].replace(/\/+$/, "");
  if (clean === "") return existsSync(join(dist, "index.html"));
  const rel = clean.slice(1);
  return (
    existsSync(join(dist, rel + ".html")) ||
    existsSync(join(dist, rel, "index.html")) ||
    existsSync(join(dist, rel)) // static asset (robots.txt, images)
  );
}

const ANCHOR = /<a\b[^>]*?\bhref="([^"]*)"/g;
const pages = walk(dist);
const broken = [];
const counts = new Map();
let total = 0;

for (const file of pages) {
  const rel = relative(dist, file).split(sep).join("/");
  const html = readFileSync(file, "utf8");
  let n = 0;
  for (const m of html.matchAll(ANCHOR)) {
    const href = m[1];
    if (!href.startsWith("/") || href.startsWith("//")) continue;
    n++;
    if (!targetExists(href)) broken.push(`${rel} -> ${href}`);
  }
  counts.set(rel, n);
  total += n;
}

const unlinked = MUST_LINK.filter((p) => counts.has(p) && counts.get(p) === 0);
const missing = MUST_LINK.filter((p) => !counts.has(p));

console.log(`[links] ${pages.length} pages, ${total} internal links`);
for (const p of MUST_LINK) console.log(`[links]   ${p}: ${counts.get(p) ?? "not exported"}`);

let failed = false;
if (broken.length) {
  failed = true;
  const uniq = [...new Set(broken)];
  console.error(`[links] ${uniq.length} broken internal link(s):`);
  for (const b of uniq.slice(0, 50)) console.error(`  ${b}`);
}
if (unlinked.length) {
  failed = true;
  console.error(`[links] no internal <a href> on: ${unlinked.join(", ")} — navigation is not crawlable`);
}
if (missing.length) {
  failed = true;
  console.error(`[links] expected pages not exported: ${missing.join(", ")}`);
}
process.exit(failed ? 1 : 0);
