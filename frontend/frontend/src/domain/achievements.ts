/**
 * Achievements / milestones.
 *
 * User-wide (not workspace-scoped) — these are personal stickers that follow
 * the user across projects.  Stored in localStorage; falls back to an
 * in-memory bucket on native so the in-session toasts still fire.
 *
 * The unlock API is idempotent: calling unlockAchievement('first_audit') ten
 * times only ever toasts/persists once.  That lets call-sites blindly fire
 * unlocks at the obvious points (after analyze, after remediate, after
 * share-link create) without bookkeeping.
 *
 * To celebrate the unlock we lazily import the toast bus so this module
 * itself stays free of UI deps and can be safely imported from anywhere.
 */

import { Platform } from "react-native";

export interface AchievementDef {
  id: string;
  title: string;
  description: string;
  /** Tiny glyph — emoji is fine for v1, looks good in the badge tile. */
  icon: string;
}

export interface Achievement extends AchievementDef {
  /** ISO timestamp of when the badge was first earned, or null if locked. */
  unlockedAt: string | null;
}

const STORAGE_KEY = "508-achievements-v1";

/**
 * The full catalog.  Keep ids stable — they're written to localStorage and
 * checked at runtime.  Order here is the order they appear in the grid.
 */
export const ACHIEVEMENT_DEFS: AchievementDef[] = [
  {
    id: "first_audit",
    title: "First Audit",
    description: "You ran your very first accessibility audit.",
    icon: "🚀",
  },
  {
    id: "first_fix_approved",
    title: "First Fix Approved",
    description: "You approved a remediation and produced a fixed file.",
    icon: "✅",
  },
  {
    id: "ten_audits",
    title: "10 Audits Completed",
    description: "Ten documents audited — you're building a habit.",
    icon: "📚",
  },
  {
    id: "score_ninety_plus",
    title: "Score 90+",
    description: "An audit landed above 90 — top-shelf accessibility.",
    icon: "🏆",
  },
  {
    id: "hundred_issues",
    title: "100 Issues Triaged",
    description: "You've personally reviewed 100 accessibility findings.",
    icon: "🎯",
  },
  {
    id: "first_certificate",
    title: "First Certificate",
    description: "You issued a verifiable remediation certificate.",
    icon: "🔏",
  },
  {
    id: "first_docx",
    title: "First DOCX",
    description: "Your first Word document audit.",
    icon: "📄",
  },
  {
    id: "first_pdf",
    title: "First PDF",
    description: "Your first PDF audit.",
    icon: "📕",
  },
  {
    id: "first_pptx",
    title: "First PPTX",
    description: "Your first PowerPoint deck audit.",
    icon: "📊",
  },
  {
    id: "three_day_streak",
    title: "Three-Day Streak",
    description: "Audited on three different days in a row.",
    icon: "🔥",
  },
];

/** Map id → record for quick lookup. */
const DEF_BY_ID: Record<string, AchievementDef> = ACHIEVEMENT_DEFS.reduce(
  (acc, d) => {
    acc[d.id] = d;
    return acc;
  },
  {} as Record<string, AchievementDef>,
);

/** In-memory fallback for native or when localStorage barfs. */
let memoryUnlocked: Record<string, string> = {};

/**
 * Subscribers fired whenever a *new* achievement is unlocked.  Used by the
 * Achievements screen to throw a confetti burst when the user is currently
 * looking at it (the toast still fires for everyone else).  Stays in this
 * module to avoid a circular import via the toast layer.
 */
type UnlockListener = (id: string) => void;
const unlockListeners = new Set<UnlockListener>();
export function subscribeToAchievementUnlock(listener: UnlockListener): () => void {
  unlockListeners.add(listener);
  return () => {
    unlockListeners.delete(listener);
  };
}

function _isWeb(): boolean {
  return Platform.OS === "web" && typeof window !== "undefined";
}

function _readUnlocked(): Record<string, string> {
  if (!_isWeb()) return { ...memoryUnlocked };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    // Defensive: only keep entries with string ids and string timestamps.
    const safe: Record<string, string> = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof k === "string" && typeof v === "string") {
        safe[k] = v;
      }
    }
    return safe;
  } catch {
    return {};
  }
}

