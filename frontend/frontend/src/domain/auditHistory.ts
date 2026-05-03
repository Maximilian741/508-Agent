/**
 * Persistent audit history.
 *
 * Stores a small, bounded history of recent audits in localStorage so users
 * can see past work without a server.  Each entry has a *summary* (always
 * present, used for the home-page list) plus an *optional* full snapshot
 * (the PipelineResponse + decisions + log + filename) so the user can re-
 * open a past audit without re-uploading the document.
 *
 * Web-only — on native we just keep an in-memory list for the session.
 */

import { Platform } from "react-native";

import { PipelineResponse } from "../api/client";

export interface AuditHistoryEntry {
  /** Stable id, generated client-side. */
  id: string;
  /** Source filename. */
  filename: string;
  /** ISO timestamp when the audit ran. */
  ranAt: string;
  /** Live score at save time. */
  score: number;
  /** Letter grade. */
  grade: string;
  /** How many issues the analyzer found. */
  totalIssues: number;
  /** Document format. */
  sourceFormat: string;
  /** Counts by user decision. */
  approved: number;
  rejected: number;
  pending: number;
  /**
   * Full audit snapshot — present when the user has saved enough state to
   * resume the audit later.  Bounded by `localStorage` quota (~5 MB) so we
   * truncate or skip very large reports.
   */
  snapshot?: AuditSnapshot;
}

export interface AuditSnapshot {
  /** The full PipelineResponse from /pipeline/analyze. */
  report: PipelineResponse;
  /** Per-violation user decisions. */
  decisions: Record<
    string,
    {
      decision: "pending" | "approved" | "rejected";
      customText?: string;
      /** Reviewer note attached to this decision; survives refresh + restore. */
      note?: string;
      decidedAt?: string;
    }
  >;
  /** A bounded tail of the decision log. */
  decisionLog: Array<{
    violationId: string;
    title: string;
    decision: "pending" | "approved" | "rejected";
    at: string;
    previous: {
      decision: "pending" | "approved" | "rejected";
      customText?: string;
      note?: string;
    };
  }>;
}

const STORAGE_KEY = "508-audit-history-v2";
const LEGACY_KEY = "508-audit-history-v1";
const MAX_ENTRIES = 50;
const MAX_SNAPSHOT_BYTES = 350_000;

let memoryStore: AuditHistoryEntry[] = [];

export function loadHistory(): AuditHistoryEntry[] {
  if (Platform.OS !== "web") {
    return [...memoryStore];
  }
  try {
    const raw =
      window.localStorage.getItem(STORAGE_KEY) ||
      window.localStorage.getItem(LEGACY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((e) => e && typeof e.id === "string")
      .slice(0, MAX_ENTRIES) as AuditHistoryEntry[];
  } catch {
    return [];
  }
}

export function saveHistory(entries: AuditHistoryEntry[]): void {
  const trimmed = entries.slice(0, MAX_ENTRIES);
  if (Platform.OS !== "web") {
    memoryStore = trimmed;
    return;
  }
  try {
    const json = JSON.stringify(trimmed);
    window.localStorage.setItem(STORAGE_KEY, json);
  } catch {
    // Likely quota exceeded — try again with snapshots stripped.
    try {
      const slim = trimmed.map(({ snapshot, ...rest }) => rest);
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(slim));
    } catch {
      // ignore
    }
  }
}

export function appendHistory(entry: AuditHistoryEntry): AuditHistoryEntry[] {
  const existing = loadHistory();
  // If the snapshot is too large, drop it to keep summary in storage.
  let safe = entry;
  if (entry.snapshot) {
    try {
      const size = JSON.stringify(entry.snapshot).length;
      if (size > MAX_SNAPSHOT_BYTES) {
        safe = { ...entry, snapshot: undefined };
      }
    } catch {
      safe = { ...entry, snapshot: undefined };
    }
  }
  const next = [safe, ...existing.filter((e) => e.id !== safe.id)].slice(0, MAX_ENTRIES);
  saveHistory(next);
  return next;
}

export function findHistoryEntry(id: string): AuditHistoryEntry | null {
  return loadHistory().find((e) => e.id === id) ?? null;
}

export function clearHistory(): void {
  if (Platform.OS !== "web") {
    memoryStore = [];
    return;
  }
  try {
    window.localStorage.removeItem(STORAGE_KEY);
    window.localStorage.removeItem(LEGACY_KEY);
  } catch {
    // ignore
  }
}
