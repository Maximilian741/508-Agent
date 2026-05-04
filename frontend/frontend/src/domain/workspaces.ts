/**
 * Local-only workspace model.
 *
 * Lets the user keep multiple "projects" (e.g. "Marketing audits", "Q4 docs",
 * "Personal") with their own audit history.  Persisted to localStorage:
 *
 *   508-workspaces-v1         → array of Workspace records
 *   508-active-workspace-v1   → id of the currently selected workspace
 *
 * Audit history keys are namespaced per workspace by callers — see
 * domain/auditHistory.ts for the migration path from the legacy unscoped key.
 */

import { Platform } from "react-native";

export interface Workspace {
  id: string;
  name: string;
  createdAt: string;
  /** Hex color used for the navbar chip + dropdown swatch. */
  color: string;
}

const WORKSPACES_KEY = "508-workspaces-v1";
const ACTIVE_WORKSPACE_KEY = "508-active-workspace-v1";
export const DEFAULT_WORKSPACE_ID = "ws-personal";
const DEFAULT_WORKSPACE_NAME = "Personal";

/** Palette cycled through when minting new workspaces. */
const COLOR_PALETTE = [
  "#2D5BFF",
  "#16A34A",
  "#F59E0B",
  "#DC2626",
  "#7C3AED",
  "#0891B2",
  "#DB2777",
  "#475569",
];

let memoryWorkspaces: Workspace[] | null = null;
let memoryActive: string | null = null;

function _isWeb(): boolean {
  return Platform.OS === "web" && typeof window !== "undefined";
}

function _readWorkspaces(): Workspace[] | null {
  if (!_isWeb()) return memoryWorkspaces;
  try {
    const raw = window.localStorage.getItem(WORKSPACES_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter(
      (w) => w && typeof w.id === "string" && typeof w.name === "string",
    ) as Workspace[];
  } catch {
    return null;
  }
}

function _writeWorkspaces(workspaces: Workspace[]): void {
  if (!_isWeb()) {
    memoryWorkspaces = workspaces;
    return;
  }
  try {
    window.localStorage.setItem(WORKSPACES_KEY, JSON.stringify(workspaces));
  } catch {
    // ignore quota
  }
}

function _readActive(): string | null {
  if (!_isWeb()) return memoryActive;
  try {
    return window.localStorage.getItem(ACTIVE_WORKSPACE_KEY);
  } catch {
    return null;
  }
}

function _writeActive(id: string): void {
  if (!_isWeb()) {
    memoryActive = id;
    return;
  }
  try {
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, id);
  } catch {
    // ignore
  }
}

/** Mint the default "Personal" workspace if storage is empty. */
function _ensureDefault(): Workspace[] {
  const existing = _readWorkspaces();
  if (existing && existing.length > 0) return existing;
  const seed: Workspace = {
    id: DEFAULT_WORKSPACE_ID,
    name: DEFAULT_WORKSPACE_NAME,
    createdAt: new Date().toISOString(),
    color: COLOR_PALETTE[0],
  };
  _writeWorkspaces([seed]);
  if (!_readActive()) {
    _writeActive(seed.id);
  }
  return [seed];
}

export function loadWorkspaces(): Workspace[] {
  return _ensureDefault();
}

export function saveWorkspaces(workspaces: Workspace[]): void {
  _writeWorkspaces(workspaces);
}

export function getActiveWorkspace(): Workspace {
  const all = _ensureDefault();
  const activeId = _readActive();
  const found = all.find((w) => w.id === activeId);
  if (found) return found;
  // Active pointer is stale — fall back to first workspace.
  _writeActive(all[0].id);
  return all[0];
}

export function setActiveWorkspace(id: string): Workspace {
  const all = _ensureDefault();
  const found = all.find((w) => w.id === id);
  if (!found) {
    throw new Error(`Unknown workspace id: ${id}`);
  }
  _writeActive(id);
  return found;
}

export function createWorkspace(name: string): Workspace {
  const all = _ensureDefault();
  const trimmed = (name || "").trim() || `Workspace ${all.length + 1}`;
  const id = `ws-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 6)}`;
  const next: Workspace = {
    id,
    name: trimmed,
    createdAt: new Date().toISOString(),
    color: COLOR_PALETTE[all.length % COLOR_PALETTE.length],
  };
  _writeWorkspaces([...all, next]);
  return next;
}

export function renameWorkspace(id: string, name: string): Workspace[] {
  const all = _ensureDefault();
  const trimmed = (name || "").trim();
  if (!trimmed) return all;
  const next = all.map((w) => (w.id === id ? { ...w, name: trimmed } : w));
  _writeWorkspaces(next);
  return next;
}

export function deleteWorkspace(id: string): Workspace[] {
  let all = _ensureDefault();
  if (all.length <= 1) {
    // Always keep at least one workspace.
    return all;
  }
  all = all.filter((w) => w.id !== id);
  _writeWorkspaces(all);
  // Clean up its history bucket too.
  if (_isWeb()) {
    try {
      window.localStorage.removeItem(historyKeyFor(id));
    } catch {
      // ignore
    }
  }
  // If we deleted the active one, hop to the first remaining.
  if (_readActive() === id) {
    _writeActive(all[0].id);
  }
  return all;
}

/**
 * Per-workspace localStorage key for audit history.  Callers should use this
 * instead of hardcoding the key so a workspace switch swaps the history.
 */
export function historyKeyFor(workspaceId: string): string {
  return `508-audit-history-v2-${workspaceId}`;
}
