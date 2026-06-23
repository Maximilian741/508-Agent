/**
 * Heading-outline analysis for the free standalone tool (/tools/headings).
 *
 * Pure, client-side, no backend. Accepts either HTML (<h1>…<h6>) or
 * Markdown ATX headings (#, ##, …) pasted by the user and reports the document
 * outline plus the common WCAG 1.3.1 / 2.4.6 structural problems screen-reader
 * users hit when navigating by heading.
 */

export type OutlineSeverity = "error" | "warning" | "info";

export interface OutlineHeading {
  /** 1–6. */
  level: number;
  /** Visible heading text (tags/markers stripped). */
  text: string;
}

export interface OutlineIssue {
  severity: OutlineSeverity;
  message: string;
}

export interface OutlineResult {
  headings: OutlineHeading[];
  issues: OutlineIssue[];
  /** True when there are no error/warning issues. */
  ok: boolean;
}

function _stripTags(s: string): string {
  return s
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ")
    .trim();
}

/** Extract (level, text) headings from pasted HTML or Markdown. */
export function extractHeadings(input: string): OutlineHeading[] {
  const text = input || "";
  const headings: OutlineHeading[] = [];

  // Prefer HTML <h1>…<h6> if the input contains any.
  const htmlRe = /<h([1-6])\b[^>]*>([\s\S]*?)<\/h\1>/gi;
  let m: RegExpExecArray | null;
  let foundHtml = false;
  while ((m = htmlRe.exec(text)) !== null) {
    foundHtml = true;
    headings.push({ level: parseInt(m[1], 10), text: _stripTags(m[2]) });
  }
  if (foundHtml) return headings;

  // Otherwise parse Markdown ATX headings: up to 3 leading spaces, 1–6 '#',
  // a space, then the text (trailing '#' run is decorative and stripped).
  for (const line of text.split(/\r?\n/)) {
    const mm = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (mm) headings.push({ level: mm[1].length, text: mm[2].trim() });
  }
  return headings;
}

function _detectIssues(headings: OutlineHeading[]): OutlineIssue[] {
  const issues: OutlineIssue[] = [];
  if (headings.length === 0) {
    issues.push({
      severity: "info",
      message:
        "No headings found. Paste content that contains headings — HTML (<h1>…<h6>) or Markdown (#, ##, …).",
    });
    return issues;
  }

  const h1Count = headings.filter((h) => h.level === 1).length;
  if (h1Count === 0) {
    issues.push({
      severity: "error",
      message: "No H1. Every document needs exactly one top-level heading that names the whole page.",
    });
  } else if (h1Count > 1) {
    issues.push({
      severity: "warning",
      message: `Found ${h1Count} H1 headings. A document usually has a single H1; use H2–H6 for everything beneath it.`,
    });
  }

  if (headings[0].level !== 1) {
    issues.push({
      severity: "warning",
      message: `The first heading is an H${headings[0].level}, not an H1. Start the outline at H1.`,
    });
  }

  headings.forEach((h, i) => {
    if (!h.text) {
      issues.push({
        severity: "error",
        message: `Heading ${i + 1} (H${h.level}) is empty. A heading with no text is invisible to screen-reader navigation.`,
      });
    }
    if (i > 0) {
      const prev = headings[i - 1].level;
      if (h.level > prev + 1) {
        issues.push({
          severity: "warning",
          message: `Heading ${i + 1} jumps from H${prev} to H${h.level}. Don't skip levels — step down one at a time (an H${prev + 1} belongs here).`,
        });
      }
    }
  });

  return issues;
}

export function analyzeHeadingOutline(input: string): OutlineResult {
  const headings = extractHeadings(input);
  const issues = _detectIssues(headings);
  const ok = headings.length > 0 && !issues.some((i) => i.severity !== "info");
  return { headings, issues, ok };
}
