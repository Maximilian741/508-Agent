/**
 * Credit cost helpers for the remediation pipeline.
 *
 * Mirrors the backend's DOC_FORMAT_COSTS table in app/api/credits.py. Keep
 * this in sync with that file - the backend is the source of truth, but the
 * frontend uses these numbers to show users a "this will cost N credits"
 * confirmation before kicking off a paid run. When /pipeline/analyze reports
 * `summary.cost`, callers use THAT and only fall back to this table.
 *
 * Converted uploads are priced as what they become (the backend's
 * app/intake.effective_format): .doc/.rtf/.odt are fixed and returned as a
 * .docx, .xls/.ods as .xlsx, .ppt/.odp as .pptx, and an image as a PDF.
 */

export type RemediateFormat = "pdf" | "docx" | "pptx" | "html" | "xlsx";

const COSTS: Record<RemediateFormat, number> = {
  pdf: 5,
  docx: 3,
  pptx: 4,
  html: 3,
  xlsx: 3,
};

const DEFAULT_COST = 5;

/** Extension (no dot) -> the format it is fixed, written and priced as. */
const EFFECTIVE_FORMAT: Record<string, RemediateFormat> = {
  pdf: "pdf",
  docx: "docx",
  pptx: "pptx",
  xlsx: "xlsx",
  html: "html",
  htm: "html",
  // Converted before checking (app/intake on the backend).
  doc: "docx",
  rtf: "docx",
  odt: "docx",
  xls: "xlsx",
  ods: "xlsx",
  ppt: "pptx",
  odp: "pptx",
  png: "pdf",
  jpg: "pdf",
  jpeg: "pdf",
  gif: "pdf",
  bmp: "pdf",
  tif: "pdf",
  tiff: "pdf",
  webp: "pdf",
};

/**
 * Return the credit cost for remediating a document of the given format.
 *
 * Accepts a few common shapes (with or without a leading dot, mixed case)
 * and falls back to the default cost on anything unrecognized so the UI
 * never silently shows "0 credits" for a chargeable run.
 */
export function costFor(format: string | null | undefined): number {
  if (!format) return DEFAULT_COST;
  const key = format.trim().toLowerCase().replace(/^\./, "");
  const eff = EFFECTIVE_FORMAT[key];
  return eff ? COSTS[eff] : DEFAULT_COST;
}

/**
 * Best-effort format extraction from a File object. Prefers the extension
 * over the MIME type because Office MIMEs are wordy and often missing on
 * web uploads. Returns the EFFECTIVE format (a .doc is a docx job).
 */
export function formatForFile(
  file: { name?: string; type?: string } | null | undefined,
): RemediateFormat | null {
  if (!file) return null;
  const name = (file.name || "").toLowerCase();
  const m = /\.([a-z0-9]+)$/.exec(name);
  if (m && EFFECTIVE_FORMAT[m[1]]) return EFFECTIVE_FORMAT[m[1]];
  const type = (file.type || "").toLowerCase();
  if (type.includes("pdf")) return "pdf";
  if (type.includes("wordprocessingml")) return "docx";
  if (type.includes("presentationml")) return "pptx";
  if (type.includes("spreadsheetml")) return "xlsx";
  if (type.includes("html")) return "html";
  if (type.startsWith("image/")) return "pdf";
  return null;
}
