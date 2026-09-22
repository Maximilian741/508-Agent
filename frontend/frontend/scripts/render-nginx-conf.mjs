// Render the production nginx config FROM the static export.
//
// Why generated instead of hand-written:
//
//  * Routing. `expo export` with web.output=static writes /pricing as
//    pricing.html and /fix/missing-alt-text as fix/missing-alt-text.html. The
//    old `try_files $uri $uri/ /index.html` never looked for `$uri.html`, so
//    every public URL was answered with the DASHBOARD's HTML — its <title>, its
//    description, its content — and every per-page SEO tag we ship was
//    invisible to anything that reads the raw response. Dynamic routes
//    (share/[id]) are discovered from the export, so a new one can't regress.
//  * CSP. The export contains an inline bootstrap script. A strict
//    `script-src 'self'` would blank the site; `'unsafe-inline'` would make the
//    CSP decorative. We hash the exact inline scripts that shipped, so the
//    policy is strict AND can't drift from the bundle after an Expo upgrade.
//  * Header inheritance. nginx drops server-level `add_header` in any location
//    that sets its own (e.g. Cache-Control), which silently stripped
//    X-Frame-Options from index.html. Headers are repeated in every location.
//
// Usage: EXPO_PUBLIC_API_URL=https://api.example.com \
//          node scripts/render-nginx-conf.mjs [distDir] [outFile]

import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

const dist = process.argv[2] || "dist";
const outFile = process.argv[3] || "nginx.conf";

if (!existsSync(dist)) {
  console.error(`[nginx] dist dir not found: ${dist}`);
  process.exit(1);
}

// ---- API origin (connect-src / img-src) ------------------------------------
const rawApi = (process.env.EXPO_PUBLIC_API_URL || "").trim();
let apiOrigin = "http://localhost:8000"; // the app's own fallback (src/config/backendUrl.ts)
if (rawApi) {
  try {
    apiOrigin = new URL(rawApi).origin;
  } catch {
    console.error(`[nginx] EXPO_PUBLIC_API_URL is not a valid URL: ${rawApi}`);
    process.exit(1);
  }
} else {
  // Same precedence as the app (src/config/backendUrl.ts): without the build
  // env var it reads /backend_url.txt at runtime, so the CSP must allow that.
  const runtimeFile = join(dist, "backend_url.txt");
  const runtime = existsSync(runtimeFile) ? readFileSync(runtimeFile, "utf8").trim() : "";
  try {
    if (/^https?:\/\/.+/i.test(runtime)) apiOrigin = new URL(runtime).origin;
  } catch {
    /* keep the fallback */
  }
  console.warn(`[nginx] EXPO_PUBLIC_API_URL not set — CSP connect-src allows ${apiOrigin} only.`);
}

// ---- Walk the export --------------------------------------------------------
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith(".html")) out.push(p);
  }
  return out;
}
const htmlFiles = walk(dist);

// Executable inline scripts only. JSON-LD and other data blocks are not
// executed, so CSP doesn't govern them and they must not be hashed (hashing
// per-page JSON-LD would put dozens of hashes in the header).
const EXECUTABLE_TYPES = new Set(["", "module", "text/javascript", "application/javascript"]);
const SCRIPT = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
const hashes = new Set();
for (const file of htmlFiles) {
  const html = readFileSync(file, "utf8");
  for (const m of html.matchAll(SCRIPT)) {
    const attrs = m[1];
    if (/\bsrc\s*=/.test(attrs)) continue;
    const type = (attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i)?.[1] || "").toLowerCase();
    if (!EXECUTABLE_TYPES.has(type)) continue;
    if (!m[2].length) continue;
    hashes.add(`'sha256-${createHash("sha256").update(m[2], "utf8").digest("base64")}'`);
  }
}
// A handful of identical bootstrap snippets is expected. Many distinct ones
// means something is inlining per-page code, and a hash list would be
// unmaintainable — stop the build rather than ship a CSP that breaks pages.
if (hashes.size > 10) {
  console.error(`[nginx] ${hashes.size} distinct inline scripts in the export — refusing to hash them all.`);
  process.exit(1);
}