function _writeUnlocked(unlocked: Record<string, string>): void {
  if (!_isWeb()) {
    memoryUnlocked = { ...unlocked };
    return;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(unlocked));
  } catch {
    // ignore quota
  }
}

/**
 * Return the full catalog with unlockedAt populated for any earned ones.
 */
export function loadAchievements(): Achievement[] {
  const unlocked = _readUnlocked();
  return ACHIEVEMENT_DEFS.map((d) => ({
    ...d,
    unlockedAt: unlocked[d.id] ?? null,
  }));
}

export function unlockedCount(): number {
  return Object.keys(_readUnlocked()).length;
}

const TRIAGE_COUNT_KEY = "508-triage-count-v1";

/**
 * Bump the persistent count of findings the user has triaged (approved,
 * rejected, or edited) and unlock the 100-issues badge when it crosses the
 * line. Counter survives reloads on web; in-memory on native.
 */
let memoryTriageCount = 0;
export function recordTriagedFinding(): void {
  let count: number;
  if (_isWeb()) {
    try {
      count = (parseInt(window.localStorage.getItem(TRIAGE_COUNT_KEY) || "0", 10) || 0) + 1;
      window.localStorage.setItem(TRIAGE_COUNT_KEY, String(count));
    } catch {
      count = ++memoryTriageCount;
    }
  } else {
    count = ++memoryTriageCount;
  }
  if (count >= 100) {
    unlockAchievement("hundred_issues");
  }
}

/**
 * Unlock the 3-day-streak badge when the audit history shows runs on three
 * consecutive calendar days (local time). Call after appending history.
 */
export function checkStreakFromHistory(ranAtIsoDates: string[]): void {
  if (isUnlocked("three_day_streak")) return;
  const days = new Set(
    ranAtIsoDates
      .map((iso) => {
        const d = new Date(iso);
        return Number.isFinite(d.getTime())
          ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
          : null;
      })
      .filter(Boolean) as string[],
  );
  if (days.size < 3) return;
  // Check for any 3 consecutive days among the run dates.
  const stamps = [...days]
    .map((k) => {
      const [y, m, d] = k.split("-").map(Number);
      return new Date(y, m, d).getTime();
    })
    .sort((a, b) => a - b);
  const DAY = 24 * 60 * 60 * 1000;
  let run = 1;
  for (let i = 1; i < stamps.length; i++) {
    run = stamps[i] - stamps[i - 1] === DAY ? run + 1 : 1;
    if (run >= 3) {
      unlockAchievement("three_day_streak");
      return;
    }
  }
}

export function isUnlocked(id: string): boolean {
  return Boolean(_readUnlocked()[id]);
}

/**
 * Idempotently unlock an achievement.  Returns true if this call actually
 * unlocked something (so the caller can decide whether to celebrate further);
 * we also fire a toast on first unlock so the user always sees feedback.
 *
 * Unknown ids are silently dropped — there's no value in throwing here, the
 * worst case is a typo and we'd rather not crash an audit flow over a badge.
 */
export function unlockAchievement(id: string): boolean {
  const def = DEF_BY_ID[id];
  if (!def) return false;
  const unlocked = _readUnlocked();
  if (unlocked[id]) return false;
  unlocked[id] = new Date().toISOString();
  _writeUnlocked(unlocked);
  // Lazy-import the toast bus so this module remains a pure-data dep.
  if (_isWeb()) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { toastBus } = require("../ui/toast");
      if (toastBus && typeof toastBus.push === "function") {
        toastBus.push({
          tone: "success",
          title: `🏆 New badge: ${def.title}`,
          description: def.description,
          durationMs: 5000,
        });
      }
    } catch {
      // Toast bus not available — silently skip.
    }
  }
  // Notify any in-app listeners (e.g. the Achievements screen for confetti).
  // Wrapped so a buggy listener can't suppress the unlock return value.
  for (const listener of unlockListeners) {
    try {
      listener(id);
    } catch {
      // ignore listener errors
    }
  }
  return true;
}

/**
 * Reset everything.  Used by the "Replay tour" / dev affordances; not in the
 * user-facing flow today but cheap to ship.
 */
export function clearAchievements(): void {
  if (!_isWeb()) {
    memoryUnlocked = {};
    return;
  }
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

/** Convenience for the dashboard tile. */
export function totalAchievements(): number {
  return ACHIEVEMENT_DEFS.length;
}
