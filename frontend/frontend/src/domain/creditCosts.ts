/**
 * Credit cost helpers for the remediation pipeline.
 *
 * Mirrors the backend's DOC_FORMAT_COSTS table in app/api/credits.py. Keep
 * this in sync with that file - the backend is the source of truth, but the
 * frontend uses these numbers to show users a "this will cost N credits"
 * confirmation before kicking off a paid run.
 */

export type RemediateFormat = "pdf" | "docx" | "pptx";

const COSTS: Record<RemediateFormat, number> = {
  pdf: 5,
  docx: 3,
  pptx: 4,
};

const DEFAULT_COST = 5;

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
  if (key === "pdf" || key === "docx" || key === "pptx") {
    return COSTS[key];
  }
  return DEFAULT_COST;
}

/**
 * Best-effort format extraction from a File object. Prefers the extension
 * over the MIME type because Office MIMEs are wordy and often missing on
 * web uploads.
 */
export function formatForFile(
  file: { name?: string; type?: string } | null | undefined,
): RemediateFormat | null {
  if (!file) return null;
  const name = (file.name || "").toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".docx")) return "docx";
  if (name.endsWith(".pptx")) return "pptx";
  const type = (file.type || "").toLowerCase();
  if (type.includes("pdf")) return "pdf";
  if (type.includes("wordprocessingml")) return "docx";
  if (type.includes("presentationml")) return "pptx";
  return null;
}
