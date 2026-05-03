/**
 * Plain-language catalog of every accessibility flag the backend can emit.
 *
 * Every entry maps a backend rule code (e.g. "DOCUMENT_TITLE_MISSING") to a
 * human-readable explanation suitable for a remediator who is auditing a
 * document.  The shape is intentionally rich: the same data drives the issue
 * card, the audit-progress meter, the fix preview, and the standards chips.
 *
 * If you add a new backend flag, add an entry here too — the UI defaults to
 * `unknownIssue()` if a code is missing, but the audit screen will read like
 * the backend's raw enum until you fill it in.
 */

export type IssueSeverity = "error" | "warning" | "info";

export interface IssueCatalogEntry {
  /** Backend rule id, e.g. "MISSING_ALT_TEXT". */
  ruleId: string;
  /** Plain-English heading shown at the top of the issue card. */
  title: string;
  /** One-sentence summary suitable for a list view. */
  summary: string;
  /** Two- or three-sentence explanation of *why* this matters. */
  why: string;
  /** What the auto-fix will do, in concrete terms. */
  autoFix: string;
  /** What manual judgment is required (if any). Empty string = fully automatic. */
  manualJudgment: string;
  /** Default severity if the backend doesn't override it. */
  severity: IssueSeverity;
  /** Standards citations grouped by spec. */
  standards: { wcag: string[]; section508: string[]; pdfUa: string[] };
  /** WCAG technique URL the user can read for more context. */
  learnMoreUrl: string;
}

