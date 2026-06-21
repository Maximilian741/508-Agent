/**
 * Link-text accessibility helpers (WCAG 2.4.4 Link Purpose).
 *
 * Pure, dependency-free functions mirrored from the backend analyzer
 * (app/analyzers/link_analyzer.py) so the standalone Link Text Checker tool's
 * verdict matches what the engine would flag. No network, no platform APIs.
 */

/** Generic filler that says nothing out of context (kept in sync with backend). */
export const NON_DESCRIPTIVE_LINK_TEXT = new Set<string>([
  "click here", "here", "read more", "more", "link", "this", "learn more",
  "details", "click", "more info", "go here", "this page", "url", "link here",
  "continue reading", "see more", "view more", "website", "web site",
  "read this", "full story",
  "read", "info", "the link", "this link", "this article", "this document",
  "this report", "this post", "click this link", "click the link", "click below",
  "click for more", "click here for more", "click to read", "click to read more",
  "click to learn more", "click for details", "tap here", "press here",
  "open link", "go to link", "more details", "find out more", "see details",
  "see here", "view this", "check it out", "check this out", "follow this link",
  "follow the link", "full article", "read here", "read on",
]);

const URL_TEXT_RE =
  /^(?:https?:\/\/\S+|www\.\S+|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.(?:com|org|net|edu|gov|mil|int|io|co|us|uk|ca|au|de|fr|jp|cn|info|biz|app|dev|ai)(?:\/\S*)?)$/i;

export function normalizeLinkText(text: string): string {
  return (text || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function looksLikeUrl(text: string): boolean {
  return URL_TEXT_RE.test((text || "").trim());
}

export interface LinkVerdict {
  ok: boolean;
  /** Plain-English explanation shown to the user. */
  reason: string;
}

/** Judge a piece of link text exactly as the engine's LinkTextAnalyzer would. */
export function checkLinkText(text: string): LinkVerdict {
  const raw = (text || "").trim();
  const norm = normalizeLinkText(raw);
  if (!norm) {
    return { ok: false, reason: "This link has no readable text — a screen reader has nothing to announce for it." };
  }
  if (looksLikeUrl(raw)) {
    return { ok: false, reason: "The link text is a raw URL — screen readers read it out character by character, which is slow and confusing." };
  }
  if (NON_DESCRIPTIVE_LINK_TEXT.has(norm)) {
    return { ok: false, reason: `“${raw}” is generic. In a screen reader's list of links (pulled out of context) it doesn't say where the link goes.` };
  }
  if (norm.length <= 2) {
    return { ok: false, reason: "The link text is too short to describe its destination." };
  }
  return { ok: true, reason: "This link text describes its destination — exactly what a screen-reader user navigating by link needs." };
}

/**
 * Suggest a descriptive label derived from the destination URL. Platform-safe
 * (manual parse, no `new URL`). Returns null when nothing useful can be derived.
 */
export function suggestLinkText(url: string): string | null {
  let u = (url || "").trim();
  if (!u) return null;
  u = u.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").replace(/^www\./i, "");
  const hashless = u.split("#")[0].split("?")[0];
  const parts = hashless.split("/");
  const host = parts.shift() || "";
  const segs = parts.filter(Boolean);
  let last = segs.length ? segs[segs.length - 1] : "";
  last = last.replace(/\.[a-z0-9]{1,5}$/i, ""); // strip a file extension
  try {
    last = decodeURIComponent(last);
  } catch {
    /* leave as-is on malformed encoding */
  }
  last = last.replace(/[-_+]+/g, " ").replace(/%20/gi, " ").replace(/\s+/g, " ").trim();
  const titleCase = (s: string) => s.replace(/\b\w/g, (c) => c.toUpperCase());
  const titled = last ? titleCase(last) : "";
  const hostWord = host ? titleCase(host.split(".")[0] || host) : "";
  if (titled && hostWord) return `${titled} (${hostWord})`;
  if (titled) return titled;
  if (hostWord) return `Visit ${hostWord}`;
  return null;
}
