/**
 * Hand-crafted sample audit responses.
 *
 * Three realistic scenarios so a brand-new user can try the audit flow
 * without finding their own file.  Each sample matches the
 * :class:`PipelineResponse` shape so it drops directly into the audit screen
 * exactly the way a real backend response would.
 */

import { PipelineResponse } from "../api/client";

export interface SampleDocument {
  id: string;
  title: string;
  description: string;
  /** Approximate "what kind of document is this?" hint for the UI. */
  scenario: "clean" | "typical" | "messy";
  buildResponse: () => PipelineResponse;
}

const goodDocResponse: PipelineResponse = {
  summary: {
    documentId: "sample-clean",
    sourceFormat: "docx",
    title: "Q4 Budget Summary",
    language: "en",
    pageCount: 4,
    nodeCount: 87,
    imageCount: 2,
    tableCount: 3,
  },
  violations: [
    {
      id: "vio-img-12-missing_alt_text",
      ruleId: "MISSING_ALT_TEXT",
      severity: "error",
      description: "Image is missing alternative text.",
      nodeId: "docx-img-1",
      page: 2,
      standards: { wcag_2_1: ["1.1.1"], section_508: ["E205.1"], pdf_ua: ["7.1-4"] },
      evidence: { node_type: "image", page: 2 },
      recommendedActions: ["GENERATE_ALT_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
    },
  ],
  executions: [
    {
      actionCode: "GENERATE_ALT_TEXT",
      targetNodeId: "docx-img-1",
      status: "success",
      notes:
        "Generated alt text via heuristic (confidence 0.40). Pending human review. Text='Image 1 — chart of quarterly revenue by region'.",
    },
  ],
  score: { initialIssues: 1, fixedAutomatically: 1, pendingManual: 0, score: 92, grade: "A" },
  aiProvider: "heuristic",
};

const typicalDocResponse: PipelineResponse = {
  summary: {
    documentId: "sample-typical",
    sourceFormat: "pdf",
    title: "Annual Report 2025",
    language: "en",
    pageCount: 24,
    nodeCount: 420,
    imageCount: 9,
    tableCount: 6,
  },
  violations: [
    {
      id: "vio-doc-1-document_title_missing",
      ruleId: "DOCUMENT_TITLE_MISSING",
      severity: "warning",
      description: "Document title is missing.",
      nodeId: "doc-1",
      page: null,
      standards: { wcag_2_1: ["2.4.2"], section_508: ["E207.4"], pdf_ua: ["7.1-2"] },
      evidence: { title: null },
      recommendedActions: ["SET_DOCUMENT_TITLE", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-img-3-missing_alt_text",
      ruleId: "MISSING_ALT_TEXT",
      severity: "error",
      description: "Image is missing alternative text.",
      nodeId: "page-2-img1",
      page: 2,
      standards: { wcag_2_1: ["1.1.1"], section_508: ["E205.1"], pdf_ua: ["7.1-4"] },
      evidence: { node_type: "image", page: 2 },
      recommendedActions: ["GENERATE_ALT_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-img-7-missing_alt_text",
      ruleId: "MISSING_ALT_TEXT",
      severity: "error",
      description: "Image is missing alternative text.",
      nodeId: "page-7-img1",
      page: 7,
      standards: { wcag_2_1: ["1.1.1"], section_508: ["E205.1"], pdf_ua: ["7.1-4"] },
      evidence: { node_type: "image", page: 7 },
      recommendedActions: ["GENERATE_ALT_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-h-3-heading_level_jump",
      ruleId: "HEADING_LEVEL_JUMP",
      severity: "warning",
      description: "Heading levels jump by more than one.",
      nodeId: "page-3-h2",
      page: 3,
      standards: { wcag_2_1: ["1.3.1"], section_508: ["E207.2"], pdf_ua: ["7.3-5"] },
      evidence: { level: 4 },
      recommendedActions: ["NORMALIZE_HEADING_LEVEL"],
    },
    {
      id: "vio-tbl-2-missing_headers",
      ruleId: "TABLE_MISSING_HEADERS",
      severity: "error",
      description: "Table is missing header cells.",
      nodeId: "page-12-table1",
      page: 12,
      standards: { wcag_2_1: ["1.3.1"], section_508: ["E205.2"], pdf_ua: ["7.3-5"] },
      evidence: { node_type: "table", page: 12 },
      recommendedActions: ["ADD_TABLE_HEADERS", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-link-5-non_descriptive",
      ruleId: "LINK_TEXT_NON_DESCRIPTIVE",
      severity: "warning",
      description: "Link text is not descriptive.",
      nodeId: "page-5-link1",
      page: 5,
      standards: { wcag_2_1: ["2.4.4"], section_508: ["E205.4"], pdf_ua: ["7.6-6"] },
      evidence: { text: "click here", target: "https://example.com/report.pdf" },
      recommendedActions: ["IMPROVE_LINK_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
    },
  ],
  executions: [
    {
      actionCode: "SET_DOCUMENT_TITLE",
      targetNodeId: "doc-1",
      status: "success",
      notes:
        "Set document title from None to 'Annual Report 2025' via heuristic (0.35).",
    },
    {
      actionCode: "GENERATE_ALT_TEXT",
      targetNodeId: "page-2-img1",
      status: "success",
      notes: "Generated alt text via heuristic. Pending human review.",
    },
    {
      actionCode: "GENERATE_ALT_TEXT",
      targetNodeId: "page-7-img1",
      status: "success",
      notes: "Generated alt text via heuristic. Pending human review.",
    },
    {
      actionCode: "NORMALIZE_HEADING_LEVEL",
      targetNodeId: "page-3-h2",
      status: "success",
      notes: "Normalized heading level from 4 to 3. Rule=normalized_relative_to_previous_heading.",
    },
    {
      actionCode: "ADD_TABLE_HEADERS",
      targetNodeId: "page-12-table1",
      status: "success",
      notes: "Promoted 4 cells in row 1 to TH/scope=col.",
    },
    {
      actionCode: "IMPROVE_LINK_TEXT",
      targetNodeId: "page-5-link1",
      status: "success",
      notes: "Rewrote link text 'click here' → 'Read the example.com report' via heuristic (0.45).",
    },
  ],
  score: { initialIssues: 6, fixedAutomatically: 6, pendingManual: 0, score: 78.4, grade: "C" },
  aiProvider: "heuristic",
};

const messyDocResponse: PipelineResponse = {
  summary: {
    documentId: "sample-messy",
    sourceFormat: "pptx",
    title: "",
    language: "",
    pageCount: 18,
    nodeCount: 312,
    imageCount: 14,
    tableCount: 4,
  },
  violations: [
    {
      id: "vio-doc-1-title",
      ruleId: "DOCUMENT_TITLE_MISSING",
      severity: "warning",
      description: "Document title is missing.",
      nodeId: "doc-1",
      page: null,
      standards: { wcag_2_1: ["2.4.2"], section_508: ["E207.4"], pdf_ua: ["7.1-2"] },
      evidence: {},
      recommendedActions: ["SET_DOCUMENT_TITLE", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-doc-1-language",
      ruleId: "DOCUMENT_LANGUAGE_MISSING",
      severity: "error",
      description: "Document language is missing.",
      nodeId: "doc-1",
      page: null,
      standards: { wcag_2_1: ["3.1.1"], section_508: ["E207.1"], pdf_ua: ["7.2-1"] },
      evidence: {},
      recommendedActions: ["SET_DOCUMENT_LANGUAGE", "FLAG_FOR_MANUAL_REVIEW"],
    },
    ...Array.from({ length: 9 }, (_, i) => ({
      id: `vio-img-${i + 1}-missing_alt_text`,
      ruleId: "MISSING_ALT_TEXT" as const,
      severity: "error" as const,
      description: "Image is missing alternative text.",
      nodeId: `slide-${i + 2}-img-1`,
      page: i + 2,
      standards: { wcag_2_1: ["1.1.1"], section_508: ["E205.1"], pdf_ua: ["7.1-4"] },
      evidence: { node_type: "image", page: i + 2 },
      recommendedActions: ["GENERATE_ALT_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
    })),
    {
      id: "vio-tbl-1-missing_headers",
      ruleId: "TABLE_MISSING_HEADERS",
      severity: "error",
      description: "Table is missing header cells.",
      nodeId: "slide-5-table-1",
      page: 5,
      standards: { wcag_2_1: ["1.3.1"], section_508: ["E205.2"], pdf_ua: ["7.3-5"] },
      evidence: {},
      recommendedActions: ["ADD_TABLE_HEADERS", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-tbl-2-missing_headers",
      ruleId: "TABLE_MISSING_HEADERS",
      severity: "error",
      description: "Table is missing header cells.",
      nodeId: "slide-9-table-1",
      page: 9,
      standards: { wcag_2_1: ["1.3.1"], section_508: ["E205.2"], pdf_ua: ["7.3-5"] },
      evidence: {},
      recommendedActions: ["ADD_TABLE_HEADERS", "FLAG_FOR_MANUAL_REVIEW"],
    },
    {
      id: "vio-doc-1-reading_order",
      ruleId: "READING_ORDER_AMBIGUOUS",
      severity: "warning",
      description: "Reading order is ambiguous or inconsistent.",
      nodeId: "doc-1",
      page: null,
      standards: { wcag_2_1: ["1.3.2"], section_508: ["E207.2"], pdf_ua: ["7.3-5"] },
      evidence: {},
      recommendedActions: ["RESOLVE_READING_ORDER", "FLAG_FOR_MANUAL_REVIEW"],
    },
  ],
  executions: [
    {
      actionCode: "SET_DOCUMENT_TITLE",
      targetNodeId: "doc-1",
      status: "success",
      notes: "Set document title from None to 'Untitled Document' via heuristic (0.35).",
    },
    {
      actionCode: "SET_DOCUMENT_LANGUAGE",
      targetNodeId: "doc-1",
      status: "success",
      notes: "Set language to 'en' via heuristic (0.55).",
    },
    ...Array.from({ length: 9 }, (_, i) => ({
      actionCode: "GENERATE_ALT_TEXT",
      targetNodeId: `slide-${i + 2}-img-1`,
      status: "success" as const,
      notes: "Generated alt text via heuristic. Pending human review.",
    })),
    {
      actionCode: "ADD_TABLE_HEADERS",
      targetNodeId: "slide-5-table-1",
      status: "success",
      notes: "Inserted synthetic header row with 4 placeholder columns.",
    },
    {
      actionCode: "ADD_TABLE_HEADERS",
      targetNodeId: "slide-9-table-1",
      status: "success",
      notes: "Inserted synthetic header row with 3 placeholder columns.",
    },
    {
      actionCode: "RESOLVE_READING_ORDER",
      targetNodeId: "doc-1",
      status: "success",
      notes: "Reordered children of 4 node(s) using positional hints.",
    },
  ],
  score: { initialIssues: 14, fixedAutomatically: 14, pendingManual: 0, score: 54.2, grade: "F" },
  aiProvider: "heuristic",
};

export const SAMPLE_DOCUMENTS: SampleDocument[] = [
  {
    id: "clean",
    title: "Q4 Budget Summary",
    description: "A near-clean document. One missing alt text. Quick win.",
    scenario: "clean",
    buildResponse: () => goodDocResponse,
  },
  {
    id: "typical",
    title: "Annual Report 2025",
    description: "Realistic mid-tier doc — title, alt text, headings, link, table issues.",
    scenario: "typical",
    buildResponse: () => typicalDocResponse,
  },
  {
    id: "messy",
    title: "Quarterly Town Hall.pptx",
    description: "Lots of images with no alt, no title, no language, broken tables. The deep end.",
    scenario: "messy",
    buildResponse: () => messyDocResponse,
  },
];
