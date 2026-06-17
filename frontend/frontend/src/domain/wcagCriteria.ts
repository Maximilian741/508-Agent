/**
 * WCAG 2.1 success criteria (Level A + AA) and the engine's evaluation map.
 *
 * Drives the procurement-grade Accessibility Conformance Report (VPAT-style).
 * The report lists EVERY A/AA criterion. For the criteria this engine actually
 * tests (CRITERION_RULES below) it reports a real verdict derived from the
 * document's findings; every other criterion is honestly marked "Not evaluated
 * by automated testing" — we never claim conformance for something we didn't
 * check. That honesty is exactly what makes an automated report credible to an
 * auditor.
 */

export type WcagLevel = "A" | "AA";

export interface WcagCriterion {
  num: string;
  title: string;
  level: WcagLevel;
}

/** WCAG 2.1 Level A and AA success criteria, in spec order. */
export const WCAG_21_CRITERIA: WcagCriterion[] = [
  // 1. Perceivable
  { num: "1.1.1", title: "Non-text Content", level: "A" },
  { num: "1.2.1", title: "Audio-only and Video-only (Prerecorded)", level: "A" },
  { num: "1.2.2", title: "Captions (Prerecorded)", level: "A" },
  { num: "1.2.3", title: "Audio Description or Media Alternative (Prerecorded)", level: "A" },
  { num: "1.2.4", title: "Captions (Live)", level: "AA" },
  { num: "1.2.5", title: "Audio Description (Prerecorded)", level: "AA" },
  { num: "1.3.1", title: "Info and Relationships", level: "A" },
  { num: "1.3.2", title: "Meaningful Sequence", level: "A" },
  { num: "1.3.3", title: "Sensory Characteristics", level: "A" },
  { num: "1.3.4", title: "Orientation", level: "AA" },
  { num: "1.3.5", title: "Identify Input Purpose", level: "AA" },
  { num: "1.4.1", title: "Use of Color", level: "A" },
  { num: "1.4.2", title: "Audio Control", level: "A" },
  { num: "1.4.3", title: "Contrast (Minimum)", level: "AA" },
  { num: "1.4.4", title: "Resize Text", level: "AA" },
  { num: "1.4.5", title: "Images of Text", level: "AA" },
  { num: "1.4.10", title: "Reflow", level: "AA" },
  { num: "1.4.11", title: "Non-text Contrast", level: "AA" },
  { num: "1.4.12", title: "Text Spacing", level: "AA" },
  { num: "1.4.13", title: "Content on Hover or Focus", level: "AA" },
  // 2. Operable
  { num: "2.1.1", title: "Keyboard", level: "A" },
  { num: "2.1.2", title: "No Keyboard Trap", level: "A" },
  { num: "2.1.4", title: "Character Key Shortcuts", level: "A" },
  { num: "2.2.1", title: "Timing Adjustable", level: "A" },
  { num: "2.2.2", title: "Pause, Stop, Hide", level: "A" },
  { num: "2.3.1", title: "Three Flashes or Below Threshold", level: "A" },
  { num: "2.4.1", title: "Bypass Blocks", level: "A" },
  { num: "2.4.2", title: "Page Titled", level: "A" },
  { num: "2.4.3", title: "Focus Order", level: "A" },
  { num: "2.4.4", title: "Link Purpose (In Context)", level: "A" },
  { num: "2.4.5", title: "Multiple Ways", level: "AA" },
  { num: "2.4.6", title: "Headings and Labels", level: "AA" },
  { num: "2.4.7", title: "Focus Visible", level: "AA" },
  { num: "2.5.1", title: "Pointer Gestures", level: "A" },
  { num: "2.5.2", title: "Pointer Cancellation", level: "A" },
  { num: "2.5.3", title: "Label in Name", level: "A" },
  { num: "2.5.4", title: "Motion Actuation", level: "A" },
  // 3. Understandable
  { num: "3.1.1", title: "Language of Page", level: "A" },
  { num: "3.1.2", title: "Language of Parts", level: "AA" },
  { num: "3.2.1", title: "On Focus", level: "A" },
  { num: "3.2.2", title: "On Input", level: "A" },
  { num: "3.2.3", title: "Consistent Navigation", level: "AA" },
  { num: "3.2.4", title: "Consistent Identification", level: "AA" },
  { num: "3.3.1", title: "Error Identification", level: "A" },
  { num: "3.3.2", title: "Labels or Instructions", level: "A" },
  { num: "3.3.3", title: "Error Suggestion", level: "AA" },
  { num: "3.3.4", title: "Error Prevention (Legal, Financial, Data)", level: "AA" },
  // 4. Robust
  { num: "4.1.1", title: "Parsing", level: "A" },
  { num: "4.1.2", title: "Name, Role, Value", level: "A" },
  { num: "4.1.3", title: "Status Messages", level: "AA" },
];

/**
 * Which engine rule(s) test each criterion. A criterion present here is one the
 * engine evaluates; absent → reported as "Not evaluated by automated testing".
 * Kept in lock-step with src/domain/issueCatalog.ts standards mappings.
 */
