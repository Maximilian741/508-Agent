/**
 * OpenACR (machine-readable VPAT/ACR) export.
 *
 * Serializes the engine's per-WCAG-criterion verdicts (from ./wcagCriteria) into
 * the GSA OpenACR JSON shape (https://github.com/GSA/openacr) so agencies,
 * auditors, and procurement systems can ingest the conformance result directly
 * instead of re-typing from the HTML report.
 *
 * This is a PURE re-serialization of verdicts the engine already computed and
 * already shows in the HTML conformance report — no new detection, no scoring,
 * no charging. The honesty contract is preserved exactly: criteria the engine
 * does not test pass through as "not-evaluated" with their existing honest
 * remark, never as a positive attestation. Deterministic: two exports of the
 * same input are identical except for an explicitly-passed generatedAt.
 */

import {
  BatchCriterionVerdict,
  ConformanceStatus,
  CriterionVerdict,
} from "./wcagCriteria";

/** OpenACR adherence level strings. */
export type OpenAcrLevel =
  | "supports"
  | "partially-supports"
  | "does-not-support"
  | "not-evaluated";

export function acrLevelFromStatus(status: ConformanceStatus): OpenAcrLevel {
  switch (status) {
    case "Supports":
      return "supports";
    case "Partially Supports":
      return "partially-supports";
    case "Does Not Support":
      return "does-not-support";
    default:
      return "not-evaluated";
  }
}

function _formatDescription(filename: string): string {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  const label: Record<string, string> = {
    pdf: "PDF document",
    docx: "Word document",
    pptx: "PowerPoint presentation",
    html: "HTML document",
    htm: "HTML document",
  };
  return label[ext] || "Electronic document";
}

interface AcrCriterion {
  num: string;
  components: { name: string; adherence: { level: OpenAcrLevel; notes: string } }[];
}

function _criterion(v: CriterionVerdict, notes: string): AcrCriterion {
  // This product remediates documents, so the relevant OpenACR component is
  // "electronic-docs".
  return {
    num: v.num,
    components: [
      { name: "electronic-docs", adherence: { level: acrLevelFromStatus(v.status), notes } },
    ],
  };
}

function _chapters(
  verdicts: CriterionVerdict[],
  noteFor: (v: CriterionVerdict) => string,
): Record<string, unknown> {
  const a = verdicts.filter((v) => v.level === "A").map((v) => _criterion(v, noteFor(v)));
  const aa = verdicts.filter((v) => v.level === "AA").map((v) => _criterion(v, noteFor(v)));
  return {
    success_criteria_level_a: { notes: "", disabled: false, criteria: a },
    success_criteria_level_aa: { notes: "", disabled: false, criteria: aa },
  };
}

const _DISCLAIMER =
  "Automated accessibility analysis by 508 Agent. This is an automated remediation summary, " +
  "not a formal third-party conformance determination. Success criteria shown as 'not-evaluated' " +
  "are not assessed by automated tooling and require manual review by a qualified evaluator.";

export interface BuildOpenAcrOpts {
  filename: string;
  verdicts: CriterionVerdict[];
  scoreNum?: number;
  scoreGrade?: string;
  /** True when the verdicts describe the remediated (re-scanned) file. */
  assessedFixedFile?: boolean;
  /** ISO timestamp; passed in so the output is deterministic/testable. */
  generatedAt?: string;
}

/** Build an OpenACR JSON object for a single document. */
export function buildOpenAcr(opts: BuildOpenAcrOpts): Record<string, unknown> {
  const { filename, verdicts, scoreNum, scoreGrade, assessedFixedFile, generatedAt } = opts;
  const date = (generatedAt || "").slice(0, 10);
  const scoreNote =
    typeof scoreNum === "number"
      ? ` Automated score: ${Math.round(scoreNum)}${scoreGrade ? ` (${scoreGrade})` : ""}.`
      : "";
  return {
    title: `${filename} Accessibility Conformance Report`,
    product: {
      name: filename,
      version: "",
      description: _formatDescription(filename),
    },
    author: { name: "508 Agent", company_name: "508 Agent" },
    vendor: { name: "", company_name: "", email: "" },
    report_date: date,
    last_modified_date: date,
    notes:
      (assessedFixedFile
        ? "Reflects the remediated document (re-analyzed after fixes were applied)."
        : "Reflects the document as analyzed.") + scoreNote,
    evaluation_methods_used: "Automated analysis by 508 Agent.",
    legal_disclaimer: _DISCLAIMER,
    related_openacr: [],
    standards: ["WCAG-2.1"],
    catalog: "WCAG 2.1 (Level A and AA)",
    chapters: _chapters(verdicts, (v) => v.remark),
  };
}

export interface BuildBatchOpenAcrOpts {
  verdicts: BatchCriterionVerdict[];
  documentCount: number;
  projectName?: string;
  generatedAt?: string;
}

/** Build a consolidated OpenACR JSON object across a batch of documents. */
export function buildBatchOpenAcr(opts: BuildBatchOpenAcrOpts): Record<string, unknown> {
  const { verdicts, documentCount, projectName, generatedAt } = opts;
  const date = (generatedAt || "").slice(0, 10);
  const name = projectName || `Document set (${documentCount} document${documentCount === 1 ? "" : "s"})`;
  return {
    title: `${name} Accessibility Conformance Report`,
    product: {
      name,
      version: "",
      description: `Consolidated report across ${documentCount} document${documentCount === 1 ? "" : "s"}. Each criterion reflects the worst result across the set.`,
    },
    author: { name: "508 Agent", company_name: "508 Agent" },
    vendor: { name: "", company_name: "", email: "" },
    report_date: date,
    last_modified_date: date,
    notes: `Consolidated across ${documentCount} document${documentCount === 1 ? "" : "s"}; a criterion's level is the worst result found in the set.`,
    evaluation_methods_used: "Automated analysis by 508 Agent.",
    legal_disclaimer: _DISCLAIMER,
    related_openacr: [],
    standards: ["WCAG-2.1"],
    catalog: "WCAG 2.1 (Level A and AA)",
    chapters: _chapters(verdicts, (v) => {
      const bv = v as BatchCriterionVerdict;
      if (bv.evaluated && bv.docsAffected > 0) {
        return `${bv.remark} Affects ${bv.docsAffected} of ${bv.totalDocs} document${bv.totalDocs === 1 ? "" : "s"}.`;
      }
      return bv.remark;
    }),
  };
}
