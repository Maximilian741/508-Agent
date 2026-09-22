/**
 * The one-step "fix my file" plan: what we can fix, what needs a person,
 * what it costs, and the plain words for each.
 *
 * HONESTY: the promise ("we can fix 5") comes from the backend's
 * `violation.autoFixable` / `summary` when present (shared API contract: the
 * backend dry-runs the offline fixes, so a fix it would refuse is never
 * promised). On an older backend we fall back to a MIRROR of
 * pipeline._PERSISTED_ACTIONS - the only fixes the output bytes can carry -
 * and, while the descriptions are drafted by the offline heuristic, we do NOT
 * promise picture descriptions or table titles (the heuristic writes nearby
 * text, which is not clearly better than nothing). After the fix, the result
 * is always read from the response (`fixed` / `persistedFixes`), never from
 * this promise.
 *
 * Copy rule: short sentences, everyday words, no rule ids, no standards names.
 */
import type { PipelineResponse, PipelineRemediateResult, PipelineViolation } from "../api/client";
import { costFor, formatForFile, RemediateFormat } from "./creditCosts";
import { GUIDE_EXCLUDED, ruleSlug } from "./fixGuides";
import { lookupIssue } from "./issueCatalog";

// ---------------------------------------------------------------------------
// Accepted files
// ---------------------------------------------------------------------------

/** Every extension the drop zone takes (the backend's app/intake list). */
export const ACCEPTED_EXTENSIONS = [
  "pdf",
  "docx",
  "pptx",
  "xlsx",
  "html",
  "htm",
  "doc",
  "xls",
  "ppt",
  "rtf",
  "odt",
  "ods",
  "odp",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "bmp",
  "tif",
  "tiff",
  "webp",
] as const;

/** The <input accept> value. */
export const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.map((e) => "." + e).join(",");

/** Shown under the drop zone. */
export const ACCEPTED_SENTENCE = "PDF, Word, PowerPoint, Excel, web pages and pictures.";

export function fileExtension(name: string | null | undefined): string {
  const m = /\.([A-Za-z0-9]+)$/.exec((name || "").trim());
  return m ? m[1].toLowerCase() : "";
}

export function isAcceptedFile(file: { name?: string } | null | undefined): boolean {
  const ext = fileExtension(file?.name);
  return (ACCEPTED_EXTENSIONS as readonly string[]).includes(ext);
}

export function isImageFile(file: { name?: string; type?: string } | null | undefined): boolean {
  const ext = fileExtension(file?.name);
  return ["png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"].includes(ext);
}

export function isPdfFile(file: { name?: string; type?: string } | null | undefined): boolean {
  return fileExtension(file?.name) === "pdf" || (file?.type || "").toLowerCase() === "application/pdf";
}

// ---------------------------------------------------------------------------
// The persistence mirror (fallback only)
// ---------------------------------------------------------------------------

/**
 * MIRROR of backend app/api/pipeline.py `_PERSISTED_ACTIONS` (+ the xlsx
 * set). Used only when /pipeline/analyze does not send `autoFixable`.
 */