export const CRITERION_RULES: Record<string, string[]> = {
  "1.1.1": [
    "MISSING_ALT_TEXT",
    "DECORATIVE_IMAGE_WITH_ALT",
    "ALT_TEXT_NOT_DESCRIPTIVE",
    "SCANNED_DOCUMENT_NO_TEXT",
  ],
  "1.3.1": [
    "HEADING_LEVEL_JUMP",
    "SKIPPED_HEADING_LEVEL",
    "TABLE_MISSING_HEADERS",
    "TABLE_HEADER_SCOPE_INVALID",
    "LIST_STRUCTURE_INVALID",
    "HEADING_TEXT_EMPTY",
    "TABLE_CAPTION_MISSING",
    "TEXT_STYLED_AS_HEADING",
    "PDF_UNTAGGED",
    "DOCUMENT_NO_HEADINGS",
    "SLIDE_TITLE_MISSING",
  ],
  "1.3.2": ["READING_ORDER_AMBIGUOUS"],
  "1.4.3": ["LOW_CONTRAST_TEXT"],
  "1.4.5": ["SCANNED_DOCUMENT_NO_TEXT"],
  "2.4.2": ["DOCUMENT_TITLE_MISSING", "SLIDE_TITLE_MISSING"],
  "2.4.4": ["LINK_TEXT_NON_DESCRIPTIVE", "LINK_TARGET_BROKEN"],
  "2.4.6": ["HEADING_TEXT_EMPTY", "DOCUMENT_NO_HEADINGS"],
  "3.1.1": ["DOCUMENT_LANGUAGE_MISSING"],
  "3.3.2": ["FORM_FIELD_UNLABELED"],
  "4.1.2": ["FORM_FIELD_UNLABELED"],
};

export type ConformanceStatus =
  | "Supports"
  | "Partially Supports"
  | "Does Not Support"
  | "Not Evaluated";

/**
 * Criteria the engine only tests a SUBSET of. A clean automated result for
 * these does not justify a bare "Supports" attestation (e.g. 1.3.1 covers far
 * more than the headings/tables/lists the engine inspects), so the report
 * marks them as partial-coverage and the remark says so explicitly — closing
 * the "overclaim by tone" gap an auditor would flag.
 */
export const PARTIAL_COVERAGE_CRITERIA: Record<string, string> = {
  "1.1.1": "images checked for alt text",
  "1.3.1": "headings, tables, lists and document structure checked",
  "1.3.2": "PowerPoint shape order checked",
  "2.4.4": "link text checked",
  "2.4.6": "heading presence and labels checked",
};

export interface CriterionVerdict {
  num: string;
  title: string;
  level: WcagLevel;
  status: ConformanceStatus;
  remark: string;
  evaluated: boolean;
  /** True when the engine only assesses part of this criterion (see remark). */
  partialCoverage: boolean;
}

interface FindingLike {
  ruleId: string;
  severity: string; // "error" | "warning" | "info"
}

/**
 * Compute a per-criterion verdict from the document's findings.
 *
 * - Criteria the engine doesn't test → "Not Evaluated" (honest).
 * - Tested, no finding → "Supports".
 * - Tested, an error-severity finding → "Does Not Support".
 * - Tested, only warnings/info → "Partially Supports".
 */
export function computeConformance(findings: FindingLike[]): CriterionVerdict[] {
  return WCAG_21_CRITERIA.map((c) => {
    const rules = CRITERION_RULES[c.num];
    const partialCoverage = c.num in PARTIAL_COVERAGE_CRITERIA;
    if (!rules || rules.length === 0) {
      return {
        ...c,
        evaluated: false,
        partialCoverage: false,
        status: "Not Evaluated" as const,
        remark: "Not assessed by automated analysis. Requires manual review.",
      };
    }
    const matching = findings.filter((f) => rules.includes(f.ruleId));
    if (matching.length === 0) {
      // Clean — but for partial-coverage criteria say exactly what we checked
      // so a green "Supports" is never read as a full attestation.
      const remark = partialCoverage
        ? `No issues found in the automated checks (${PARTIAL_COVERAGE_CRITERIA[c.num]}); other aspects of this criterion require manual review.`
        : "No issues detected for this criterion in the automated scan.";
      return { ...c, evaluated: true, partialCoverage, status: "Supports" as const, remark };
    }
    const errors = matching.filter((m) => m.severity === "error").length;
    const others = matching.length - errors;
    const status: ConformanceStatus = errors > 0 ? "Does Not Support" : "Partially Supports";
    const parts: string[] = [];
    if (errors > 0) parts.push(`${errors} error-level issue${errors === 1 ? "" : "s"}`);
    if (others > 0) parts.push(`${others} advisory issue${others === 1 ? "" : "s"}`);
    return {
      ...c,
      evaluated: true,
      partialCoverage,
      status,
      remark: `${matching.length} finding${matching.length === 1 ? "" : "s"} (${parts.join(", ")}).`,
    };
  });
}

export interface ConformanceTotals {
  supports: number;
  partial: number;
  unsupported: number;
  notEvaluated: number;
  evaluatedTotal: number;
}

export function conformanceTotals(verdicts: CriterionVerdict[]): ConformanceTotals {
  const supports = verdicts.filter((v) => v.status === "Supports").length;
  const partial = verdicts.filter((v) => v.status === "Partially Supports").length;
  const unsupported = verdicts.filter((v) => v.status === "Does Not Support").length;
  const notEvaluated = verdicts.filter((v) => v.status === "Not Evaluated").length;
  return {
    supports,
    partial,
    unsupported,
    notEvaluated,
    evaluatedTotal: supports + partial + unsupported,
  };
}
