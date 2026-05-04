/**
 * Local-only account model.
 *
 * Storage:
 *   508-account-v1   ->  the single Account record (or absent if signed out)
 *
 * This is a pre-backend stub: there is no real auth yet.  Sign-in just mints
 * a local record so the rest of the UI (credit chip, profile, billing) has
 * something to bind to.  When real auth lands, swap the impl of signIn and
 * loadAccount to fetch from the server; the rest of the app shouldn't care.
 *
 * SSR / non-web safe: every storage access is gated behind a window guard.
 */

import { Platform } from "react-native";

export type HistoryKind = "purchase" | "spend" | "grant";

export interface HistoryEntry {
  id: string;
  at: string;
  kind: HistoryKind;
  amount: number;
  description: string;
}

export interface Account {
  id: string;
  email: string;
  displayName: string;
  createdAt: string;
  credits: number;
  history: HistoryEntry[];
}

const ACCOUNT_KEY = "508-account-v1";
const STARTER_CREDITS = 25;

let memoryAccount: Account | null = null;

function _isWeb(): boolean {
  return Platform.OS === "web" && typeof window !== "undefined";
}

function _read(): Account | null {
  if (!_isWeb()) return memoryAccount;
  try {
    const raw = window.localStorage.getItem(ACCOUNT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      !parsed ||
      typeof parsed.id !== "string" ||
      typeof parsed.email !== "string" ||
      typeof parsed.displayName !== "string"
    ) {
      return null;
    }
    return {
      id: parsed.id,
      email: parsed.email,
      displayName: parsed.displayName,
      createdAt: typeof parsed.createdAt === "string" ? parsed.createdAt : new Date().toISOString(),
      credits: typeof parsed.credits === "number" ? parsed.credits : 0,
      history: Array.isArray(parsed.history) ? parsed.history.filter(_isHistoryEntry) : [],
    };
  } catch {
    return null;
  }
}

function _isHistoryEntry(x: any): x is HistoryEntry {
  return (
    x &&
    typeof x.id === "string" &&
    typeof x.at === "string" &&
    (x.kind === "purchase" || x.kind === "spend" || x.kind === "grant") &&
    typeof x.amount === "number" &&
    typeof x.description === "string"
  );
}

function _write(account: Account | null): void {
  if (!_isWeb()) {
    memoryAccount = account;
    return;
  }
  try {
    if (account === null) {
      window.localStorage.removeItem(ACCOUNT_KEY);
    } else {
      window.localStorage.setItem(ACCOUNT_KEY, JSON.stringify(account));
    }
  } catch {
    // ignore quota
  }
}

function _newId(prefix: string): string {
  return prefix + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);
}

function _appendHistory(account: Account, entry: Omit<HistoryEntry, "id" | "at">): Account {
  const full: HistoryEntry = {
    id: _newId("h"),
    at: new Date().toISOString(),
    ...entry,
  };
  return {
    ...account,
    history: [full, ...account.history].slice(0, 200),
  };
}

/** Read the current account from storage, or null when signed out. */
export function loadAccount(): Account | null {
  return _read();
}

/**
 * Mint a new local account if none exists, otherwise update the existing
 * one's email/displayName and return it. Always returns a non-null account.
 */
export function signIn(email: string, displayName?: string): Account {
  const trimmedEmail = (email || "").trim();
  const trimmedName = (displayName || "").trim() || trimmedEmail.split("@")[0] || "You";
  const existing = _read();
  if (existing) {
    const updated: Account = {
      ...existing,
      email: trimmedEmail || existing.email,
      displayName: trimmedName || existing.displayName,
    };
    _write(updated);
    return updated;
  }
  const fresh: Account = {
    id: _newId("acct"),
    email: trimmedEmail,
    displayName: trimmedName,
    createdAt: new Date().toISOString(),
    credits: 0,
    history: [],
  };
  _write(fresh);
  return fresh;
}

/** Forget the local account entirely. */
export function signOut(): void {
  _write(null);
}

/**
 * Add credits and append a history entry. No-op when signed out.
 */
export function addCredits(n: number, description: string): void {
  if (!Number.isFinite(n) || n <= 0) return;
  const acc = _read();
  if (!acc) return;
  const next: Account = {
    ...acc,
    credits: acc.credits + n,
  };
  _write(_appendHistory(next, { kind: "purchase", amount: n, description }));
}

/**
 * Spend credits if balance is sufficient. Returns true on success, false on
 * insufficient funds or signed-out state.
 */
export function spendCredits(n: number, description: string): boolean {
  if (!Number.isFinite(n) || n <= 0) return false;
  const acc = _read();
  if (!acc) return false;
  if (acc.credits < n) return false;
  const next: Account = {
    ...acc,
    credits: acc.credits - n,
  };
  _write(_appendHistory(next, { kind: "spend", amount: -n, description }));
  return true;
}

/**
 * Grant the one-time starter pack of 25 credits. Idempotent: if the account
 * already has any "grant" history entry, this is a no-op.
 */
export function grantStarterCredits(): void {
  const acc = _read();
  if (!acc) return;
  const alreadyGranted = acc.history.some((h) => h.kind === "grant");
  if (alreadyGranted) return;
  const next: Account = {
    ...acc,
    credits: acc.credits + STARTER_CREDITS,
  };
  _write(
    _appendHistory(next, {
      kind: "grant",
      amount: STARTER_CREDITS,
      description: "Welcome bonus - starter credits",
    }),
  );
}
