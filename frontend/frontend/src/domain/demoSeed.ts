/**
 * Demo data seeder.
 *
 * Loads a believable batch of audit history, achievements, workspaces, and
 * manual-review items so a brand-new install isn't a sea of empty states.
 *
 * Everything written here is **scoped to localStorage** and clearly labelled
 * "demo" so the user can wipe it with one click.  Nothing here ever touches
 * the network or the backend.
 *
 * Trigger surface: a "Load demo data" button on Settings — and a matching
 * "Clear demo data" that removes only the seeded rows (not the user's real
 * work, since real audits carry their own filename / id space).
 */

import { Platform } from "react-native";

import { PipelineResponse } from "../api/client";
import {
  ACHIEVEMENT_DEFS,
  unlockAchievement,
  clearAchievements as clearAchievementsRaw,
} from "./achievements";
import {
  AuditHistoryEntry,
  appendHistory,
  loadHistory,
  saveHistory,
} from "./auditHistory";
import {
  Workspace,
  createWorkspace,
  loadWorkspaces,
  saveWorkspaces,
} from "./workspaces";

/** Marker so we can selectively clear only the demo entries later. */
const DEMO_TAG = "demo:";

/* ------------------------------------------------------------------ *
 * Pre-baked PipelineResponse fixtures                                 *
 *                                                                     *
 * Each one is a full, restorable snapshot — that means the demo       *
 * "Recent audits" entries on the dashboard can be opened and walked   *
 * through end-to-end, just like a real audit.                         *
 * ------------------------------------------------------------------ */

interface DemoFixture {
  filename: string;
  format: "pdf" | "docx" | "pptx";
  ranAt: string; // ISO
  approved: number;
  rejected: number;
  pending: number;
  score: number;
  grade: string;
  build: () => PipelineResponse;
}