const PERSISTED_ACTIONS: Record<RemediateFormat, ReadonlySet<string>> = {
  docx: new Set([
    "ADD_TABLE_HEADERS",
    "FILL_FORM_FIELD_LABELS",
    "FIX_CONTRAST",
    "FIX_LIST_STRUCTURE",
    "GENERATE_ALT_TEXT",
    "GENERATE_TABLE_CAPTION",
    "IMPROVE_LINK_TEXT",
    "NORMALIZE_HEADING_LEVEL",
    "PROMOTE_HEADING",
    "REMOVE_DECORATIVE_ALT_TEXT",
    "SET_DOCUMENT_LANGUAGE",
    "SET_DOCUMENT_TITLE",
  ]),
  pptx: new Set([
    "ADD_TABLE_HEADERS",
    "FIX_CONTRAST",
    "FIX_LIST_STRUCTURE",
    "GENERATE_ALT_TEXT",
    "IMPROVE_LINK_TEXT",
    "REMOVE_DECORATIVE_ALT_TEXT",
    "SET_DOCUMENT_LANGUAGE",
    "SET_DOCUMENT_TITLE",
    "SET_SLIDE_TITLE",
  ]),
  pdf: new Set([
    "ADD_OCR_TEXT_LAYER",
    "FILL_FORM_FIELD_LABELS",
    "GENERATE_ALT_TEXT",
    "REMOVE_DECORATIVE_ALT_TEXT",
    "SET_DOCUMENT_LANGUAGE",
    "SET_DOCUMENT_TITLE",
    "TAG_PDF_STRUCTURE",
  ]),
  html: new Set([
    "ADD_TABLE_HEADERS",
    "FILL_FORM_FIELD_LABELS",
    "FIX_CONTRAST",
    "FIX_LIST_STRUCTURE",
    "FIX_POSITIVE_TABINDEX",
    "GENERATE_ALT_TEXT",
    "GENERATE_TABLE_CAPTION",
    "IMPROVE_LINK_TEXT",
    "NORMALIZE_HEADING_LEVEL",
    "SET_DOCUMENT_LANGUAGE",
    "SET_DOCUMENT_TITLE",
    "SET_INPUT_AUTOCOMPLETE",
  ]),
  xlsx: new Set([
    "SET_DOCUMENT_TITLE",
    "SET_DOCUMENT_LANGUAGE",
    "GENERATE_ALT_TEXT",
    "REMOVE_DECORATIVE_ALT_TEXT",
    "ADD_TABLE_HEADERS",
  ]),
};

/** Actions whose offline (heuristic) draft is not promised in the fallback. */
const DRAFT_ONLY_ACTIONS = new Set(["GENERATE_ALT_TEXT", "GENERATE_TABLE_CAPTION"]);

/** The format a job is fixed and priced as: the report's, else the file's. */
export function jobFormat(report: PipelineResponse | null, file: { name?: string; type?: string } | null): RemediateFormat | null {
  const src = (report?.summary?.sourceFormat || "").toLowerCase().replace(/^\./, "");
  if (src === "pdf" || src === "docx" || src === "pptx" || src === "xlsx") return src;
  if (src === "html" || src === "htm") return "html";
  return formatForFile(file);
}

/**
 * Can approving this finding change the file? The backend's answer when it
 * sent one; otherwise the persistence mirror (conservative, see file doc).
 */
export function isAutoFixable(v: PipelineViolation, fmt: RemediateFormat | null, aiProvider?: string | null): boolean {
  if (typeof v.autoFixable === "boolean") return v.autoFixable;
  if (!fmt) return false;
  const persisted = PERSISTED_ACTIONS[fmt];
  const actions = (v.recommendedActions || []).filter((a) => a !== "FLAG_FOR_MANUAL_REVIEW" && persisted.has(a));
  if (actions.length === 0) return false;
  const heuristic = !aiProvider || aiProvider === "heuristic";
  if (heuristic && actions.every((a) => DRAFT_ONLY_ACTIONS.has(a))) return false;
  return true;
}

export interface FixPlan {
  total: number;
  auto: PipelineViolation[];
  manual: PipelineViolation[];
  /** Credits a fix costs (0 when there is nothing we can fix). */
  cost: number;
  format: RemediateFormat | null;
}

export function buildPlan(report: PipelineResponse, file: { name?: string; type?: string } | null): FixPlan {
  const fmt = jobFormat(report, file);
  const violations = report.violations || [];
  const auto: PipelineViolation[] = [];
  const manual: PipelineViolation[] = [];
  for (const v of violations) {
    if (isAutoFixable(v, fmt, report.aiProvider)) auto.push(v);
    else manual.push(v);
  }
  const s = report.summary || ({} as PipelineResponse["summary"]);
  // The backend's price when it sent one (it is 0 when nothing is fixable);
  // our mirror of DOC_FORMAT_COSTS otherwise.
  const cost = auto.length === 0 ? 0 : typeof s.cost === "number" && s.cost > 0 ? s.cost : costFor(fmt);
  return { total: violations.length, auto, manual, cost, format: fmt };
}

// ---------------------------------------------------------------------------
// Copy
// ---------------------------------------------------------------------------

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

