// Generate sitemap.xml into the static export and append the Sitemap line to
// robots.txt. Runs AFTER `expo export` (see the frontend Dockerfile), because
// a sitemap requires absolute URLs and only the build knows PUBLIC_BASE_URL.
//
// Usage:  PUBLIC_BASE_URL=https://app.example.com node scripts/generate-sitemap.mjs [distDir]
// Without PUBLIC_BASE_URL it prints a warning and writes nothing — a sitemap
// with a placeholder domain is worse than none (Google fetches the fake URLs
// and marks the whole sitemap as broken).

import { existsSync, appendFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// Public, indexable routes only — must stay consistent with public/robots.txt
// (everything NOT listed there as Disallow). Ordered roughly by importance.
const PUBLIC_ROUTES = [
  "/",
  "/landing",
  "/audit",
  "/scan-url",
  "/quick-audit",
  "/batch",
  "/pricing",
  "/savings",
  "/assessment",
  "/faq",
  "/help",
  "/about",
  "/security",
  "/privacy",
  "/terms",
  "/tools/contrast",
  "/tools/alt-text",
  "/tools/link-text",
  "/tools/headings",
  "/tools/readability",
  "/tools/palette",
  "/tools/accessibility-statement",
];

const base = (process.env.PUBLIC_BASE_URL || "").trim().replace(/\/+$/, "");
const dist = process.argv[2] || "dist";

if (!base || !/^https?:\/\//.test(base)) {
  console.warn(
    "[sitemap] PUBLIC_BASE_URL not set — skipping sitemap.xml. " +
      "Set it as a build arg so search engines get absolute URLs.",
  );
  process.exit(0);
}
if (!existsSync(dist)) {
  console.error(`[sitemap] dist dir not found: ${dist}`);
  process.exit(1);
}

// Per-issue fix guides are generated from the catalog (generateStaticParams),
// so discover them from the export instead of hand-listing — the sitemap can
// never lag the pages that actually shipped. Skips the "[slug].html" shell.
const { readdirSync } = await import("node:fs");
const fixDir = join(dist, "fix");
const FIX_ROUTES = existsSync(fixDir)
  ? readdirSync(fixDir)
      .filter((f) => f.endsWith(".html") && !f.startsWith("[") && f !== "index.html")
      .map((f) => `/fix/${f.slice(0, -".html".length)}`)
      .sort()
  : [];
const ALL_ROUTES = [...PUBLIC_ROUTES, "/fix", ...FIX_ROUTES];

const today = new Date().toISOString().slice(0, 10);
const xml =
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
  `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
  ALL_ROUTES.map(
    (r) => `  <url><loc>${base}${r === "/" ? "" : r}</loc><lastmod>${today}</lastmod></url>`,
  ).join("\n") +
  `\n</urlset>\n`;

writeFileSync(join(dist, "sitemap.xml"), xml);

const robots = join(dist, "robots.txt");
if (existsSync(robots)) {
  appendFileSync(robots, `\nSitemap: ${base}/sitemap.xml\n`);
}
console.log(
  `[sitemap] wrote ${ALL_ROUTES.length} URLs (${FIX_ROUTES.length} fix guides) for ${base}`,
);