function mkAlt(id: string, page: number, node: string): any {
  return {
    id,
    ruleId: "MISSING_ALT_TEXT",
    severity: "error",
    description: "Image is missing alternative text.",
    nodeId: node,
    page,
    standards: { wcag_2_1: ["1.1.1"], section_508: ["E205.1"], pdf_ua: ["7.1-4"] },
    evidence: { node_type: "image", page },
    recommendedActions: ["GENERATE_ALT_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
  };
}

function mkExec(action: string, node: string, notes: string): any {
  return { actionCode: action, targetNodeId: node, status: "success", notes };
}

function fixtureMarketingDeck(): PipelineResponse {
  return {
    summary: {
      documentId: "demo-marketing",
      sourceFormat: "pptx",
      title: "Spring Campaign Kickoff",
      language: "en",
      pageCount: 22,
      nodeCount: 380,
      imageCount: 11,
      tableCount: 3,
    },
    violations: [
      mkAlt("demo-mk-img-1", 2, "slide-2-img-1"),
      mkAlt("demo-mk-img-2", 4, "slide-4-img-2"),
      mkAlt("demo-mk-img-3", 7, "slide-7-img-1"),
      {
        id: "demo-mk-link-1",
        ruleId: "LINK_TEXT_NON_DESCRIPTIVE",
        severity: "warning",
        description: "Link text is not descriptive.",
        nodeId: "slide-9-link-1",
        page: 9,
        standards: { wcag_2_1: ["2.4.4"], section_508: ["E205.4"], pdf_ua: ["7.6-6"] },
        evidence: { text: "more info", target: "https://example.com/spring" },
        recommendedActions: ["IMPROVE_LINK_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
      },
      {
        id: "demo-mk-tbl-1",
        ruleId: "TABLE_MISSING_HEADERS",
        severity: "error",
        description: "Table is missing header cells.",
        nodeId: "slide-12-table-1",
        page: 12,
        standards: { wcag_2_1: ["1.3.1"], section_508: ["E205.2"], pdf_ua: ["7.3-5"] },
        evidence: { node_type: "table" },
        recommendedActions: ["ADD_TABLE_HEADERS", "FLAG_FOR_MANUAL_REVIEW"],
      },
    ],
    executions: [
      mkExec("GENERATE_ALT_TEXT", "slide-2-img-1", "Generated alt text via heuristic. Pending human review."),
      mkExec("GENERATE_ALT_TEXT", "slide-4-img-2", "Generated alt text via heuristic. Pending human review."),
      mkExec("GENERATE_ALT_TEXT", "slide-7-img-1", "Generated alt text via heuristic. Pending human review."),
      mkExec("IMPROVE_LINK_TEXT", "slide-9-link-1", "Rewrote link text 'more info' → 'Read the Spring campaign brief'."),
      mkExec("ADD_TABLE_HEADERS", "slide-12-table-1", "Promoted 4 cells in row 1 to TH/scope=col."),
    ],
    score: { initialIssues: 5, fixedAutomatically: 5, pendingManual: 0, score: 84.0, grade: "B" },
    aiProvider: "heuristic",
  };
}

function fixtureAnnualReport(): PipelineResponse {
  return {
    summary: {
      documentId: "demo-annual",
      sourceFormat: "pdf",
      title: "Annual Report 2025",
      language: "en",
      pageCount: 48,
      nodeCount: 920,
      imageCount: 18,
      tableCount: 11,
    },
    violations: [
      {
        id: "demo-ar-doc-title",
        ruleId: "DOCUMENT_TITLE_MISSING",
        severity: "warning",
        description: "Document title is missing.",
        nodeId: "doc-1",
        page: null,
        standards: { wcag_2_1: ["2.4.2"], section_508: ["E207.4"], pdf_ua: ["7.1-2"] },
        evidence: {},
        recommendedActions: ["SET_DOCUMENT_TITLE", "FLAG_FOR_MANUAL_REVIEW"],
      },
      mkAlt("demo-ar-img-2", 4, "page-4-img-1"),
      mkAlt("demo-ar-img-7", 11, "page-11-img-2"),
      mkAlt("demo-ar-img-9", 17, "page-17-img-1"),
      {
        id: "demo-ar-h-1",
        ruleId: "HEADING_LEVEL_JUMP",
        severity: "warning",
        description: "Heading levels jump by more than one.",
        nodeId: "page-3-h",
        page: 3,
        standards: { wcag_2_1: ["1.3.1"], section_508: ["E207.2"], pdf_ua: ["7.3-5"] },
        evidence: { level: 4 },
        recommendedActions: ["NORMALIZE_HEADING_LEVEL"],
      },
      {
        id: "demo-ar-tbl-1",
        ruleId: "TABLE_MISSING_HEADERS",
        severity: "error",
        description: "Table is missing header cells.",
        nodeId: "page-22-table-1",
        page: 22,
        standards: { wcag_2_1: ["1.3.1"], section_508: ["E205.2"], pdf_ua: ["7.3-5"] },
        evidence: {},
        recommendedActions: ["ADD_TABLE_HEADERS", "FLAG_FOR_MANUAL_REVIEW"],
      },
    ],
    executions: [
      mkExec("SET_DOCUMENT_TITLE", "doc-1", "Set title to 'Annual Report 2025' via heuristic (0.62)."),
      mkExec("GENERATE_ALT_TEXT", "page-4-img-1", "Generated alt text via heuristic."),
      mkExec("GENERATE_ALT_TEXT", "page-11-img-2", "Generated alt text via heuristic."),
      mkExec("GENERATE_ALT_TEXT", "page-17-img-1", "Generated alt text via heuristic."),
      mkExec("NORMALIZE_HEADING_LEVEL", "page-3-h", "Normalized heading level from 4 to 3."),
      mkExec("ADD_TABLE_HEADERS", "page-22-table-1", "Promoted 5 cells in row 1 to TH/scope=col."),
    ],
    score: { initialIssues: 6, fixedAutomatically: 6, pendingManual: 0, score: 81.5, grade: "B" },
    aiProvider: "heuristic",
  };
}

function fixtureTownHall(): PipelineResponse {
  return {
    summary: {
      documentId: "demo-townhall",
      sourceFormat: "pptx",
      title: "",
      language: "",
      pageCount: 14,
      nodeCount: 240,
      imageCount: 9,
      tableCount: 2,
    },
    violations: [
      {
        id: "demo-th-doc-title",
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
        id: "demo-th-doc-lang",
        ruleId: "DOCUMENT_LANGUAGE_MISSING",
        severity: "error",
        description: "Document language is missing.",
        nodeId: "doc-1",
        page: null,
        standards: { wcag_2_1: ["3.1.1"], section_508: ["E207.1"], pdf_ua: ["7.2-1"] },
        evidence: {},
        recommendedActions: ["SET_DOCUMENT_LANGUAGE", "FLAG_FOR_MANUAL_REVIEW"],
      },
      ...Array.from({ length: 7 }, (_, i) =>
        mkAlt(`demo-th-img-${i + 1}`, i + 2, `slide-${i + 2}-img-1`),
      ),
    ],
    executions: [
      mkExec("SET_DOCUMENT_TITLE", "doc-1", "Set title to 'Town Hall' via heuristic (0.40)."),
      mkExec("SET_DOCUMENT_LANGUAGE", "doc-1", "Set language to 'en' via heuristic (0.55)."),
      ...Array.from({ length: 7 }, (_, i) =>
        mkExec("GENERATE_ALT_TEXT", `slide-${i + 2}-img-1`, "Generated alt text via heuristic."),
      ),
    ],
    score: { initialIssues: 9, fixedAutomatically: 9, pendingManual: 0, score: 64.2, grade: "D" },
    aiProvider: "heuristic",
  };
}

function fixtureCleanBudget(): PipelineResponse {
  return {
    summary: {
      documentId: "demo-budget",
      sourceFormat: "docx",
      title: "Q1 Operations Budget",
      language: "en",
      pageCount: 6,
      nodeCount: 110,
      imageCount: 1,
      tableCount: 4,
    },
    violations: [mkAlt("demo-bd-img-1", 3, "docx-img-1")],
    executions: [
      mkExec("GENERATE_ALT_TEXT", "docx-img-1", "Generated alt text via heuristic. Pending human review."),
    ],
    score: { initialIssues: 1, fixedAutomatically: 1, pendingManual: 0, score: 96.0, grade: "A" },
    aiProvider: "heuristic",
  };
}

/**
 * Small DOCX with a single contrast violation — represents the kind of
 * one-off marketing flyer where the only blocker is brand-team color choices.
 */
function fixtureContrastFlyer(): PipelineResponse {
  return {
    summary: {
      documentId: "demo-flyer",
      sourceFormat: "docx",
      title: "Wellness Week Flyer",
      language: "en",
      pageCount: 2,
      nodeCount: 64,
      imageCount: 2,
      tableCount: 0,
    },
    violations: [
      {
        id: "demo-fl-contrast-1",
        ruleId: "LOW_CONTRAST_TEXT",
        severity: "error",
        description: "Text color contrast is below the 4.5:1 minimum.",
        nodeId: "docx-p-7",
        page: 1,
        standards: { wcag_2_1: ["1.4.3"], section_508: ["E207.1"], pdf_ua: ["7.1-1"] },
        evidence: { foreground: "#9aa0a6", background: "#ffffff", ratio: 2.85 },
        recommendedActions: ["INCREASE_TEXT_CONTRAST", "FLAG_FOR_MANUAL_REVIEW"],
      },
      mkAlt("demo-fl-img-1", 1, "docx-img-1"),
    ],
    executions: [
      mkExec(
        "INCREASE_TEXT_CONTRAST",
        "docx-p-7",
        "Darkened body color from #9aa0a6 to #4a4f55 (ratio 7.2:1).",
      ),
      mkExec("GENERATE_ALT_TEXT", "docx-img-1", "Generated alt text via heuristic."),
    ],
    score: { initialIssues: 2, fixedAutomatically: 2, pendingManual: 0, score: 91.5, grade: "A" },
    aiProvider: "heuristic",
  };
}

/**
 * 100+ page PDF stub with a long tail of heading-jump issues.  Mirrors what a
 * sloppy product-doc export from Confluence tends to look like.
 */
function fixtureHandbookPdf(): PipelineResponse {
  const headingPages = [4, 9, 17, 23, 31, 38, 49, 56, 64, 71, 82, 95, 108, 119];
  return {
    summary: {
      documentId: "demo-handbook",
      sourceFormat: "pdf",
      title: "Engineering Handbook v3",
      language: "en",
      pageCount: 124,
      nodeCount: 2840,
      imageCount: 22,
      tableCount: 17,
    },
    violations: [
      ...headingPages.map((p, i) => ({
        id: `demo-hb-h-${i + 1}`,
        ruleId: "HEADING_LEVEL_JUMP",
        severity: "warning" as const,
        description: "Heading levels jump by more than one.",
        nodeId: `page-${p}-h`,
        page: p,
        standards: { wcag_2_1: ["1.3.1"], section_508: ["E207.2"], pdf_ua: ["7.3-5"] },
        evidence: { level: i % 2 === 0 ? 4 : 5 },
        recommendedActions: ["NORMALIZE_HEADING_LEVEL"],
      })),
      mkAlt("demo-hb-img-1", 12, "page-12-img-1"),
      mkAlt("demo-hb-img-2", 47, "page-47-img-2"),
      {
        id: "demo-hb-tbl-1",
        ruleId: "TABLE_MISSING_HEADERS",
        severity: "error",
        description: "Table is missing header cells.",
        nodeId: "page-88-table-1",
        page: 88,
        standards: { wcag_2_1: ["1.3.1"], section_508: ["E205.2"], pdf_ua: ["7.3-5"] },
        evidence: {},
        recommendedActions: ["ADD_TABLE_HEADERS", "FLAG_FOR_MANUAL_REVIEW"],
      },
    ],
    executions: [
      ...headingPages.map((p) =>
        mkExec("NORMALIZE_HEADING_LEVEL", `page-${p}-h`, `Normalized heading level on page ${p}.`),
      ),
      mkExec("GENERATE_ALT_TEXT", "page-12-img-1", "Generated alt text via heuristic."),
      mkExec("GENERATE_ALT_TEXT", "page-47-img-2", "Generated alt text via heuristic."),
      mkExec("ADD_TABLE_HEADERS", "page-88-table-1", "Promoted 6 cells in row 1 to TH/scope=col."),
    ],
    score: { initialIssues: 17, fixedAutomatically: 17, pendingManual: 0, score: 58.3, grade: "F" },
    aiProvider: "heuristic",
  };
}

/**
 * One-pager PPTX — single slide with two small issues. Quick win.
 */
function fixtureOnePagerPptx(): PipelineResponse {
  return {
    summary: {
      documentId: "demo-onepager",
      sourceFormat: "pptx",
      title: "All-Hands Recap",
      language: "en",
      pageCount: 1,
      nodeCount: 38,
      imageCount: 2,
      tableCount: 0,
    },
    violations: [
      mkAlt("demo-op-img-1", 1, "slide-1-img-1"),
      {
        id: "demo-op-link-1",
        ruleId: "LINK_TEXT_NON_DESCRIPTIVE",
        severity: "warning",
        description: "Link text is not descriptive.",
        nodeId: "slide-1-link-1",
        page: 1,
        standards: { wcag_2_1: ["2.4.4"], section_508: ["E205.4"], pdf_ua: ["7.6-6"] },
        evidence: { text: "here", target: "https://intranet.example.com/recap" },
        recommendedActions: ["IMPROVE_LINK_TEXT", "FLAG_FOR_MANUAL_REVIEW"],
      },
    ],
    executions: [
      mkExec("GENERATE_ALT_TEXT", "slide-1-img-1", "Generated alt text via heuristic."),
      mkExec(
        "IMPROVE_LINK_TEXT",
        "slide-1-link-1",
        "Rewrote link text 'here' → 'View the All-Hands recap on the intranet'.",
      ),
    ],
    score: { initialIssues: 2, fixedAutomatically: 2, pendingManual: 0, score: 93.2, grade: "A" },
    aiProvider: "heuristic",
  };
}

function daysAgoIso(days: number, hour = 10, minute = 0): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  d.setHours(hour, minute, 0, 0);
  return d.toISOString();
}

const FIXTURES: DemoFixture[] = [
  {
    filename: "Q1 Operations Budget.docx",
    format: "docx",
    ranAt: daysAgoIso(0, 11, 12),
    approved: 1,
    rejected: 0,
    pending: 0,
    score: 96.0,
    grade: "A",
    build: fixtureCleanBudget,
  },
  {
    filename: "Spring Campaign Kickoff.pptx",
    format: "pptx",
    ranAt: daysAgoIso(1, 14, 8),
    approved: 4,
    rejected: 1,
    pending: 0,
    score: 84.0,
    grade: "B",
    build: fixtureMarketingDeck,
  },
  {
    filename: "Annual Report 2025.pdf",
    format: "pdf",
    ranAt: daysAgoIso(2, 9, 47),
    approved: 5,
    rejected: 1,
    pending: 0,
    score: 81.5,
    grade: "B",
    build: fixtureAnnualReport,
  },
  {
    filename: "Q4 Town Hall.pptx",
    format: "pptx",
    ranAt: daysAgoIso(4, 16, 30),
    approved: 6,
    rejected: 1,
    pending: 2,
    score: 64.2,
    grade: "D",
    build: fixtureTownHall,
  },
  {
    filename: "Wellness Week Flyer.docx",
    format: "docx",
    ranAt: daysAgoIso(5, 11, 3),
    approved: 2,
    rejected: 0,
    pending: 0,
    score: 91.5,
    grade: "A",
    build: fixtureContrastFlyer,
  },
  {
    filename: "All-Hands Recap.pptx",
    format: "pptx",
    ranAt: daysAgoIso(6, 9, 18),
    approved: 2,
    rejected: 0,
    pending: 0,
    score: 93.2,
    grade: "A",
    build: fixtureOnePagerPptx,
  },
  {
    filename: "Onboarding Handbook.pdf",
    format: "pdf",
    ranAt: daysAgoIso(7, 13, 55),
    approved: 5,
    rejected: 0,
    pending: 0,
    score: 88.7,
    grade: "B",
    build: fixtureMarketingDeck,
  },
  {
    filename: "Engineering Handbook v3.pdf",
    format: "pdf",
    ranAt: daysAgoIso(9, 14, 40),
    approved: 12,
    rejected: 3,
    pending: 2,
    score: 58.3,
    grade: "F",
    build: fixtureHandbookPdf,
  },
  {
    filename: "Investor Update Jan.pdf",
    format: "pdf",
    ranAt: daysAgoIso(11, 10, 5),
    approved: 4,
    rejected: 2,
    pending: 0,
    score: 76.0,
    grade: "C",
    build: fixtureAnnualReport,
  },
  {
    filename: "Brand Refresh Deck.pptx",
    format: "pptx",
    ranAt: daysAgoIso(16, 15, 22),
    approved: 3,
    rejected: 1,
    pending: 1,
    score: 71.4,
    grade: "C",
    build: fixtureMarketingDeck,
  },
];

function buildHistoryEntry(f: DemoFixture, index: number): AuditHistoryEntry {
  const report = f.build();
  // Stable, demo-prefixed id so we can cleanup later.
  const id = `${DEMO_TAG}${index}-${f.format}-${f.filename
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 40)}`;
  // Construct fake decisions matching the totals.
  const totalIssues = report.violations.length;
  const decisions: Record<string, any> = {};
  for (let i = 0; i < totalIssues; i++) {
    const v = report.violations[i];
    let decision: "approved" | "rejected" | "pending" = "pending";
    if (i < f.approved) decision = "approved";
    else if (i < f.approved + f.rejected) decision = "rejected";
    decisions[v.id] = {
      decision,
      decidedAt: decision === "pending" ? undefined : f.ranAt,
    };
  }
  return {
    id,
    filename: f.filename,
    ranAt: f.ranAt,
    score: f.score,
    grade: f.grade,
    totalIssues,
    sourceFormat: f.format,
    approved: f.approved,
    rejected: f.rejected,
    pending: f.pending,
    snapshot: {
      report,
      decisions,
      decisionLog: [],
    },
  };
}

/**
 * Mint a "Marketing audits" workspace beside Personal so the dropdown has
 * something interesting to switch to.
 */
function ensureDemoWorkspaces(): Workspace[] {
  const all = loadWorkspaces();
  const hasMarketing = all.some((w) => w.name.toLowerCase().includes("marketing"));
  if (!hasMarketing) {
    createWorkspace("Marketing audits");
  }
  const hasCompliance = loadWorkspaces().some((w) =>
    w.name.toLowerCase().includes("compliance"),
  );
  if (!hasCompliance) {
    createWorkspace("Compliance review");
  }
  return loadWorkspaces();
}

/**
 * Public — wipe and rewrite history with a curated demo set.
 *
 * `mode: "merge"` keeps the user's real entries and prepends the demo set;
 * `mode: "replace"` drops everything in the active workspace first.  Default
 * is merge so we never destroy real work.
 */
export function loadDemoData(opts?: { mode?: "merge" | "replace" }): {
  historyAdded: number;
  achievementsUnlocked: number;
  workspacesCreated: number;
} {
  const mode = opts?.mode ?? "merge";
  const before = loadHistory();
  if (mode === "replace") {
    saveHistory([]);
  }

  let added = 0;
  // Insert oldest → newest so most recent ends up on top after appendHistory.
  const ordered = [...FIXTURES].sort(
    (a, b) => new Date(a.ranAt).getTime() - new Date(b.ranAt).getTime(),
  );
  ordered.forEach((f, i) => {
    const entry = buildHistoryEntry(f, i);
    appendHistory(entry);
    added += 1;
  });

  // Cumulative violation count across the demo set — used to decide whether
  // the "hundred issues" achievement makes sense to unlock alongside.
  const cumulativeViolations = FIXTURES.reduce(
    (sum, f) => sum + f.approved + f.rejected + f.pending,
    0,
  );

  // Unlock the big-ticket achievements that line up with the demo run.
  // The demo spans 16 days, which more than covers the three-day streak;
  // hundred_issues only fires when the seeded violations actually reach 100.
  const baseAchievements = [
    "first_audit",
    "first_fix_approved",
    "first_pdf",
    "first_pptx",
    "first_docx",
    "score_ninety_plus",
    "ten_audits",
    "three_day_streak",
  ];
  if (cumulativeViolations >= 100) {
    baseAchievements.push("hundred_issues");
  }

  let unlocked = 0;
  for (const id of baseAchievements) {
    if (unlockAchievement(id)) unlocked += 1;
  }

  const wsBefore = loadWorkspaces().length;
  const wsAfter = ensureDemoWorkspaces().length;

  return {
    historyAdded: added,
    achievementsUnlocked: unlocked,
    workspacesCreated: Math.max(0, wsAfter - wsBefore),
  };
}

/**
 * Remove only the demo rows from the active workspace.  Real audits the user
 * ran themselves stay put.  Workspaces and achievements are untouched (the
 * user may have built on top of them).
 */
export function clearDemoData(): { removed: number } {
  const all = loadHistory();
  const kept = all.filter((e) => !e.id.startsWith(DEMO_TAG));
  saveHistory(kept);
  return { removed: all.length - kept.length };
}

/**
 * Aggressive reset — used by the dev "Replay tour" affordance.  Wipes demo
 * history, resets achievements to locked, and removes any seeded workspaces
 * (those with names that match our seed set).
 */
export function nukeDemoData(): void {
  clearDemoData();
  if (Platform.OS === "web") {
    clearAchievementsRaw();
  }
  // Drop seeded workspaces that the user hasn't renamed.
  try {
    const seeded = new Set(["Marketing audits", "Compliance review"]);
    const ws = loadWorkspaces();
    const kept = ws.filter((w) => !seeded.has(w.name));
    if (kept.length !== ws.length && kept.length > 0) {
      saveWorkspaces(kept);
    }
  } catch {
    // ignore
  }
}

/** Returns true if the active workspace currently has any demo rows. */
export function hasDemoData(): boolean {
  return loadHistory().some((e) => e.id.startsWith(DEMO_TAG));
}

/** Total number of catalog achievements — exposed so the UI can size things. */
export const TOTAL_DEMO_ACHIEVEMENTS = ACHIEVEMENT_DEFS.length;