/** "We found 7 things. We can fix 5 automatically. 2 need a quick look from you." */
export function resultHeadline(plan: FixPlan): { title: string; body: string } {
  const { total, auto, manual } = plan;
  if (total === 0) {
    return {
      title: "Good news: we didn't find anything to fix.",
      body: "This file already passed every check we run.",
    };
  }
  const title = `We found ${total} ${plural(total, "thing", "things")} to fix.`;
  if (auto.length === 0) {
    return {
      title,
      body: `We can't fix ${plural(total, "it", "any of them")} automatically. ${plural(total, "It needs", "They need")} a quick look from you. Here's what to do.`,
    };
  }
  const canFix = auto.length === total ? (total === 1 ? "We can fix it automatically." : "We can fix all of them automatically.") : `We can fix ${auto.length} automatically.`;
  const rest = manual.length === 0 ? "" : ` ${manual.length} ${plural(manual.length, "needs", "need")} a quick look from you.`;
  return { title, body: canFix + rest };
}

/** The one button. Its label carries the price; pressing it is the go-ahead. */
export function fixButtonLabel(cost: number, credits: number | null): string {
  if (credits === null) return `Fix it – uses ${cost} ${plural(cost, "credit", "credits")}`;
  return `Fix it – uses ${cost} of your ${credits} ${plural(credits, "credit", "credits")}`;
}

/** After the run: "We fixed 5 of 7." */
export function fixedHeadline(fixed: number, total: number): string {
  if (fixed === 0) return "We couldn't safely fix anything in this file.";
  if (fixed >= total) return total === 1 ? "We fixed the one thing we found." : `We fixed all ${total}.`;
  return `We fixed ${fixed} of ${total}.`;
}

const PLAIN_TITLES: Record<string, string> = {
  MISSING_ALT_TEXT: "A picture has no description",
  ALT_TEXT_NOT_DESCRIPTIVE: "A picture's description doesn't say what it shows",
  DECORATIVE_IMAGE_WITH_ALT: "A decoration gets read out loud",
  HEADING_LEVEL_JUMP: "A heading skips a level",
  SKIPPED_HEADING_LEVEL: "Headings are out of order",
  TABLE_MISSING_HEADERS: "A table has no header row",
  TABLE_HEADER_SCOPE_INVALID: "A table's headings aren't tied to their cells",
  LIST_STRUCTURE_INVALID: "A list is typed out, not a real list",
  LINK_TEXT_NON_DESCRIPTIVE: "A link's words don't say where it goes",
  IFRAME_TITLE_MISSING: "An embedded frame has no name",
  INPUT_AUTOCOMPLETE_MISSING: "A form field doesn't say what it's for",
  LABEL_IN_NAME_MISMATCH: "A button's spoken name doesn't match its words",
  POSITIVE_TABINDEX: "The keyboard order is forced",
  LINK_NAME_MISSING: "A link has no words",
  DOCUMENT_LANGUAGE_MISSING: "The file doesn't say what language it's in",
  DOCUMENT_TITLE_MISSING: "The file has no title",
  READING_ORDER_AMBIGUOUS: "The reading order may be mixed up",
  HEADING_TEXT_EMPTY: "A heading is empty",
  TABLE_CAPTION_MISSING: "A table has no title",
  TABLE_COMPLEX_NEEDS_SUMMARY: "A complex table needs a short explanation",
  TABLE_NESTED: "A table sits inside another table",
  LINK_TARGET_BROKEN: "A link goes nowhere",
  TEXT_STYLED_AS_HEADING: "Text looks like a heading but isn't marked as one",
  PDF_UNTAGGED: "The PDF has no reading structure",
  SCANNED_DOCUMENT_NO_TEXT: "The pages are pictures of text",
  DOCUMENT_NO_HEADINGS: "The file has no headings",
  SLIDE_TITLE_MISSING: "A slide has no title",
  FORM_FIELD_UNLABELED: "A form field has no label",
  LOW_CONTRAST_TEXT: "Some text may be too faint to read",
  ANALYSIS_TRUNCATED: "Only part of this file was checked",
  SHEET_NAME_DEFAULT: "A sheet still has its default name",
  DATA_RANGE_HEADERS_UNCLEAR: "A block of data has no clear header row",
};