// Dynamic route shells: share/[id].html -> location ^~ /share/ { ... }
const dynamicShells = htmlFiles
  .map((f) => relative(dist, f).split(sep).join("/"))
  .filter((rel) => /(^|\/)\[[^/]+\]\.html$/.test(rel))
  .map((rel) => ({ prefix: "/" + rel.replace(/\[[^/]+\]\.html$/, ""), shell: "/" + rel }))
  .filter((d) => d.prefix !== "/")
  .sort((a, b) => b.prefix.length - a.prefix.length);

const hasNotFound = existsSync(join(dist, "+not-found.html"));

// A localhost API and its 127.0.0.1 spelling are the same backend, and the app
// falls back between them (localhost resolves to ::1 first on Windows, where
// uvicorn binds IPv4). Allow both or a local production-like run is blocked by
// its own CSP. Never widens anything in a real deployment: a public API host
// is neither spelling.
const apiOrigins = [apiOrigin];
try {
  const u = new URL(apiOrigin);
  if (u.hostname === "localhost" || u.hostname === "127.0.0.1") {
    u.hostname = u.hostname === "localhost" ? "127.0.0.1" : "localhost";
    apiOrigins.push(u.origin);
  }
} catch {
  /* apiOrigin was validated above */
}
const apiSrc = apiOrigins.join(" ");

// ---- Policy -----------------------------------------------------------------
const csp = [
  "default-src 'self'",
  `script-src 'self' ${[...hashes].sort().join(" ")}`.trim(),
  // react-native-web writes styles into <style> tags and style="" attributes.
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${apiSrc}`,
  "font-src 'self' data:",
  `connect-src 'self' ${apiSrc}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const HEADERS = [
  `add_header Content-Security-Policy "${csp}" always;`,
  `add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;`,
  `add_header X-Content-Type-Options "nosniff" always;`,
  `add_header X-Frame-Options "DENY" always;`,
  `add_header Referrer-Policy "no-referrer" always;`,
  `add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;`,
];

const indent = (lines, n) => lines.map((l) => " ".repeat(n) + l).join("\n");
const block = (match, cache, tryFiles) =>
  `    location ${match} {\n${indent(HEADERS, 8)}\n        add_header Cache-Control "${cache}" always;\n        try_files ${tryFiles};\n    }`;

const IMMUTABLE = "public, max-age=31536000, immutable";
// HTML and unhashed files revalidate every time (cheap 304s via ETag), so a
// deploy is picked up immediately and no page references a bundle that's gone.
const REVALIDATE = "no-cache";

const conf = `# GENERATED by scripts/render-nginx-conf.mjs at image build — do not edit by hand.
# Inline-script hashes: ${hashes.size}. Dynamic routes: ${dynamicShells.map((d) => d.prefix).join(", ") || "none"}.
server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    server_tokens off;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/javascript application/json
               application/xml image/svg+xml font/woff2;
${hasNotFound ? "\n    error_page 404 /+not-found.html;\n" : ""}
    # Content-hashed build output: safe to cache forever.
${block("^~ /_expo/static/", IMMUTABLE, "$uri =404")}
${block("^~ /assets/", IMMUTABLE, "$uri =404")}

    # The bundled pdf.js (the home page draws the customer's own PDF pages
    # with it). Browsers refuse a module script served as octet-stream, and
    # older nginx mime.types have no .mjs entry.
${block("^~ /pdfjs/", REVALIDATE, "$uri =404").replace("location ^~ /pdfjs/ {\n", "location ^~ /pdfjs/ {\n        types { text/javascript mjs; }\n        default_type text/javascript;\n")}

    # Dynamic routes: serve the exported shell so hydration matches the route.
${dynamicShells.map((d) => block(`^~ ${d.prefix}`, REVALIDATE, `$uri $uri.html $uri/index.html "${d.shell}"`)).join("\n")}

    # Every other route is a real exported file (/pricing -> pricing.html).
${block("/", REVALIDATE, `$uri $uri.html $uri/index.html ${hasNotFound ? "=404" : "/index.html"}`)}
}
`;

writeFileSync(outFile, conf);
console.log(
  `[nginx] wrote ${outFile}: ${hashes.size} script hash(es), ${dynamicShells.length} dynamic route(s), api ${apiOrigin}`,
);
