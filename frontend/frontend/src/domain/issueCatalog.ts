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
      "Word documents: we'll renumber the heading to one level deeper than the previous heading and write it into the file. PDF and PowerPoint: the renumbering is queued for manual remediation in the source document.",
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
    autoFix:
      "Word documents: we'll renumber the out-of-sequence heading so the outline steps one level at a time (we renumber rather than invent new headings). PDF and PowerPoint: queued for manual remediation.",
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
      "Word and PowerPoint: if your table's first row already looks like labels (short, no trailing punctuation), we'll promote it to a real header row in the file. Otherwise we'll insert a synthetic header row labeled \"Column 1, Column 2…\" so you can rename it. PDF tables are queued for manual remediation.",
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
      "We flag this for review — writing per-cell scope back into the file isn't supported yet, so this finding is queued for manual remediation rather than silently claimed as fixed.",
    manualJudgment:
      "In the source document, set scope=col on first-row headers and scope=row on first-column headers. Cells spanning rows and columns may need the rare scope=both.",
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
    title: "List is typed as plain text, not a real list",
    summary: "Lines typed like \"- item\" or \"1. item\" carry no list semantics.",
    why:
      "Screen readers announce \"List of 5 items\" before reading a list, then count down each one. Lines typed with a literal dash or number are read as disconnected paragraphs — no list, no item count, no structure.",
    autoFix:
      "Word and PowerPoint: fixed automatically and saved into your download — typed runs become real lists (Word numbering definitions / PowerPoint bullet formatting) with the literal markers stripped. PDF list rebuilds are queued for manual remediation instead of being silently claimed.",
    manualJudgment:
      "Check the converted list reads in the right order. In PDFs, tag items as LI/LBody.",
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
      "Word and PowerPoint: we'll rewrite the link's display text in the file with a descriptive replacement based on its destination or surrounding text — \"click here\" pointing at docs.example.com becomes \"Read the example.com docs.\" PDF link text is queued for manual remediation.",
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
      "We flag this for review — automatically re-sorting content order is risky enough to do more harm than good, so it is queued for manual remediation rather than silently claimed as fixed.",
    manualJudgment:
      "Reorder the content in the source application so the logical (tab/tag) order matches the visual order. Newsletters and infographics usually need structure-tree work in the source app.",
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

  ALT_TEXT_NOT_DESCRIPTIVE: {
    ruleId: "ALT_TEXT_NOT_DESCRIPTIVE",
    title: "Image alt text is a filename or placeholder",
    summary: "Alt text reads like \"image1.png\" or \"Picture 1\" — it describes nothing.",
    why:
      "Alt text like a filename (\"DSC_0042.jpg\") or a generic placeholder (\"Picture 1\", \"image\") passes the does-it-have-alt check but tells a screen reader user nothing about what the image shows. WCAG 1.1.1 requires the text alternative to convey the image's purpose, not just exist.",
    autoFix:
      "We'll regenerate a real description (vision AI when configured, otherwise a heuristic from surrounding text) and replace the filename/placeholder. You review and approve before it's applied.",
    manualJudgment:
      "Always review the regenerated description — the model can be wrong about what the image shows or what's relevant in context.",
    severity: "warning",
    standards: {
      wcag: ["1.1.1 Non-text Content"],
      section508: ["E205.1"],
      pdfUa: ["7.1-4"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/images/",
  },

  TEXT_STYLED_AS_HEADING: {
    ruleId: "TEXT_STYLED_AS_HEADING",
    title: "Text looks like a heading but isn't one",
    summary: "Large/bold text (or Word's Title style) that never entered the heading outline.",
    why:
      "Screen-reader users navigate by pulling up the document's heading list and jumping to a section. Text that is merely styled big and bold — the classic title page — looks like a heading to sighted readers but is invisible in that list, so whole sections effectively disappear from navigation (WCAG 1.3.1).",
    autoFix:
      "We flag this for review — promoting text to a heading requires choosing the right level (H1? H2?), which depends on the document's structure, so we don't guess.",
    manualJudgment:
      "In Word, select the text and apply a real heading style (Home → Styles → Heading 1/2/3) instead of manual bold/size formatting. Word's Title style is also not a navigational heading — use Heading 1 for the document title.",
    severity: "warning",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E207.2"],
      pdfUa: [],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/page-structure/headings/",
  },

  PDF_UNTAGGED: {
    ruleId: "PDF_UNTAGGED",
    title: "PDF has no structure tags",
    summary: "The text is readable, but screen readers get no headings, lists, or tables.",
    why:
      "An untagged PDF is the most common real-world PDF accessibility failure. The words are extractable, so it 'looks fine' — but assistive technology receives one undifferentiated text stream: no heading navigation, no list announcements, no table semantics, no reading structure (WCAG 1.3.1, PDF/UA). Most checkers don't even look.",
    autoFix:
      "We reconstruct a real structure tree directly into the remediated file: headings by font hierarchy, lists, tables (from text geometry and ruling lines), figures with alt text, and running headers/footers marked as artifacts — plus the MarkInfo, ParentTree, and XMP metadata PDF/UA expects.",
    manualJudgment:
      "Heuristic reconstruction is conservative — spot-check the remediated file's reading order and heading levels, especially for complex multi-column layouts.",
    severity: "error",
    standards: {
      wcag: ["1.3.1 Info and Relationships"],
      section508: ["E205.4"],
      pdfUa: ["7.1-2"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Techniques/pdf/PDF2",
  },

  SCANNED_DOCUMENT_NO_TEXT: {
    ruleId: "SCANNED_DOCUMENT_NO_TEXT",
    title: "PDF is a scanned image — OCR required",
    summary: "This PDF is a scanned image, not text. A screen reader cannot read any of it.",
    why:
      "When a PDF is just images of pages (scanned paper, photos of slides), the words you see are pixels — assistive technology cannot extract or read them. This is the single most-common reason a PDF that 'looks fine' is completely inaccessible. WCAG 1.1.1 requires a text alternative; WCAG 1.4.5 prefers real text over images of text.",
    autoFix:
      "On deployments with OCR enabled, approving this fix recognizes each scanned page and adds an invisible, position-matched text layer — the output becomes searchable and the structure tagger then organizes the recognized text. Where OCR isn't enabled, this is honestly queued for manual remediation instead of being claimed.",
    manualJudgment:
      "Always proofread OCR output — recognition errors on low-quality scans are common. If OCR isn't enabled here, run it yourself (Adobe Acrobat: Tools → Scan & OCR; or Tesseract), verify the text, and re-upload.",
    severity: "error",
    standards: {
      wcag: ["1.1.1 Non-text Content", "1.4.5 Images of Text"],
      section508: ["E205.1"],
      pdfUa: ["7.1-2"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Understanding/non-text-content.html",
  },

  DOCUMENT_NO_HEADINGS: {
    ruleId: "DOCUMENT_NO_HEADINGS",
    title: "Document has no headings",
    summary: "A long document with no headings at all — nothing to navigate by.",
    why:
      "Headings are how screen-reader and keyboard users skim a document and jump to the part they need. A multi-page document with zero headings forces a slow, linear read from the top with no way to orient. WCAG 2.4.6 expects headings to organize substantial content.",
    autoFix:
      "We flag this for review — deciding which lines should become headings requires human judgment about the document's structure, so there's no safe automatic fix.",
    manualJudgment:
      "In the source document, apply real heading styles (Heading 1/2/3) to section titles so the outline becomes navigable. Don't just bold text — use the actual heading styles.",
    severity: "warning",
    standards: {
      wcag: ["2.4.6 Headings and Labels", "1.3.1 Info and Relationships"],
      section508: ["E207.2"],
      pdfUa: ["7.3-1"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/tutorials/page-structure/headings/",
  },

  SLIDE_TITLE_MISSING: {
    ruleId: "SLIDE_TITLE_MISSING",
    title: "Slide has no title",
    summary: "A slide is missing a title — the primary way to navigate a deck.",
    why:
      "Screen-reader users move through a presentation by slide title, and the title placeholder is what assistive tech announces when a slide opens. A slide with no title (or with text typed into a stray text box instead of the title placeholder) leaves users unsure where they are. WCAG 2.4.2 / 1.3.1.",
    autoFix:
      "We flag this for review — a meaningful slide title depends on the slide's content, so it needs human input rather than an automatic guess.",
    manualJudgment:
      "Add a title in the slide's Title placeholder (PowerPoint's Outline view is the fastest way). If a slide is intentionally title-less, give it a title and hide it off-canvas only as a last resort.",
    severity: "error",
    standards: {
      wcag: ["2.4.2 Page Titled", "1.3.1 Info and Relationships"],
      section508: ["E205.4"],
      pdfUa: [],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Techniques/general/G91",
  },

  FORM_FIELD_UNLABELED: {
    ruleId: "FORM_FIELD_UNLABELED",
    title: "Form field has no label",
    summary: "An interactive form field has no accessible name.",
    why:
      "When a screen reader lands on an unlabeled field it announces something like \"edit text, blank\" — the user has no idea what to type. Every input needs a programmatically associated label so its purpose is clear. WCAG 3.3.2 (Labels or Instructions) and 4.1.2 (Name, Role, Value).",
    autoFix:
      "We flag this for review — the correct label depends on what the field is for, which can't be inferred reliably.",
    manualJudgment:
      "Give the field an accessible name: in a PDF set the field's tooltip (TU); in Word give the content control a Title/Tag in its properties.",
    severity: "error",
    standards: {
      wcag: ["3.3.2 Labels or Instructions", "4.1.2 Name, Role, Value"],
      section508: ["E205.4"],
      pdfUa: ["7.18-1"],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Understanding/labels-or-instructions.html",
  },

  LOW_CONTRAST_TEXT: {
    ruleId: "LOW_CONTRAST_TEXT",
    title: "Text contrast may be too low",
    summary: "Text colour may not meet the WCAG AA minimum against its background.",
    why:
      "Low-contrast text is hard to read for people with low vision, colour-vision deficiencies, or anyone on a dim screen or in bright light. WCAG 1.4.3 requires a contrast ratio of at least 4.5:1 for normal text (3:1 for large text) between the text and its background.",
    autoFix:
      "We flag this for review — recolouring text or backgrounds is a visual-design decision, so we detect the problem rather than silently changing your colours.",
    manualJudgment:
      "Darken the text or lighten the background until the ratio reaches 4.5:1 (3:1 for large/bold text). A contrast checker confirms the exact ratio. Note: contrast is only assessed where colours are explicit — inherited/themed colours and coloured-background pages still need a manual check.",
    severity: "warning",
    standards: {
      wcag: ["1.4.3 Contrast (Minimum)"],
      section508: ["E205.4"],
      pdfUa: [],
    },
    learnMoreUrl: "https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html",
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