const WHAT_TO_DO: Record<string, string> = {
  MISSING_ALT_TEXT: "Add one sentence that says what the picture shows.",
  ALT_TEXT_NOT_DESCRIPTIVE: "Replace the file name or placeholder with what the picture shows.",
  DECORATIVE_IMAGE_WITH_ALT: "Mark the picture as decorative so screen readers skip it.",
  HEADING_LEVEL_JUMP: "Change this heading's level so it follows the one before it (Heading 2 after Heading 1).",
  SKIPPED_HEADING_LEVEL: "Change this heading's level so it follows the one before it (Heading 2 after Heading 1).",
  TABLE_MISSING_HEADERS: "Mark the first row as the table's header row.",
  TABLE_HEADER_SCOPE_INVALID: "Mark which row or column each heading cell belongs to.",
  LIST_STRUCTURE_INVALID: "Use your editor's list button instead of typing dashes or numbers.",
  LINK_TEXT_NON_DESCRIPTIVE: "Change the link's words to say where it goes, like “2024 annual report”.",
  IFRAME_TITLE_MISSING: "Give the embedded frame a short title.",
  INPUT_AUTOCOMPLETE_MISSING: "Say what the field is for (name, email, phone) so browsers can fill it in.",
  LABEL_IN_NAME_MISMATCH: "Make the button's hidden name start with the words people can see.",
  POSITIVE_TABINDEX: "Remove the forced tab order so the keyboard follows the page.",
  LINK_NAME_MISSING: "Give this link words that say where it goes.",
  DOCUMENT_LANGUAGE_MISSING: "Set the file's language (for example, English) in its properties.",
  DOCUMENT_TITLE_MISSING: "Give the file a title in its properties.",
  READING_ORDER_AMBIGUOUS: "Check that the text reads in the right order, top to bottom.",
  HEADING_TEXT_EMPTY: "Type the heading's words, or delete the empty heading.",
  TABLE_CAPTION_MISSING: "Give the table a one-line title that says what it shows.",
  TABLE_COMPLEX_NEEDS_SUMMARY: "Add a sentence before the table that explains how it's laid out.",
  TABLE_NESTED: "Move the inner table out into its own table.",
  LINK_TARGET_BROKEN: "Fix the link's address, or remove the link.",
  TEXT_STYLED_AS_HEADING: "Apply a real heading style (Heading 1, Heading 2) to this text.",
  PDF_UNTAGGED: "Export the PDF again from the program that made it, with “tagged PDF” turned on.",
  SCANNED_DOCUMENT_NO_TEXT: "Run text recognition (OCR) on the scan, or upload the original file instead.",
  DOCUMENT_NO_HEADINGS: "Turn your section titles into real headings.",
  SLIDE_TITLE_MISSING: "Give this slide a title (it can sit off the edge of the slide).",
  FORM_FIELD_UNLABELED: "Give this form field a label that says what to type.",
  LOW_CONTRAST_TEXT: "Make this text darker, or its background lighter.",
  ANALYSIS_TRUNCATED: "Split the file into smaller parts and check each one.",
  SHEET_NAME_DEFAULT: "Rename the sheet's tab to say what's on it.",
  DATA_RANGE_HEADERS_UNCLEAR: "Add a row of column headings above the data.",
};

/** Plain title for a finding (never the rule id). */
export function plainTitle(v: Pick<PipelineViolation, "ruleId" | "description">): string {
  const t = PLAIN_TITLES[v.ruleId];
  if (t) return t;
  // An unknown rule's catalog title is just the lower-cased id; use the
  // backend's own sentence instead.
  if (isCatalogued(v.ruleId)) return lookupIssue(v.ruleId).title;
  return firstSentence(v.description) || "Something needs a closer look";
}

function isCatalogued(ruleId: string): boolean {
  return !lookupIssue(ruleId).summary.startsWith("An accessibility rule was flagged");
}

