/**
 * Persist & restore the active audit's working state.
 *
 * The full PipelineResponse can be large, but it's still cheap to serialize
 * for an in-progress audit — and "I refreshed and lost everything" is one of
 * the worst UX failures we can ship.  Auto-save fires on every decision, and
 * the audit screen restores from this slot if it finds one on mount.
 *
 * Stored under a single key; only the most recent audit is kept.  Cleared
 * automatically when the user uploads a new file (since starting fresh
 * implies they're done with the previous one).
 */

import { Platform } from "react-native";

import { PipelineResponse } from "../api/client";

export type Decision = "pending" | "approved" | "rejected";

export interface IssueState {
  decision: Decision;
  customText?: string;
  /** Reviewer note attached to this decision; survives refresh + restore. */
  note?: string;
  decidedAt?: string;
}

export interface DecisionLogItem {
  violationId: string;
  title: string;
  decision: Decision;
  at: string;
  previous: IssueState;
}

export interface AuditDraft {
  filename: string;
  report: PipelineResponse;
  decisions: Record<string, IssueState>;
  decisionLog: DecisionLogItem[];
  reviewIndex: number;
  savedAt: string;
}

const STORAGE_KEY = "508-audit-draft-v1";

export function saveDraft(draft: AuditDraft): void {
  if (Platform.OS !== "web") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // ignore quota errors
  }
}

export function loadDraft(): AuditDraft | null {
  if (Platform.OS !== "web") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (!parsed.report || !parsed.filename) return null;
    return parsed as AuditDraft;
  } catch {
    return null;
  }
}

export function clearDraft(): void {
  if (Platform.OS !== "web") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