const C: Record<string, IssueCatalogEntry> = {
  MISSING_ALT_TEXT: {
    ruleId: "MISSING_ALT_TEXT",
    title: "Image is missing alt text",
    summary: "An image has no description for screen readers.",
    why:
      "People who use screen readers can't see images. Alt text describes what the image shows so they can understand the meaning. Without it, the image is silently skipped.",
    autoFix:
      "We'll generate a suggested description from the image content (using vision AI when configured, otherwise a heuristic from surrounding text). You'll review and approve before it's applied.",
    manualJudgment:
      "Always review AI-generated alt text. The model can be wrong about what an image shows or what's relevant.",
    severity: "error",
    standards: {
      wcag: ["1.1.1 Non-text Content"],
      section508: ["E205.1"],
      pdfUa: ["7.1-4"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/images/",
  },

  DECORATIVE_IMAGE_WITH_ALT: {
    ruleId: "DECORATIVE_IMAGE_WITH_ALT",
    title: "Decorative image has unnecessary alt text",
    summary: "A purely decorative image is announced by screen readers.",
    why:
      "Decorative images (dividers, background flourishes) shouldn't be announced — they add noise without meaning. Alt text on decorative images forces screen readers to read out irrelevant descriptions.",
    autoFix: "We'll remove the alt text and mark the image as a decorative artifact.",
    manualJudgment: "",
    severity: "warning",
    standards: {
      wcag: ["1.1.1 Non-text Content"],
      section508: ["E205.1"],
      pdfUa: ["7.1-4"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/images/decorative/",
  },

  HEADING_LEVEL_JUMP: {
    ruleId: "HEADING_LEVEL_JUMP",
    title: "Heading skips a level",
    summary: "A heading jumps from H1 directly to H3 (or similar).",
    why:
      "Headings build a navigable outline of the document. When levels skip, screen reader users can't tell whether they've moved deeper into a sub-section or jumped past content. Always step one level at a time.",
    autoFix:
      "We'll renumber the heading to one level deeper than the previous heading. Subsequent headings stay where they are unless they also skip.",
    manualJudgment:
      "Skim the renumbered section to make sure the new level reflects the document's actual structure.",
    severity: "warning",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E207.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/page-structure/headings/",
  },

  SKIPPED_HEADING_LEVEL: {
    ruleId: "SKIPPED_HEADING_LEVEL",
    title: "Heading hierarchy is broken",
    summary: "A heading level is missing in the outline (e.g. H1 → H3 with no H2).",
    why:
      "Same problem as a heading jump: the outline becomes unparseable for assistive technology. People who navigate by heading lose their place.",
    autoFix: "We'll insert the missing intermediate level so the outline reads cleanly.",
    manualJudgment:
      "If the gap is intentional (e.g. you skipped a section), reject the auto-fix and address the source document instead.",
    severity: "warning",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E207.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/page-structure/headings/",
  },

  TABLE_MISSING_HEADERS: {
    ruleId: "TABLE_MISSING_HEADERS",
    title: "Table has no header row",
    summary: "Data table is missing header cells, so columns aren't labeled.",
    why:
      "Screen readers announce a header cell whenever the user moves to a data cell underneath it (\"Total — $4,200\"). Without headers, users hear only \"$4,200\" with no context about which column.",
    autoFix:
      "If your table's first row already looks like labels (short, no trailing punctuation), we'll promote it to header cells with column scope. Otherwise we'll insert a synthetic header row labeled \"Column 1, Column 2…\" so you can rename it.",
    manualJudgment:
      "If we synthesized headers, replace the placeholder labels with real ones. This is the most common manual edit.",
    severity: "error",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E205.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/tables/",
  },

  TABLE_HEADER_SCOPE_INVALID: {
    ruleId: "TABLE_HEADER_SCOPE_INVALID",
    title: "Header cell missing scope",
    summary: "A header cell exists but doesn't say whether it labels a column or a row.",
    why:
      "Without scope=\"col\" or scope=\"row\", screen readers have to guess which header to announce for each data cell. They often guess wrong.",
    autoFix:
      "We'll set scope=col on cells in the first row, scope=row on cells in the first column, and propagate the rest based on position.",
    manualJudgment:
      "Cells that span multiple rows and columns may need the rare scope=both — review tables with merged header cells.",
    severity: "warning",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E205.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/tables/two-headers/",
  },

  LIST_STRUCTURE_INVALID: {
    ruleId: "LIST_STRUCTURE_INVALID",
    title: "List is structurally broken",
    summary: "Items in a list aren't tagged as list items.",
    why:
      "Screen readers announce \"List of 5 items\" before reading a list, then count down each one. Without proper list-item tagging, users hear loose paragraphs and lose count.",
    autoFix: "We'll wrap any non-list-item children in proper <li> nodes so the list reads as a list.",
    manualJudgment: "",
    severity: "warning",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E207.2"],
      pdfUa: ["7.3-3"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/page-structure/content/#lists",
  },

  LINK_TEXT_NON_DESCRIPTIVE: {
    ruleId: "LINK_TEXT_NON_DESCRIPTIVE",
    title: "Link text is generic",
    summary: "Anchor reads \"click here\" or similar — no clue where it leads.",
    why:
      "Many screen reader users navigate by pulling up a list of every link on the page. \"Click here\" links become a wall of identical entries — useless. Each link should make sense out of context.",
    autoFix:
      "We'll suggest a descriptive replacement based on the link's destination URL or surrounding text. For example, \"click here\" pointing to docs.example.com becomes \"Read the example.com docs.\"",
    manualJudgment:
      "AI rewrites are guesses. Verify the new text actually describes where the link goes before approving.",
    severity: "warning",
    standards: {
      wcag: ["2.4.4 Link Purpose (In Context)"],
      section508: ["E205.4"],
      pdfUa: ["7.6-6"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tips/writing/#make-link-text-meaningful",
  },

  DOCUMENT_LANGUAGE_MISSING: {
    ruleId: "DOCUMENT_LANGUAGE_MISSING",
    title: "Document language not declared",
    summary: "No /Lang attribute set, so screen readers don't know what language to use.",
    why:
      "Modern screen readers switch voice / pronunciation based on language. Without a declared language, they fall back to the system default, which may pronounce English text as Spanish (or vice versa).",
    autoFix:
      "We'll detect the language from the document text and set it. Defaults to \"en\" if detection fails.",
    manualJudgment:
      "Multilingual documents need per-section language tags too. Auto-fix only handles the document-level setting.",
    severity: "error",
    standards: {
      wcag: ["3.1.1 Language of Page"],
      section508: ["E207.1"],
      pdfUa: ["7.2-1"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Techniques/general/G134",
  },

  DOCUMENT_TITLE_MISSING: {
    ruleId: "DOCUMENT_TITLE_MISSING",
    title: "Document has no title",
    summary: "The document's metadata title is empty.",
    why:
      "When users open a document, screen readers announce the title before anything else. Without it, users hear the file name (often a meaningless ID like \"Q3-final-v7.pdf\") or nothing at all.",
    autoFix:
      "We'll suggest a title based on the document's first heading, or the filename if there's no heading.",
    manualJudgment:
      "Confirm the suggested title actually describes the document — auto-suggestions can be too generic.",
    severity: "warning",
    standards: {
      wcag: ["2.4.2 Page Titled"],
      section508: ["E207.4"],
      pdfUa: ["7.1-2"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Techniques/general/G88",
  },

  READING_ORDER_AMBIGUOUS: {
    ruleId: "READING_ORDER_AMBIGUOUS",
    title: "Reading order is unclear",
    summary: "Content placement doesn't match a clear top-to-bottom flow.",
    why:
      "Screen readers read content in the order it appears in the file's structure tree, not the visual order. If a sidebar comes after the main text in the tree but is visually placed first, the screen reader user will hear them out of order.",
    autoFix:
      "Where we have positional data (top/left coordinates from PPTX or DOCX), we'll re-sort siblings by position. Where we don't, we leave the structure alone.",
    manualJudgment:
      "Visually rearranged docs (newsletters, infographics) often need manual structure-tree work in the source app — auto-fix is a starting point, not a finish.",
    severity: "warning",
    standards: {
      wcag: ["1.3.2 Meaningful Sequence"],
      section508: ["E207.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Understanding/meaningful-sequence.html",
  },

  HEADING_TEXT_EMPTY: {
    ruleId: "HEADING_TEXT_EMPTY",
    title: "Heading has no text",
    summary: "A heading is tagged at a level but has no readable content.",
    why:
      "Headings are how screen reader users navigate a document — they pull up a list of all headings and jump in. An empty heading announces \"Heading level 2:\" with nothing after it, leaving a landmark that goes nowhere. WCAG 2.4.6 requires headings to describe topic or purpose.",
    autoFix:
      "We'll flag for manual review — heading/caption/link-target requires human input.",
    manualJudgment:
      "Decide whether the heading should hold actual text (write it) or whether it shouldn't be a heading at all (demote it to a paragraph or remove it).",
    severity: "error",
    standards: {
      wcag: ["2.4.6 Headings and Labels", "1.3.1 Info and Relationships"],
      section508: ["E207.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/page-structure/headings/",
  },

  TABLE_CAPTION_MISSING: {
    ruleId: "TABLE_CAPTION_MISSING",
    title: "Table has no caption",
    summary: "Table is not preceded by a label or caption identifying what it shows.",
    why:
      "A short caption above a data table tells screen reader users what they're about to navigate before the row-by-row read-out begins. Without it, users land in a grid of numbers with no context. WCAG 1.3.1 expects relationships like \"this label belongs to that table\" to be programmatically determinable.",
    autoFix:
      "We'll flag for manual review — heading/caption/link-target requires human input.",
    manualJudgment:
      "Add a caption metadata property, or place a short heading/paragraph immediately above the table that describes what the table contains (e.g. \"Q3 revenue by region\").",
    severity: "warning",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E205.2"],
      pdfUa: ["7.3-5"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/tables/caption-summary/",
  },

  LINK_TARGET_BROKEN: {
    ruleId: "LINK_TARGET_BROKEN",
    title: "Link target is missing or unsafe",
    summary: "Anchor has no destination, or points to \"#\", a bare scheme, or javascript:.",
    why:
      "A link is supposed to take you somewhere. Empty hrefs, bare \"#\" placeholders, naked \"http://\" stubs, and \"javascript:\" pseudo-URLs all leave keyboard and screen reader users stuck on a control with no destination. WCAG 2.4.4 requires that the purpose of each link be clear, which assumes the link actually goes somewhere.",
    autoFix:
      "We'll flag for manual review — heading/caption/link-target requires human input.",
    manualJudgment:
      "Replace placeholder targets with the real destination URL. If the element shouldn't be a link at all (e.g. a JS-driven button), convert it to a button instead.",
    severity: "warning",
    standards: {
      wcag: ["2.4.4 Link Purpose (In Context)"],
      section508: ["E205.4"],
      pdfUa: ["7.6-6"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Understanding/link-purpose-in-context.html",
  },
};

export function lookupIssue(ruleId: string): IssueCatalogEntry {
  return C[ruleId] || unknownIssue(ruleId);
}

export function unknownIssue(ruleId: string): IssueCatalogEntry {
  return {
    ruleId,
    title: ruleId.replace(/_/g, " ").toLowerCase(),
    summary: "An accessibility rule was flagged that this UI doesn't recognize yet.",
    why: "The backend reported a rule code we don't have plain-language copy for. The auto-fix may still work — try it and review the result.",
    autoFix: "Whatever the backend's deterministic rule does for this code.",
    manualJudgment: "Review carefully — the fix is opaque from the UI's perspective.",
    severity: "info",
    standards: { wcag: [], section508: [], pdfUa: [] },
    learnMoreUrl: "",
  };
}

export const CATALOG_ENTRIES = Object.values(C);