/** One sentence: what a person should do about it. */
export function whatToDo(v: Pick<PipelineViolation, "ruleId" | "description">): string {
  return WHAT_TO_DO[v.ruleId] || firstSentence(v.description) || "Take a look at this part of the file.";
}

function firstSentence(text: string | null | undefined): string {
  const t = (text || "").trim();
  if (!t) return "";
  const m = /^(.{8,220}?[.!?])(\s|$)/.exec(t);
  return (m ? m[1] : t.slice(0, 220)).trim();
}

/** The /fix guide for this rule, or the guide index when it has none. */
export function guideHref(ruleId: string): string {
  if (!isCatalogued(ruleId) || GUIDE_EXCLUDED.has(ruleId)) return "/fix";
  return `/fix/${ruleSlug(ruleId)}`;
}

/** "Page 3", "Slide 2", or "" — where to look, in words. */
export function whereLabel(v: PipelineViolation, fmt: RemediateFormat | null): string {
  const page = v.location?.page ?? v.page ?? null;
  if (typeof page === "number" && page > 0) {
    return fmt === "pptx" ? `Slide ${page}` : `Page ${page}`;
  }
  const kind = v.location?.kind;
  if (kind === "document" || (!kind && !v.location)) {
    if (/^DOCUMENT_|^PDF_|^SCANNED_|ANALYSIS_TRUNCATED/.test(v.ruleId)) return "The whole file";
  }
  return "";
}

// ---------------------------------------------------------------------------
// After the fix
// ---------------------------------------------------------------------------

export type RowOutcome = "fixed" | "needs-you";

/**
 * Per finding: did the fix reach the file? Read from the response only.
 * Prefers the contract's `violations[].fixed`; else a SUCCESS execution of
 * one of the finding's own actions on its own node. Nothing is "fixed" when
 * the backend persisted nothing (it then returned the original bytes).
 */
export function outcomes(
  planned: PipelineViolation[],
  result: PipelineRemediateResult,
): Record<string, RowOutcome> {
  const out: Record<string, RowOutcome> = {};
  const persisted = typeof result.persistedFixes === "number" ? result.persistedFixes : null;
  const byId = new Map<string, PipelineViolation>();
  for (const v of result.violations || []) byId.set(v.id, v);
  for (const v of planned) {
    if (persisted === 0) {
      out[v.id] = "needs-you";
      continue;
    }
    const echoed = byId.get(v.id);
    if (echoed && typeof echoed.fixed === "boolean") {
      out[v.id] = echoed.fixed ? "fixed" : "needs-you";
      continue;
    }
    const ok = (result.executions || []).some(
      (e) =>
        e.status === "success" &&
        e.targetNodeId === v.nodeId &&
        (v.recommendedActions || []).includes(e.actionCode),
    );
    out[v.id] = ok ? "fixed" : "needs-you";
  }
  return out;
}

/** How many we fixed, for "We fixed 5 of 7" (never more than we found). */
export function fixedCount(total: number, rowOutcomes: Record<string, RowOutcome>, result: PipelineRemediateResult): number {
  const persisted = typeof result.persistedFixes === "number" ? result.persistedFixes : null;
  if (persisted === 0) return 0;
  const rows = Object.values(rowOutcomes).filter((o) => o === "fixed").length;
  // The contract's per-finding `fixed` is the same reconciled truth the
  // charge gate counted, per finding: use it as is.
  if (result.violations && result.violations.some((v) => typeof v.fixed === "boolean")) return Math.min(rows, total);
  // Older backend: rows come from executions. persistedFixes counts fixes
  // that reached the bytes; never report more than that, nor more than found.
  if (persisted !== null) return Math.min(rows > 0 ? rows : persisted, persisted, total);
  return Math.min(rows, total);
}

/** Absolute download URL (the API returns signed, relative paths). */
export function absoluteUrl(url: string, baseUrl: string): string {
  if (!url) return url;
  if (/^https?:\/\//i.test(url)) return url;
  return `${baseUrl.replace(/\/+$/, "")}${url.startsWith("/") ? "" : "/"}${url}`;
}

/** Human file size. */
export function fileSizeLabel(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}
